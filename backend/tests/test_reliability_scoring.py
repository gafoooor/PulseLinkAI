"""Unit tests for the reliability scoring formula (Task 6.1).

Covers the documented baseline from design section 4.3:
* neutral acceptance prior (0.5) when a donor has no offer history,
* the score stays within ``[0, 100]``,
* recency decay (recent ~1.0, old smaller, ``None`` -> 0),
* the volume factor caps at 10 donations,
* call efficiency is the inverse of ``calls_to_donations_ratio``,
* the weighted-sum composition and the ``build_reliability_score`` wrapper.

Requirements: 3.1, 3.2, 3.4
"""

from __future__ import annotations

from datetime import date

import pytest

from pulselink.common.enums import DonorTier
from pulselink.common.models import ReliabilityScore
from pulselink.reliability.scoring import (
    NEUTRAL_ACCEPTANCE_PRIOR,
    RECENCY_HALF_LIFE_DAYS,
    WEIGHTS,
    DonorStats,
    build_reliability_score,
    recency_factor,
    reliability_score,
    tier_for,
)

TODAY = date(2024, 6, 1)


# --------------------------------------------------------------------------- #
# Neutral acceptance prior (Requirement 3.4)
# --------------------------------------------------------------------------- #
def test_neutral_prior_when_no_offer_history() -> None:
    """offered == 0 -> acceptance uses the neutral 0.5 prior."""
    result = reliability_score(DonorStats(accepted=0, offered=0), TODAY)
    assert result["components"]["acceptanceRatio"] == NEUTRAL_ACCEPTANCE_PRIOR


def test_acceptance_ratio_used_when_offer_history_exists() -> None:
    """offered > 0 -> acceptance is accepted / offered, not the prior."""
    result = reliability_score(DonorStats(accepted=3, offered=4), TODAY)
    assert result["components"]["acceptanceRatio"] == pytest.approx(0.75)


# --------------------------------------------------------------------------- #
# Score bounds (Requirements 3.1, 3.2)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "stats",
    [
        DonorStats(),  # empty donor
        DonorStats(accepted=10, offered=10, calls_to_donations_ratio=1.0,
                   donations_till_date=50, last_donation_date=TODAY),  # best case
        DonorStats(accepted=0, offered=10, calls_to_donations_ratio=99.0,
                   donations_till_date=0, last_donation_date=None),  # worst case
    ],
)
def test_score_within_bounds(stats: DonorStats) -> None:
    result = reliability_score(stats, TODAY)
    assert 0.0 <= result["score"] <= 100.0


def test_best_case_score_is_100() -> None:
    """Perfect acceptance + max volume + min calls + donating today -> 100."""
    stats = DonorStats(
        accepted=10, offered=10, calls_to_donations_ratio=1.0,
        donations_till_date=10, last_donation_date=TODAY,
    )
    assert reliability_score(stats, TODAY)["score"] == 100.0


# --------------------------------------------------------------------------- #
# Recency decay
# --------------------------------------------------------------------------- #
def test_recency_none_is_zero() -> None:
    assert recency_factor(None, TODAY) == 0.0


def test_recency_today_is_one() -> None:
    assert recency_factor(TODAY, TODAY) == pytest.approx(1.0)


def test_recency_half_life_is_half() -> None:
    last = date(2024, 6, 1)
    today = date(2024, 6, 1)  # placeholder; compute via timedelta below
    from datetime import timedelta

    today = last + timedelta(days=RECENCY_HALF_LIFE_DAYS)
    assert recency_factor(last, today) == pytest.approx(0.5)


def test_recency_recent_greater_than_old() -> None:
    from datetime import timedelta

    recent = recency_factor(TODAY - timedelta(days=10), TODAY)
    old = recency_factor(TODAY - timedelta(days=300), TODAY)
    assert recent > old
    assert 0.0 < old < recent <= 1.0


def test_recency_future_date_clamped_to_one() -> None:
    from datetime import timedelta

    assert recency_factor(TODAY + timedelta(days=30), TODAY) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Volume factor caps at 10 donations
# --------------------------------------------------------------------------- #
def test_volume_caps_at_ten() -> None:
    at_cap = reliability_score(DonorStats(donations_till_date=10), TODAY)
    above_cap = reliability_score(DonorStats(donations_till_date=1000), TODAY)
    assert at_cap["components"]["volumeFactor"] == 1.0
    assert above_cap["components"]["volumeFactor"] == 1.0


def test_volume_partial_below_cap() -> None:
    result = reliability_score(DonorStats(donations_till_date=4), TODAY)
    assert result["components"]["volumeFactor"] == pytest.approx(0.4)


# --------------------------------------------------------------------------- #
# Call efficiency is the inverse of calls_to_donations_ratio
# --------------------------------------------------------------------------- #
def test_call_efficiency_inverse() -> None:
    result = reliability_score(DonorStats(calls_to_donations_ratio=4.0), TODAY)
    assert result["components"]["callEfficiency"] == pytest.approx(0.25)


def test_call_efficiency_capped_at_one_for_low_ratio() -> None:
    """A ratio <= 1 (incl. the dataset default 0) yields full efficiency 1.0."""
    assert reliability_score(
        DonorStats(calls_to_donations_ratio=0.0), TODAY
    )["components"]["callEfficiency"] == 1.0
    assert reliability_score(
        DonorStats(calls_to_donations_ratio=0.5), TODAY
    )["components"]["callEfficiency"] == 1.0


def test_high_ratio_lowers_efficiency() -> None:
    """Dataset's pathological 23:1 ratio -> small but positive efficiency."""
    eff = reliability_score(
        DonorStats(calls_to_donations_ratio=23.0), TODAY
    )["components"]["callEfficiency"]
    assert 0.0 < eff < 0.05


# --------------------------------------------------------------------------- #
# Weighted-sum composition
# --------------------------------------------------------------------------- #
def test_weights_sum_to_one() -> None:
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_score_equals_weighted_sum() -> None:
    stats = DonorStats(
        accepted=1, offered=2, calls_to_donations_ratio=2.0,
        donations_till_date=5, last_donation_date=TODAY,
    )
    result = reliability_score(stats, TODAY)
    c = result["components"]
    expected = round(
        (
            WEIGHTS["acceptance"] * c["acceptanceRatio"]
            + WEIGHTS["calls"] * c["callEfficiency"]
            + WEIGHTS["volume"] * c["volumeFactor"]
            + WEIGHTS["recency"] * c["recencyFactor"]
        )
        * 100,
        1,
    )
    assert result["score"] == expected


# --------------------------------------------------------------------------- #
# tier_for basic bands
# --------------------------------------------------------------------------- #
def test_tier_for_bands() -> None:
    assert tier_for(80.0, 0.8) == DonorTier.ANCHOR.value
    assert tier_for(80.0, 0.5) == DonorTier.STEADY.value  # high score, low accept
    assert tier_for(60.0, 0.9) == DonorTier.STEADY.value
    assert tier_for(20.0, 0.9) == DonorTier.GROWING.value


# --------------------------------------------------------------------------- #
# build_reliability_score wrapper produces a valid domain model
# --------------------------------------------------------------------------- #
def test_build_reliability_score_model() -> None:
    model = build_reliability_score("donor-1", DonorStats(), TODAY)
    assert isinstance(model, ReliabilityScore)
    assert model.donor_id == "donor-1"
    assert 0.0 <= model.score <= 100.0
    assert model.tier in set(DonorTier)
    assert model.components.acceptance_ratio == NEUTRAL_ACCEPTANCE_PRIOR
