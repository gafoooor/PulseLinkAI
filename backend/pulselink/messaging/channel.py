"""The pluggable ``MessageChannel`` seam (Task 9.1).

This module defines the base messaging seam that lets the Notification /
Messaging Service deliver slot offers through whatever provider is configured,
without the business logic knowing or caring which one:

* ``MockChannel``  — the offline demo channel. It "delivers" a message by
  recording it in an in-memory log and returning a deliverable receipt. No
  network, no app install, fully deterministic.
* real ``sms`` / ``whatsapp`` providers — selected by config in production
  (implemented in later tasks).
* the ``voice`` provider — PulseLink Voice's ``VoiceChannel`` *extends* this
  same seam (Task 10.1), so the supporting types here are intentionally kept
  small and cleanly extensible.

Scope note (Task 9.1 only): this file defines the base seam, the supporting
message/receipt types, the ``MockChannel``, and the config-selectable
``get_message_channel`` factory. Multilingual template rendering (9.2),
``notify_slot`` / ``handle_inbound`` and the consent gate (9.3), and the voice
channel (10.x) are implemented in their own tasks.

Requirements: 12.2, 6.1
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from pulselink.common.config import MessageChannelProvider, Settings, get_settings
from pulselink.common.enums import SlotResponse
from pulselink.common.models import ContactPoint, NotificationChannel

# A delivery receipt's lifecycle status. ``MockChannel`` reports ``delivered``;
# real providers may also report ``queued`` / ``sent`` / ``failed``.
DeliveryStatus = Literal["queued", "sent", "delivered", "failed"]

# The two donor actions every offer carries, regardless of channel.
MessageActionKind = Literal["accept", "decline"]


# --------------------------------------------------------------------------- #
# Supporting message / receipt types
# --------------------------------------------------------------------------- #
class MessageAction(BaseModel):
    """A single donor action attached to an offer — Accept or Decline.

    Carries both a channel-agnostic ``keyword`` (e.g. an SMS reply word such as
    ``ACCEPT``) and an optional ``deep_link`` (the install-free Donor_Interface
    URL). A channel renders whichever representation it supports.
    """

    kind: MessageActionKind
    label: str = Field(min_length=1)
    keyword: Optional[str] = None
    deep_link: Optional[str] = None


class LocalizedMessage(BaseModel):
    """A rendered, localized offer ready to hand to a channel.

    ``text`` is rendered in the donor's preferred language (template rendering
    is Task 9.2); ``actions`` carries the Accept / Decline choices.
    """

    lang: str = Field(min_length=1)
    text: str = Field(min_length=1)
    actions: list[MessageAction] = Field(default_factory=list)


class DeliveryReceipt(BaseModel):
    """The result of handing a message to a channel.

    Kept deliberately small and channel-agnostic so the ``VoiceChannel``
    (Task 10.1) can return the same receipt shape from its ``send`` while
    layering its richer ``VoiceOutcome`` on top.
    """

    message_id: str = Field(min_length=1)
    channel: NotificationChannel
    status: DeliveryStatus
    to_contact_id: str = Field(min_length=1)
    lang: Optional[str] = None
    sent_at: datetime


# --------------------------------------------------------------------------- #
# The MessageChannel seam
# --------------------------------------------------------------------------- #
@runtime_checkable
class MessageChannel(Protocol):
    """Pluggable delivery seam (Requirement 12.2).

    ``MockChannel`` serves the offline demo; real SMS / WhatsApp providers plug
    in for production. PulseLink Voice's ``VoiceChannel`` extends this Protocol
    (Task 10.1), so implementations only need a ``send`` to participate.
    """

    def send(self, to: ContactPoint, body: LocalizedMessage) -> DeliveryReceipt:
        """Deliver ``body`` to the contact ``to`` and return a receipt."""
        ...


# --------------------------------------------------------------------------- #
# MockChannel — the offline demo implementation
# --------------------------------------------------------------------------- #
class SentMessage(BaseModel):
    """An inspectable record of one message the ``MockChannel`` "delivered"."""

    to: ContactPoint
    body: LocalizedMessage
    receipt: DeliveryReceipt


class InboundSimulation(BaseModel):
    """A simulated inbound donor response recorded by the demo channel.

    This lets a test/demo simulate an Accept/Decline against a previously sent
    message without any telephony or app. The full inbound-handling flow
    (``handle_inbound``) is Task 9.3; this is just the offline hook.
    """

    message_id: str = Field(min_length=1)
    contact_id: str = Field(min_length=1)
    response: SlotResponse


class MockChannel:
    """Offline ``MessageChannel`` that records messages instead of sending them.

    Delivering a message appends it to an in-memory log and returns a
    deliverable receipt with a deterministic, monotonically increasing
    ``message_id``. Nothing leaves the process; no app install is required.
    The recorded log is inspectable via :attr:`sent` so tests/demos can assert
    what was "delivered", and :meth:`simulate_inbound` lets them simulate an
    Accept/Decline reply.
    """

    CHANNEL: NotificationChannel = "mock"

    def __init__(self) -> None:
        self._sent: list[SentMessage] = []
        self._inbound: list[InboundSimulation] = []
        self._counter: int = 0

    def send(self, to: ContactPoint, body: LocalizedMessage) -> DeliveryReceipt:
        """Record the message in-memory and return a deliverable receipt."""
        self._counter += 1
        receipt = DeliveryReceipt(
            message_id=f"mock-msg-{self._counter}",
            channel=self.CHANNEL,
            status="delivered",
            to_contact_id=to.contact_id,
            lang=body.lang,
            sent_at=datetime.now(timezone.utc),
        )
        self._sent.append(SentMessage(to=to, body=body, receipt=receipt))
        return receipt

    @property
    def sent(self) -> list[SentMessage]:
        """A copy of the recorded delivery log (most recent last)."""
        return list(self._sent)

    @property
    def inbound(self) -> list[InboundSimulation]:
        """A copy of the simulated inbound-response log."""
        return list(self._inbound)

    def simulate_inbound(
        self, message_id: str, response: SlotResponse
    ) -> InboundSimulation:
        """Simulate a donor's Accept/Decline reply to a previously sent message.

        Looks up the original sent message to resolve the contact it was
        delivered to, records the simulated response, and returns it. Raises
        ``KeyError`` if ``message_id`` was never sent through this channel.
        """
        for record in self._sent:
            if record.receipt.message_id == message_id:
                simulation = InboundSimulation(
                    message_id=message_id,
                    contact_id=record.to.contact_id,
                    response=response,
                )
                self._inbound.append(simulation)
                return simulation
        raise KeyError(f"no message sent with message_id={message_id!r}")


# --------------------------------------------------------------------------- #
# Config-selectable factory
# --------------------------------------------------------------------------- #
def get_message_channel(settings: Optional[Settings] = None) -> MessageChannel:
    """Return the configured ``MessageChannel`` (Requirement 12.2).

    Defaults to ``MockChannel`` for the offline demo. The ``sms`` / ``whatsapp``
    providers and the ``voice`` provider (PulseLink Voice's ``VoiceChannel``,
    Task 10.1) are wired in by later tasks; selecting them now raises
    ``NotImplementedError`` so the seam fails loudly rather than silently
    falling back.
    """
    settings = settings or get_settings()
    provider = settings.message_channel

    if provider is MessageChannelProvider.MOCK:
        return MockChannel()

    if provider in (MessageChannelProvider.SMS, MessageChannelProvider.WHATSAPP):
        raise NotImplementedError(
            f"MESSAGE_CHANNEL={provider.value!r} is configured but the real "
            "SMS/WhatsApp provider is implemented in a later task. Use 'mock' "
            "for the offline demo."
        )

    if provider is MessageChannelProvider.VOICE:
        raise NotImplementedError(
            "MESSAGE_CHANNEL='voice' selects PulseLink Voice's VoiceChannel, "
            "which is implemented in Task 10.1. Use 'mock' for the offline demo."
        )

    raise NotImplementedError(  # pragma: no cover - defensive, enum is exhaustive
        f"Unsupported MESSAGE_CHANNEL={provider!r}"
    )
