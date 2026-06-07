"""Privacy integration tests for consent, RBAC, encryption, and audit (Task 13.5).

Tasks 13.1–13.4 each ship focused unit tests for their own seam. This file is
the *integration* layer: it weaves those seams together through the real
``MessagingService`` send path so the privacy guarantees are exercised end to
end, the way they actually compose at runtime.

Five things are covered (Requirements 7.1, 7.2, 7.4, 7.5, 11.1):

1. **Send-time consent gate + revocation suppression (7.1, 7.2).** A donor with
   an active ``contact_for_slots`` scope is contacted; after :meth:`revoke`,
   ``has_active_scope`` flips to ``False``, the messaging gate suppresses the
   offer (channel never called, nothing audited, ``None`` returned), and a
   ``consent_revoked`` event fires so pending work can be purged.
2. **Role-scoped access enforcement (7.5, 10.2).** A coordinator can view a
   patient in their own city but not cross-city; a donor sees only their own
   assignment; :func:`require` raises ``AccessDenied`` on the deny path.
3. **PII redaction + encryption at rest (7.4).** :func:`redact` masks a phone;
   :func:`store_contact` stores only ciphertext (plaintext absent) and
   :func:`reveal` round-trips on the authorized path.
4. **Audit entry creation (11.1).** ``audit_notification_sent`` /
   ``audit_parse_confirmed`` append entries carrying actor / action / timestamp.
5. **No plaintext PII anywhere in the trail (7.4).** Across a full
   send-then-confirm flow, the raw phone value never appears in any audit field
   nor in the channel's delivery log.

Offline only: ``InMemoryConsentService`` + ``MockChannel`` +
``InMemoryAuditLog`` + the local ``ContactCipher``.

Requirements: 7.1, 7.2, 7.4, 7.5, 11.1
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from pulselink.common.audit import (
    ACTION_NOTIFICATION_SENT,
    ACTION_PARSE_CONFIRMED,
    InMemoryAuditLog,
    audit_parse_confirmed,
)
from pulselink.common.consent import EVENT_CONSENT_REVOKED, InMemoryConsentService
from pulselink.common.encryption import ContactCipher, redact, reveal, store_contact
from pulselink.common.enums import (
    AssignmentStatus,
    BloodGroup,
    ConsentScope,
    SlotStatus,
)
from pulselink.common.event_bus import InMemoryEventBus
from pulselink.common.models import (
    Donor,
    Patient,
    Slot,
    SlotAssignment,
    Window,
)
from pulselink.gateway.rbac import (
    AccessDenied,
    Principal,
    Role,
    can_view_patient,
    can_view_slot_assignment,
    require,
)
from pulselink.messaging.service import MESSAGING_ACTOR, MessagingService

KEY = "change-me-demo-only-32byte-key!!"
RAW_PHONE = "+919876543210"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def make_patient(patient_id: str = "p1", city_id: str = "hyd") -> Patient:
    return Patient(
        patient_id=patient_id,
        city_id=city_id,
        blood_group=BloodGroup.O_POSITIVE,
        quantity_required=2,
        cadence_days=21,
        consent_id="consent-p1",
    )


def make_donor(donor_id: str = "donor-1", lang: str | None = "en") -> Donor:
    return Donor(
        donor_id=donor_id,
        city_id="hyd",
        blood_group=BloodGroup.O_POSITIVE,
        role="Bridge Donor",
        donor_type="Regular Donor",
        preferred_lang=lang,
        donations_till_date=4,
        total_calls=6,
        calls_to_donations_ratio=1.5,
        consent_id="consent-donor-1",
    )


def make_slot(slot_id: str = "s1", patient_id: str = "p1") -> Slot:
    return Slot(
        slot_id=slot_id,
        subscription_id="sub1",
        patient_id=patient_id,
        window=Window(
            start=date(2024, 6, 1),
            expected=date(2024, 6, 3),
            end=date(2024, 6, 5),
        ),
        units_needed=2,
        status=SlotStatus.OFFERED,
    )


def make_assignment(donor_id: str = "donor-1", slot_id: str = "s1") -> SlotAssignment:
    return SlotAssignment(
        assignment_id="a1",
        slot_id=slot_id,
        donor_id=donor_id,
        rank=0,
        status=AssignmentStatus.ACTIVE,
    )


def build_messaging_stack():
    """Wire the offline messaging stack with consent, channel, and audit.

    Returns the bus, consent service, mock channel, audit log, and the
    ``MessagingService`` that ties them together — the same composition used
    at runtime, minus the network.
    """
    bus = InMemoryEventBus()
    consent = InMemoryConsentService(event_bus=bus)
    from pulselink.messaging.channel import MockChannel

    channel = MockChannel()
    audit = InMemoryAuditLog()
    service = MessagingService(channel=channel, consent=consent, audit=audit)
    return bus, consent, channel, audit, service


# =========================================================================== #
# 1. Send-time consent gate + revocation suppression (Req 7.1, 7.2)
# =========================================================================== #
def test_send_gate_allows_then_revocation_suppresses_and_fires_event():
    """Grant → send succeeds; revoke → send suppressed + consent_revoked fires."""
    bus, consent, channel, audit, service = build_messaging_stack()

    revoked_events: list[dict] = []
    bus.subscribe(EVENT_CONSENT_REVOKED, revoked_events.append)

    donor = make_donor()
    slot = make_slot()
    cipher = ContactCipher(KEY)
    contact = store_contact(donor.donor_id, "phone", RAW_PHONE, cipher=cipher)

    # Grant the contact_for_slots scope: the send-time gate now passes.
    consent.grant(
        donor.donor_id, "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v1"
    )
    sent = service.notify_slot(slot, donor, contact)

    assert sent is not None
    assert len(channel.sent) == 1
    # A sent offer is audited (Req 11.1).
    assert [e.action for e in audit.entries()] == [ACTION_NOTIFICATION_SENT]

    # Revocation: has_active_scope flips, the gate suppresses the next offer,
    # and the cascade event fires so pending work can be purged (Req 7.2).
    consent.revoke(donor.donor_id)
    assert consent.has_active_scope(donor.donor_id, ConsentScope.CONTACT_FOR_SLOTS) is False

    suppressed = service.notify_slot(slot, donor, contact)

    assert suppressed is None
    # No second message was delivered and no second audit entry was written.
    assert len(channel.sent) == 1
    assert len(audit.entries()) == 1
    # The revocation cascade fired exactly once carrying the subject id.
    assert len(revoked_events) == 1
    assert revoked_events[0]["subject_id"] == donor.donor_id


def test_send_suppressed_when_consent_never_granted():
    """With no consent on file the gate suppresses the offer from the start."""
    _, _, channel, audit, service = build_messaging_stack()
    donor = make_donor(donor_id="donor-2")
    contact = store_contact(donor.donor_id, "phone", RAW_PHONE)

    result = service.notify_slot(make_slot(), donor, contact)

    assert result is None
    assert channel.sent == []
    assert audit.entries() == []


# =========================================================================== #
# 2. Role-scoped access enforcement (Req 7.5, 10.2)
# =========================================================================== #
def test_coordinator_sees_in_city_patient_not_cross_city():
    """A coordinator is bound to their assigned city (Req 10.2)."""
    coordinator = Principal(role=Role.COORDINATOR, subject_id="staff1", city_id="hyd")
    in_city = make_patient(patient_id="p1", city_id="hyd")
    cross_city = make_patient(patient_id="p2", city_id="blr")

    assert can_view_patient(coordinator, in_city) is True
    assert can_view_patient(coordinator, cross_city) is False


def test_donor_sees_only_their_own_assignment():
    """A donor may view their own assigned slot but not another donor's."""
    own = Principal(role=Role.DONOR, subject_id="donor-1")
    mine = make_assignment(donor_id="donor-1")
    someone_elses = make_assignment(donor_id="donor-9")

    assert can_view_slot_assignment(own, mine) is True
    assert can_view_slot_assignment(own, someone_elses) is False


def test_require_raises_access_denied_on_deny_path():
    """The require() guard turns a denied check into an HTTP 403 AccessDenied."""
    intruder = Principal(role=Role.PATIENT, subject_id="p1")
    other_patient = make_patient(patient_id="p2", city_id="hyd")

    with pytest.raises(AccessDenied) as exc_info:
        require(can_view_patient(intruder, other_patient), "not your patient")

    assert exc_info.value.status_code == 403
    assert isinstance(exc_info.value, PermissionError)

    # And it passes silently when the check allows access.
    require(can_view_patient(Principal(role=Role.PATIENT, subject_id="p2"), other_patient))


# =========================================================================== #
# 3. PII redaction + encryption at rest (Req 7.4)
# =========================================================================== #
def test_redact_masks_phone_for_safe_logging():
    """redact() leaves only the last two characters visible."""
    masked = redact(RAW_PHONE)

    assert masked.endswith("10")
    assert masked[:-2] == "*" * (len(RAW_PHONE) - 2)
    # No run of real phone digits survives.
    assert "9876543210" not in masked


def test_store_contact_holds_only_ciphertext_and_reveal_round_trips():
    """Stored ContactPoint carries ciphertext only; reveal() recovers plaintext."""
    cipher = ContactCipher(KEY)
    contact = store_contact("donor-1", "phone", RAW_PHONE, cipher=cipher)

    # Plaintext is absent from what is persisted (Req 7.3 / 7.4).
    assert contact.value_encrypted != RAW_PHONE
    assert RAW_PHONE not in contact.value_encrypted
    assert "9876543210" not in contact.value_encrypted
    # The narrow authorized path round-trips back to the original value.
    assert reveal(contact, cipher=cipher) == RAW_PHONE


# =========================================================================== #
# 4. Audit entry creation (Req 11.1)
# =========================================================================== #
def test_audit_helpers_append_entries_with_actor_action_timestamp():
    """parse-confirmed and notification-sent both append well-formed entries."""
    audit = InMemoryAuditLog()
    before = datetime.now(timezone.utc)

    audit_parse_confirmed(audit, "coordinator:7", "bridge-9", subject_type="patient")

    bus, consent, channel, _, service = build_messaging_stack()
    donor = make_donor()
    consent.grant(donor.donor_id, "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v1")
    service.notify_slot(make_slot(), donor, store_contact(donor.donor_id, "phone", RAW_PHONE), audit=audit)

    after = datetime.now(timezone.utc)
    entries = audit.entries()
    actions = [e.action for e in entries]
    assert ACTION_PARSE_CONFIRMED in actions
    assert ACTION_NOTIFICATION_SENT in actions

    for entry in entries:
        assert entry.actor  # actor present
        assert entry.action  # action present
        assert entry.timestamp.tzinfo is not None
        assert before <= entry.timestamp <= after

    # The notification entry is attributed to the messaging service.
    notif_entry = next(e for e in entries if e.action == ACTION_NOTIFICATION_SENT)
    assert notif_entry.actor == MESSAGING_ACTOR
    assert notif_entry.subject_id == "donor-1"


# =========================================================================== #
# 5. No plaintext PII anywhere in the trail (Req 7.4)
# =========================================================================== #
def test_no_raw_phone_value_in_audit_or_channel_log_across_flow():
    """A full send flow never leaks the raw phone into audit or delivery logs."""
    _, consent, channel, audit, service = build_messaging_stack()
    donor = make_donor()
    slot = make_slot()
    contact = store_contact(donor.donor_id, "phone", RAW_PHONE)
    consent.grant(donor.donor_id, "donor", [ConsentScope.CONTACT_FOR_SLOTS], version="v1")

    service.notify_slot(slot, donor, contact)

    # Audit fields never contain the raw phone (Req 7.4).
    for entry in audit.entries():
        for field in (entry.actor, entry.action, entry.subject_id, entry.subject_type, entry.detail):
            assert RAW_PHONE not in (field or "")

    # The channel only ever holds the encrypted contact, never the plaintext.
    for record in channel.sent:
        assert record.to.value_encrypted != RAW_PHONE
        assert RAW_PHONE not in record.to.value_encrypted
        assert RAW_PHONE not in record.body.text
