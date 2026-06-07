"""Property-based test for the forecasting engine (Task 4.3).

**Property 1: Window validity** — for any transfusion history, positive seed
cadence, and last-transfusion date, the predicted window satisfies
``start <= expected <= end``, is symmetric around ``expected``, and its
half-width is clamped to ``[MIN_HALF_WIDTH, MAX_HALF_WIDTH]``; the confidence
stays within the unit interval.

**Validates: Requirements 2.1**

This exercises the real ``estimate_cadence`` + ``predict_window`` implementation
(no mocks) across a wide, intelligently constrained input space.
"""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from pulselink.forecasting.engine import (
    MAX_HALF_WIDTH,
    MIN_HALF_WIDTH,
    estimate_cadence,
    predict_window,
)

# Bounded date range keeps generated gaps (and therefore the algorithm) in a
# realistic span while still covering tiny and large inter-transfusion gaps.
_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31))

# Lists of varying length: 0 (empty), 1 (single record), and many records.
_history = st.lists(_dates, min_size=0, max_size=12)

# Seed cadence is always positive (see Patient validation: cadenceDays > 0).
_seed_cadence = st.integers(min_value=1, max_value=365).map(float)


@given(history=_history, seed_cadence=_seed_cadence, last_transfusion=_dates)
def test_window_validity(
    history: list[date], seed_cadence: float, last_transfusion: date
) -> None:
    """Property 1: the predicted window is always valid and well-formed."""
    est = estimate_cadence(history, seed_cadence)
    pred = predict_window(last_transfusion, est)
    w = pred.window

    # Ordering: start <= expected <= end (also enforced by the Window model).
    assert w.start <= w.expected <= w.end

    # Symmetric half-width around the expected date.
    left = (w.expected - w.start).days
    right = (w.end - w.expected).days
    assert left == right

    # Half-width clamped to the documented bounds.
    assert MIN_HALF_WIDTH <= left <= MAX_HALF_WIDTH

    # Confidence is a probability in [0, 1].
    assert 0.0 <= pred.confidence <= 1.0
