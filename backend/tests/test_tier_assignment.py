"""Unit tests for donor tier assignment (Task 6.2).

Formalizes and verifies the tier-assignment bands from design section 4.3
(Requirement 3.3): a score (with acceptance) maps to exactly one of
``anchor`` / ``steady`` / ``growing`` using non-overlapping, exhaustive bands.

These tests focus narrowly on tier assignment and live in their own file so
they do not collide with task 6.1's ``test_reliability_scoring.py``.

Covered here:
* boundary scores at 45 and 75 land in the higher (half-open) band,
* the acceptance threshold of 0.70 gates ``anchor`` vs ``steady``,
* exactly one valid tier is returned for any input, and
* exhaustiveness across a 0..100 score sweep (and defensively beyond).

Requirements: 3.3
"""

from __future__ import annotations

import pytest

from pulselink.common.enums import DonorTier
from pulselink.reliability.scoring import (
    ANCHOR_MIN_ACCEPTANCE,
    ANCHOR_MIN_SCORE,
    STEADY_MIN_SCORE,
    tier_for,
    tier_for_enum,
)

# Acceptance values high/low enough to isolate the score-band behavior.
HIGH_ACCEPT = 0.90  # >= ANCHOR_MIN_ACCEPTANCE
LOW_ACCEPT = 0.10   # < ANCHOR_MIN_ACCEPTANCE


# --------------------------------------------------------------------------- #
# Boundary scores (half-open bands: 45 and 75 belong to the higher band)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "score, acceptance, expected",
    [
        (44.9, HIGH_ACCEPT, DonorTier.GROWING),   # just below steady floor
        (45.0, HIGH_ACCEPT, DonorTier.STEADY),    # exactly at steady floor
        (74.9, HIGH_ACCEPT, DonorTier.STEADY),    # just below anchor floor
        (75.0, 0.70, DonorTier.ANCHOR),           # at anchor floor + accept==0.70
        (75.0, 0.69, DonorTier.STEADY),           # at anchor floor but accept<0.70
    ],
)
def test_boundary_scores(
    score: float, acceptance: float, expected: DonorTier
) -> None:
    assert tier_for_enum(score, acceptance) is expected
    assert tier_for(score, acceptance) == expected.value


def test_acceptance_threshold_is_the_documented_value() -> None:
    """Anchor requires acceptance >= 0.70 (the design 4.3 threshold)."""
    assert ANCHOR_MIN_ACCEPTANCE == pytest.approx(0.70)
    assert ANCHOR_MIN_SCORE == pytest.approx(75.0)
    assert STEADY_MIN_SCORE == pytest.approx(45.0)


def test_high_score_low_acceptance_is_steady_not_anchor() -> None:
    """A strong score with weak acceptance is demoted to steady, never anchor."""
    assert tier_for_enum(99.0, ANCHOR_MIN_ACCEPTANCE - 0.01) is DonorTier.STEADY


# --------------------------------------------------------------------------- #
# Exactly one valid tier is returned
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("acceptance", [0.0, 0.5, 0.69, 0.70, 1.0])
def test_returns_exactly_one_valid_tier(acceptance: float) -> None:
    for score_tenths in range(0, 1001):  # 0.0 .. 100.0 in 0.1 steps
        score = score_tenths / 10.0
        tier = tier_for_enum(score, acceptance)
        assert isinstance(tier, DonorTier)
        assert tier in set(DonorTier)


# --------------------------------------------------------------------------- #
# Exhaustiveness + non-overlap across a sweep of scores
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("acceptance", [0.0, 0.69, 0.70, 1.0])
def test_exhaustive_over_score_sweep(acceptance: float) -> None:
    """Every score in [0, 100] yields a valid tier matching the documented band."""
    for score_tenths in range(0, 1001):
        score = score_tenths / 10.0
        tier = tier_for_enum(score, acceptance)

        # Recompute the expected band independently to prove non-overlap:
        # exactly one of these three predicates is true for any input.
        is_anchor = score >= 75.0 and acceptance >= 0.70
        is_steady = (not is_anchor) and score >= 45.0
        is_growing = score < 45.0 and not is_anchor

        matches = [is_anchor, is_steady, is_growing]
        assert matches.count(True) == 1, (score, acceptance, matches)

        if is_anchor:
            assert tier is DonorTier.ANCHOR
        elif is_steady:
            assert tier is DonorTier.STEADY
        else:
            assert tier is DonorTier.GROWING


def test_defensive_out_of_range_scores() -> None:
    """Scores outside [0, 100] still map to exactly one tier (no gaps)."""
    assert tier_for_enum(-1000.0, 1.0) is DonorTier.GROWING
    assert tier_for_enum(1000.0, 1.0) is DonorTier.ANCHOR
    assert tier_for_enum(1000.0, 0.0) is DonorTier.STEADY


def test_string_wrapper_matches_enum() -> None:
    """tier_for is exactly the .value of tier_for_enum for representative inputs."""
    for score in (0.0, 45.0, 60.0, 75.0, 100.0):
        for acceptance in (0.0, 0.70, 1.0):
            assert tier_for(score, acceptance) == tier_for_enum(score, acceptance).value
