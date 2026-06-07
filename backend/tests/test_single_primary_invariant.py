"""Unit tests for the single-active-primary invariant (Task 8.2).

Covers Requirement 4.5 — "WHILE a slot is not fulfilled, THE
Subscription_Generator SHALL maintain at most one Slot_Assignment with primary
status for that slot at any time" — for the helpers appended to
:mod:`pulselink.subscription.generate`:

* a freshly generated slot satisfies the invariant;
* a slot manually given two active rank-0 primaries is detected as violating,
  and ``enforce_single_active_primary`` normalizes it back to exactly one;
* a slot with one active primary plus active backups (rank > 0) is valid
  (backups are not primaries);
* a FULFILLED slot is vacuously valid (the invariant only binds while
  unfulfilled);
* the chosen status convention (ACTIVE/CONFIRMED count; DECLINED/PROMOTED/
  EXPIRED do not).
"""

from __future__ import annotations

from datetime import date

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import AssignmentStatus, BloodGroup, ConsentScope, SlotStatus
from pulselink.common.models import Donor, Patient, Slot, SlotAssignment, Window
from pulselink.matching.match import MatchCandidate
from pulselink.reliability.scoring import DonorStats
from pulselink.subscription.generate import (
    active_primary,
    enforce_single_active_primary,
    generate,
    is_active_primary,
    is_single_active_primary,
)

TODAY = date(2024, 1, 1)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _window() -> Window:
    return Window(start=date(2024, 1, 28), expected=date(2024, 1, 31), end=date(2024, 2, 3))


def _assignment(rank: int, status: AssignmentStatus, donor_id: str | None = None) -> SlotAssignment:
    did = donor_id or f"d{rank}"
    return SlotAssignment(
        assignment_id=f"a-{did}-r{rank}",
        slot_id="slot-1",
        donor_id=did,
        rank=rank,
        status=status,
    )


def _slot(assignments: list[SlotAssignment], *, status: SlotStatus = SlotStatus.PLANNED) -> Slot:
    return Slot(
        slot_id="slot-1",
        subscription_id="sub-1",
        patient_id="p1",
        window=_window(),
        units_needed=2,
        status=status,
        assignments=assignments,
    )


def _patient() -> Patient:
    return Patient(
        patient_id="p1",
        city_id="city-1",
        blood_group=BloodGroup.AB_POSITIVE,
        quantity_required=2,
        cadence_days=30.0,
        last_transfusion_date=date(2024, 1, 1),
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
    return [MatchCandidate(_donor(d), DonorStats(donations_till_date=0)) for d in donor_ids]


def _consent_store(*donor_ids: str) -> InMemoryConsentService:
    store = InMemoryConsentService()
    for d in donor_ids:
        store.grant(d, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    return store


# --------------------------------------------------------------------------- #
# is_active_primary: convention
# --------------------------------------------------------------------------- #
def test_rank_zero_active_is_active_primary():
    assert is_active_primary(_assignment(0, AssignmentStatus.ACTIVE)) is True


def test_rank_zero_confirmed_is_active_primary():
    assert is_active_primary(_assignment(0, AssignmentStatus.CONFIRMED)) is True


def test_active_backup_is_not_primary():
    # rank > 0 is never a primary, even when ACTIVE.
    assert is_active_primary(_assignment(1, AssignmentStatus.ACTIVE)) is False


def test_declined_or_promoted_or_expired_primary_is_not_active():
    for status in (
        AssignmentStatus.DECLINED,
        AssignmentStatus.PROMOTED,
        AssignmentStatus.EXPIRED,
    ):
        assert is_active_primary(_assignment(0, status)) is False


# --------------------------------------------------------------------------- #
# Freshly generated slots satisfy the invariant
# --------------------------------------------------------------------------- #
def test_freshly_generated_slots_satisfy_the_invariant():
    sub = generate(
        _patient(),
        horizon_days=90,
        candidates=_candidates("d1", "d2", "d3"),
        consent_store=_consent_store("d1", "d2", "d3"),
        today=TODAY,
    )
    assert sub.slots  # non-empty
    for slot in sub.slots:
        assert is_single_active_primary(slot) is True
        primary = active_primary(slot)
        assert primary is not None
        assert primary.rank == 0
        assert primary.status is AssignmentStatus.ACTIVE


# --------------------------------------------------------------------------- #
# One primary + active backups is valid (backups are not primaries)
# --------------------------------------------------------------------------- #
def test_one_primary_with_active_backups_is_valid():
    slot = _slot(
        [
            _assignment(0, AssignmentStatus.ACTIVE, "primary"),
            _assignment(1, AssignmentStatus.ACTIVE, "backup1"),
            _assignment(2, AssignmentStatus.ACTIVE, "backup2"),
        ]
    )
    assert is_single_active_primary(slot) is True
    assert active_primary(slot).donor_id == "primary"


# --------------------------------------------------------------------------- #
# Two active rank-0 primaries: detected + normalized
# --------------------------------------------------------------------------- #
def test_two_active_primaries_are_detected_as_violating():
    slot = _slot(
        [
            _assignment(0, AssignmentStatus.ACTIVE, "primary-a"),
            _assignment(0, AssignmentStatus.ACTIVE, "primary-b"),
            _assignment(1, AssignmentStatus.ACTIVE, "backup1"),
        ]
    )
    assert is_single_active_primary(slot) is False


def test_enforce_normalizes_two_primaries_to_one():
    slot = _slot(
        [
            _assignment(0, AssignmentStatus.ACTIVE, "primary-a"),
            _assignment(0, AssignmentStatus.CONFIRMED, "primary-b"),
            _assignment(1, AssignmentStatus.ACTIVE, "backup1"),
        ]
    )

    fixed = enforce_single_active_primary(slot)

    # Exactly one active primary now, and the invariant holds.
    assert is_single_active_primary(fixed) is True
    primaries = [a for a in fixed.assignments if is_active_primary(a)]
    assert len(primaries) == 1
    # The first active primary in order is kept as the rank-0 primary.
    assert primaries[0].donor_id == "primary-a"
    assert primaries[0].rank == 0

    # No donor is dropped; the duplicate is demoted to a backup (rank > 0).
    donor_ids = {a.donor_id for a in fixed.assignments}
    assert donor_ids == {"primary-a", "primary-b", "backup1"}
    demoted = next(a for a in fixed.assignments if a.donor_id == "primary-b")
    assert demoted.rank > 0
    assert is_active_primary(demoted) is False


def test_enforce_is_pure_and_does_not_mutate_input():
    slot = _slot(
        [
            _assignment(0, AssignmentStatus.ACTIVE, "primary-a"),
            _assignment(0, AssignmentStatus.ACTIVE, "primary-b"),
        ]
    )
    original_ranks = [a.rank for a in slot.assignments]

    enforce_single_active_primary(slot)

    # Input slot is unchanged.
    assert [a.rank for a in slot.assignments] == original_ranks
    assert is_single_active_primary(slot) is False


def test_enforce_leaves_a_valid_slot_unchanged():
    slot = _slot(
        [
            _assignment(0, AssignmentStatus.ACTIVE, "primary"),
            _assignment(1, AssignmentStatus.ACTIVE, "backup1"),
        ]
    )
    fixed = enforce_single_active_primary(slot)
    assert [(a.donor_id, a.rank, a.status) for a in fixed.assignments] == [
        (a.donor_id, a.rank, a.status) for a in slot.assignments
    ]


# --------------------------------------------------------------------------- #
# Fulfilled slots are vacuously valid
# --------------------------------------------------------------------------- #
def test_fulfilled_slot_is_vacuously_valid():
    # Even with two active rank-0 assignments, a fulfilled slot is out of scope.
    slot = _slot(
        [
            _assignment(0, AssignmentStatus.CONFIRMED, "primary-a"),
            _assignment(0, AssignmentStatus.CONFIRMED, "primary-b"),
        ],
        status=SlotStatus.FULFILLED,
    )
    assert is_single_active_primary(slot) is True


# --------------------------------------------------------------------------- #
# A slot with no active primary (e.g. primary declined) is valid
# --------------------------------------------------------------------------- #
def test_no_active_primary_is_valid():
    slot = _slot(
        [
            _assignment(0, AssignmentStatus.DECLINED, "primary"),
            _assignment(1, AssignmentStatus.ACTIVE, "backup1"),
        ]
    )
    assert is_single_active_primary(slot) is True
    assert active_primary(slot) is None
