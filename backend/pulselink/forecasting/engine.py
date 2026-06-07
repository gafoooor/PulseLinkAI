"""Forecasting engine: EWMA cadence estimation + transfusion-window prediction.

This is the interpretable, in-process baseline used in **both** the offline demo
and the budget production profile (no SageMaker, no model retraining). It
implements the algorithm from design section 4.2:

* :func:`estimate_cadence` — an exponentially weighted moving average (EWMA)
  over observed inter-transfusion gaps, seeded by ``frequency_in_days`` when the
  history is sparse, so recent transfusions matter more and the estimate
  re-learns as new records arrive.
* :func:`predict_window` — a window ``[start, end]`` around an ``expected`` date
  whose half-width grows with recent gap variability and shrinks as the sample
  count (confidence) rises, clamped to ``[MIN_HALF_WIDTH, MAX_HALF_WIDTH]``.

Guarantees (design postconditions):
* ``start <= expected <= end`` (also enforced by the shared ``Window`` model).
* ``MIN_HALF_WIDTH <= (expected - start) <= MAX_HALF_WIDTH``.
* ``confidence`` in ``[0, 1]``.
* When a patient has fewer than two transfusion dates (``samples == 0``), the
  seed cadence is used and the half-width is the maximum half-width — the window
  is widest when we know least (Requirement 2.7).

The re-learn event handler (``record_transfusion_and_relearn``) is implemented
in task 4.2; this module provides the pure functions it will call.

Requirements: 2.1, 2.2, 2.3, 2.7
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import pstdev

from pulselink.common.models import Window

# EWMA weight for the most recent gap (0..1): higher = more responsive to the
# latest gap, lower = smoother. 0.4 balances responsiveness against noise.
ALPHA = 0.4
# The window is never narrower than +/-1 day and never wider than +/-7 days, so
# it stays honest about uncertainty while remaining actionable (Requirement 2.2).
MIN_HALF_WIDTH = 1
MAX_HALF_WIDTH = 7
# Sample count at which the forecast is treated as fully confident.
CONFIDENCE_SAMPLES = 6.0


@dataclass(frozen=True)
class CadenceEstimate:
    """A patient's cadence estimate derived from transfusion history.

    Attributes:
        cadence_days: EWMA of inter-transfusion gaps in days (the seed cadence
            when there are no observed gaps).
        gap_stdev: Population standard deviation of the observed gaps (0 when
            fewer than two gaps were observed).
        samples: Number of observed inter-transfusion gaps (``len(dates) - 1``).
    """

    cadence_days: float
    gap_stdev: float
    samples: int


@dataclass(frozen=True)
class WindowPrediction:
    """A predicted transfusion window plus its confidence and sample basis.

    ``window`` is the shared :class:`~pulselink.common.models.Window` model and
    therefore always satisfies ``start <= expected <= end``.
    """

    window: Window
    confidence: float
    based_on_samples: int


def estimate_cadence(
    transfusion_dates: list[date], seed_cadence: float
) -> CadenceEstimate:
    """Estimate cadence as an EWMA over observed inter-transfusion gaps.

    Falls back to ``seed_cadence`` (from the dataset ``frequency_in_days``) when
    there are not enough dates to observe a gap.

    Args:
        transfusion_dates: The patient's transfusion dates (any order).
        seed_cadence: Seed cadence in days, used when the history is sparse.
            Must be positive.

    Returns:
        A :class:`CadenceEstimate`. When fewer than two dates are supplied,
        ``samples == 0``, ``gap_stdev == 0.0`` and ``cadence_days == seed_cadence``.
    """
    dates = sorted(transfusion_dates)
    gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    if not gaps:
        return CadenceEstimate(cadence_days=seed_cadence, gap_stdev=0.0, samples=0)

    # EWMA: seed with the first gap, then fold in each subsequent gap so the
    # most recent gaps dominate. After processing the first k gaps, ``ewma`` is
    # the exponentially weighted average of those k gaps (loop invariant).
    ewma = float(gaps[0])
    for gap in gaps[1:]:
        ewma = ALPHA * gap + (1 - ALPHA) * ewma

    gap_stdev = pstdev(gaps) if len(gaps) > 1 else 0.0
    return CadenceEstimate(cadence_days=ewma, gap_stdev=gap_stdev, samples=len(gaps))


def predict_window(last_transfusion: date, est: CadenceEstimate) -> WindowPrediction:
    """Predict the next transfusion window from the last date and a cadence estimate.

    The expected date is ``last_transfusion + round(cadence_days)``. The
    half-width is driven by recent gap variability, damped by the sample count,
    and clamped to ``[MIN_HALF_WIDTH, MAX_HALF_WIDTH]``. Confidence rises with
    the sample count and saturates at ``CONFIDENCE_SAMPLES`` samples.

    When ``est.samples == 0`` (a patient with fewer than two transfusion dates),
    the half-width is set to ``MAX_HALF_WIDTH`` — the window is widest when we
    know least (Requirement 2.7).

    Args:
        last_transfusion: The patient's most recent transfusion date.
        est: The cadence estimate from :func:`estimate_cadence`.

    Returns:
        A :class:`WindowPrediction` whose ``window`` satisfies
        ``start <= expected <= end`` and whose ``confidence`` is in ``[0, 1]``.
    """
    expected = last_transfusion + timedelta(days=round(est.cadence_days))
    confidence = min(1.0, est.samples / CONFIDENCE_SAMPLES)

    if est.samples == 0:
        # Fewer than two records: widest window (least information) per Req 2.7.
        half_width = MAX_HALF_WIDTH
    else:
        raw_half = max(est.gap_stdev, MIN_HALF_WIDTH)
        damped = round(raw_half * (1.5 - 0.5 * confidence))
        # Clamp to [MIN_HALF_WIDTH, MAX_HALF_WIDTH].
        half_width = int(min(MAX_HALF_WIDTH, max(MIN_HALF_WIDTH, damped)))

    window = Window(
        start=expected - timedelta(days=half_width),
        expected=expected,
        end=expected + timedelta(days=half_width),
    )
    return WindowPrediction(
        window=window,
        confidence=round(confidence, 2),
        based_on_samples=est.samples,
    )
