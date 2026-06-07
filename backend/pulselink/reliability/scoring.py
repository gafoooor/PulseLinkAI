"""Donor reliability scoring — the interpretable in-process baseline (Task 6.1).

This module implements the documented 0–100 reliability score from design
section 4.3. It is a transparent, deterministic baseline that blends four
signals the dataset already exposes; there is no SageMaker and no model
retraining here.

Score = weighted sum (weights sum to 1.0) of four factors, each in ``[0, 1]``:

* ``acceptanceRatio`` (weight 0.40) — ``accepted / offered``; a neutral prior
  of 0.5 is used when a donor has no offer history (``offered == 0``).
* ``callEfficiency`` (weight 0.20) — ``1 / max(1, calls_to_donations_ratio)``;
  fewer calls per realized donation means a more reliable donor.
* ``volumeFactor`` (weight 0.20) — ``min(1, donations_till_date / 10)``;
  rewards proven, repeat donors and caps at 10 donations.
* ``recencyFactor`` (weight 0.20) — exponential decay on days since
  ``last_donation_date`` with a 120-day half-life; ``None`` -> 0.0.

Because the weights sum to 1.0 and every factor is in ``[0, 1]``, the raw
weighted sum is in ``[0, 1]`` and the score is in ``[0, 100]``. The score is
clamped defensively to guarantee the bound regardless of rounding.

The tier-assignment bands (``tier_for``) are implemented here so the scoring
module is self-contained; task 6.2 formalizes and verifies the bands. The
``on_donor_response`` recompute (task 6.3) scores a donor before and after a
response so a ``DECLINED`` response never raises the score (Requirement 3.5).

Requirements: 3.1, 3.2, 3.4, 3.5
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, TypedDict

from pulselink.common.enums import DonorTier, SlotResponse
from pulselink.common.models import ReliabilityComponents, ReliabilityScore

# --------------------------------------------------------------------------- #
# Tunable constants (per-design; tunable per city in production).
# --------------------------------------------------------------------------- #
WEIGHTS = {"acceptance": 0.40, "calls": 0.20, "volume": 0.20, "recency": 0.20}
RECENCY_HALF_LIFE_DAYS = 120  # exponential decay half-life (days)
NEUTRAL_ACCEPTANCE_PRIOR = 0.5  # used when a donor has no offer history
VOLUME_CAP = 10.0  # donations_till_date at/above this map to volume == 1.0

# Tier thresholds (documented and tunable; formalized in task 6.2).
ANCHOR_MIN_SCORE = 75.0
ANCHOR_MIN_ACCEPTANCE = 0.70
STEADY_MIN_SCORE = 45.0


# --------------------------------------------------------------------------- #
# Input + output shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DonorStats:
    """The historical signals a reliability score is computed from.

    The seeded dataset carries volume / recency / call signals but not yet
    per-offer ``accepted`` / ``offered`` counts, so ``offered == 0`` is a valid
    state that triggers the neutral acceptance prior (Requirement 3.4).

    Preconditions (design section 4.3): ``offered >= accepted >= 0`` and
    ``donations_till_date >= 0``.
    """

    accepted: int = 0
    offered: int = 0
    calls_to_donations_ratio: float = 0.0
    donations_till_date: int = 0
    last_donation_date: Optional[date] = None


class ScoreComponents(TypedDict):
    """Component breakdown returned alongside the raw score (each in [0, 1])."""

    acceptanceRatio: float
    callEfficiency: float
    volumeFactor: float
    recencyFactor: float


class ScoreResult(TypedDict):
    """The dict result of :func:`reliability_score`."""

    score: float
    tier: str
    components: ScoreComponents


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def recency_factor(last_donation: Optional[date], today: date) -> float:
    """Exponential-decay recency factor in ``[0, 1]``.

    ``None`` (no recorded donation) -> ``0.0``. Otherwise ``0.5 ** (days / H)``
    where ``days = max(0, (today - last_donation).days)`` and ``H`` is the
    120-day half-life: a donation today scores ``1.0`` and decays to ``0.5`` at
    the half-life. Future dates are clamped to ``0`` days -> ``1.0``.
    """
    if last_donation is None:
        return 0.0
    days = max(0, (today - last_donation).days)
    return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into ``[low, high]`` (defensive bound guarantee)."""
    return max(low, min(high, value))


def reliability_score(d: DonorStats, today: date) -> ScoreResult:
    """Compute the 0–100 reliability score, tier, and component breakdown.

    Implements design section 4.3 exactly:

    * ``acceptance = accepted / offered`` when ``offered > 0``, else the neutral
      0.5 prior (Requirement 3.4).
    * ``calls = 1 / max(1, calls_to_donations_ratio)`` (call efficiency).
    * ``volume = min(1, donations_till_date / 10)`` (capped at 10).
    * ``recency`` = :func:`recency_factor`.

    The raw weighted sum lies in ``[0, 1]`` (weights sum to 1.0, factors in
    ``[0, 1]``); the score is ``round(raw * 100, 1)`` and clamped to
    ``[0, 100]`` defensively (Requirements 3.1, 3.2).
    """
    acceptance = (
        d.accepted / d.offered if d.offered > 0 else NEUTRAL_ACCEPTANCE_PRIOR
    )
    calls = 1.0 / max(1.0, d.calls_to_donations_ratio)
    volume = min(1.0, d.donations_till_date / VOLUME_CAP)
    recency = recency_factor(d.last_donation_date, today)

    # Clamp each factor into [0, 1] defensively (e.g. acceptance > 1 if the
    # accepted/offered precondition were ever violated by upstream data).
    acceptance = _clamp(acceptance, 0.0, 1.0)
    calls = _clamp(calls, 0.0, 1.0)
    volume = _clamp(volume, 0.0, 1.0)
    recency = _clamp(recency, 0.0, 1.0)

    raw = (
        WEIGHTS["acceptance"] * acceptance
        + WEIGHTS["calls"] * calls
        + WEIGHTS["volume"] * volume
        + WEIGHTS["recency"] * recency
    )
    score = _clamp(round(raw * 100, 1), 0.0, 100.0)

    return {
        "score": score,
        "tier": tier_for(score, acceptance),
        "components": {
            "acceptanceRatio": acceptance,
            "callEfficiency": calls,
            "volumeFactor": volume,
            "recencyFactor": recency,
        },
    }


def tier_for_enum(score: float, acceptance: float) -> DonorTier:
    """Map ``(score, acceptance)`` to exactly one :class:`DonorTier` band.

    This is the canonical tier-assignment function (Requirement 3.3,
    formalized in task 6.2). The bands are **non-overlapping** and
    **exhaustive** over every real ``score`` (and every ``acceptance``), so
    every input maps to exactly one tier with no gaps and no overlaps:

    * ``anchor``  — ``score >= 75`` **and** ``acceptance >= 0.70``.
    * ``steady``  — everything else with ``score >= 45``. This is precisely
      ``45 <= score < 75`` **or** (``score >= 75`` **and** ``acceptance < 0.70``).
    * ``growing`` — ``score < 45`` (the exhaustive fallback).

    The function reaches exactly one ``return`` for any input because the three
    guards partition the input space: the first ``return`` claims the anchor
    region; whatever is not anchor falls through to the ``score >= 45`` test,
    which together with the final unconditional ``return`` splits the remainder
    into ``steady`` (``>= 45``) and ``growing`` (``< 45``). There is no input
    for which zero branches fire (the last ``return`` has no guard) and none for
    which two fire (each branch returns immediately). The boundaries are
    half-open by construction — ``45`` and ``75`` belong to the higher band, and
    the acceptance threshold ``0.70`` belongs to ``anchor`` — so the bands never
    overlap. This holds for scores in ``[0, 100]`` and defensively for any value
    outside that range (very negative -> ``growing``; very large with high
    acceptance -> ``anchor``).
    """
    if score >= ANCHOR_MIN_SCORE and acceptance >= ANCHOR_MIN_ACCEPTANCE:
        return DonorTier.ANCHOR
    if score >= STEADY_MIN_SCORE:
        return DonorTier.STEADY
    return DonorTier.GROWING


def tier_for(score: float, acceptance: float) -> str:
    """Return the tier band's string value for ``(score, acceptance)``.

    Thin string-valued wrapper over :func:`tier_for_enum` (the canonical,
    enum-returning implementation). Bands (design section 4.3): ``anchor``
    (score >= 75 **and** acceptance >= 0.70), ``steady`` (45 <= score < 75, or
    score >= 75 with acceptance < 0.70), else ``growing``.
    """
    return tier_for_enum(score, acceptance).value


def build_reliability_score(
    donor_id: str, d: DonorStats, today: date
) -> ReliabilityScore:
    """Build the validated :class:`ReliabilityScore` domain model for a donor.

    Convenience wrapper around :func:`reliability_score` that produces the
    Pydantic model used across services (score in ``[0, 100]``, components each
    in ``[0, 1]``), stamped with a UTC ``computed_at``.
    """
    result = reliability_score(d, today)
    components = result["components"]
    return ReliabilityScore(
        donor_id=donor_id,
        score=result["score"],
        tier=DonorTier(result["tier"]),
        components=ReliabilityComponents(
            acceptance_ratio=components["acceptanceRatio"],
            call_efficiency=components["callEfficiency"],
            volume_factor=components["volumeFactor"],
            recency_factor=components["recencyFactor"],
        ),
        computed_at=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Donor-response recompute (Task 6.3, Requirement 3.5)
# --------------------------------------------------------------------------- #
#
# A donor response always means an offer was made, so every response increments
# ``offered``. Only an ``ACCEPTED`` response also increments ``accepted``:
#
#   * ``ACCEPTED``    -> offered += 1, accepted += 1
#   * ``DECLINED``    -> offered += 1, accepted unchanged
#   * ``NO_RESPONSE`` -> offered += 1, accepted unchanged
#
# Because ``DECLINED`` (and ``NO_RESPONSE``) leave ``accepted`` fixed while
# growing ``offered``, the acceptance ratio ``accepted / offered`` can only stay
# equal or drop, and every other factor (calls / volume / recency) is unchanged
# by a response. The weighted sum is monotonic in each factor, so the recomputed
# score is guaranteed ``<=`` the pre-response score for the same baseline. This
# is exactly Requirement 3.5 / design Property 4. (The neutral 0.5 acceptance
# prior used when ``offered == 0`` only ever decreases on the first decline:
# ``0/1 = 0 <= 0.5``.)


@dataclass(frozen=True)
class ResponseRecompute:
    """Result of :func:`on_donor_response`.

    Carries everything a caller needs to persist after a donor responds: the
    score *before* the response, the recomputed score+tier *after* it, and the
    updated :class:`DonorStats` (so the new counts can be saved).
    """

    donor_id: str
    response: "SlotResponse"
    previous_score: ReliabilityScore
    score: ReliabilityScore
    stats: DonorStats


def apply_response(stats: DonorStats, response: "SlotResponse") -> DonorStats:
    """Return updated :class:`DonorStats` after a single donor ``response``.

    Pure helper (no scoring, no I/O). A response always means an offer was made
    (``offered += 1``); only ``ACCEPTED`` also increments ``accepted``.
    ``DECLINED`` and ``NO_RESPONSE`` increment ``offered`` only, so the
    acceptance ratio can only stay equal or drop. All other signals are carried
    through unchanged. The ``offered >= accepted >= 0`` precondition is
    preserved by construction.
    """
    accepted = stats.accepted + (1 if response is SlotResponse.ACCEPTED else 0)
    offered = stats.offered + 1
    return DonorStats(
        accepted=accepted,
        offered=offered,
        calls_to_donations_ratio=stats.calls_to_donations_ratio,
        donations_till_date=stats.donations_till_date,
        last_donation_date=stats.last_donation_date,
    )


def on_donor_response(
    donor_id: str,
    stats: DonorStats,
    response: "SlotResponse",
    today: date,
) -> ResponseRecompute:
    """Recompute a donor's score and tier after a single ``response``.

    Implements the Reliability_Service's ``onDonorResponse`` (design Component 3)
    and Requirement 3.5: it scores the donor *before* and *after* the response so
    a ``DECLINED`` (or ``NO_RESPONSE``) response can never raise the score.

    The response's effect on the baseline is modelled by :func:`apply_response`
    (an offer was made, so ``offered += 1``; ``ACCEPTED`` also bumps
    ``accepted``). Because only ``ACCEPTED`` grows the numerator while every
    response grows the denominator, the acceptance ratio — and therefore the
    monotone weighted score — can only rise on ``ACCEPTED`` and can only stay
    equal or fall on ``DECLINED`` / ``NO_RESPONSE``.

    Returns a :class:`ResponseRecompute` with the previous score, the recomputed
    score+tier, and the updated stats so the caller can persist all three.
    """
    previous_score = build_reliability_score(donor_id, stats, today)
    updated_stats = apply_response(stats, response)
    recomputed_score = build_reliability_score(donor_id, updated_stats, today)

    # Defensive guarantee of Requirement 3.5: a DECLINED (or NO_RESPONSE)
    # response must never raise the score relative to the pre-response baseline.
    # The math above already ensures this; the assert documents and protects the
    # invariant against future changes to the formula.
    if response is not SlotResponse.ACCEPTED:
        assert recomputed_score.score <= previous_score.score, (
            "Requirement 3.5 violated: a non-accept response raised the score "
            f"({previous_score.score} -> {recomputed_score.score})"
        )

    return ResponseRecompute(
        donor_id=donor_id,
        response=response,
        previous_score=previous_score,
        score=recomputed_score,
        stats=updated_stats,
    )
