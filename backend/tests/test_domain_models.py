"""Unit tests for the shared domain enums and models (Task 1.2).

Covers:
* the closed enum value sets (8 blood groups, tiers, statuses, responses, scopes),
* valid model construction for every entity,
* the validation rules: ``cadence_days > 0``, ``quantity_required >= 1``,
  ``donations_till_date >= 0``, ``units_given >= 1``, future-date rejection,
  the Window ordering invariant, score/confidence bounds, the
  Patient-must-reference-consent rule, the clinical-cadence flag-not-reject
  behaviour, and that Notification.channel includes ``"voice"``.

Requirements: 1.3, 2.1, 3.1, 4.4, 5.5, 10.1
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pulselink.common.enums import (
    AssignmentStatus,
    BloodGroup,
    ConsentScope,
    DonorTier,
    SlotResponse,
    SlotStatus,
)
from pulselink.common.models import (
    CLINICAL_CADENCE_MAX_DAYS,
    CLINICAL_CADENCE_MIN_DAYS,
    Consent,
    ContactPoint,
    Donor,
    Notification,
    Patient,
    ReliabilityComponents,
    ReliabilityScore,
    Slot,
    SlotAssignment,
    Subscription,
    TransfusionRecord,
    Window,
)

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)
TODAY = date(2024, 1, 1)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
def test_blood_group_has_eight_canonical_values():
    assert {bg.value for bg in BloodGroup} == {
        "A Positive",
        "A Negative",
        "B Positive",
        "B Negative",
        "AB Positive",
        "AB Negative",
        "O Positive",
        "O Negative",
    }


def test_donor_tier_values():
    assert {t.value for t in DonorTier} == {"anchor", "steady", "growing"}


def test_slot_status_values():
    assert {s.value for s in SlotStatus} == {
        "planned",
        "offered",
        "confirmed",
        "fulfilled",
        "missed",
    }


def test_assignment_status_values():
    assert {a.value for a in AssignmentStatus} == {
        "active",
        "declined",
        "confirmed",
        "promoted",
        "expired",
    }


def test_slot_response_values():
    assert {r.value for r in SlotResponse} == {"ACCEPTED", "DECLINED", "NO_RESPONSE"}


def test_consent_scope_values():
    assert {c.value for c in ConsentScope} == {
        "store_contact",
        "contact_for_slots",
        "share_with_coordinator",
        "use_in_forecasting",
    }


# --------------------------------------------------------------------------- #
# Builders for valid instances
# --------------------------------------------------------------------------- #
def make_patient(**overrides) -> Patient:
    base = dict(
        patient_id="bridge-1",
        city_id="hyd",
        blood_group=BloodGroup.B_POSITIVE,
        quantity_required=2,
        cadence_days=21,
        consent_id="consent-1",
    )
    base.update(overrides)
    return Patient(**base)


def make_donor(**overrides) -> Donor:
    base = dict(
        donor_id="user-1",
        city_id="hyd",
        blood_group=BloodGroup.O_POSITIVE,
        role="Bridge Donor",
        donor_type="Regular Donor",
        preferred_lang="te",
        lat=17.38,
        lng=78.48,
        eligibility_status="eligible",
        donations_till_date=10,
        total_calls=12,
        calls_to_donations_ratio=1.2,
        consent_id="consent-2",
    )
    base.update(overrides)
    return Donor(**base)


# --------------------------------------------------------------------------- #
# Patient
# --------------------------------------------------------------------------- #
def test_patient_valid_construction():
    p = make_patient()
    assert p.patient_id == "bridge-1"
    assert p.blood_group is BloodGroup.B_POSITIVE
    assert p.bridge_status == "active"


@pytest.mark.parametrize("bad_cadence", [0, -1, -30])
def test_patient_rejects_non_positive_cadence(bad_cadence):
    with pytest.raises(ValidationError):
        make_patient(cadence_days=bad_cadence)


@pytest.mark.parametrize("bad_qty", [0, -1])
def test_patient_rejects_quantity_below_one(bad_qty):
    with pytest.raises(ValidationError):
        make_patient(quantity_required=bad_qty)


def test_patient_requires_consent_reference():
    with pytest.raises(ValidationError):
        make_patient(consent_id="")


def test_patient_in_band_cadence_is_flagged_true():
    assert make_patient(cadence_days=CLINICAL_CADENCE_MIN_DAYS).cadence_in_clinical_band
    assert make_patient(cadence_days=CLINICAL_CADENCE_MAX_DAYS).cadence_in_clinical_band


def test_patient_out_of_band_cadence_allowed_but_flagged_false():
    # Out-of-band cadence is accepted (not rejected) but flagged via the helper.
    p = make_patient(cadence_days=1958)
    assert p.cadence_days == 1958
    assert p.cadence_in_clinical_band is False
    assert make_patient(cadence_days=3).cadence_in_clinical_band is False


# --------------------------------------------------------------------------- #
# TransfusionRecord
# --------------------------------------------------------------------------- #
def test_transfusion_record_valid():
    r = TransfusionRecord(
        record_id="r1",
        patient_id="bridge-1",
        date=date(2023, 12, 1),
        units_given=2,
        recorded_by="coord-1",
    )
    assert r.units_given == 2


def test_transfusion_record_rejects_future_date():
    future = datetime.now(timezone.utc).date() + timedelta(days=5)
    with pytest.raises(ValidationError):
        TransfusionRecord(
            record_id="r1",
            patient_id="bridge-1",
            date=future,
            units_given=1,
            recorded_by="coord-1",
        )


@pytest.mark.parametrize("bad_units", [0, -3])
def test_transfusion_record_rejects_units_below_one(bad_units):
    with pytest.raises(ValidationError):
        TransfusionRecord(
            record_id="r1",
            patient_id="bridge-1",
            date=date(2023, 12, 1),
            units_given=bad_units,
            recorded_by="coord-1",
        )


# --------------------------------------------------------------------------- #
# Donor
# --------------------------------------------------------------------------- #
def test_donor_valid_construction():
    d = make_donor()
    assert d.role == "Bridge Donor"
    assert d.donations_till_date == 10


@pytest.mark.parametrize("bad_donations", [-1, -100])
def test_donor_rejects_negative_donations(bad_donations):
    with pytest.raises(ValidationError):
        make_donor(donations_till_date=bad_donations)


def test_donor_rejects_negative_calls_and_ratio():
    with pytest.raises(ValidationError):
        make_donor(total_calls=-1)
    with pytest.raises(ValidationError):
        make_donor(calls_to_donations_ratio=-0.5)


def test_donor_rejects_invalid_role():
    with pytest.raises(ValidationError):
        make_donor(role="Random Donor")


# --------------------------------------------------------------------------- #
# ReliabilityScore + components
# --------------------------------------------------------------------------- #
def make_score(**overrides) -> ReliabilityScore:
    base = dict(
        donor_id="user-1",
        score=72.5,
        tier=DonorTier.STEADY,
        components=ReliabilityComponents(
            acceptance_ratio=0.8,
            recency_factor=0.6,
            volume_factor=0.5,
            call_efficiency=0.7,
        ),
        computed_at=NOW,
    )
    base.update(overrides)
    return ReliabilityScore(**base)


def test_reliability_score_valid():
    s = make_score()
    assert s.tier is DonorTier.STEADY


@pytest.mark.parametrize("bad_score", [-1, 100.1, 250])
def test_reliability_score_out_of_bounds_rejected(bad_score):
    with pytest.raises(ValidationError):
        make_score(score=bad_score)


def test_reliability_components_bounds_enforced():
    with pytest.raises(ValidationError):
        ReliabilityComponents(
            acceptance_ratio=1.5,
            recency_factor=0.6,
            volume_factor=0.5,
            call_efficiency=0.7,
        )


# --------------------------------------------------------------------------- #
# Window
# --------------------------------------------------------------------------- #
def test_window_valid_ordering():
    w = Window(start=date(2024, 1, 1), expected=date(2024, 1, 3), end=date(2024, 1, 5))
    assert w.start <= w.expected <= w.end


def test_window_rejects_out_of_order():
    with pytest.raises(ValidationError):
        Window(start=date(2024, 1, 5), expected=date(2024, 1, 3), end=date(2024, 1, 1))


# --------------------------------------------------------------------------- #
# Slot, SlotAssignment, Subscription
# --------------------------------------------------------------------------- #
def make_assignment(**overrides) -> SlotAssignment:
    base = dict(
        assignment_id="a1",
        slot_id="s1",
        donor_id="user-1",
        rank=0,
        status=AssignmentStatus.ACTIVE,
    )
    base.update(overrides)
    return SlotAssignment(**base)


def make_slot(**overrides) -> Slot:
    base = dict(
        slot_id="s1",
        subscription_id="sub1",
        patient_id="bridge-1",
        window=Window(
            start=date(2024, 1, 1), expected=date(2024, 1, 3), end=date(2024, 1, 5)
        ),
        units_needed=2,
        status=SlotStatus.PLANNED,
        assignments=[make_assignment()],
    )
    base.update(overrides)
    return Slot(**base)


def test_slot_and_assignment_construction():
    slot = make_slot()
    assert slot.assignments[0].rank == 0
    assert slot.status is SlotStatus.PLANNED


def test_assignment_rejects_negative_rank():
    with pytest.raises(ValidationError):
        make_assignment(rank=-1)


def test_subscription_construction_with_slots():
    sub = Subscription(
        subscription_id="sub1",
        patient_id="bridge-1",
        cadence_days=21,
        horizon_days=90,
        slots=[make_slot()],
        created_at=NOW,
        updated_at=NOW,
    )
    assert len(sub.slots) == 1


@pytest.mark.parametrize("bad_cadence", [0, -5])
def test_subscription_rejects_non_positive_cadence(bad_cadence):
    with pytest.raises(ValidationError):
        Subscription(
            subscription_id="sub1",
            patient_id="bridge-1",
            cadence_days=bad_cadence,
            horizon_days=90,
            created_at=NOW,
            updated_at=NOW,
        )


# --------------------------------------------------------------------------- #
# Notification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("channel", ["sms", "whatsapp", "voice", "mock"])
def test_notification_accepts_all_channels_including_voice(channel):
    n = Notification(
        notification_id="n1",
        donor_id="user-1",
        slot_id="s1",
        channel=channel,
        lang="te",
    )
    assert n.channel == channel
    assert n.status == "queued"


def test_notification_rejects_unknown_channel():
    with pytest.raises(ValidationError):
        Notification(
            notification_id="n1",
            donor_id="user-1",
            slot_id="s1",
            channel="carrier-pigeon",
            lang="te",
        )


# --------------------------------------------------------------------------- #
# Consent + ContactPoint
# --------------------------------------------------------------------------- #
def test_consent_active_and_scope_helpers():
    c = Consent(
        consent_id="consent-1",
        subject_id="user-1",
        subject_type="donor",
        scopes=[ConsentScope.CONTACT_FOR_SLOTS, ConsentScope.STORE_CONTACT],
        granted_at=NOW,
        version="v1",
    )
    assert c.is_active
    assert c.has_active_scope(ConsentScope.CONTACT_FOR_SLOTS)
    assert not c.has_active_scope(ConsentScope.USE_IN_FORECASTING)


def test_consent_revoked_has_no_active_scope():
    c = Consent(
        consent_id="consent-1",
        subject_id="user-1",
        subject_type="donor",
        scopes=[ConsentScope.CONTACT_FOR_SLOTS],
        granted_at=NOW,
        revoked_at=NOW + timedelta(days=1),
        version="v1",
    )
    assert not c.is_active
    assert not c.has_active_scope(ConsentScope.CONTACT_FOR_SLOTS)


def test_contact_point_construction():
    cp = ContactPoint(
        contact_id="c1",
        subject_id="user-1",
        type="phone",
        value_encrypted="enc::abc123",
        preferred_lang="te",
    )
    assert cp.value_encrypted == "enc::abc123"
    assert cp.type == "phone"
