"""Unit tests for the MessageChannel seam and MockChannel (Task 9.1).

Covers:
* ``MockChannel.send`` records the message and returns a deliverable receipt.
* The recorded log is inspectable and ordered.
* ``get_message_channel`` returns a ``MockChannel`` under default/mock config.
* Non-mock providers raise ``NotImplementedError`` (wired in later tasks).
* ``MockChannel`` satisfies the ``MessageChannel`` Protocol — no network calls.

Requirements: 12.2, 6.1
"""

from __future__ import annotations

import dataclasses

import pytest

from pulselink.common.config import MessageChannelProvider, Settings, get_settings
from pulselink.common.enums import SlotResponse
from pulselink.common.models import ContactPoint
from pulselink.messaging.channel import (
    DeliveryReceipt,
    LocalizedMessage,
    MessageAction,
    MessageChannel,
    MockChannel,
    get_message_channel,
)


def _contact(contact_id: str = "c1") -> ContactPoint:
    return ContactPoint(
        contact_id=contact_id,
        subject_id="d1",
        type="phone",
        value_encrypted="enc::+910000000000",
        preferred_lang="te",
    )


def _offer(lang: str = "te") -> LocalizedMessage:
    return LocalizedMessage(
        lang=lang,
        text="Blood slot offer",
        actions=[
            MessageAction(kind="accept", label="Accept", keyword="ACCEPT"),
            MessageAction(kind="decline", label="Decline", keyword="DECLINE"),
        ],
    )


def _settings(channel: MessageChannelProvider) -> Settings:
    """Build settings with the given channel, inheriting all other defaults."""
    return dataclasses.replace(get_settings(), message_channel=channel)


def test_send_records_message_and_returns_receipt():
    """send() returns a deliverable receipt and records the message (Req 6.1)."""
    channel = MockChannel()
    contact = _contact()
    body = _offer()

    receipt = channel.send(contact, body)

    assert isinstance(receipt, DeliveryReceipt)
    assert receipt.status == "delivered"
    assert receipt.channel == "mock"
    assert receipt.to_contact_id == contact.contact_id
    assert receipt.lang == body.lang
    assert receipt.message_id

    # The message is recorded in the inspectable log.
    assert len(channel.sent) == 1
    recorded = channel.sent[0]
    assert recorded.to.contact_id == contact.contact_id
    assert recorded.body.text == body.text
    assert recorded.receipt.message_id == receipt.message_id


def test_recorded_log_is_inspectable_and_ordered_with_unique_ids():
    """Multiple sends are logged in order with unique, deterministic ids."""
    channel = MockChannel()

    r1 = channel.send(_contact("c1"), _offer())
    r2 = channel.send(_contact("c2"), _offer())

    assert [m.receipt.message_id for m in channel.sent] == [r1.message_id, r2.message_id]
    assert r1.message_id != r2.message_id
    assert [m.to.contact_id for m in channel.sent] == ["c1", "c2"]


def test_sent_property_returns_a_copy():
    """Mutating the returned log must not affect the channel's internal state."""
    channel = MockChannel()
    channel.send(_contact(), _offer())

    snapshot = channel.sent
    snapshot.clear()

    assert len(channel.sent) == 1


def test_simulate_inbound_records_accept_decline_reply():
    """The demo channel can simulate an inbound Accept/Decline (offline hook)."""
    channel = MockChannel()
    receipt = channel.send(_contact("c9"), _offer())

    sim = channel.simulate_inbound(receipt.message_id, SlotResponse.ACCEPTED)

    assert sim.message_id == receipt.message_id
    assert sim.contact_id == "c9"
    assert sim.response is SlotResponse.ACCEPTED
    assert channel.inbound == [sim]


def test_simulate_inbound_unknown_message_raises():
    channel = MockChannel()
    with pytest.raises(KeyError):
        channel.simulate_inbound("does-not-exist", SlotResponse.DECLINED)


def test_mock_channel_satisfies_protocol():
    """MockChannel structurally satisfies the MessageChannel seam."""
    assert isinstance(MockChannel(), MessageChannel)


def test_factory_returns_mock_under_default_config():
    """With no overrides, the factory yields the offline MockChannel (Req 12.2)."""
    channel = get_message_channel(_settings(MessageChannelProvider.MOCK))
    assert isinstance(channel, MockChannel)


def test_factory_uses_ambient_settings_when_none_passed():
    """Calling with no settings reads the (default mock) environment config."""
    channel = get_message_channel()
    assert isinstance(channel, MockChannel)


@pytest.mark.parametrize(
    "provider",
    [
        MessageChannelProvider.SMS,
        MessageChannelProvider.WHATSAPP,
        MessageChannelProvider.VOICE,
    ],
)
def test_factory_non_mock_providers_not_yet_implemented(provider):
    """Real providers are wired in later tasks; selecting them fails loudly."""
    with pytest.raises(NotImplementedError):
        get_message_channel(_settings(provider))
