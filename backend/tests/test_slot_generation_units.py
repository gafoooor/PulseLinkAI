"""Focused unit tests for slot generation internals (Task 8.5).

Complements ``test_subscription_generate.py`` (Task 8.1) without duplicating it.
Where 8.1 establishes the headline behaviour, this module drills into the
arithmetic edges of horizon coverage and the contract between the matcher's
reliability order and the resulting assignment ranks, exercising the targeted
internals directly:

* :func:`pulselink.subscription.generate._plan_windows` — horizon coverage edge
  cases (horizon exactly == cadence, exact multiples, non-multiples needing
  ``ceil`` rounding, very large horizons) and per-window ordering/stepping.
* :func:`pulselink.subscription.generate._build_assignments` — contiguous
  ``0..n`` ranks with the primary at rank 0 and donor order following the
  matcher's reliability order.
* :func:`pulselink.subscription.generate.generate` — the same properties
  end-to-end, plus ``max_backups`` capping, using a stub ``rank_fn`` with a
  known order so assertions are deterministic and offline.

Validates: Requirements 4.4
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import AssignmentStatus, BloodGroup, ConsentScope
from pulselink.common.models import Donor, Patient
from pulselink.forecasting.engine import CadenceEstimate, WindowPrediction
from pulselink.common.models import Window
from pulselink.matching.match import MatchCandidate, RankedDonor
from pulselink.reliability.scoring import DonorStats
from pulselink.subscription.generate import (
    _build_assignments,
    _plan_windows,
    generate,
)

TODAY = date(2024, 1, 1)
ANCHOR = date(2024, 1, 1)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _patient(
    *,
    cadence_days: float = 30.0,
    last_transfusion_date=ANCHOR,
    quantity_required: int = 2,
) -> Patient:
    return Patient(
        patient_id="p1",
        city_id="city-1",
        blood_group=BloodGroup.AB_POSITIVE,
        quantity_required=quantity_required,
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


def _ranked_donor(donor_id: str, score: float) -> RankedDonor:
    return RankedDonor(
        donor_id=donor_id,
        donor=_donor(donor_id),
        score=score,
        tier="growing",
        distance_km=0.0,
    )


def _stub_rank(order: list[str]):
    """Return a rank_fn seam yielding donors in a fixed, known order.

    Scores are intentionally *ascending* down the list while the list order is
    the reliability order, so any assertion that ranks follow the list (not the
    score) actually proves the assignment honours the matcher's order verbatim.
    """

    ranked = [_ranked_donor(did, score=float(idx)) for idx, did in enumerate(order)]

    def _fn(*, candidates, **_kwargs):  # noqa: ANN001
        return list(ranked)

    return _fn


def _estimate(cadence_days: float) -> CadenceEstimate:
    return CadenceEstimate(cadence_days=cadence_days, gap_stdev=0.0, samples=3)


def _first_prediction(anchor: date, cadence: int, half_width: int) -> WindowPrediction:
    expected = anchor + timedelta(days=cadence)
    window = Window(
        start=expected - timedelta(days=half_width),
        expected=expected,
        end=expected + timedelta(days=half_width),
    )
    return WindowPrediction(window=window, confidence=0.9, based_on_samples=3)


# --------------------------------------------------------------------------- #
# _plan_windows — horizon coverage edge cases (Requirement 4.4)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "horizon_days, cadence, expected_slots",
    [
        (30, 30, 1),     # horizon exactly == cadence -> single slot
        (60, 30, 2),     # exact multiple -> no rounding
        (90, 30, 3),     # exact multiple -> no rounding
        (100, 30, 4),    # not a multiple -> ceil(100/30) == 4
        (31, 30, 2),     # one day past a multiple -> rounds up
        (7, 30, 1),      # shorter than cadence -> floor at one slot
        (3650, 30, math.ceil(3650 / 30)),  # very large horizon -> many slots
        (365, 7, math.ceil(365 / 7)),      # weekly cadence over a year
    ],
)
def test_plan_windows_covers_horizon_with_ceil_slot_count(
    horizon_days, cadence, expected_slots
):
    estimate = _estimate(float(cadence))
    first = _first_prediction(ANCHOR, cadence, half_width=2)

    windows = _plan_windows(
        anchor=ANCHOR,
        horizon_days=horizon_days,
        estimate=estimate,
        first_prediction=first,
    )

    assert len(windows) == expected_slots
    # The plan always reaches or passes the horizon's end.
    assert windows[-1].expected >= ANCHOR + timedelta(days=horizon_days) or (
        # ...unless a single sub-cadence slot covers a horizon < cadence.
        expected_slots == 1
    )


def test_plan_windows_steps_by_cadence_and_orders_each_window():
    cadence = 21
    half_width = 3
    estimate = _estimate(float(cadence))
    first = _first_prediction(ANCHOR, cadence, half_width)

    windows = _plan_windows(
        anchor=ANCHOR,
        horizon_days=120,
        estimate=estimate,
        first_prediction=first,
    )

    for k, window in enumerate(windows, start=1):
        # Each window is internally ordered.
        assert window.start <= window.expected <= window.end
        # Expected centre steps by exactly one cadence per slot.
        assert window.expected == ANCHOR + timedelta(days=k * cadence)
        # Half-width from the first prediction is reused for every window.
        assert (window.expected - window.start).days == half_width
        assert (window.end - window.expected).days == half_width


def test_plan_windows_rounds_fractional_cadence_for_stepping():
    # Cadence 29.6 rounds to 30 for the walk-forward step.
    estimate = _estimate(29.6)
    first = _first_prediction(ANCHOR, cadence=30, half_width=2)

    windows = _plan_windows(
        anchor=ANCHOR,
        horizon_days=90,
        estimate=estimate,
        first_prediction=first,
    )

    assert len(windows) == math.ceil(90 / 30)
    assert [w.expected for w in windows[1:]] == [
        ANCHOR + timedelta(days=60),
        ANCHOR + timedelta(days=90),
    ]


# --------------------------------------------------------------------------- #
# _build_assignments — contiguous ranks + matcher order (Requirement 4.4)
# --------------------------------------------------------------------------- #
def test_build_assignments_ranks_are_contiguous_zero_to_n_with_primary_first():
    ranked = [_ranked_donor(f"d{i}", score=float(10 - i)) for i in range(4)]

    assignments = _build_assignments("slot-x", ranked)

    ranks = [a.rank for a in assignments]
    assert ranks == [0, 1, 2, 3]              # contiguous 0..n
    assert assignments[0].rank == 0           # primary at rank 0
    assert all(a.status is AssignmentStatus.ACTIVE for a in assignments)
    # Assignment ids and slot linkage are derived from the slot id + rank.
    assert assignments[0].assignment_id == "slot-x-r0"
    assert all(a.slot_id == "slot-x" for a in assignments)


def test_build_assignments_preserves_matcher_reliability_order():
    # Reliability order is c -> a -> b, regardless of any score numbering.
    ranked = [
        _ranked_donor("c", score=1.0),
        _ranked_donor("a", score=2.0),
        _ranked_donor("b", score=3.0),
    ]

    assignments = _build_assignments("slot-y", ranked)

    # Donor order in the assignments mirrors the matcher's list order exactly.
    assert [a.donor_id for a in assignments] == ["c", "a", "b"]
    assert [a.rank for a in assignments] == [0, 1, 2]


def test_build_assignments_empty_ranked_leaves_slot_uncovered():
    assert _build_assignments("slot-z", []) == []


# --------------------------------------------------------------------------- #
# generate — end-to-end ordering + capping with a known-order stub (Req 4.4)
# --------------------------------------------------------------------------- #
def test_generate_assignment_order_follows_stub_reliability_order():
    patient = _patient()
    order = ["zulu", "alpha", "mike"]  # the matcher's chosen reliability order

    sub = generate(
        patient,
        horizon_days=60,
        candidates=_candidates("alpha", "mike", "zulu"),
        consent_store=_consent_store("alpha", "mike", "zulu"),
        today=TODAY,
        max_backups=2,
        rank_fn=_stub_rank(order),
    )

    for slot in sub.slots:
        assert [a.donor_id for a in slot.assignments] == order
        assert [a.rank for a in slot.assignments] == [0, 1, 2]
        primaries = [a for a in slot.assignments if a.rank == 0]
        assert len(primaries) == 1
        assert primaries[0].donor_id == "zulu"


@pytest.mark.parametrize(
    "max_backups, expected_ranks",
    [
        (0, [0]),            # primary only, no backups
        (1, [0, 1]),         # primary + 1 backup
        (3, [0, 1, 2, 3]),   # primary + 3 backups
    ],
)
def test_generate_caps_assignments_at_primary_plus_max_backups(
    max_backups, expected_ranks
):
    patient = _patient()
    order = ["d0", "d1", "d2", "d3", "d4"]

    sub = generate(
        patient,
        horizon_days=30,
        candidates=_candidates(*order),
        consent_store=_consent_store(*order),
        today=TODAY,
        max_backups=max_backups,
        rank_fn=_stub_rank(order),
    )

    slot = sub.slots[0]
    assert [a.rank for a in slot.assignments] == expected_ranks
    assert [a.donor_id for a in slot.assignments] == order[: len(expected_ranks)]


def test_generate_large_horizon_yields_many_ordered_slots():
    patient = _patient(cadence_days=30.0)

    sub = generate(
        patient,
        horizon_days=3650,
        candidates=_candidates("d1"),
        consent_store=_consent_store("d1"),
        today=TODAY,
        rank_fn=_stub_rank(["d1"]),
    )

    assert len(sub.slots) == math.ceil(3650 / 30)
    # Slots step strictly forward by cadence and every window stays ordered.
    expecteds = [s.window.expected for s in sub.slots]
    assert expecteds == sorted(expecteds)
    assert len(set(expecteds)) == len(expecteds)  # strictly increasing
    for slot in sub.slots:
        w = slot.window
        assert w.start <= w.expected <= w.end
