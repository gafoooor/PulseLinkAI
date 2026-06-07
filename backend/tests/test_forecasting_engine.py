"""Unit tests for the forecasting engine (Task 4.1).

Covers EWMA cadence estimation and window prediction:
* empty / single-date history -> seed cadence + maximum half-width (Req 2.7),
* multiple dates -> EWMA sanity, window ordering, half-width clamped to [1, 7],
  confidence in [0, 1] (Reqs 2.1, 2.2, 2.3),
* an outlier gap widens but does not break the window,
* ``start <= expected <= end`` always holds.

Requirements: 2.1, 2.2, 2.3, 2.7
"""

from __future__ import annotations

from datetime import date, timedelta

from pulselink.forecasting.engine import (
    ALPHA,
    MAX_HALF_WIDTH,
    MIN_HALF_WIDTH,
    CadenceEstimate,
    estimate_cadence,
    predict_window,
)

SEED = 21.0
LAST = date(2024, 1, 1)


def _half_width(pred) -> int:
    return (pred.window.expected - pred.window.start).days


# --------------------------------------------------------------------------- #
# estimate_cadence
# --------------------------------------------------------------------------- #
def test_empty_history_uses_seed_cadence():
    est = estimate_cadence([], seed_cadence=SEED)
    assert est == CadenceEstimate(cadence_days=SEED, gap_stdev=0.0, samples=0)


def test_single_date_uses_seed_cadence():
    est = estimate_cadence([LAST], seed_cadence=SEED)
    assert est.cadence_days == SEED
    assert est.gap_stdev == 0.0
    assert est.samples == 0


def test_two_dates_single_gap_yields_that_gap():
    dates = [date(2024, 1, 1), date(2024, 1, 22)]  # 21-day gap
    est = estimate_cadence(dates, seed_cadence=SEED)
    assert est.samples == 1
    assert est.cadence_days == 21
    assert est.gap_stdev == 0.0  # population stdev is 0 for a single gap


def test_dates_are_sorted_before_gap_computation():
    ordered = [date(2024, 1, 1), date(2024, 1, 21), date(2024, 2, 10)]
    shuffled = [date(2024, 2, 10), date(2024, 1, 1), date(2024, 1, 21)]
    assert estimate_cadence(ordered, SEED) == estimate_cadence(shuffled, SEED)


def test_constant_gaps_ewma_equals_gap():
    # Gaps of exactly 20 days each -> EWMA collapses to 20.
    dates = [date(2024, 1, 1) + timedelta(days=20 * i) for i in range(5)]
    est = estimate_cadence(dates, seed_cadence=SEED)
    assert est.samples == 4
    assert est.cadence_days == 20
    assert est.gap_stdev == 0.0


def test_ewma_weights_recent_gaps_more():
    # Gaps: 10 then 30. EWMA = ALPHA*30 + (1-ALPHA)*10.
    dates = [date(2024, 1, 1), date(2024, 1, 11), date(2024, 2, 10)]
    est = estimate_cadence(dates, seed_cadence=SEED)
    expected_ewma = ALPHA * 30 + (1 - ALPHA) * 10
    assert est.samples == 2
    assert est.cadence_days == expected_ewma
    # EWMA sits between the two observed gaps.
    assert 10 < est.cadence_days < 30


def test_gap_stdev_is_population_stdev_for_multiple_gaps():
    dates = [date(2024, 1, 1), date(2024, 1, 11), date(2024, 2, 10)]  # gaps 10, 30
    est = estimate_cadence(dates, seed_cadence=SEED)
    # pstdev([10, 30]) == 10.0
    assert est.gap_stdev == 10.0


# --------------------------------------------------------------------------- #
# predict_window — seed / sparse history (Req 2.7)
# --------------------------------------------------------------------------- #
def test_empty_history_window_uses_max_half_width():
    est = estimate_cadence([], seed_cadence=SEED)
    pred = predict_window(LAST, est)
    assert _half_width(pred) == MAX_HALF_WIDTH
    assert pred.based_on_samples == 0
    assert pred.confidence == 0.0
    # expected = last + round(seed)
    assert pred.window.expected == LAST + timedelta(days=round(SEED))


def test_single_date_window_uses_max_half_width():
    est = estimate_cadence([LAST], seed_cadence=SEED)
    pred = predict_window(LAST, est)
    assert _half_width(pred) == MAX_HALF_WIDTH
    assert pred.based_on_samples == 0


# --------------------------------------------------------------------------- #
# predict_window — ordering, clamping, confidence bounds
# --------------------------------------------------------------------------- #
def test_window_ordering_and_symmetry():
    dates = [date(2024, 1, 1), date(2024, 1, 22), date(2024, 2, 12)]
    pred = predict_window(date(2024, 2, 12), estimate_cadence(dates, SEED))
    w = pred.window
    assert w.start <= w.expected <= w.end
    # Symmetric half-width around expected.
    assert (w.expected - w.start).days == (w.end - w.expected).days


def test_half_width_clamped_within_bounds():
    dates = [date(2024, 1, 1), date(2024, 1, 22), date(2024, 2, 12)]
    pred = predict_window(date(2024, 2, 12), estimate_cadence(dates, SEED))
    assert MIN_HALF_WIDTH <= _half_width(pred) <= MAX_HALF_WIDTH


def test_confidence_within_unit_interval_and_grows_with_samples():
    few = estimate_cadence(
        [date(2024, 1, 1), date(2024, 1, 22), date(2024, 2, 12)], SEED
    )
    many_dates = [date(2024, 1, 1) + timedelta(days=21 * i) for i in range(8)]
    many = estimate_cadence(many_dates, SEED)

    pred_few = predict_window(date(2024, 2, 12), few)
    pred_many = predict_window(many_dates[-1], many)

    for pred in (pred_few, pred_many):
        assert 0.0 <= pred.confidence <= 1.0
    # More samples -> at least as confident; saturates at 1.0.
    assert pred_many.confidence >= pred_few.confidence
    assert pred_many.confidence == 1.0  # 7 gaps >= CONFIDENCE_SAMPLES


def test_outlier_gap_widens_window_but_stays_clamped_and_ordered():
    # A large outlier gap drives gap_stdev high; half-width must still clamp to 7.
    dates = [date(2024, 1, 1), date(2024, 1, 22), date(2024, 6, 1)]
    pred = predict_window(date(2024, 6, 1), estimate_cadence(dates, SEED))
    assert _half_width(pred) == MAX_HALF_WIDTH
    assert pred.window.start <= pred.window.expected <= pred.window.end


def test_start_before_expected_before_end_across_varied_histories():
    base = date(2024, 1, 1)
    histories = [
        [],
        [base],
        [base, base + timedelta(days=15)],
        [base, base + timedelta(days=10), base + timedelta(days=40)],
        [base + timedelta(days=21 * i) for i in range(6)],
    ]
    for hist in histories:
        est = estimate_cadence(hist, SEED)
        last = max(hist) if hist else base
        w = predict_window(last, est).window
        assert w.start <= w.expected <= w.end
        assert MIN_HALF_WIDTH <= (w.expected - w.start).days <= MAX_HALF_WIDTH
