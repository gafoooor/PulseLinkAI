"""Unit tests for the donor-response recompute (Task 6.3, Requirement 3.5).

These cover ``apply_response`` (the pure stats update) and
``on_donor_response`` (the before/after recompute), verifying that:

* an offer is always recorded (``offered += 1``) on any response,
* ``ACCEPTED`` also increments ``accepted``; ``DECLINED`` / ``NO_RESPONSE`` do not,
* a ``DECLINED`` response yields a recomputed score ``<=`` the prior score
  (Requirement 3.5), and ``NO_RESPONSE`` behaves the same monotonic-down way,
* an ``ACCEPTED`` response yields a recomputed score ``>=`` the prior score
  (acceptance ratio rises or holds),
* the tier is recomputed from the new score, and the returned stats are correct.

Requirements: 3.5
"""

from __future__ import annotations

from datetime import date

import pytest

from pulselink.common.enums import DonorTier, SlotResponse
from pulselink.common.models import ReliabilityScore
from pulselink.reliability.scoring import (
    DonorStats,
    apply_response,
    build_reliability_score,
    on_donor_response,
)

TODAY = date(2024, 6, 1)
DONOR = "donor-42"


# --------------------------------------------------------------------------- #
# apply_response: stats updated correctly
# --------------------------------------------------------------------------- #
def test_apply_response_accepted_increments_both() -> None:
    stats = DonorStats(accepted=2, offered=5)
    updated = apply_response(stats, SlotResponse.ACCEPTED)
    assert updated.accepted == 3
    assert updated.offered == 6


def test_apply_response_declined_increments_offered_only() -> None:
    stats = DonorStats(accepted=2, offered=5)
    updated = apply_response(stats, SlotResponse.DECLINED)
    assert updated.accepted == 2
    assert updated.offered == 6


def test_apply_response_no_response_increments_offered_only() -> None:
    stats = DonorStats(accepted=2, offered=5)
    updated = apply_response(stats, SlotResponse.NO_RESPONSE)
    assert updated.accepted == 2
    assert updated.offered == 6


def test_apply_response_preserves_other_signals() -> None:
    stats = DonorStats(
        accepted=1,
        offered=2,
        calls_to_donations_ratio=4.0,
        donations_till_date=7,
        last_donation_date=date(2024, 5, 1),
    )
    updated = apply_response(stats, SlotResponse.DECLINED)
    assert updated.calls_to_donations_ratio == 4.0
    assert updated.donations_till_date == 7
    assert updated.last_donation_date == date(2024, 5, 1)


def test_apply_response_preserves_precondition() -> None:
    """offered >= accepted >= 0 holds after any response."""
    stats = DonorStats(accepted=5, offered=5)
    for response in SlotResponse:
        updated = apply_response(stats, response)
        assert updated.offered >= updated.accepted >= 0


# --------------------------------------------------------------------------- #
# on_donor_response: DECLINED never raises the score (Requirement 3.5)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "stats",
    [
        DonorStats(),  # no offer history -> neutral 0.5 prior
        DonorStats(accepted=8, offered=10, calls_to_donations_ratio=2.0,
                   donations_till_date=8, last_donation_date=TODAY),
        DonorStats(accepted=0, offered=4),  # already all-declines
        DonorStats(accepted=10, offered=10, calls_to_donations_ratio=1.0,
                   donations_till_date=10, last_donation_date=TODAY),  # best case
    ],
)
def test_declined_never_raises_score(stats: DonorStats) -> None:
    result = on_donor_response(DONOR, stats, SlotResponse.DECLINED, TODAY)
    assert result.score.score <= result.previous_score.score


@pytest.mark.parametrize(
    "stats",
    [
        DonorStats(),
        DonorStats(accepted=8, offered=10, calls_to_donations_ratio=2.0,
                   donations_till_date=8, last_donation_date=TODAY),
        DonorStats(accepted=0, offered=4),
    ],
)
def test_no_response_never_raises_score(stats: DonorStats) -> None:
    """NO_RESPONSE is monotonic-down like DECLINED."""
    result = on_donor_response(DONOR, stats, SlotResponse.NO_RESPONSE, TODAY)
    assert result.score.score <= result.previous_score.score


def test_declined_with_neutral_prior_drops_below_half_prior() -> None:
    """First-ever decline: acceptance falls from the 0.5 prior to 0/1."""
    result = on_donor_response(DONOR, DonorStats(), SlotResponse.DECLINED, TODAY)
    assert result.score.components.acceptance_ratio == 0.0
    assert result.score.score <= result.previous_score.score


# --------------------------------------------------------------------------- #
# on_donor_response: ACCEPTED never lowers the score
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "stats",
    [
        DonorStats(),  # neutral prior -> rises to 1.0 acceptance
        DonorStats(accepted=2, offered=10, calls_to_donations_ratio=3.0,
                   donations_till_date=4, last_donation_date=TODAY),
        DonorStats(accepted=5, offered=5),  # already perfect acceptance -> holds
    ],
)
def test_accepted_never_lowers_score(stats: DonorStats) -> None:
    result = on_donor_response(DONOR, stats, SlotResponse.ACCEPTED, TODAY)
    assert result.score.score >= result.previous_score.score


def test_accepted_raises_acceptance_ratio() -> None:
    stats = DonorStats(accepted=2, offered=10)
    result = on_donor_response(DONOR, stats, SlotResponse.ACCEPTED, TODAY)
    prior = result.previous_score.components.acceptance_ratio
    after = result.score.components.acceptance_ratio
    assert after >= prior


# --------------------------------------------------------------------------- #
# on_donor_response: tier recomputed and stats updated
# --------------------------------------------------------------------------- #
def test_result_carries_updated_stats() -> None:
    stats = DonorStats(accepted=3, offered=4)
    result = on_donor_response(DONOR, stats, SlotResponse.DECLINED, TODAY)
    assert result.stats.accepted == 3
    assert result.stats.offered == 5
    assert result.donor_id == DONOR
    assert result.response is SlotResponse.DECLINED


def test_result_score_matches_updated_stats() -> None:
    """The recomputed score equals build_reliability_score on the new stats."""
    stats = DonorStats(accepted=3, offered=4, donations_till_date=5,
                       last_donation_date=TODAY)
    result = on_donor_response(DONOR, stats, SlotResponse.ACCEPTED, TODAY)
    expected = build_reliability_score(DONOR, result.stats, TODAY)
    assert result.score.score == expected.score
    assert result.score.tier == expected.tier


def test_result_types_are_reliability_scores() -> None:
    result = on_donor_response(DONOR, DonorStats(), SlotResponse.DECLINED, TODAY)
    assert isinstance(result.score, ReliabilityScore)
    assert isinstance(result.previous_score, ReliabilityScore)
    assert result.score.tier in set(DonorTier)


def test_tier_recomputed_can_drop_on_decline() -> None:
    """A borderline-anchor donor can fall to a lower tier after a decline."""
    # Many declines drag acceptance — and the score — down across responses.
    stats = DonorStats(accepted=7, offered=10, calls_to_donations_ratio=2.0,
                       donations_till_date=10, last_donation_date=TODAY)
    result = on_donor_response(DONOR, stats, SlotResponse.DECLINED, TODAY)
    # Tier never improves on a decline (ordering anchor > steady > growing).
    order = {DonorTier.ANCHOR: 2, DonorTier.STEADY: 1, DonorTier.GROWING: 0}
    assert order[result.score.tier] <= order[result.previous_score.tier]
