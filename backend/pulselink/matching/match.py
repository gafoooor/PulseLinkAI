"""Donor matching for a slot — eligibility gate + reliability ranking (Task 7.1).

This module is the **Matching_Service** of the design (Component 3's ranking
half / design section 4.4). It answers one question for the Subscription
Generator (Task 8): *given a slot's patient blood group, predicted window, and
city, which donors may we offer it to, and in what order?*

The ranking is a pure, in-process function over candidate donors so the demo
runs entirely offline. It implements four acceptance criteria:

* **Requirement 10.2 — city scoping.** Matching is always scoped to a single
  ``city_id``; donors in other cities are excluded.
* **Requirement 4.2 — eligibility / consent / compatibility.** A donor is only
  included when they are **blood-compatible** with the patient, have
  ``eligibility_status == "eligible"``, and hold an **active
  ``contact_for_slots`` consent scope** (checked via the injected
  :class:`~pulselink.common.consent.ConsentStore`, the same seam messaging and
  voice consult).
* **Requirement 4.1 — the next-eligible-date gate.** A donor whose
  ``next_eligible_date`` is set and falls **after** the slot window ``end`` is
  excluded (they would not be eligible in time). ``None`` means no restriction,
  and a date on or before ``window.end`` is allowed.
* **Requirement 4.3 — ranking.** Eligible donors are ordered by
  Reliability_Score **descending**, breaking ties by **shorter distance** to
  the patient first.

Distance uses a pure :func:`distance_km` haversine helper so no database is
needed for the demo. In production this proximity ordering is delegated to
**PostGIS** (the design's spatial store); the haversine here is the offline
equivalent and yields the same ordering.

Requirements: 4.1, 4.2, 4.3, 10.2
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from pulselink.common.consent import ConsentStore
from pulselink.common.enums import BloodGroup, ConsentScope
from pulselink.common.models import Donor, Window
from pulselink.reliability.scoring import DonorStats, reliability_score

# --------------------------------------------------------------------------- #
# Blood compatibility (ABO / Rh donor -> recipient table)
# --------------------------------------------------------------------------- #
# A donor can give to a recipient iff the recipient appears in the donor's
# compatibility set below. This is the standard red-cell donor->recipient
# table: O- is the universal donor, AB+ is the universal recipient, Rh- can
# give to Rh- and Rh+, and Rh+ can only give to Rh+.
_DONOR_TO_RECIPIENTS: dict[BloodGroup, frozenset[BloodGroup]] = {
    BloodGroup.O_NEGATIVE: frozenset(BloodGroup),  # universal donor -> all 8
    BloodGroup.O_POSITIVE: frozenset(
        {
            BloodGroup.O_POSITIVE,
            BloodGroup.A_POSITIVE,
            BloodGroup.B_POSITIVE,
            BloodGroup.AB_POSITIVE,
        }
    ),
    BloodGroup.A_NEGATIVE: frozenset(
        {
            BloodGroup.A_NEGATIVE,
            BloodGroup.A_POSITIVE,
            BloodGroup.AB_NEGATIVE,
            BloodGroup.AB_POSITIVE,
        }
    ),
    BloodGroup.A_POSITIVE: frozenset(
        {BloodGroup.A_POSITIVE, BloodGroup.AB_POSITIVE}
    ),
    BloodGroup.B_NEGATIVE: frozenset(
        {
            BloodGroup.B_NEGATIVE,
            BloodGroup.B_POSITIVE,
            BloodGroup.AB_NEGATIVE,
            BloodGroup.AB_POSITIVE,
        }
    ),
    BloodGroup.B_POSITIVE: frozenset(
        {BloodGroup.B_POSITIVE, BloodGroup.AB_POSITIVE}
    ),
    BloodGroup.AB_NEGATIVE: frozenset(
        {BloodGroup.AB_NEGATIVE, BloodGroup.AB_POSITIVE}
    ),
    BloodGroup.AB_POSITIVE: frozenset({BloodGroup.AB_POSITIVE}),
}


def blood_compatible(donor_group: BloodGroup, patient_group: BloodGroup) -> bool:
    """True iff a donor with ``donor_group`` can give to ``patient_group``.

    Implements the standard ABO/Rh red-cell donor->recipient compatibility
    table: ``O Negative`` is the universal donor (gives to all eight groups),
    ``AB Positive`` is the universal recipient (accepts all eight), an Rh-
    donor can give to the matching Rh- and Rh+ recipient, and an Rh+ donor can
    only give to Rh+ recipients.
    """
    return patient_group in _DONOR_TO_RECIPIENTS[donor_group]


def blood_match_score(donor_group: BloodGroup, patient_group: BloodGroup) -> float:
    """Normalised blood-compatibility score: 1.0 same group, 0.8 compatible, 0.0 incompatible."""
    if donor_group == patient_group:
        return 1.0
    if blood_compatible(donor_group, patient_group):
        return 0.8
    return 0.0


def distance_score(distance_km_val: float) -> float:
    """Map distance in km to a normalised proximity score via 1/(1+km)."""
    if not math.isfinite(distance_km_val) or distance_km_val < 0:
        return 0.0
    return 1.0 / (1.0 + distance_km_val)


# --------------------------------------------------------------------------- #
# Distance (pure haversine; PostGIS in production)
# --------------------------------------------------------------------------- #
_EARTH_RADIUS_KM = 6371.0088


def distance_km(
    lat1: Optional[float],
    lng1: Optional[float],
    lat2: Optional[float],
    lng2: Optional[float],
) -> float:
    """Great-circle distance in km between two points (pure haversine).

    Returns ``math.inf`` when any coordinate is missing so donors with unknown
    location sort *after* located donors on a reliability tie (they never beat
    a donor with a real, shorter distance). Production delegates this proximity
    ordering to PostGIS; this haversine is the offline equivalent.
    """
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return math.inf

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- #
# Input + output shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MatchCandidate:
    """A donor plus the historical stats needed to score them.

    Bundles a :class:`~pulselink.common.models.Donor` with their
    :class:`~pulselink.reliability.scoring.DonorStats` so the Subscription
    Generator (Task 8) can hand :func:`rank_donors_for_slot` a single clean
    input shape. The reliability score is computed inside the matcher from
    ``stats`` so callers do not have to pre-score donors.
    """

    donor: Donor
    stats: DonorStats


@dataclass(frozen=True)
class RankedDonor:
    """A donor that passed the eligibility gate, with score and distance.

    Returned in ranked order (reliability descending, then distance ascending).
    Carries the originating :class:`Donor` so the caller can build slot
    assignments without a second lookup.
    """

    donor_id: str
    donor: Donor
    score: float
    tier: str
    distance_km: float


# --------------------------------------------------------------------------- #
# The matcher
# --------------------------------------------------------------------------- #
def _is_eligible(
    donor: Donor,
    *,
    patient_blood_group: BloodGroup,
    window: Window,
    city_id: str,
    consent_store: ConsentStore,
) -> bool:
    """Apply the full eligibility gate (Requirements 10.2, 4.2, 4.1)."""
    # Requirement 10.2: scope to a single city.
    if donor.city_id != city_id:
        return False
    # Requirement 4.2: eligible status.
    if donor.eligibility_status != "eligible":
        return False
    # Requirement 4.2: blood compatibility (donor -> patient).
    if not blood_compatible(donor.blood_group, patient_blood_group):
        return False
    # Requirement 4.2: active contact_for_slots consent scope.
    if not consent_store.has_active_scope(
        donor.donor_id, ConsentScope.CONTACT_FOR_SLOTS
    ):
        return False
    # Requirement 4.1: exclude when next_eligible_date is set and falls AFTER
    # the window end (None => no restriction; on/before window.end is allowed).
    if (
        donor.next_eligible_date is not None
        and donor.next_eligible_date > window.end
    ):
        return False
    return True


def rank_donors_for_slot(
    *,
    patient_blood_group: BloodGroup,
    window: Window,
    city_id: str,
    candidates: Iterable[MatchCandidate],
    consent_store: ConsentStore,
    patient_lat: Optional[float] = None,
    patient_lng: Optional[float] = None,
    today: date,
) -> list[RankedDonor]:
    """Filter to eligible donors for a slot and rank them.

    Eligibility gate (a donor must pass *all* of these):

    * same ``city_id`` (Requirement 10.2);
    * ``eligibility_status == "eligible"`` (Requirement 4.2);
    * blood-compatible with the patient (Requirement 4.2);
    * holds an active ``contact_for_slots`` consent scope (Requirement 4.2);
    * ``next_eligible_date`` is ``None`` **or** on/before ``window.end`` —
      i.e. excluded only when it is set and falls *after* the window end
      (Requirement 4.1).

    Ranking (Requirement 4.3): Reliability_Score **descending**, ties broken by
    **shorter distance** to the patient first. Distance is the pure haversine
    :func:`distance_km` (PostGIS in production); donors with unknown
    coordinates sort after located donors on a tie.

    Returns the ranked list of :class:`RankedDonor`.
    """
    eligible: list[RankedDonor] = []
    for candidate in candidates:
        donor = candidate.donor
        if not _is_eligible(
            donor,
            patient_blood_group=patient_blood_group,
            window=window,
            city_id=city_id,
            consent_store=consent_store,
        ):
            continue

        result = reliability_score(candidate.stats, today)
        dist = distance_km(patient_lat, patient_lng, donor.lat, donor.lng)
        eligible.append(
            RankedDonor(
                donor_id=donor.donor_id,
                donor=donor,
                score=result["score"],
                tier=result["tier"],
                distance_km=dist,
            )
        )

    # Reliability descending (negate), then distance ascending. Python's sort
    # is stable, so equal (score, distance) candidates keep input order.
    eligible.sort(key=lambda r: (-r.score, r.distance_km))
    return eligible
