"""Property-based test for the single-active-primary invariant (Task 8.4).

**Property 6: Promotion safety (invariant portion)** — at most one assignment
per slot is active/primary at any time. This file covers the *invariant* portion
of Property 6 for the Subscription Generator's single-active-primary helpers in
:mod:`pulselink.subscription.generate`:

* :func:`is_active_primary` — a rank-0 assignment in ACTIVE/CONFIRMED status;
* :func:`is_single_active_primary` — the slot honors "at most one active
  primary while unfulfilled";
* :func:`enforce_single_active_primary` — the normalizer that restores the
  invariant without dropping any donor.

**Validates: Requirements 6.1**

Three properties are checked across a wide, intelligently-constrained input
space (no mocks; the real ``generate`` and helpers are exercised):

1. **Generation establishes the invariant.** Every slot of a freshly generated
   subscription (built from a random small candidate set) satisfies
   ``is_single_active_primary``.
2. **Enforcement restores the invariant, losslessly and idempotently.** For an
   *arbitrary* slot (random assignments with random ranks — including duplicate
   rank-0 — random statuses, and a random slot status),
   ``enforce_single_active_primary`` yields a slot that satisfies
   ``is_single_active_primary``, drops no donor, and is idempotent (enforcing
   twice equals enforcing once).
3. **Active-primary convention.** ``is_active_primary`` is true *only* for a
   rank-0 assignment whose status is ACTIVE or CONFIRMED.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import (
    AssignmentStatus,
    BloodGroup,
    ConsentScope,
    SlotStatus,
)
from pulselink.common.models import Donor, Patient, Slot, SlotAssignment, Window
from pulselink.matching.match import MatchCandidate
from pulselink.reliability.scoring import DonorStats
from pulselink.subscription.generate import (
    enforce_single_active_primary,
    generate,
    is_active_primary,
    is_single_active_primary,
)

TODAY = date(2024, 1, 1)
LAST_TRANSFUSION = date(2024, 1, 1)

_ALL_STATUSES = list(AssignmentStatus)
_ALL_SLOT_STATUSES = list(SlotStatus)


# --------------------------------------------------------------------------- #
# Strategy: an arbitrary Slot with random assignments (Property 2 + 3)
# --------------------------------------------------------------------------- #
def _window() -> Window:
    return Window(
        start=date(2024, 1, 28), expected=date(2024, 1, 31), end=date(2024, 2, 3)
    )


@st.composite
def _assignments(draw: st.DrawFn) -> list[SlotAssignment]:
    """Generate a random assignment list with duplicate rank-0 made likely.

    Ranks are drawn from a small range (0..3) biased toward 0 so multiple
    active rank-0 primaries — the violation ``enforce_single_active_primary``
    must repair — show up frequently. Donor ids are drawn from a tiny pool so
    duplicate donors can also occur, exercising the "no donor dropped"
    multiset check.
    """
    n = draw(st.integers(min_value=0, max_value=6))
    assignments: list[SlotAssignment] = []
    for i in range(n):
        rank = draw(
            st.one_of(
                st.just(0),  # bias toward rank 0 to create duplicate primaries
                st.integers(min_value=0, max_value=3),
            )
        )
        status = draw(st.sampled_from(_ALL_STATUSES))
        donor_id = draw(st.sampled_from(["d0", "d1", "d2", "d3", "d4"]))
        assignments.append(
            SlotAssignment(
                assignment_id=f"a{i}",
                slot_id="slot-1",
                donor_id=donor_id,
                rank=rank,
                status=status,
            )
        )
    return assignments


@st.composite
def _slots(draw: st.DrawFn) -> Slot:
    """An arbitrary slot: random assignments + a random lifecycle status."""
    return Slot(
        slot_id="slot-1",
        subscription_id="sub-1",
        patient_id="p1",
        window=_window(),
        units_needed=draw(st.integers(min_value=1, max_value=4)),
        status=draw(st.sampled_from(_ALL_SLOT_STATUSES)),
        assignments=draw(_assignments()),
    )


def _assignment_fingerprint(slot: Slot) -> list[tuple[str, str, int, str]]:
    """A stable, order-preserving fingerprint of a slot's assignments."""
    return [
        (a.assignment_id, a.donor_id, a.rank, a.status.value)
        for a in slot.assignments
    ]


# --------------------------------------------------------------------------- #
# Strategy + helpers: a freshly generated subscription (Property 1)
# --------------------------------------------------------------------------- #
def _donor(donor_id: str) -> Donor:
    # O Negative is the universal donor -> blood-compatible with any patient,
    # so generated slots actually get ranked assignments.
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


def _candidates(donor_ids: list[str]) -> list[MatchCandidate]:
    return [
        MatchCandidate(_donor(d), DonorStats(donations_till_date=0)) for d in donor_ids
    ]


def _consent_store(donor_ids: list[str]) -> InMemoryConsentService:
    store = InMemoryConsentService()
    for d in donor_ids:
        store.grant(d, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    return store


def _patient(cadence_days: float) -> Patient:
    return Patient(
        patient_id="p1",
        city_id="city-1",
        blood_group=BloodGroup.AB_POSITIVE,  # universal recipient
        quantity_required=2,
        cadence_days=cadence_days,
        last_transfusion_date=LAST_TRANSFUSION,
        consent_id="consent-p1",
    )


# --------------------------------------------------------------------------- #
# Property 1: generation establishes the single-active-primary invariant
# --------------------------------------------------------------------------- #
@settings(max_examples=150, deadline=None)
@given(
    n_donors=st.integers(min_value=0, max_value=4),
    cadence_days=st.integers(min_value=7, max_value=45).map(float),
    horizon_days=st.integers(min_value=1, max_value=180),
    max_backups=st.integers(min_value=0, max_value=4),
)
def test_generated_slots_satisfy_single_active_primary(
    n_donors: int, cadence_days: float, horizon_days: int, max_backups: int
) -> None:
    """Property 6 (invariant): freshly generated slots hold at most one primary."""
    donor_ids = [f"d{i}" for i in range(n_donors)]
    sub = generate(
        _patient(cadence_days),
        horizon_days=horizon_days,
        candidates=_candidates(donor_ids),
        consent_store=_consent_store(donor_ids),
        today=TODAY,
        max_backups=max_backups,
    )

    assert sub.slots  # a subscription is never empty
    for slot in sub.slots:
        # At most one active primary per slot — the invariant itself.
        assert is_single_active_primary(slot)
        active_primaries = [a for a in slot.assignments if is_active_primary(a)]
        assert len(active_primaries) <= 1


# --------------------------------------------------------------------------- #
# Property 2: enforcement restores the invariant, losslessly + idempotently
# --------------------------------------------------------------------------- #
@settings(max_examples=300, deadline=None)
@given(slot=_slots())
def test_enforce_restores_invariant_losslessly_and_idempotently(slot: Slot) -> None:
    """Property 6 (invariant): ``enforce`` yields a single-primary slot.

    For ANY slot: the result satisfies ``is_single_active_primary``, no donor is
    dropped (the multiset of donor ids is preserved), and enforcement is
    idempotent (enforcing twice equals enforcing once).

    Per Requirement 4.5/6.1 the invariant only binds *while a slot is not
    fulfilled*: ``is_single_active_primary`` is vacuously true for a FULFILLED
    slot, so the raw "at most one active primary" count is asserted only for
    non-fulfilled slots (where the invariant actually constrains the slot).
    """
    fixed = enforce_single_active_primary(slot)

    # (a) The invariant now holds (vacuously true for a FULFILLED slot).
    assert is_single_active_primary(fixed)
    if fixed.status is not SlotStatus.FULFILLED:
        assert sum(1 for a in fixed.assignments if is_active_primary(a)) <= 1

    # (b) No donor is dropped: same number of assignments and same donor multiset.
    assert len(fixed.assignments) == len(slot.assignments)
    assert Counter(a.donor_id for a in fixed.assignments) == Counter(
        a.donor_id for a in slot.assignments
    )

    # (c) Idempotent: enforcing the already-fixed slot changes nothing.
    twice = enforce_single_active_primary(fixed)
    assert _assignment_fingerprint(twice) == _assignment_fingerprint(fixed)
    assert twice.status == fixed.status


# --------------------------------------------------------------------------- #
# Property 3: active-primary convention (rank 0 + ACTIVE/CONFIRMED only)
# --------------------------------------------------------------------------- #
@settings(max_examples=200, deadline=None)
@given(
    rank=st.integers(min_value=0, max_value=5),
    status=st.sampled_from(_ALL_STATUSES),
)
def test_is_active_primary_only_for_rank_zero_active_or_confirmed(
    rank: int, status: AssignmentStatus
) -> None:
    """``is_active_primary`` is true iff rank == 0 and status is ACTIVE/CONFIRMED."""
    assignment = SlotAssignment(
        assignment_id="a-1",
        slot_id="slot-1",
        donor_id="d0",
        rank=rank,
        status=status,
    )

    expected = rank == 0 and status in (
        AssignmentStatus.ACTIVE,
        AssignmentStatus.CONFIRMED,
    )
    assert is_active_primary(assignment) is expected
