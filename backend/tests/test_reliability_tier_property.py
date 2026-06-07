"""Property-based test for donor tier consistency (Task 6.5).

**Property 5: Tier consistency** — ``tier_for`` (and its canonical enum form
``tier_for_enum``) returns exactly one tier for any input, and the three
reliability bands (``anchor`` / ``steady`` / ``growing``) are
**non-overlapping** and **exhaustive**: every ``(score, acceptance)`` pair
satisfies exactly one band predicate, and the returned tier matches that
predicate.

**Validates: Requirements 3.1**

This exercises the real ``tier_for_enum`` / ``tier_for`` implementation (no
mocks) across a wide, intelligently constrained input space: scores spanning
``[0, 100]`` and defensively beyond (``-50 .. 150``), and acceptance values
spanning ``[0, 1]`` and a little beyond. The band predicates are recomputed
independently here so passing proves partition (exactly-one) rather than just
re-asserting the implementation.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.enums import DonorTier
from pulselink.reliability.scoring import (
    ANCHOR_MIN_ACCEPTANCE,
    ANCHOR_MIN_SCORE,
    STEADY_MIN_SCORE,
    tier_for,
    tier_for_enum,
)

# Scores cover the documented [0, 100] range and defensively spill beyond it
# (very negative -> growing; very large -> anchor/steady) to prove the bands
# stay exhaustive and non-overlapping for any real input.
_scores = st.floats(
    min_value=-50.0,
    max_value=150.0,
    allow_nan=False,
    allow_infinity=False,
)

# Acceptance covers the [0, 1] unit interval and a small margin beyond it.
_acceptance = st.floats(
    min_value=-0.1,
    max_value=1.1,
    allow_nan=False,
    allow_infinity=False,
)


@given(score=_scores, acceptance=_acceptance)
def test_tier_consistency(score: float, acceptance: float) -> None:
    """Property 5: exactly one band fires and the returned tier matches it."""
    tier = tier_for_enum(score, acceptance)

    # The function always returns a valid DonorTier (total over the input space).
    assert isinstance(tier, DonorTier)
    assert tier in set(DonorTier)

    # Recompute the three band predicates independently from the documented
    # thresholds. These are written as mutually exclusive, exhaustive clauses:
    #   anchor  = score >= 75 AND acceptance >= 0.70
    #   steady  = (not anchor) AND score >= 45
    #   growing = (not anchor) AND score < 45
    is_anchor = score >= ANCHOR_MIN_SCORE and acceptance >= ANCHOR_MIN_ACCEPTANCE
    is_steady = (not is_anchor) and score >= STEADY_MIN_SCORE
    is_growing = (not is_anchor) and score < STEADY_MIN_SCORE

    # Non-overlapping + exhaustive: exactly one predicate is true.
    matches = [is_anchor, is_steady, is_growing]
    assert matches.count(True) == 1, (score, acceptance, matches)

    # The returned tier matches the single true predicate.
    if is_anchor:
        assert tier is DonorTier.ANCHOR
    elif is_steady:
        assert tier is DonorTier.STEADY
    else:
        assert tier is DonorTier.GROWING

    # The string wrapper is exactly the .value of the canonical enum form.
    assert tier_for(score, acceptance) == tier.value
