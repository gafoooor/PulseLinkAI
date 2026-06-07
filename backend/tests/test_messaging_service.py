"""Unit tests for the Messaging Service: notify_slot + handle_inbound (Task 9.3).

Fully offline (``MockChannel`` + ``InMemoryConsentService`` +
``InMemoryAuditLog``). Covers:

* With an active ``contact_for_slots`` consent scope, ``notify_slot`` renders in
  the donor's language, delivers through the channel (recorded), writes a PII-
  free audit entry, and returns a ``sent``/``delivered`` Notification.
* WITHOUT an active scope (never granted, or revoked) ``notify_slot`` sends
  NOTHING — the channel log stays empty and no audit entry is written. This is
  the consent gate (Requirement 7.1 / Property 8).
* ``handle_inbound`` maps ACCEPT / DECLINE (pre-parsed and raw keyword) to the
  right ``SlotResponse`` and advances the tracked notification's state.
* The service fails closed when no consent store is available.

Requirements: 6.1, 7.1
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from pulselink.common.audit import (
    ACTION_NOTIFICATION_SENT,
    InMemoryAuditLog,
)
from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import BloodGroup, ConsentScope, SlotResponse
from pulselink.common.models import (
    ContactPoint,
    Donor,
    Slot,
    Window,
)
from pulselink.messaging.channel import MockChannel
from pulselink.messaging.service import InboundMessage, MessagingService

# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #
DONOR_ID = "donor-1"
CONSENT_VERSION = "v1"


def _donor(preferred_lang: str | None = "te") -> Donor:
    return Donor(
        donor_id=DONOR_ID,
        city_id="city-1",
        blood_group=BloodGroup.O_POSITIVE,
        role="Bridge Donor",
        donor_type="Regular Donor",
        preferred_lang=preferred_lang,
        donations_till_date=5,
        total_calls=10,
        calls_to_donations_ratio=2.0,
        consent_id="consent-1",
    )


def _contact() -> ContactPoint:
    return ContactPoint(
        contact_id="contact-1",
        subject_id=DONOR_ID,
        type="phone",
        value_encrypted="enc::+910000000000",
        preferred_lang="te",
    )


def _slot() -> Slot:
    return Slot(
        slot_id="slot-1",
        subscription_id="sub-1",
        patient_id="bridge-1",
        window=Window(
            start=date(2025, 1, 10),
            expected=date(2025, 1, 12),
            end=date(2025, 1, 14),
        ),
        units_needed=2,
    )


def _consent_with_scope(*scopes: ConsentScope) -> InMemoryConsentService:
    consent = InMemoryConsentService()
    consent.grant(DONOR_ID, "donor", list(scopes), CONSENT_VERSION)
    return consent


# --------------------------------------------------------------------------- #
# notify_slot — happy path (active consent)
# --------------------------------------------------------------------------- #
def test_notify_slot_with_active_consent_sends_localized_offer():
    """Active consent: render in donor lang, deliver, audit, return Notification."""
    channel = MockChannel()
    consent = _consent_with_scope(ConsentScope.CONTACT_FOR_SLOTS)
    audit = InMemoryAuditLog()
    service = MessagingService(channel=channel, consent=consent, audit=audit)

    notification = service.notify_slot(_slot(), _donor("te"), _contact())

    # A Notification is returned in a sent/delivered state.
    assert notification is not None
    assert notification.donor_id == DONOR_ID
    assert notification.slot_id == "slot-1"
    assert notification.status in ("sent", "delivered")
    assert notification.channel == "mock"
    assert notification.sent_at is not None

    # The offer was rendered in the donor's preferred language and delivered.
    assert len(channel.sent) == 1
    sent = channel.sent[0]
    assert sent.to.contact_id == "contact-1"
    assert notification.lang == "te"
    assert sent.body.lang == "te"
    # Slot details + both actions are present (rendering contract, Task 9.2).
    assert "2" in sent.body.text  # units_needed rendered
    kinds = {action.kind for action in sent.body.actions}
    assert kinds == {"accept", "decline"}


def test_notify_slot_writes_pii_free_audit_entry():
    """A sent offer appends one notification.sent audit entry, channel only."""
    channel = MockChannel()
    consent = _consent_with_scope(ConsentScope.CONTACT_FOR_SLOTS)
    audit = InMemoryAuditLog()
    service = MessagingService(channel=channel, consent=consent, audit=audit)

    service.notify_slot(_slot(), _donor(), _contact())

    entries = audit.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == ACTION_NOTIFICATION_SENT
    assert entry.subject_id == DONOR_ID
    assert entry.subject_type == "donor"
    assert "channel=mock" in (entry.detail or "")
    # The plaintext contact value must never reach the audit log (Req 7.4).
    assert "+910000000000" not in (entry.detail or "")


def test_notify_slot_falls_back_to_default_language_when_unknown_lang():
    """An unsupported preferred_lang falls back per the rendering rules."""
    channel = MockChannel()
    consent = _consent_with_scope(ConsentScope.CONTACT_FOR_SLOTS)
    service = MessagingService(channel=channel, consent=consent)

    notification = service.notify_slot(_slot(), _donor("zz"), _contact())

    assert notification is not None
    # "zz" is not in the catalog, so it resolves to a supported fallback lang.
    assert notification.lang != "zz"
    assert channel.sent[0].body.lang == notification.lang


# --------------------------------------------------------------------------- #
# notify_slot — the consent gate (suppression)
# --------------------------------------------------------------------------- #
def test_notify_slot_without_consent_scope_sends_nothing():
    """No active contact_for_slots scope: nothing is sent (the consent gate)."""
    channel = MockChannel()
    # Donor consented only to store_contact, NOT contact_for_slots.
    consent = _consent_with_scope(ConsentScope.STORE_CONTACT)
    audit = InMemoryAuditLog()
    service = MessagingService(channel=channel, consent=consent, audit=audit)

    notification = service.notify_slot(_slot(), _donor(), _contact())

    assert notification is None
    assert channel.sent == []          # channel never called
    assert audit.entries() == []       # no audit entry written


def test_notify_slot_after_revocation_sends_nothing():
    """A revoked donor is suppressed — the consent gate (Req 7.1 / Property 8)."""
    channel = MockChannel()
    consent = _consent_with_scope(ConsentScope.CONTACT_FOR_SLOTS)
    audit = InMemoryAuditLog()
    service = MessagingService(channel=channel, consent=consent, audit=audit)

    consent.revoke(DONOR_ID)

    notification = service.notify_slot(_slot(), _donor(), _contact())

    assert notification is None
    assert channel.sent == []
    assert audit.entries() == []


def test_notify_slot_for_donor_with_no_consent_on_file_sends_nothing():
    """A donor with no consent record at all is suppressed (fail closed per donor)."""
    channel = MockChannel()
    consent = InMemoryConsentService()  # nothing granted to anyone
    service = MessagingService(channel=channel, consent=consent)

    assert service.notify_slot(_slot(), _donor(), _contact()) is None
    assert channel.sent == []


def test_notify_slot_without_any_consent_store_fails_closed():
    """No consent store available at all: the service refuses to send."""
    channel = MockChannel()
    service = MessagingService(channel=channel)  # no consent store injected

    with pytest.raises(ValueError):
        service.notify_slot(_slot(), _donor(), _contact())

    assert channel.sent == []


# --------------------------------------------------------------------------- #
# handle_inbound — mapping replies to SlotResponse + updating state
# --------------------------------------------------------------------------- #
def test_handle_inbound_preparsed_accept_and_decline():
    """A pre-parsed response maps straight through."""
    service = MessagingService(consent=_consent_with_scope())

    assert (
        service.handle_inbound(InboundMessage(response=SlotResponse.ACCEPTED))
        is SlotResponse.ACCEPTED
    )
    assert (
        service.handle_inbound(InboundMessage(response=SlotResponse.DECLINED))
        is SlotResponse.DECLINED
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ACCEPT", SlotResponse.ACCEPTED),
        ("accept", SlotResponse.ACCEPTED),
        ("yes", SlotResponse.ACCEPTED),
        ("1", SlotResponse.ACCEPTED),
        ("DECLINE", SlotResponse.DECLINED),
        (" decline ", SlotResponse.DECLINED),
        ("no", SlotResponse.DECLINED),
        ("2", SlotResponse.DECLINED),
        ("maybe", SlotResponse.NO_RESPONSE),
        ("", SlotResponse.NO_RESPONSE),
    ],
)
def test_handle_inbound_keyword_mapping(text, expected):
    """Raw inbound keywords map to the right SlotResponse (NO_RESPONSE if unknown)."""
    service = MessagingService(consent=_consent_with_scope())
    assert service.handle_inbound(InboundMessage(text=text)) is expected


def test_handle_inbound_updates_tracked_notification_via_message_id():
    """An inbound reply linked by message_id advances the notification state."""
    channel = MockChannel()
    consent = _consent_with_scope(ConsentScope.CONTACT_FOR_SLOTS)
    service = MessagingService(channel=channel, consent=consent)

    notification = service.notify_slot(_slot(), _donor(), _contact())
    assert notification is not None

    # Simulate the donor declining the message the MockChannel delivered.
    message_id = channel.sent[0].receipt.message_id
    sim = channel.simulate_inbound(message_id, SlotResponse.DECLINED)

    response = service.handle_inbound(InboundMessage.from_inbound_simulation(sim))

    assert response is SlotResponse.DECLINED
    updated = service.get_notification(notification.notification_id)
    assert updated is not None
    assert updated.status == "responded"
    assert updated.response is SlotResponse.DECLINED
    assert updated.responded_at is not None


def test_handle_inbound_updates_tracked_notification_via_notification_id():
    """An inbound reply linked by notification_id advances the notification state."""
    channel = MockChannel()
    consent = _consent_with_scope(ConsentScope.CONTACT_FOR_SLOTS)
    service = MessagingService(channel=channel, consent=consent)

    notification = service.notify_slot(_slot(), _donor(), _contact())
    assert notification is not None

    response = service.handle_inbound(
        InboundMessage(notification_id=notification.notification_id, text="ACCEPT")
    )

    assert response is SlotResponse.ACCEPTED
    updated = service.get_notification(notification.notification_id)
    assert updated is not None
    assert updated.status == "responded"
    assert updated.response is SlotResponse.ACCEPTED


def test_handle_inbound_unknown_reference_still_returns_response():
    """A reply with no matching tracked notification still resolves a response."""
    service = MessagingService(consent=_consent_with_scope())

    response = service.handle_inbound(
        InboundMessage(message_id="never-sent", text="ACCEPT")
    )

    assert response is SlotResponse.ACCEPTED
    assert service.notifications == []
