"""Unit tests for donor matching (Task 7.1).

Covers the four acceptance criteria of ``rank_donors_for_slot`` and its
helpers, fully offline:

* blood-compatibility table spot checks (Requirement 4.2);
* eligibility / consent / ``next_eligible_date`` filtering (Requirements 4.1, 4.2);
* city scoping (Requirement 10.2);
* ranking by reliability descending with a distance tie-break (Requirement 4.3).
"""

from __future__ import annotations

from datetime import date

import pytest

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import BloodGroup, ConsentScope
from pulselink.common.models import Donor, Window
from pulselink.matching.match import (
    MatchCandidate,
    blood_compatible,
    distance_km,
    rank_donors_for_slot,
)
from pulselink.reliability.scoring import DonorStats

TODAY = date(2024, 1, 1)
WINDOW = Window(start=date(2024, 1, 10), expected=date(2024, 1, 12), end=date(2024, 1, 14))

ALL_GROUPS = list(BloodGroup)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _donor(
    donor_id: str,
    *,
    blood_group: BloodGroup = BloodGroup.O_NEGATIVE,
    city_id: str = "city-1",
    eligibility_status: str = "eligible",
    next_eligible_date=None,
    lat=None,
    lng=None,
) -> Donor:
    return Donor(
        donor_id=donor_id,
        city_id=city_id,
        blood_group=blood_group,
        role="Bridge Donor",
        donor_type="Regular Donor",
        lat=lat,
        lng=lng,
        next_eligible_date=next_eligible_date,
        eligibility_status=eligibility_status,
        donations_till_date=0,
        total_calls=0,
        calls_to_donations_ratio=0.0,
        consent_id=f"consent-{donor_id}",
    )


def _stats(donations: int = 0) -> DonorStats:
    return DonorStats(donations_till_date=donations)


def _consent_store(*granted_ids: str) -> InMemoryConsentService:
    store = InMemoryConsentService()
    for did in granted_ids:
        store.grant(did, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    return store


def _rank(candidates, consent_store, *, patient_group=BloodGroup.AB_POSITIVE,
          patient_lat=None, patient_lng=None, city_id="city-1"):
    return rank_donors_for_slot(
        patient_blood_group=patient_group,
        window=WINDOW,
        city_id=city_id,
        candidates=candidates,
        consent_store=consent_store,
        patient_lat=patient_lat,
        patient_lng=patient_lng,
        today=TODAY,
    )


# --------------------------------------------------------------------------- #
# Blood compatibility (Requirement 4.2)
# --------------------------------------------------------------------------- #
def test_o_negative_is_universal_donor():
    for recipient in ALL_GROUPS:
        assert blood_compatible(BloodGroup.O_NEGATIVE, recipient) is True


def test_ab_positive_accepts_every_donor():
    for donor_group in ALL_GROUPS:
        assert blood_compatible(donor_group, BloodGroup.AB_POSITIVE) is True


def test_ab_positive_only_gives_to_ab_positive():
    for recipient in ALL_GROUPS:
        expected = recipient is BloodGroup.AB_POSITIVE
        assert blood_compatible(BloodGroup.AB_POSITIVE, recipient) is expected


@pytest.mark.parametrize(
    "donor_group,patient_group",
    [
        (BloodGroup.A_POSITIVE, BloodGroup.O_POSITIVE),   # A+ cannot give to O+
        (BloodGroup.B_POSITIVE, BloodGroup.A_POSITIVE),   # B+ cannot give to A+
        (BloodGroup.O_POSITIVE, BloodGroup.O_NEGATIVE),   # Rh+ cannot give to Rh-
        (BloodGroup.A_NEGATIVE, BloodGroup.B_NEGATIVE),   # A- cannot give to B-
        (BloodGroup.AB_NEGATIVE, BloodGroup.A_POSITIVE),  # AB- cannot give to A+
    ],
)
def test_incompatible_pairs_excluded(donor_group, patient_group):
    assert blood_compatible(donor_group, patient_group) is False


def test_rh_negative_gives_to_both_polarities():
    # A- gives to A-, A+, AB-, AB+ but not the B / O families.
    assert blood_compatible(BloodGroup.A_NEGATIVE, BloodGroup.A_POSITIVE) is True
    assert blood_compatible(BloodGroup.A_NEGATIVE, BloodGroup.AB_NEGATIVE) is True
    assert blood_compatible(BloodGroup.A_NEGATIVE, BloodGroup.O_NEGATIVE) is False


# --------------------------------------------------------------------------- #
# Eligibility / consent / next_eligible_date filtering (Req 4.1, 4.2)
# --------------------------------------------------------------------------- #
def test_compatible_eligible_consented_donor_is_included():
    d = _donor("d1")
    ranked = _rank([MatchCandidate(d, _stats())], _consent_store("d1"))
    assert [r.donor_id for r in ranked] == ["d1"]


def test_blood_incompatible_donor_excluded():
    # O+ donor cannot give to an O- patient.
    d = _donor("d1", blood_group=BloodGroup.O_POSITIVE)
    ranked = _rank(
        [MatchCandidate(d, _stats())],
        _consent_store("d1"),
        patient_group=BloodGroup.O_NEGATIVE,
    )
    assert ranked == []


def test_not_eligible_status_excluded():
    d = _donor("d1", eligibility_status="not eligible")
    ranked = _rank([MatchCandidate(d, _stats())], _consent_store("d1"))
    assert ranked == []


def test_missing_consent_scope_excluded():
    d = _donor("d1")
    ranked = _rank([MatchCandidate(d, _stats())], _consent_store())  # no grant
    assert ranked == []


def test_revoked_consent_excluded():
    store = _consent_store("d1")
    store.revoke("d1")
    d = _donor("d1")
    ranked = _rank([MatchCandidate(d, _stats())], store)
    assert ranked == []


def test_next_eligible_date_after_window_end_excluded():
    d = _donor("d1", next_eligible_date=date(2024, 1, 15))  # after window.end (Jan 14)
    ranked = _rank([MatchCandidate(d, _stats())], _consent_store("d1"))
    assert ranked == []


def test_next_eligible_date_none_allowed():
    d = _donor("d1", next_eligible_date=None)
    ranked = _rank([MatchCandidate(d, _stats())], _consent_store("d1"))
    assert [r.donor_id for r in ranked] == ["d1"]


def test_next_eligible_date_on_window_end_allowed():
    d = _donor("d1", next_eligible_date=WINDOW.end)  # exactly Jan 14 — allowed
    ranked = _rank([MatchCandidate(d, _stats())], _consent_store("d1"))
    assert [r.donor_id for r in ranked] == ["d1"]


def test_next_eligible_date_before_window_end_allowed():
    d = _donor("d1", next_eligible_date=date(2024, 1, 5))
    ranked = _rank([MatchCandidate(d, _stats())], _consent_store("d1"))
    assert [r.donor_id for r in ranked] == ["d1"]


# --------------------------------------------------------------------------- #
# City scoping (Requirement 10.2)
# --------------------------------------------------------------------------- #
def test_donor_in_other_city_excluded():
    here = _donor("here", city_id="city-1")
    away = _donor("away", city_id="city-2")
    store = _consent_store("here", "away")
    ranked = _rank(
        [MatchCandidate(here, _stats()), MatchCandidate(away, _stats())],
        store,
        city_id="city-1",
    )
    assert [r.donor_id for r in ranked] == ["here"]


# --------------------------------------------------------------------------- #
# Ranking: reliability desc, distance tie-break (Requirement 4.3)
# --------------------------------------------------------------------------- #
def test_ranked_by_reliability_descending():
    # More donations => higher volume factor => higher score.
    low = _donor("low")
    high = _donor("high")
    store = _consent_store("low", "high")
    ranked = _rank(
        [MatchCandidate(low, _stats(donations=0)),
         MatchCandidate(high, _stats(donations=10))],
        store,
    )
    assert [r.donor_id for r in ranked] == ["high", "low"]
    assert ranked[0].score >= ranked[1].score


def test_distance_tie_break_when_scores_equal():
    # Identical stats => identical reliability score; nearer donor ranks first.
    patient_lat, patient_lng = 12.9716, 77.5946  # Bangalore
    near = _donor("near", lat=12.9720, lng=77.5950)   # ~tens of meters away
    far = _donor("far", lat=13.0827, lng=80.2707)     # Chennai, ~290 km away
    store = _consent_store("near", "far")
    ranked = _rank(
        [MatchCandidate(far, _stats(donations=5)),
         MatchCandidate(near, _stats(donations=5))],
        store,
        patient_lat=patient_lat,
        patient_lng=patient_lng,
    )
    assert [r.donor_id for r in ranked] == ["near", "far"]
    assert ranked[0].distance_km < ranked[1].distance_km


def test_donor_with_unknown_location_sorts_after_located_on_tie():
    patient_lat, patient_lng = 12.9716, 77.5946
    located = _donor("located", lat=12.9720, lng=77.5950)
    unknown = _donor("unknown", lat=None, lng=None)
    store = _consent_store("located", "unknown")
    ranked = _rank(
        [MatchCandidate(unknown, _stats(donations=5)),
         MatchCandidate(located, _stats(donations=5))],
        store,
        patient_lat=patient_lat,
        patient_lng=patient_lng,
    )
    assert [r.donor_id for r in ranked] == ["located", "unknown"]


def test_reliability_beats_distance():
    # A far but more-reliable donor still outranks a near low-reliability one:
    # reliability is the primary key, distance only the tie-break.
    patient_lat, patient_lng = 12.9716, 77.5946
    near_low = _donor("near_low", lat=12.9720, lng=77.5950)
    far_high = _donor("far_high", lat=13.0827, lng=80.2707)
    store = _consent_store("near_low", "far_high")
    ranked = _rank(
        [MatchCandidate(near_low, _stats(donations=0)),
         MatchCandidate(far_high, _stats(donations=10))],
        store,
        patient_lat=patient_lat,
        patient_lng=patient_lng,
    )
    assert [r.donor_id for r in ranked] == ["far_high", "near_low"]


# --------------------------------------------------------------------------- #
# distance_km helper
# --------------------------------------------------------------------------- #
def test_distance_zero_for_same_point():
    assert distance_km(12.0, 77.0, 12.0, 77.0) == pytest.approx(0.0, abs=1e-6)


def test_distance_missing_coordinate_is_infinite():
    assert distance_km(None, 77.0, 12.0, 77.0) == float("inf")
    assert distance_km(12.0, 77.0, 12.0, None) == float("inf")


def test_distance_known_pair_is_reasonable():
    # Bangalore -> Chennai is roughly 290 km great-circle.
    d = distance_km(12.9716, 77.5946, 13.0827, 80.2707)
    assert 250 < d < 320
