"""Unit tests for ``regenerate_from_window`` wiring (Task 8.3).

Covers Requirement 4.4 — refreshing a patient's subscription from their latest
forecast window after a re-learn changes their cadence. All offline (no DB,
injected/real seams):

* after the cadence shortens, regeneration produces windows that step forward by
  the new (shorter) cadence and cover the horizon with more slots;
* ``subscription_id`` is preserved when the prior subscription is passed, and
  the original ``created_at`` is carried forward while ``updated_at`` advances;
* the horizon is inherited from the prior subscription when not given explicitly;
* each refreshed slot still carries a primary at rank 0 plus ascending backups
  and satisfies the single-active-primary invariant (Requirement 4.5);
* the end-to-end re-learn → regenerate wiring reflects the post-relearn cadence.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import AssignmentStatus, BloodGroup, ConsentScope
from pulselink.common.models import Donor, Patient, TransfusionRecord
from pulselink.forecasting.relearn import (
    InMemoryTransfusionRepo,
    record_transfusion_and_relearn,
)
from pulselink.matching.match import MatchCandidate
from pulselink.reliability.scoring import DonorStats
from pulselink.subscription.generate import (
    generate,
    is_single_active_primary,
    regenerate_from_window,
)

TODAY = date(2024, 1, 1)
LAST_TRANSFUSION = date(2024, 1, 1)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _patient(
    *,
    cadence_days: float = 30.0,
    last_transfusion_date=LAST_TRANSFUSION,
) -> Patient:
    return Patient(
        patient_id="p1",
        city_id="city-1",
        blood_group=BloodGroup.AB_POSITIVE,
        quantity_required=2,
        cadence_days=cadence_days,
        last_transfusion_date=last_transfusion_date,
        consent_id="consent-p1",
    )


def _donor(donor_id: str) -> Donor:
    return Donor(
        donor_id=donor_id,
        city_id="city-1",
        blood_group=BloodGroup.O_NEGATIVE,
        role="Bridge Donor",
        donor_type="Regular Donor",
        next_eligible_date=None,
        eligibility_status="eligible",
        donations_till_date=0,
        total_calls=0,
        calls_to_donations_ratio=0.0,
        consent_id=f"consent-{donor_id}",
    )


def _candidates(*donor_ids: str) -> list[MatchCandidate]:
    return [
        MatchCandidate(_donor(did), DonorStats(donations_till_date=0))
        for did in donor_ids
    ]


def _consent_store(*donor_ids: str) -> InMemoryConsentService:
    store = InMemoryConsentService()
    for did in donor_ids:
        store.grant(did, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    return store


# --------------------------------------------------------------------------- #
# Shorter cadence => windows reflect the new cadence (Requirement 4.4)
# --------------------------------------------------------------------------- #
def test_regenerate_reflects_shorter_cadence_in_windows():
    # Original plan at a 30-day cadence over a 90-day horizon => 3 slots.
    original = _patient(cadence_days=30.0)
    sub = generate(
        original,
        horizon_days=90,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )
    assert len(sub.slots) == math.ceil(90 / 30)
    assert sub.slots[0].window.expected == date(2024, 1, 31)

    # Re-learn shortens the cadence to 15 days; regenerate from the new window.
    updated = _patient(cadence_days=15.0)
    refreshed = regenerate_from_window(
        updated,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        existing_subscription=sub,
    )

    # Cadence on the subscription reflects the new (shorter) cadence.
    assert refreshed.cadence_days == 15.0
    # First window now steps forward by the new cadence (15 days, not 30).
    assert refreshed.slots[0].window.expected == date(2024, 1, 16)
    # The horizon is unchanged (inherited) but is now covered by more slots.
    assert refreshed.horizon_days == 90
    assert len(refreshed.slots) == math.ceil(90 / 15)
    assert len(refreshed.slots) > len(sub.slots)


def test_regenerate_windows_step_forward_by_new_cadence():
    updated = _patient(cadence_days=20.0)
    refreshed = regenerate_from_window(
        updated,
        horizon_days=60,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )
    expected = [s.window.expected for s in refreshed.slots]
    assert expected == [date(2024, 1, 21), date(2024, 2, 10), date(2024, 3, 1)]


# --------------------------------------------------------------------------- #
# subscription_id preservation + lifecycle timestamps
# --------------------------------------------------------------------------- #
def test_subscription_id_preserved_when_prior_subscription_passed():
    sub = generate(
        _patient(cadence_days=30.0),
        horizon_days=60,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        subscription_id="sub-custom-1",
    )
    assert sub.subscription_id == "sub-custom-1"

    refreshed = regenerate_from_window(
        _patient(cadence_days=15.0),
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        existing_subscription=sub,
    )

    # Same plan id, and slot ids are derived from it.
    assert refreshed.subscription_id == "sub-custom-1"
    for slot in refreshed.slots:
        assert slot.subscription_id == "sub-custom-1"
        assert slot.slot_id.startswith("sub-custom-1-slot-")


def test_regenerate_preserves_created_at_and_advances_updated_at():
    created = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
    sub = generate(
        _patient(cadence_days=30.0),
        horizon_days=30,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        now=created,
    )

    refreshed_at = datetime(2024, 1, 20, 9, 30, tzinfo=timezone.utc)
    refreshed = regenerate_from_window(
        _patient(cadence_days=15.0),
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        existing_subscription=sub,
        now=refreshed_at,
    )

    assert refreshed.created_at == created
    assert refreshed.updated_at == refreshed_at


def test_regenerate_does_not_mutate_prior_subscription():
    sub = generate(
        _patient(cadence_days=30.0),
        horizon_days=60,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )
    original_slot_count = len(sub.slots)
    original_cadence = sub.cadence_days

    regenerate_from_window(
        _patient(cadence_days=10.0),
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        existing_subscription=sub,
    )

    assert len(sub.slots) == original_slot_count
    assert sub.cadence_days == original_cadence


# --------------------------------------------------------------------------- #
# Horizon handling
# --------------------------------------------------------------------------- #
def test_horizon_inherited_from_existing_subscription():
    sub = generate(
        _patient(cadence_days=30.0),
        horizon_days=120,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )
    refreshed = regenerate_from_window(
        _patient(cadence_days=30.0),
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        existing_subscription=sub,
    )
    assert refreshed.horizon_days == 120


def test_horizon_required_without_existing_subscription():
    import pytest

    with pytest.raises(ValueError):
        regenerate_from_window(
            _patient(),
            candidates=_candidates("d1"),
            consent_store=_consent_store("d1"),
            today=TODAY,
        )


# --------------------------------------------------------------------------- #
# Slots still carry primary + backups and satisfy the invariant (Req 4.5)
# --------------------------------------------------------------------------- #
def test_refreshed_slots_keep_primary_backups_and_single_primary_invariant():
    sub = generate(
        _patient(cadence_days=30.0),
        horizon_days=60,
        candidates=_candidates("d1", "d2", "d3"),
        consent_store=_consent_store("d1", "d2", "d3"),
        today=TODAY,
    )
    refreshed = regenerate_from_window(
        _patient(cadence_days=15.0),
        candidates=_candidates("d1", "d2", "d3"),
        consent_store=_consent_store("d1", "d2", "d3"),
        today=TODAY,
        existing_subscription=sub,
        max_backups=2,
    )

    assert refreshed.slots  # non-empty
    for slot in refreshed.slots:
        ranks = [a.rank for a in slot.assignments]
        assert ranks == [0, 1, 2]
        primaries = [a for a in slot.assignments if a.rank == 0]
        assert len(primaries) == 1
        assert primaries[0].status is AssignmentStatus.ACTIVE
        # Single-active-primary invariant holds (Requirement 4.5).
        assert is_single_active_primary(slot) is True


# --------------------------------------------------------------------------- #
# End-to-end: re-learn changes cadence, then regenerate reflects it
# --------------------------------------------------------------------------- #
def test_relearn_then_regenerate_reflects_new_cadence():
    # Patient with a sparse history seeded at 30 days.
    patient = _patient(cadence_days=30.0, last_transfusion_date=date(2024, 1, 1))
    sub = generate(
        patient,
        horizon_days=60,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )

    # Record a transfusion ~15 days after the last one; the engine re-learns a
    # shorter cadence from the observed gap.
    repo = InMemoryTransfusionRepo()
    repo.append_transfusion(
        "p1",
        TransfusionRecord(
            record_id="t0", patient_id="p1", date=date(2024, 1, 1), units_given=2
        ),
    )
    est = record_transfusion_and_relearn(
        patient,
        TransfusionRecord(
            record_id="t1", patient_id="p1", date=date(2024, 1, 16), units_given=2
        ),
        repo,
    )
    assert est.cadence_days < 30.0  # cadence shortened from the 15-day gap

    # Build the post-relearn patient and regenerate from the updated window.
    updated = _patient(
        cadence_days=est.cadence_days, last_transfusion_date=date(2024, 1, 16)
    )
    refreshed = regenerate_from_window(
        updated,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        existing_subscription=sub,
        transfusion_dates=repo.get_transfusion_dates("p1"),
        seed_cadence=updated.cadence_days,
    )

    # The refreshed plan reflects the new cadence and anchors on the latest date.
    assert refreshed.subscription_id == sub.subscription_id
    cadence = max(1, round(est.cadence_days))
    # First expected window steps one (new) cadence past the latest transfusion.
    assert refreshed.slots[0].window.expected == date(2024, 1, 16) + timedelta(
        days=cadence
    )
