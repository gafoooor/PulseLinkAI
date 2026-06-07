"""Focused unit tests for matching filters and tie-break (Task 7.3).

These complement the broader suite in ``test_matching.py`` (Task 7.1) and
deliberately avoid duplicating it. They drill into three areas with cases the
7.1 suite does not cover:

* **Consent filtering (Requirement 4.2).** The matcher requires an *active
  ``contact_for_slots``* scope specifically. Here we add the "only other
  scopes" case — a donor who consented to ``store_contact`` / other scopes but
  not ``contact_for_slots`` — plus a mixed batch where only the
  ``contact_for_slots``-consented donors survive.
* **Eligibility filtering (Requirement 4.2).** A mixed batch of
  ``eligible`` / ``not eligible`` donors, asserting only the eligible ones pass,
  and a combined ``next_eligible_date`` filter where ``None`` / on / before the
  window end are kept and an after-window date is dropped — all in one call.
* **Proximity tie-break (Requirement 4.3).** Ordering across *three or more*
  equal-reliability donors at varying distances (nearest first), and several
  unknown-coordinate donors sorting *after* every located donor on a tie.

All cases are offline and deterministic.
"""

from __future__ import annotations

from datetime import date

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import BloodGroup, ConsentScope
from pulselink.common.models import Donor, Window
from pulselink.matching.match import MatchCandidate, rank_donors_for_slot
from pulselink.reliability.scoring import DonorStats

TODAY = date(2024, 1, 1)
WINDOW = Window(
    start=date(2024, 1, 10),
    expected=date(2024, 1, 12),
    end=date(2024, 1, 14),
)

# Patient location (Bangalore) used for the proximity tie-break cases. Donors
# placed at increasing latitude offsets give a clean, monotonic distance order.
PATIENT_LAT, PATIENT_LNG = 12.9716, 77.5946


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


def _stats(donations: int = 5) -> DonorStats:
    # Equal stats across donors => equal reliability score, isolating the
    # distance tie-break as the only differentiator.
    return DonorStats(donations_till_date=donations)


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
# Consent filtering — the "only other scopes" gap (Requirement 4.2)
# --------------------------------------------------------------------------- #
def test_consent_with_only_other_scopes_excluded():
    # Donor granted store_contact + share_with_coordinator but NOT
    # contact_for_slots => not contactable for a slot => excluded.
    store = InMemoryConsentService()
    store.grant(
        "d1",
        "donor",
        [ConsentScope.STORE_CONTACT, ConsentScope.SHARE_WITH_COORDINATOR],
        "v1",
    )
    ranked = _rank([MatchCandidate(_donor("d1"), _stats())], store)
    assert ranked == []


def test_only_contact_for_slots_consented_donors_kept_in_mixed_batch():
    store = InMemoryConsentService()
    # has the right scope
    store.grant("has_scope", "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    # has only an unrelated scope
    store.grant("other_scope", "donor", [ConsentScope.USE_IN_FORECASTING], "v1")
    # had the right scope, then revoked
    store.grant("revoked", "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    store.revoke("revoked")
    # no consent at all => "no_consent" donor not granted anything

    candidates = [
        MatchCandidate(_donor("has_scope"), _stats()),
        MatchCandidate(_donor("other_scope"), _stats()),
        MatchCandidate(_donor("revoked"), _stats()),
        MatchCandidate(_donor("no_consent"), _stats()),
    ]
    ranked = _rank(candidates, store)
    assert [r.donor_id for r in ranked] == ["has_scope"]


# --------------------------------------------------------------------------- #
# Eligibility-status + next_eligible_date filtering (Requirement 4.2)
# --------------------------------------------------------------------------- #
def test_only_eligible_status_donors_kept_in_mixed_batch():
    store = InMemoryConsentService()
    for did in ("ok1", "nope", "ok2"):
        store.grant(did, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    candidates = [
        MatchCandidate(_donor("ok1", eligibility_status="eligible"), _stats()),
        MatchCandidate(_donor("nope", eligibility_status="not eligible"), _stats()),
        MatchCandidate(_donor("ok2", eligibility_status="eligible"), _stats()),
    ]
    ranked = _rank(candidates, store)
    assert sorted(r.donor_id for r in ranked) == ["ok1", "ok2"]


def test_next_eligible_date_window_filter_across_batch():
    # One donor per next_eligible_date case, all otherwise eligible/consented:
    #   none      -> kept (no restriction)
    #   before    -> kept (eligible well before the window)
    #   on_end    -> kept (exactly the window end is allowed)
    #   after_end -> dropped (would not be eligible in time)
    store = InMemoryConsentService()
    for did in ("none", "before", "on_end", "after_end"):
        store.grant(did, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    candidates = [
        MatchCandidate(_donor("none", next_eligible_date=None), _stats()),
        MatchCandidate(_donor("before", next_eligible_date=date(2024, 1, 5)), _stats()),
        MatchCandidate(_donor("on_end", next_eligible_date=WINDOW.end), _stats()),
        MatchCandidate(_donor("after_end", next_eligible_date=date(2024, 1, 15)), _stats()),
    ]
    ranked = _rank(candidates, store)
    assert sorted(r.donor_id for r in ranked) == ["before", "none", "on_end"]
    assert "after_end" not in {r.donor_id for r in ranked}


# --------------------------------------------------------------------------- #
# Proximity tie-break across 3+ equal-score donors (Requirement 4.3)
# --------------------------------------------------------------------------- #
def test_three_equal_score_donors_ordered_nearest_first():
    # Same blood group + identical stats => identical reliability score, so the
    # distance tie-break decides the whole order. Increasing latitude offsets
    # give strictly increasing distance from the patient.
    store = InMemoryConsentService()
    for did in ("near", "mid", "far"):
        store.grant(did, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    near = _donor("near", lat=12.98, lng=PATIENT_LNG)   # ~tiny offset
    mid = _donor("mid", lat=13.10, lng=PATIENT_LNG)     # farther north
    far = _donor("far", lat=13.50, lng=PATIENT_LNG)     # farthest north
    # Provide them OUT of order to prove sorting, not input order, wins.
    candidates = [
        MatchCandidate(far, _stats()),
        MatchCandidate(near, _stats()),
        MatchCandidate(mid, _stats()),
    ]
    ranked = _rank(candidates, store, patient_lat=PATIENT_LAT, patient_lng=PATIENT_LNG)
    assert [r.donor_id for r in ranked] == ["near", "mid", "far"]
    # Distances are strictly increasing down the ranked list.
    dists = [r.distance_km for r in ranked]
    assert dists[0] < dists[1] < dists[2]


def test_unknown_coordinate_donors_sort_after_all_located_on_tie():
    # Two located donors (different distances) and two unknown-coordinate
    # donors, all equal score. Located donors come first ordered by distance;
    # the unknown-coordinate donors (distance == inf) come last.
    store = InMemoryConsentService()
    for did in ("near", "far", "unknown_a", "unknown_b"):
        store.grant(did, "donor", [ConsentScope.CONTACT_FOR_SLOTS], "v1")
    near = _donor("near", lat=12.98, lng=PATIENT_LNG)
    far = _donor("far", lat=13.30, lng=PATIENT_LNG)
    unknown_a = _donor("unknown_a", lat=None, lng=None)
    unknown_b = _donor("unknown_b", lat=None, lng=None)
    candidates = [
        MatchCandidate(unknown_a, _stats()),
        MatchCandidate(far, _stats()),
        MatchCandidate(unknown_b, _stats()),
        MatchCandidate(near, _stats()),
    ]
    ranked = _rank(candidates, store, patient_lat=PATIENT_LAT, patient_lng=PATIENT_LNG)
    # Located donors first (nearest then farther), unknowns last.
    assert [r.donor_id for r in ranked[:2]] == ["near", "far"]
    assert {r.donor_id for r in ranked[2:]} == {"unknown_a", "unknown_b"}
    assert ranked[2].distance_km == float("inf")
    assert ranked[3].distance_km == float("inf")
