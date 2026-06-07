"""Property-based test for the decline penalty (Task 6.6).

**Property 4: Decline penalizes** — processing a ``DECLINED`` response through
``on_donor_response`` yields a recomputed score ``<=`` the prior (pre-response)
score for every donor and every input.

**Validates: Requirements 3.2**

This exercises the real ``on_donor_response`` recompute (no mocks) across a
wide, intelligently constrained input space: it covers the neutral-prior case
(``offered == 0``), the well-formed case (``accepted <= offered``), call ratios
from 0 up to large pathological values, volumes above the cap, and recency dates
in the past, the future, and ``None``.

It also pins the two companion monotonicity facts that make Property 4
meaningful: a ``NO_RESPONSE`` behaves like a decline (score can only stay equal
or drop), while an ``ACCEPTED`` response can only keep the score equal or raise
it.
"""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.enums import SlotResponse
from pulselink.reliability.scoring import DonorStats, on_donor_response

# Bounded, realistic date range for both last-donation and "today".
_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31))

# Optional last-donation date covers None (no history) and past/future dates.
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
    """Generate DonorStats with ``accepted <= offered`` (the precondition),
    spanning the neutral-prior case (``offered == 0``, accepted forced to 0)
    and the well-formed case (``accepted`` within ``[0, offered]``)."""
    offered = draw(st.integers(min_value=0, max_value=1_000))
    accepted = draw(st.integers(min_value=0, max_value=offered))
    return DonorStats(
        accepted=accepted,
        offered=offered,
        calls_to_donations_ratio=draw(_ratios),
        donations_till_date=draw(_donations),
        last_donation_date=draw(_optional_dates),
    )


@given(stats=_donor_stats(), today=_dates)
def test_declined_response_never_raises_score(
    stats: DonorStats, today: date
) -> None:
    """Property 4: a DECLINED response yields score <= the prior score."""
    result = on_donor_response("donor-1", stats, SlotResponse.DECLINED, today)
    assert result.score.score <= result.previous_score.score


@given(stats=_donor_stats(), today=_dates)
def test_no_response_never_raises_score(
    stats: DonorStats, today: date
) -> None:
    """Companion fact: a NO_RESPONSE behaves like a decline (score <= prior)."""
    result = on_donor_response(
        "donor-1", stats, SlotResponse.NO_RESPONSE, today
    )
    assert result.score.score <= result.previous_score.score


@given(stats=_donor_stats(), today=_dates)
def test_accepted_response_never_lowers_score(
    stats: DonorStats, today: date
) -> None:
    """Companion fact: an ACCEPTED response yields score >= the prior score."""
    result = on_donor_response("donor-1", stats, SlotResponse.ACCEPTED, today)
    assert result.score.score >= result.previous_score.score
