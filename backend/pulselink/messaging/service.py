"""The Notification / Messaging Service: notify_slot + handle_inbound (Task 9.3).

This module wires the messaging seams built in Tasks 9.1 / 9.2 into the two
operations the design's Component 5 (Notification / Messaging Service) exposes:

* :meth:`MessagingService.notify_slot` — send a single-slot offer (Accept /
  Decline) to one donor through the pluggable ``MessageChannel`` and the
  install-free Donor_Interface (Requirement 6.1), **gated by consent**.
* :meth:`MessagingService.handle_inbound` — process an inbound ACCEPT / DECLINE
  reply (a keyword, a deep-link tap, or a pre-parsed response) into exactly one
  :class:`~pulselink.common.enums.SlotResponse`, updating the tracked
  notification's state. It is channel-agnostic and works directly with
  ``MockChannel.simulate_inbound``.

The consent gate (Requirement 7.1 / design Property 8)
------------------------------------------------------
Before *any* message leaves for a Contact_Point, :meth:`notify_slot` asks the
:class:`~pulselink.common.consent.ConsentStore` whether the donor holds an
**active** ``contact_for_slots`` scope at send time. If not, the offer is
**suppressed**: the channel is never called, no audit entry is written, and
``None`` is returned. This is the single, authoritative send-time check that
makes Property 8 hold — *for every notification actually sent, the recipient
had an active ``contact_for_slots`` scope at send time*.

The service **fails closed**: it requires a consent store to send at all. A
``MessagingService`` constructed without one (and not given one per call) raises
rather than silently sending without checking consent — a missing consent store
is a configuration error, never an implicit "allow".

Scope note (Task 9.3 only): this owns ``notify_slot`` / ``handle_inbound`` and
the consent gate. The ``MessageChannel`` seam + ``MockChannel`` are Task 9.1
(``channel.py``); multilingual rendering is Task 9.2 (``templates.py``). The
decline → auto-promote-backup flow is Task 12.x and the voice channel is
Task 10.x — neither is implemented here.

Requirements: 6.1, 7.1
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from pulselink.common.audit import AuditSink, audit_notification_sent
from pulselink.common.consent import ConsentStore
from pulselink.common.enums import ConsentScope, SlotResponse
from pulselink.common.models import Donor, ContactPoint, Notification, Slot
from pulselink.messaging.channel import MessageChannel, get_message_channel
from pulselink.messaging.templates import OfferContext, render_offer

# The audit actor recorded for a sent slot offer. Ids / channel metadata only —
# never a contact value (Requirement 7.4, enforced by audit_notification_sent).
MESSAGING_ACTOR = "messaging_service"

# Channel-agnostic inbound keyword vocabulary. SMS / WhatsApp replies arrive as
# free text; these map a raw reply to a donor action. The template catalog's
# default keywords ("ACCEPT" / "DECLINE") are covered, plus common synonyms and
# the 1 / 2 DTMF-style digits a donor might text back.
_ACCEPT_KEYWORDS = {"accept", "yes", "y", "1"}
_DECLINE_KEYWORDS = {"decline", "no", "n", "2"}


# --------------------------------------------------------------------------- #
# Inbound message
# --------------------------------------------------------------------------- #
class InboundMessage(BaseModel):
    """A donor's inbound reply to a slot offer, before it is interpreted.

    Deliberately channel-agnostic so SMS / WhatsApp / mock all funnel through
    the same handler. A reply may arrive as:

    * a pre-parsed ``response`` (e.g. from ``MockChannel.simulate_inbound`` or a
      deep-link tap that already resolved the action), or
    * raw ``text`` (an SMS / WhatsApp keyword such as ``"ACCEPT"`` / ``"2"``).

    ``message_id`` (the channel's delivery id) and / or ``notification_id`` link
    the reply back to the offer the service sent, so the matching notification's
    state can be updated. When neither resolves a tracked notification, the reply
    is still interpreted into a :class:`SlotResponse` and returned.
    """

    response: Optional[SlotResponse] = None
    text: Optional[str] = None
    message_id: Optional[str] = None
    notification_id: Optional[str] = None
    donor_id: Optional[str] = None
    slot_id: Optional[str] = None

    @classmethod
    def from_inbound_simulation(cls, sim: object) -> "InboundMessage":
        """Build an :class:`InboundMessage` from a ``MockChannel`` simulation.

        Duck-typed against any object exposing ``message_id`` and ``response``
        (such as ``pulselink.messaging.channel.InboundSimulation``) so the
        handler stays decoupled from the concrete channel type.
        """
        return cls(
            message_id=getattr(sim, "message_id", None),
            response=getattr(sim, "response", None),
        )


def _interpret_keyword(text: str) -> SlotResponse:
    """Map a raw inbound keyword to a :class:`SlotResponse` (NO_RESPONSE if unknown)."""
    token = text.strip().lower()
    if token in _ACCEPT_KEYWORDS:
        return SlotResponse.ACCEPTED
    if token in _DECLINE_KEYWORDS:
        return SlotResponse.DECLINED
    return SlotResponse.NO_RESPONSE


# --------------------------------------------------------------------------- #
# The Messaging Service
# --------------------------------------------------------------------------- #
class MessagingService:
    """Sends consent-gated slot offers and interprets inbound replies.

    Dependencies are injected so the whole service runs offline and
    deterministically in tests (``MockChannel`` + ``InMemoryConsentService`` +
    ``InMemoryAuditLog``):

    * ``channel``  — the :class:`MessageChannel` to deliver through. Defaults to
      :func:`~pulselink.messaging.channel.get_message_channel` (the configured
      provider, ``MockChannel`` for the demo) when neither injected nor passed
      per call.
    * ``consent``  — the :class:`~pulselink.common.consent.ConsentStore` that
      gates every send (Requirement 7.1). **Required to send**: the service
      fails closed if none is available.
    * ``audit``    — an optional :class:`~pulselink.common.audit.AuditSink`. When
      present, every *sent* offer appends a PII-free ``notification.sent`` entry.

    Sent notifications are tracked in-process (by ``notification_id`` and by the
    channel's delivery ``message_id``) so :meth:`handle_inbound` can update the
    right notification when a reply comes back.
    """

    def __init__(
        self,
        *,
        channel: Optional[MessageChannel] = None,
        consent: Optional[ConsentStore] = None,
        audit: Optional[AuditSink] = None,
    ) -> None:
        self._channel = channel
        self._consent = consent
        self._audit = audit
        self._notifications: dict[str, Notification] = {}
        self._notification_by_message_id: dict[str, str] = {}

    def notify_slot(
        self,
        slot: Slot,
        donor: Donor,
        contact_point: ContactPoint,
        *,
        channel: Optional[MessageChannel] = None,
        consent: Optional[ConsentStore] = None,
        audit: Optional[AuditSink] = None,
    ) -> Optional[Notification]:
        """Send a single-slot Accept/Decline offer, gated by consent.

        The consent gate runs **first** (Requirement 7.1 / Property 8): unless
        the donor holds an active ``contact_for_slots`` scope at this moment, the
        offer is suppressed — the channel is never called, no audit entry is
        written, and ``None`` is returned.

        When consent is active, the offer is rendered in the donor's
        ``preferred_lang`` (Task 9.2, with default-language fallback), delivered
        through the channel, audited (channel name only, no PII — Req 7.4), and
        returned as a tracked :class:`Notification`.

        Per-call ``channel`` / ``consent`` / ``audit`` override the injected
        dependencies. The service fails closed: if no consent store is available
        (neither injected nor passed), it raises ``ValueError`` rather than
        sending unchecked.
        """
        consent = consent or self._consent
        if consent is None:
            raise ValueError(
                "notify_slot requires a ConsentStore to honour the contact_for_slots "
                "consent gate (Requirement 7.1); none was injected or passed."
            )

        # --- The consent gate (Requirement 7.1 / Property 8) --------------- #
        if not consent.has_active_scope(donor.donor_id, ConsentScope.CONTACT_FOR_SLOTS):
            return None

        channel = channel or self._channel or get_message_channel()
        audit = audit or self._audit

        message = render_offer(
            OfferContext(
                bridge_id=slot.patient_id,
                window_start=slot.window.start,
                window_end=slot.window.end,
                units_needed=slot.units_needed,
            ),
            preferred_lang=donor.preferred_lang,
        )

        receipt = channel.send(contact_point, message)

        notification = Notification(
            notification_id=f"notif-{uuid.uuid4().hex}",
            donor_id=donor.donor_id,
            slot_id=slot.slot_id,
            channel=receipt.channel,
            lang=message.lang,
            status=receipt.status,
            sent_at=receipt.sent_at,
        )
        self._notifications[notification.notification_id] = notification
        self._notification_by_message_id[receipt.message_id] = notification.notification_id

        if audit is not None:
            audit_notification_sent(
                audit,
                MESSAGING_ACTOR,
                donor.donor_id,
                receipt.channel,
                detail=f"slot={slot.slot_id}",
            )

        return notification

    def handle_inbound(self, inbound: InboundMessage) -> SlotResponse:
        """Interpret an inbound reply into one :class:`SlotResponse`.

        A pre-parsed ``response`` is used directly; otherwise the raw ``text``
        keyword is interpreted (unknown keywords resolve to ``NO_RESPONSE``).
        When the reply links to a tracked notification (by ``notification_id`` or
        the channel's ``message_id``), that notification is advanced to
        ``responded`` with the resolved response and a service-set timestamp.
        The resolved :class:`SlotResponse` is always returned.
        """
        if inbound.response is not None:
            response = inbound.response
        elif inbound.text is not None:
            response = _interpret_keyword(inbound.text)
        else:
            response = SlotResponse.NO_RESPONSE

        notification = self._resolve_notification(inbound)
        if notification is not None:
            updated = notification.model_copy(
                update={
                    "status": "responded",
                    "response": response,
                    "responded_at": datetime.now(timezone.utc),
                }
            )
            self._notifications[updated.notification_id] = updated

        return response

    # ----- Tracked-notification access ------------------------------------ #
    def get_notification(self, notification_id: str) -> Optional[Notification]:
        """Return a tracked notification by id, or ``None`` if unknown."""
        return self._notifications.get(notification_id)

    @property
    def notifications(self) -> list[Notification]:
        """A copy of all notifications this service has sent (send order)."""
        return list(self._notifications.values())

    def _resolve_notification(self, inbound: InboundMessage) -> Optional[Notification]:
        """Find the tracked notification a reply refers to, if any."""
        if inbound.notification_id is not None:
            found = self._notifications.get(inbound.notification_id)
            if found is not None:
                return found
        if inbound.message_id is not None:
            notification_id = self._notification_by_message_id.get(inbound.message_id)
            if notification_id is not None:
                return self._notifications.get(notification_id)
        return None
