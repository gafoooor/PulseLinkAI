"""Property-based test for the messaging consent gate (Task 9.4).

**Property 8: Consent gate** — for every notification actually sent, the
recipient holds an active ``contact_for_slots`` scope at send time.

**Validates: Requirements 7.1**

This exercises the real :meth:`MessagingService.notify_slot` implementation
(no mocks) against a real ``MockChannel`` + ``InMemoryConsentService`` +
``InMemoryAuditLog`` across a wide, intelligently constrained consent space:

* donors with an active ``contact_for_slots`` scope (alone or alongside other
  scopes) — the send-allowed region,
* donors with no consent record at all,
* donors who consented only to *other* scopes (``store_contact`` etc.) but not
  ``contact_for_slots``,
* donors whose consent (whatever its scopes) was subsequently *revoked*.

The property is an IFF: a message lands in the channel's sent log (and a
Notification is returned, and exactly one audit entry is written) **if and only
if** the donor held an active ``contact_for_slots`` scope at send time. When the
gate suppresses the offer, the channel log stays empty, no audit entry is
written, and ``notify_slot`` returns ``None``.
"""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.audit import ACTION_NOTIFICATION_SENT, InMemoryAuditLog
from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import BloodGroup, ConsentScope, SlotResponse
from pulselink.common.models import ContactPoint, Donor, Slot, Window
from pulselink.messaging.channel import MockChannel
from pulselink.messaging.service import MessagingService

DONOR_ID = "donor-1"
CONSENT_VERSION = "v1"

# The full scope vocabulary, used to build varied grants — some including the
# gating CONTACT_FOR_SLOTS scope, some deliberately excluding it.
_ALL_SCOPES = list(ConsentScope)


# --------------------------------------------------------------------------- #
# Minimal valid domain builders
# --------------------------------------------------------------------------- #
def _donor(preferred_lang: str | None) -> Donor:
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


# --------------------------------------------------------------------------- #
# Consent-state strategy
# --------------------------------------------------------------------------- #
@st.composite
def _consent_state(draw: st.DrawFn) -> tuple[InMemoryConsentService, bool]:
    """Build a consent store for the donor and report the expected gate result.

    Returns ``(consent_service, expected_active)`` where ``expected_active`` is
    True iff the donor holds an active ``contact_for_slots`` scope at send time
    — i.e. consent was granted, includes that scope, and was not revoked.
    """
    consent = InMemoryConsentService()

    has_record = draw(st.booleans())
    if not has_record:
        # No consent on file at all → gate must suppress.
        return consent, False

    scopes = draw(
        st.lists(st.sampled_from(_ALL_SCOPES), unique=True, max_size=len(_ALL_SCOPES))
    )
    consent.grant(DONOR_ID, "donor", scopes, CONSENT_VERSION)

    revoked = draw(st.booleans())
    if revoked:
        consent.revoke(DONOR_ID)

    expected_active = (ConsentScope.CONTACT_FOR_SLOTS in scopes) and not revoked
    return consent, expected_active


# Languages: supported, unsupported, and unset — none of which affects the gate.
_langs = st.sampled_from(["te", "hi", "en", "zz", None])


@given(state=_consent_state(), preferred_lang=_langs)
def test_consent_gate_iff_active_scope(
    state: tuple[InMemoryConsentService, bool], preferred_lang: str | None
) -> None:
    """Property 8: a message is sent IFF the donor has an active scope at send time."""
    consent, expected_active = state
    channel = MockChannel()
    audit = InMemoryAuditLog()
    service = MessagingService(channel=channel, consent=consent, audit=audit)

    notification = service.notify_slot(_slot(), _donor(preferred_lang), _contact())

    # Ground truth: what the authoritative consent check reports at send time.
    assert (
        consent.has_active_scope(DONOR_ID, ConsentScope.CONTACT_FOR_SLOTS)
        is expected_active
    )

    if expected_active:
        # Send-allowed region: exactly one message in the channel log, a
        # Notification returned, and exactly one PII-free audit entry.
        assert notification is not None
        assert len(channel.sent) == 1
        assert channel.sent[0].to.contact_id == "contact-1"
        assert notification.donor_id == DONOR_ID
        assert notification.slot_id == "slot-1"

        entries = audit.entries()
        assert len(entries) == 1
        assert entries[0].action == ACTION_NOTIFICATION_SENT
        assert entries[0].subject_id == DONOR_ID
        # The plaintext contact value must never reach the audit log (Req 7.4).
        assert "+910000000000" not in (entries[0].detail or "")
    else:
        # Suppressed region: nothing sent, nothing returned, nothing audited.
        assert notification is None
        assert channel.sent == []
        assert audit.entries() == []


@given(state=_consent_state(), preferred_lang=_langs)
def test_every_sent_message_had_active_scope(
    state: tuple[InMemoryConsentService, bool], preferred_lang: str | None
) -> None:
    """Property 8 (the universal direction): if anything was sent, the scope was active.

    This is the core safety direction of the property — it must be impossible
    for a message to appear in the channel's sent log unless the donor held an
    active ``contact_for_slots`` scope at the moment of sending.
    """
    consent, _ = state
    channel = MockChannel()
    service = MessagingService(channel=channel, consent=consent)

    service.notify_slot(_slot(), _donor(preferred_lang), _contact())

    if channel.sent:
        assert consent.has_active_scope(DONOR_ID, ConsentScope.CONTACT_FOR_SLOTS)
