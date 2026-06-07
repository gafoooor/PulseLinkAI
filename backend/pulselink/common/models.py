"""Shared domain models for PulseLink (Task 1.2).

Pydantic v2 models for every core entity, mirroring the design document's Data
Models section and ``Dataset.csv`` field names. These are the in-process domain
types passed between services (request/response validation and business logic);
the relational/ORM schema used for persistence lives in
``pulselink.common.db_models`` (Task 1.3).

Validation rules encoded here (from the design + requirements):
* ``cadence_days > 0`` and ``quantity_required >= 1`` (Patient, Subscription).
* ``units_given >= 1`` and transfusion ``date`` not in the future.
* ``donations_till_date >= 0``; non-negative call counts/ratios (Donor).
* score in ``[0, 100]`` and component factors in ``[0, 1]`` (ReliabilityScore).
* ``start <= expected <= end`` (Window).
* a Patient must reference a non-empty ``consent_id``.
* clinical-cadence band (7–45 days) is *flagged, not rejected*.

Requirements: 1.3, 2.1, 3.1, 4.4, 5.5, 10.1
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from pulselink.common.enums import (
    AssignmentStatus,
    BloodGroup,
    ConsentScope,
    DonorTier,
    SlotResponse,
    SlotStatus,
)

# Typical clinical cadence band for Thalassemia Major (days). Values outside
# this band are flagged for review, never rejected.
CLINICAL_CADENCE_MIN_DAYS = 7
CLINICAL_CADENCE_MAX_DAYS = 45

DonorRole = Literal["Bridge Donor", "Emergency Donor", "Volunteer"]
DonorType = Literal["Regular Donor", "One-Time Donor", "Other"]
EligibilityStatus = Literal["eligible", "not eligible"]
BridgeStatus = Literal["active", "inactive"]
SubjectType = Literal["patient", "donor"]
ContactType = Literal["phone", "whatsapp"]
NotificationChannel = Literal["sms", "whatsapp", "voice", "mock"]
NotificationStatus = Literal["queued", "sent", "delivered", "responded", "failed"]


# --------------------------------------------------------------------------- #
# Patient
# --------------------------------------------------------------------------- #
class Patient(BaseModel):
    """A Thalassemia patient derived from a dataset ``bridge_id``."""

    patient_id: str = Field(min_length=1)
    city_id: str = Field(min_length=1)
    blood_group: BloodGroup
    quantity_required: int = Field(ge=1)
    cadence_days: float = Field(gt=0)
    last_transfusion_date: Optional[date] = None
    expected_next_transfusion_date: Optional[date] = None
    bridge_status: BridgeStatus = "active"
    consent_id: str = Field(min_length=1)
    lat: Optional[float] = None
    lng: Optional[float] = None

    @property
    def cadence_in_clinical_band(self) -> bool:
        """True when cadence is within the typical clinical band (flag, not reject)."""
        return CLINICAL_CADENCE_MIN_DAYS <= self.cadence_days <= CLINICAL_CADENCE_MAX_DAYS


# --------------------------------------------------------------------------- #
# TransfusionRecord
# --------------------------------------------------------------------------- #
class TransfusionRecord(BaseModel):
    """An actual recorded transfusion; recording one triggers cadence re-learn."""

    record_id: str = Field(min_length=1)
    patient_id: str = Field(min_length=1)
    date: date
    units_given: int = Field(ge=1)
    fulfilled_by_slot_id: Optional[str] = None
    recorded_by: Optional[str] = None

    @field_validator("date")
    @classmethod
    def _date_not_in_future(cls, value: date) -> date:
        if value > datetime.now(timezone.utc).date():
            raise ValueError("transfusion date cannot be in the future")
        return value


# --------------------------------------------------------------------------- #
# Donor
# --------------------------------------------------------------------------- #
class Donor(BaseModel):
    """A registered blood donor (dataset ``user_id``)."""

    donor_id: str = Field(min_length=1)
    city_id: str = Field(min_length=1)
    blood_group: BloodGroup
    role: DonorRole
    donor_type: DonorType
    preferred_lang: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    last_donation_date: Optional[date] = None
    next_eligible_date: Optional[date] = None
    eligibility_status: EligibilityStatus = "eligible"
    donations_till_date: int = Field(ge=0)
    total_calls: int = Field(ge=0)
    calls_to_donations_ratio: float = Field(ge=0)
    consent_id: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# ReliabilityScore + components
# --------------------------------------------------------------------------- #
class ReliabilityComponents(BaseModel):
    """The weighted factors behind a donor's reliability score (each in [0,1])."""

    acceptance_ratio: float = Field(ge=0, le=1)
    recency_factor: float = Field(ge=0, le=1)
    volume_factor: float = Field(ge=0, le=1)
    call_efficiency: float = Field(ge=0, le=1)


class ReliabilityScore(BaseModel):
    """A donor's computed reliability score, tier, and component breakdown."""

    donor_id: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
    tier: DonorTier
    components: ReliabilityComponents
    computed_at: datetime


# --------------------------------------------------------------------------- #
# Window
# --------------------------------------------------------------------------- #
class Window(BaseModel):
    """A predicted inclusive transfusion window: ``start <= expected <= end``."""

    start: date
    expected: date
    end: date

    @model_validator(mode="after")
    def _check_ordering(self) -> "Window":
        if not (self.start <= self.expected <= self.end):
            raise ValueError("window requires start <= expected <= end")
        return self


# --------------------------------------------------------------------------- #
# Slot, SlotAssignment, Subscription
# --------------------------------------------------------------------------- #
class SlotAssignment(BaseModel):
    """The link between a slot and a donor, carrying rank and status."""

    assignment_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    donor_id: str = Field(min_length=1)
    rank: int = Field(ge=0)
    status: AssignmentStatus = AssignmentStatus.ACTIVE
    offered_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None


class Slot(BaseModel):
    """A single upcoming transfusion occurrence needing donor commitments."""

    slot_id: str = Field(min_length=1)
    subscription_id: str = Field(min_length=1)
    patient_id: str = Field(min_length=1)
    window: Window
    units_needed: int = Field(ge=1)
    status: SlotStatus = SlotStatus.PLANNED
    assignments: list[SlotAssignment] = Field(default_factory=list)


class Subscription(BaseModel):
    """A patient's forward-looking recurring plan of transfusion slots."""

    subscription_id: str = Field(min_length=1)
    patient_id: str = Field(min_length=1)
    cadence_days: float = Field(gt=0)
    horizon_days: int = Field(ge=1)
    slots: list[Slot] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Notification
# --------------------------------------------------------------------------- #
class Notification(BaseModel):
    """A localized slot offer delivered to a donor over a channel."""

    notification_id: str = Field(min_length=1)
    donor_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    channel: NotificationChannel
    lang: Optional[str] = None
    status: NotificationStatus = "queued"
    response: Optional[SlotResponse] = None
    sent_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# Consent + ContactPoint (privacy-critical)
# --------------------------------------------------------------------------- #
class Consent(BaseModel):
    """Versioned consent with scoped grants and a nullable revocation time."""

    consent_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    subject_type: SubjectType
    scopes: list[ConsentScope] = Field(default_factory=list)
    granted_at: datetime
    revoked_at: Optional[datetime] = None
    version: str = Field(min_length=1)

    @property
    def is_active(self) -> bool:
        """A consent is active until it is revoked."""
        return self.revoked_at is None

    def has_active_scope(self, scope: ConsentScope) -> bool:
        """True when the consent is active and grants the requested scope."""
        return self.is_active and scope in self.scopes


class ContactPoint(BaseModel):
    """Encrypted, consent-gated contact details — separate from matching data."""

    contact_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    type: ContactType
    value_encrypted: str = Field(min_length=1)
    preferred_lang: Optional[str] = None
