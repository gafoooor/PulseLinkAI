"""Property-based test for the matching eligibility gate (Task 7.2).

**Property 9: Eligibility gate** — every offered (ranked) donor is
blood-compatible with the patient and is never offered before its
``next_eligible_date`` (when set) relative to the slot window. In full: for
every donor returned by :func:`rank_donors_for_slot`, the donor is
blood-compatible, in the target city, ``eligibility_status == "eligible"``,
holds an active ``contact_for_slots`` consent scope, and has a
``next_eligible_date`` that is either ``None`` or on/before the window ``end``.
The output is additionally non-increasing in Reliability_Score.

**Validates: Requirements 4.1**

This exercises the real :func:`rank_donors_for_slot` (no mocks) against a real
``InMemoryConsentService`` over a wide, intelligently constrained input space:
random patient blood group, a valid random window, and random candidate donors
varying every gate dimension —

* blood group (all eight ABO/Rh groups),
* ``eligibility_status`` (``eligible`` / ``not eligible``),
* ``next_eligible_date`` relative to ``window.end`` (``None`` / before / on /
  after),
* ``city_id`` (the target city and other cities),
* consent (no record, granted with/without ``contact_for_slots``, and
  granted-then-revoked).
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import BloodGroup, ConsentScope
from pulselink.common.models import Donor, Window
from pulselink.matching.match import (
    MatchCandidate,
    blood_compatible,
    rank_donors_for_slot,
)
from pulselink.reliability.scoring import DonorStats

TODAY = date(2024, 1, 1)
TARGET_CITY = "city-1"
ALL_GROUPS = list(BloodGroup)
ALL_SCOPES = list(ConsentScope)

# Finite, in-range coordinate strategy (or unknown location).
_coords = st.one_of(
    st.none(),
    st.floats(min_value=-90.0, max_value=90.0, allow_nan=False, allow_infinity=False),
)
_lngs = st.one_of(
    st.none(),
    st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _windows(draw: st.DrawFn) -> Window:
    """A valid random inclusive window with ``start <= expected <= end``."""
    end = draw(st.dates(min_value=date(2024, 1, 1), max_value=date(2025, 12, 31)))
    span = draw(st.integers(min_value=0, max_value=30))
    start = end - timedelta(days=span)
    expected = start + timedelta(days=draw(st.integers(min_value=0, max_value=span)))
    return Window(start=start, expected=expected, end=end)


@st.composite
def _scenario(draw: st.DrawFn):
    """Build a full ranking scenario varying every eligibility-gate dimension.

    Returns ``(patient_group, window, city_id, candidates, consent_store,
    patient_lat, patient_lng)``.
    """
    patient_group = draw(st.sampled_from(ALL_GROUPS))
    window = draw(_windows())
    consent = InMemoryConsentService()
    candidates: list[MatchCandidate] = []

    n = draw(st.integers(min_value=0, max_value=6))
    for i in range(n):
        donor_id = f"d{i}"

        # next_eligible_date relative to window.end: none / before / on / after.
        kind = draw(st.sampled_from(["none", "before", "on", "after"]))
        if kind == "none":
            next_eligible = None
        elif kind == "before":
            next_eligible = window.end - timedelta(
                days=draw(st.integers(min_value=1, max_value=120))
            )
        elif kind == "on":
            next_eligible = window.end
        else:  # after
            next_eligible = window.end + timedelta(
                days=draw(st.integers(min_value=1, max_value=120))
            )

        donor = Donor(
            donor_id=donor_id,
            city_id=draw(st.sampled_from([TARGET_CITY, "city-2", "city-3"])),
            blood_group=draw(st.sampled_from(ALL_GROUPS)),
            role="Bridge Donor",
            donor_type="Regular Donor",
            lat=draw(_coords),
            lng=draw(_lngs),
            next_eligible_date=next_eligible,
            eligibility_status=draw(st.sampled_from(["eligible", "not eligible"])),
            donations_till_date=draw(st.integers(min_value=0, max_value=20)),
            total_calls=0,
            calls_to_donations_ratio=draw(
                st.floats(min_value=0.0, max_value=25.0, allow_nan=False, allow_infinity=False)
            ),
            consent_id=f"consent-{donor_id}",
        )

        # Consent: none / granted (any scope subset) / granted-then-revoked.
        if draw(st.booleans()):
            scopes = draw(
                st.lists(
                    st.sampled_from(ALL_SCOPES),
                    unique=True,
                    max_size=len(ALL_SCOPES),
                )
            )
            consent.grant(donor_id, "donor", scopes, "v1")
            if draw(st.booleans()):
                consent.revoke(donor_id)

        stats = DonorStats(
            donations_till_date=donor.donations_till_date,
            calls_to_donations_ratio=donor.calls_to_donations_ratio,
        )
        candidates.append(MatchCandidate(donor, stats))

    patient_lat = draw(_coords)
    patient_lng = draw(_lngs)
    return patient_group, window, TARGET_CITY, candidates, consent, patient_lat, patient_lng


@settings(max_examples=300)
@given(scenario=_scenario())
def test_every_ranked_donor_passes_the_eligibility_gate(scenario) -> None:
    """Property 9: every ranked donor satisfies the full eligibility gate."""
    patient_group, window, city_id, candidates, consent, plat, plng = scenario

    ranked = rank_donors_for_slot(
        patient_blood_group=patient_group,
        window=window,
        city_id=city_id,
        candidates=candidates,
        consent_store=consent,
        patient_lat=plat,
        patient_lng=plng,
        today=TODAY,
    )

    donors_by_id = {c.donor.donor_id: c.donor for c in candidates}

    for ranked_donor in ranked:
        donor = donors_by_id[ranked_donor.donor_id]

        # Blood-compatible donor -> patient (Requirement 4.2 / Property 9 core).
        assert blood_compatible(donor.blood_group, patient_group) is True
        # Scoped to the target city (Requirement 10.2).
        assert donor.city_id == city_id
        # Eligible status (Requirement 4.2).
        assert donor.eligibility_status == "eligible"
        # Active contact_for_slots consent scope (Requirement 4.2).
        assert consent.has_active_scope(donor.donor_id, ConsentScope.CONTACT_FOR_SLOTS)
        # Never offered before next_eligible_date relative to the window
        # (Requirement 4.1): None means no restriction, else on/before end.
        assert (
            donor.next_eligible_date is None
            or donor.next_eligible_date <= window.end
        )

    # Ranking is non-increasing by Reliability_Score (Requirement 4.3).
    scores = [r.score for r in ranked]
    assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))
