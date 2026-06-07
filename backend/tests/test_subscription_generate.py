"""Unit tests for subscription generation (Task 8.1).

Covers the two acceptance criteria of :func:`pulselink.subscription.generate`,
fully offline (no DB, injected/real seams):

* horizon coverage with the expected number of slots (Requirement 4.4);
* one primary at rank 0 + backups at ascending ranks per slot (Requirement 4.4);
* every slot window is ordered ``start <= expected <= end``;
* graceful handling when the matcher returns fewer donors than desired backups;
* graceful handling when no eligible donors exist (uncovered slot, no crash);
* at most one rank-0 primary per slot (Requirement 4.5, structural).
"""

from __future__ import annotations

import math
from datetime import date

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import AssignmentStatus, BloodGroup, ConsentScope
from pulselink.common.models import Donor, Patient, Window
from pulselink.matching.match import MatchCandidate, RankedDonor
from pulselink.reliability.scoring import DonorStats
from pulselink.subscription.generate import generate

TODAY = date(2024, 1, 1)
LAST_TRANSFUSION = date(2024, 1, 1)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _patient(
    *,
    cadence_days: float = 30.0,
    blood_group: BloodGroup = BloodGroup.AB_POSITIVE,
    last_transfusion_date=LAST_TRANSFUSION,
    quantity_required: int = 2,
) -> Patient:
    return Patient(
        patient_id="p1",
        city_id="city-1",
        blood_group=blood_group,
        quantity_required=quantity_required,
        cadence_days=cadence_days,
        last_transfusion_date=last_transfusion_date,
        consent_id="consent-p1",
    )


def _donor(donor_id: str, *, blood_group: BloodGroup = BloodGroup.O_NEGATIVE) -> Donor:
    return Donor(
        donor_id=donor_id,
        city_id="city-1",
        blood_group=blood_group,
        role="Bridge Donor",
        donor_type="Regular Donor",
        next_eligible_date=None,
        eligibility_status="eligible",
        donations_till_date=0,
        total_calls=0,
        calls_to_donations_ratio=0.0,
        consent_id=f"consent-{donor_id}",
    )


def _candidates(*donor_ids: str, donations: int = 0) -> list[MatchCandidate]:
    return [
        MatchCandidate(_donor(did), DonorStats(donations_till_date=donations))
        for did in donor_ids
    ]


def _consent_store(*donor_ids: str) -> InMemoryConsentService:
    store = InMemoryConsentService()
    for did in donor_ids:
        store.grant(did, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    return store


def _stub_rank(ranked: list[RankedDonor]):
    """Return a rank_fn seam that always yields the same ranked list."""

    def _fn(*, candidates, **_kwargs):  # noqa: ANN001
        return list(ranked)

    return _fn


def _ranked_donor(donor_id: str, score: float) -> RankedDonor:
    return RankedDonor(
        donor_id=donor_id,
        donor=_donor(donor_id),
        score=score,
        tier="growing",
        distance_km=0.0,
    )


# --------------------------------------------------------------------------- #
# Horizon coverage (Requirement 4.4)
# --------------------------------------------------------------------------- #
def test_subscription_covers_horizon_with_expected_slot_count():
    patient = _patient(cadence_days=30.0)
    candidates = _candidates("d1", "d2", "d3")
    store = _consent_store("d1", "d2", "d3")

    sub = generate(
        patient,
        horizon_days=90,
        candidates=candidates,
        consent_store=store,
        today=TODAY,
    )

    # ceil(90 / 30) == 3 slots cover the horizon.
    assert len(sub.slots) == 3
    assert sub.horizon_days == 90
    assert sub.patient_id == "p1"


def test_horizon_shorter_than_cadence_still_yields_at_least_one_slot():
    patient = _patient(cadence_days=30.0)
    sub = generate(
        patient,
        horizon_days=10,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )
    assert len(sub.slots) == 1


def test_slot_windows_step_forward_by_cadence():
    patient = _patient(cadence_days=30.0)
    sub = generate(
        patient,
        horizon_days=90,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )
    expected_dates = [s.window.expected for s in sub.slots]
    # First expected is anchor + 30; each subsequent slot steps another 30 days.
    assert expected_dates == [
        date(2024, 1, 31),
        date(2024, 3, 1),
        date(2024, 3, 31),
    ]


# --------------------------------------------------------------------------- #
# Primary at rank 0 + ascending backups (Requirement 4.4)
# --------------------------------------------------------------------------- #
def test_each_slot_has_primary_at_rank_zero_and_ascending_backups():
    patient = _patient()
    # Three eligible donors with descending reliability so order is deterministic.
    candidates = [
        MatchCandidate(_donor("high"), DonorStats(donations_till_date=10)),
        MatchCandidate(_donor("mid"), DonorStats(donations_till_date=5)),
        MatchCandidate(_donor("low"), DonorStats(donations_till_date=0)),
    ]
    store = _consent_store("high", "mid", "low")

    sub = generate(
        patient,
        horizon_days=60,
        candidates=candidates,
        consent_store=store,
        today=TODAY,
        max_backups=2,
    )

    for slot in sub.slots:
        ranks = [a.rank for a in slot.assignments]
        # Exactly one primary (rank 0) plus ascending backup ranks 1, 2.
        assert ranks == [0, 1, 2]
        primaries = [a for a in slot.assignments if a.rank == 0]
        assert len(primaries) == 1
        assert primaries[0].status is AssignmentStatus.ACTIVE
        # Reliability order preserved: high -> mid -> low.
        assert [a.donor_id for a in slot.assignments] == ["high", "mid", "low"]


def test_at_most_one_primary_per_slot():
    patient = _patient()
    sub = generate(
        patient,
        horizon_days=90,
        candidates=_candidates("d1", "d2", "d3"),
        consent_store=_consent_store("d1", "d2", "d3"),
        today=TODAY,
    )
    for slot in sub.slots:
        assert sum(1 for a in slot.assignments if a.rank == 0) <= 1


# --------------------------------------------------------------------------- #
# Window ordering
# --------------------------------------------------------------------------- #
def test_every_slot_window_is_ordered():
    patient = _patient()
    sub = generate(
        patient,
        horizon_days=120,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )
    for slot in sub.slots:
        w = slot.window
        assert w.start <= w.expected <= w.end


# --------------------------------------------------------------------------- #
# Fewer donors than desired backups (graceful)
# --------------------------------------------------------------------------- #
def test_fewer_donors_than_desired_backups_assigns_what_is_available():
    patient = _patient()
    # Only one eligible donor but we ask for 2 backups (desired = 3 total).
    sub = generate(
        patient,
        horizon_days=30,
        candidates=_candidates("solo"),
        consent_store=_consent_store("solo"),
        today=TODAY,
        max_backups=2,
    )
    slot = sub.slots[0]
    assert [a.rank for a in slot.assignments] == [0]
    assert slot.assignments[0].donor_id == "solo"


def test_backups_capped_at_max_backups():
    patient = _patient()
    # Five eligible donors but max_backups=1 => primary + 1 backup only.
    ranked = [_ranked_donor(f"d{i}", score=float(50 - i)) for i in range(5)]
    sub = generate(
        patient,
        horizon_days=30,
        candidates=_candidates("d0", "d1", "d2", "d3", "d4"),
        consent_store=_consent_store("d0", "d1", "d2", "d3", "d4"),
        today=TODAY,
        max_backups=1,
        rank_fn=_stub_rank(ranked),
    )
    slot = sub.slots[0]
    assert [a.rank for a in slot.assignments] == [0, 1]
    assert [a.donor_id for a in slot.assignments] == ["d0", "d1"]


# --------------------------------------------------------------------------- #
# No eligible donors (uncovered slot, no crash)
# --------------------------------------------------------------------------- #
def test_no_eligible_donors_leaves_slot_uncovered():
    patient = _patient()
    # Candidates exist but none have consent granted => matcher returns none.
    sub = generate(
        patient,
        horizon_days=90,
        candidates=_candidates("d1", "d2"),
        consent_store=_consent_store(),  # no consent grants
        today=TODAY,
    )
    assert len(sub.slots) == 3
    for slot in sub.slots:
        assert slot.assignments == []


def test_empty_candidate_list_does_not_crash():
    patient = _patient()
    sub = generate(
        patient,
        horizon_days=30,
        candidates=[],
        consent_store=_consent_store(),
        today=TODAY,
    )
    assert len(sub.slots) == 1
    assert sub.slots[0].assignments == []


# --------------------------------------------------------------------------- #
# Brand-new patient (no last transfusion date) anchors at today
# --------------------------------------------------------------------------- #
def test_new_patient_without_last_transfusion_anchors_at_today():
    patient = _patient(last_transfusion_date=None)
    sub = generate(
        patient,
        horizon_days=60,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
    )
    assert len(sub.slots) == math.ceil(60 / 30)
    # First window expected is today + cadence (30 days).
    assert sub.slots[0].window.expected == date(2024, 1, 31)
