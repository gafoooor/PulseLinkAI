"""Forecasting engine edge-case unit tests (Task 4.5).

These tests deliberately go *beyond* the baseline coverage in
``test_forecasting_engine.py`` (Task 4.1). They concentrate on the boundary
behaviour called out by Requirements 2.3 and 2.7:

* **Req 2.7** — a patient with fewer than two transfusion records falls back to
  the seed cadence from ``frequency_in_days`` and the window half-width is the
  *maximum* half-width (the window is widest when we know least). This is
  exercised for empty history, a single record, and a range of seed cadences.
* **Req 2.3** — the forecast confidence is always in ``[0, 1]``: it is exactly
  ``0.0`` with no observed gaps, never decreases as more samples arrive, and
  saturates at ``1.0`` once ``CONFIDENCE_SAMPLES`` gaps are observed.
* Pathological / outlier histories (a single huge gap, a tiny-then-huge gap)
  must still yield an ordered window clamped to ``MAX_HALF_WIDTH``.

Requirements: 2.3, 2.7
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pulselink.forecasting.engine import (
    CONFIDENCE_SAMPLES,
    MAX_HALF_WIDTH,
    MIN_HALF_WIDTH,
    CadenceEstimate,
    estimate_cadence,
    predict_window,
)

LAST = date(2024, 1, 1)


def _half_width(pred) -> int:
    return (pred.window.expected - pred.window.start).days


# --------------------------------------------------------------------------- #
# Req 2.7 — sparse history: seed cadence + maximum half-width
# --------------------------------------------------------------------------- #
def test_empty_history_uses_max_half_width_and_zero_confidence():
    est = estimate_cadence([], seed_cadence=21.0)
    pred = predict_window(LAST, est)
    assert est.samples == 0
    assert _half_width(pred) == MAX_HALF_WIDTH
    assert pred.based_on_samples == 0
    assert pred.confidence == 0.0


def test_single_record_uses_seed_and_max_half_width():
    est = estimate_cadence([LAST], seed_cadence=30.0)
    pred = predict_window(LAST, est)
    assert est == CadenceEstimate(cadence_days=30.0, gap_stdev=0.0, samples=0)
    assert _half_width(pred) == MAX_HALF_WIDTH
    # Expected date is seeded purely from the seed cadence.
    assert pred.window.expected == LAST + timedelta(days=30)


@pytest.mark.parametrize("seed", [1.0, 7.0, 14.0, 21.0, 28.0, 45.0, 1958.0])
def test_seed_cadence_respected_across_values_when_sparse(seed):
    # Both empty and single-record histories must honour the seed cadence and
    # the maximum half-width regardless of the seed magnitude (Req 2.7).
    for history in ([], [LAST]):
        est = estimate_cadence(history, seed_cadence=seed)
        pred = predict_window(LAST, est)
        assert est.cadence_days == seed
        assert est.samples == 0
        assert _half_width(pred) == MAX_HALF_WIDTH
        assert pred.window.expected == LAST + timedelta(days=round(seed))
        assert pred.window.start <= pred.window.expected <= pred.window.end


def test_seed_used_with_fractional_cadence_rounds_expected():
    est = estimate_cadence([], seed_cadence=20.6)
    pred = predict_window(LAST, est)
    assert pred.window.expected == LAST + timedelta(days=21)  # round(20.6)
    assert _half_width(pred) == MAX_HALF_WIDTH


# --------------------------------------------------------------------------- #
# Req 2.3 — confidence bounds, monotonicity, and saturation
# --------------------------------------------------------------------------- #
def test_confidence_is_zero_with_no_samples():
    pred = predict_window(LAST, estimate_cadence([], seed_cadence=21.0))
    assert pred.confidence == 0.0


def test_confidence_non_decreasing_as_samples_grow():
    # Constant 21-day cadence; grow the history one transfusion at a time and
    # assert confidence never drops and always stays within [0, 1].
    confidences = []
    for n in range(1, 10):
        dates = [LAST + timedelta(days=21 * i) for i in range(n)]
        pred = predict_window(dates[-1], estimate_cadence(dates, seed_cadence=21.0))
        assert 0.0 <= pred.confidence <= 1.0
        confidences.append(pred.confidence)
    assert confidences == sorted(confidences)


def test_confidence_saturates_at_one_at_threshold_samples():
    # CONFIDENCE_SAMPLES gaps require CONFIDENCE_SAMPLES + 1 dates.
    n_dates = int(CONFIDENCE_SAMPLES) + 1
    dates = [LAST + timedelta(days=21 * i) for i in range(n_dates)]
    est = estimate_cadence(dates, seed_cadence=21.0)
    pred = predict_window(dates[-1], est)
    assert est.samples >= CONFIDENCE_SAMPLES
    assert pred.confidence == 1.0


def test_confidence_does_not_exceed_one_with_excess_samples():
    dates = [LAST + timedelta(days=21 * i) for i in range(20)]
    pred = predict_window(dates[-1], estimate_cadence(dates, seed_cadence=21.0))
    assert pred.confidence == 1.0


# --------------------------------------------------------------------------- #
# Outlier histories — window stays clamped and ordered
# --------------------------------------------------------------------------- #
def test_single_huge_gap_clamps_half_width_to_max():
    # One enormous gap drives gap variability high, but with a single gap the
    # population stdev is 0; the window must still be ordered and clamped.
    dates = [LAST, LAST + timedelta(days=1958)]
    pred = predict_window(dates[-1], estimate_cadence(dates, seed_cadence=21.0))
    assert MIN_HALF_WIDTH <= _half_width(pred) <= MAX_HALF_WIDTH
    assert pred.window.start <= pred.window.expected <= pred.window.end


def test_tiny_then_huge_gap_clamps_to_max_half_width():
    # A short gap followed by a massive outlier produces a large gap stdev,
    # which must be clamped to the maximum half-width (Req 2.2 boundary).
    dates = [LAST, LAST + timedelta(days=2), LAST + timedelta(days=400)]
    est = estimate_cadence(dates, seed_cadence=21.0)
    pred = predict_window(dates[-1], est)
    assert est.gap_stdev > MAX_HALF_WIDTH  # variability exceeds the cap
    assert _half_width(pred) == MAX_HALF_WIDTH
    assert pred.window.start <= pred.window.expected <= pred.window.end


def test_outlier_window_is_symmetric_around_expected():
    dates = [LAST, LAST + timedelta(days=3), LAST + timedelta(days=365)]
    pred = predict_window(dates[-1], estimate_cadence(dates, seed_cadence=21.0))
    w = pred.window
    assert (w.expected - w.start).days == (w.end - w.expected).days
