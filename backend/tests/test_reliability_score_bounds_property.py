"""Property-based test for reliability score bounds (Task 6.4).

**Property 3: Score bounds** — for every donor and every input, the computed
reliability score stays within ``[0, 100]``, the assigned tier is one of the
three :class:`DonorTier` values, and every component factor stays within
``[0, 1]``.

**Validates: Requirements 3.1**

This exercises the real ``reliability_score`` implementation (no mocks) across a
wide, intelligently constrained input space: it covers the neutral-prior case
(``offered == 0``), the well-formed case (``accepted <= offered``), and stresses
the defensive clamp with ``accepted > offered``; call ratios from 0 up to large
pathological values; volumes above the cap; and recency dates in the past, the
future, and ``None``.
"""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.enums import DonorTier
from pulselink.reliability.scoring import DonorStats, reliability_score

# Bounded, realistic date range for both last-donation and "today".
_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31))

# Optional last-donation date covers None (no history), and past/future dates
# relative to whatever "today" is generated.
_optional_dates = st.none() | _dates

# Non-negative donation counts, including values above the volume cap (10).
_donations = st.integers(min_value=0, max_value=10_000)

# Non-negative call ratios: 0 (dataset default), normal, and pathological highs.
_ratios = st.floats(
    min_value=0.0,
    max_value=50.0,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _donor_stats(draw: st.DrawFn) -> DonorStats:
    """Generate DonorStats spanning the neutral prior, well-formed, and
    defensive-clamp (accepted > offered) regions of the input space."""
    offered = draw(st.integers(min_value=0, max_value=1_000))
    # Allow accepted both within [0, offered] (well-formed) and beyond offered
    # (stresses the defensive acceptance clamp), plus offered == 0 (neutral
    # prior) which makes accepted irrelevant.
    accepted = draw(st.integers(min_value=0, max_value=1_000))
    return DonorStats(
        accepted=accepted,
        offered=offered,
        calls_to_donations_ratio=draw(_ratios),
        donations_till_date=draw(_donations),
        last_donation_date=draw(_optional_dates),
    )


_VALID_TIERS = {t.value for t in DonorTier}


@given(stats=_donor_stats(), today=_dates)
def test_score_bounds(stats: DonorStats, today: date) -> None:
    """Property 3: score in [0, 100], a valid tier, and components in [0, 1]."""
    result = reliability_score(stats, today)

    # Score is always within the documented [0, 100] band.
    assert 0.0 <= result["score"] <= 100.0

    # Tier is exactly one of the three documented DonorTier values.
    assert result["tier"] in _VALID_TIERS

    # Every component factor stays within the unit interval.
    for value in result["components"].values():
        assert 0.0 <= value <= 1.0
