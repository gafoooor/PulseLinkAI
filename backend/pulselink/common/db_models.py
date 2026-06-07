"""SQLAlchemy ORM table metadata for PulseLink's PostgreSQL schema (Task 1.3).

These ORM models are the source of truth for the relational schema and the
autogenerate target for Alembic (see ``migrations/env.py``). They are kept
separate from the in-process Pydantic domain models in
``pulselink.common.models`` (Task 1.2): this module is purely the persistence
schema.

Field names mirror the design's Data Models (and ``Dataset.csv``) so seeded
data maps cleanly. Design constraints encoded here:

* **City as a partition key** — every operational/matching table carries an
  indexed ``city_id`` (Requirements 10.1, 10.2).
* **Privacy by construction** — ``contact_point`` (encrypted value) and the
  append-only ``audit_log`` live apart from the operational/matching tables
  (Requirements 7.3, 11.2). ``contact_point`` deliberately has no ``city_id``;
  it is not a matching table.
* **PostGIS proximity** — ``donor`` carries lat/lng plus a geometry point for
  proximity ranking in matching (Requirement 4.3).
* **Versioned consent** — ``consent`` carries a ``version``, an array of
  scopes, and a nullable ``revoked_at`` (Requirement 7.x).

The domain enums are modelled in the Pydantic layer; columns here use portable
string types so the schema stays migration-friendly.
"""

from __future__ import annotations

from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from pulselink.common.db import Base

# ---------------------------------------------------------------------------
# Privacy-critical tables (kept apart from operational/matching data)
# ---------------------------------------------------------------------------


class Consent(Base):
    """Versioned consent with scoped grants and a nullable revocation time."""

    __tablename__ = "consent"

    consent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)  # patient|donor
    # Versioned scopes stored as an array of scope strings (ConsentScope[]).
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_consent_subject", "subject_id", "subject_type"),
    )


class ContactPoint(Base):
    """Encrypted, consent-gated contact details — separate from matching data.

    ``value_encrypted`` holds the contact value encrypted at rest; the
    plaintext value is never stored, logged, or written to audit entries
    (Requirements 7.3, 7.4).
    """

    __tablename__ = "contact_point"

    contact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # phone|whatsapp
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_lang: Mapped[str | None] = mapped_column(String(8), nullable=True)


class AuditLog(Base):
    """Append-only audit log (actor / action / timestamp).

    The application exposes no update or delete path for this table; it is the
    system of record for parse confirmations, data shares, and notifications
    (Requirements 11.1, 11.2). Contact values are never written here in
    plaintext (Requirement 7.4).
    """

    __tablename__ = "audit_log"

    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    # Optional context (no PII / no plaintext contact values).
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Operational / matching tables (every one carries an indexed city_id)
# ---------------------------------------------------------------------------


class Patient(Base):
    """A Thalassemia patient, derived from a dataset ``bridge_id``."""

    __tablename__ = "patient"

    patient_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # bridge_id
    city_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    blood_group: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity_required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cadence_days: Mapped[float] = mapped_column(Float, nullable=False)
    last_transfusion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_next_transfusion_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    bridge_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )  # active|inactive
    consent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("consent.consent_id"), nullable=True
    )

    __table_args__ = (
        Index("ix_patient_next_transfusion", "expected_next_transfusion_date"),
    )


class Donor(Base):
    """A registered blood donor (dataset ``user_id``)."""

    __tablename__ = "donor"

    donor_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # user_id
    city_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    blood_group: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    donor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    preferred_lang: Mapped[str | None] = mapped_column(String(8), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    # PostGIS point for proximity ranking (lng, lat) in WGS84 / SRID 4326.
    geom: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    last_donation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_eligible_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    eligibility_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="eligible"
    )  # eligible|not eligible
    donations_till_date: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    calls_to_donations_ratio: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    consent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("consent.consent_id"), nullable=True
    )

    __table_args__ = (
        Index("ix_donor_eligibility_status", "eligibility_status"),
        Index("ix_donor_next_eligible_date", "next_eligible_date"),
    )


class Subscription(Base):
    """A patient's forward-looking recurring plan of transfusion slots."""

    __tablename__ = "subscription"

    subscription_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("patient.patient_id"), nullable=False, index=True
    )
    city_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cadence_days: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Slot(Base):
    """A single upcoming transfusion occurrence needing donor commitments."""

    __tablename__ = "slot"

    slot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subscription_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("subscription.subscription_id"), nullable=False, index=True
    )
    patient_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("patient.patient_id"), nullable=False, index=True
    )
    city_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    window_expected: Mapped[date] = mapped_column(Date, nullable=False)
    units_needed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="planned"
    )  # planned|offered|confirmed|fulfilled|missed

    __table_args__ = (
        Index("ix_slot_status", "status"),
    )


class TransfusionRecord(Base):
    """An actual recorded transfusion; recording one triggers cadence re-learn."""

    __tablename__ = "transfusion_record"

    record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("patient.patient_id"), nullable=False, index=True
    )
    city_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    units_given: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    fulfilled_by_slot_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("slot.slot_id"), nullable=True
    )
    recorded_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SlotAssignment(Base):
    """The link between a slot and a donor, carrying rank and status."""

    __tablename__ = "slot_assignment"

    assignment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("slot.slot_id"), nullable=False, index=True
    )
    donor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("donor.donor_id"), nullable=False, index=True
    )
    city_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )  # active|declined|confirmed|promoted|expired
    offered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_slot_assignment_status", "status"),
    )


class Notification(Base):
    """A localized slot offer sent to a donor over a channel."""

    __tablename__ = "notification"

    notification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    donor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("donor.donor_id"), nullable=False, index=True
    )
    slot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("slot.slot_id"), nullable=False, index=True
    )
    city_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # sms|whatsapp|voice|mock
    lang: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="queued"
    )  # queued|sent|delivered|responded|failed
    response: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ACCEPTED|DECLINED|NO_RESPONSE
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReliabilityScore(Base):
    """A donor's computed reliability score, tier, and components."""

    __tablename__ = "reliability_score"

    donor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("donor.donor_id"), primary_key=True
    )
    city_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # anchor|steady|growing
    acceptance_ratio: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    recency_factor: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    volume_factor: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    call_efficiency: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
