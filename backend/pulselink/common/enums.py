"""Shared domain enumerations for PulseLink.

These enums are the canonical, closed value sets referenced across every
service. They mirror the design document's Data Models section exactly:

* ``BloodGroup``       — the canonical 8-value blood-group set.
* ``DonorTier``        — donor reliability tiers (anchor / steady / growing).
* ``SlotStatus``       — lifecycle of a transfusion slot.
* ``AssignmentStatus`` — lifecycle of a donor's assignment to a slot.
* ``SlotResponse``     — a donor's outcome for a single slot.
* ``ConsentScope``     — the data-use scopes a subject may grant.

Using string-valued enums keeps the on-the-wire / on-disk representation
human-readable and identical to the dataset and design notation.
"""

from __future__ import annotations

from enum import Enum


class BloodGroup(str, Enum):
    """Canonical blood-group set (maps to dataset ``bridge_blood_group``)."""

    A_POSITIVE = "A Positive"
    A_NEGATIVE = "A Negative"
    B_POSITIVE = "B Positive"
    B_NEGATIVE = "B Negative"
    AB_POSITIVE = "AB Positive"
    AB_NEGATIVE = "AB Negative"
    O_POSITIVE = "O Positive"
    O_NEGATIVE = "O Negative"


class DonorTier(str, Enum):
    """Donor reliability tiers, highest to lowest dependability."""

    ANCHOR = "anchor"
    STEADY = "steady"
    GROWING = "growing"


class SlotStatus(str, Enum):
    """Lifecycle status of a single transfusion slot."""

    PLANNED = "planned"
    OFFERED = "offered"
    CONFIRMED = "confirmed"
    FULFILLED = "fulfilled"
    MISSED = "missed"


class AssignmentStatus(str, Enum):
    """Lifecycle status of a donor's assignment to a slot."""

    ACTIVE = "active"
    DECLINED = "declined"
    CONFIRMED = "confirmed"
    PROMOTED = "promoted"
    EXPIRED = "expired"


class SlotResponse(str, Enum):
    """A donor's outcome for exactly one slot."""

    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    NO_RESPONSE = "NO_RESPONSE"


class ConsentScope(str, Enum):
    """The data-use scopes a patient or donor may grant via a ``Consent``."""

    STORE_CONTACT = "store_contact"
    CONTACT_FOR_SLOTS = "contact_for_slots"
    SHARE_WITH_COORDINATOR = "share_with_coordinator"
    USE_IN_FORECASTING = "use_in_forecasting"
