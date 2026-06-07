"""initial schema: operational, privacy, and consent tables + PostGIS

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

Creates the full PulseLink schema:

* Enables the PostGIS extension for proximity ranking (Requirement 4.3).
* Operational/matching tables, each carrying an indexed ``city_id``
  (Requirements 10.1, 10.2): patient, donor, transfusion_record,
  subscription, slot, slot_assignment, notification, reliability_score.
* Privacy-critical tables kept apart from matching data: ``contact_point``
  (encrypted value column) and the append-only ``audit_log``
  (Requirements 7.3, 11.2).
* ``consent`` with versioned scopes and a nullable ``revoked_at``.

Local PostgreSQL (+PostGIS) now; maps to RDS db.t3.micro in the budget prod
profile.
"""

from __future__ import annotations

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostGIS extension drives proximity ranking in donor matching.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # ----- Privacy / consent tables (kept apart from matching data) --------
    op.create_table(
        "consent",
        sa.Column("consent_id", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("consent_id"),
    )
    op.create_index("ix_consent_subject", "consent", ["subject_id", "subject_type"])

    op.create_table(
        "contact_point",
        sa.Column("contact_id", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("preferred_lang", sa.String(length=8), nullable=True),
        sa.PrimaryKeyConstraint("contact_id"),
    )
    op.create_index(
        op.f("ix_contact_point_subject_id"), "contact_point", ["subject_id"]
    )

    # Append-only audit log: no UPDATE/DELETE path exposed by the application.
    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("subject_type", sa.String(length=16), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(op.f("ix_audit_log_timestamp"), "audit_log", ["timestamp"])

    # ----- Operational / matching tables (each carries city_id) ------------
    op.create_table(
        "patient",
        sa.Column("patient_id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("blood_group", sa.String(length=16), nullable=False),
        sa.Column("quantity_required", sa.Integer(), nullable=False),
        sa.Column("cadence_days", sa.Float(), nullable=False),
        sa.Column("last_transfusion_date", sa.Date(), nullable=True),
        sa.Column("expected_next_transfusion_date", sa.Date(), nullable=True),
        sa.Column(
            "bridge_status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column("consent_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["consent_id"], ["consent.consent_id"]),
        sa.PrimaryKeyConstraint("patient_id"),
    )
    op.create_index(op.f("ix_patient_city_id"), "patient", ["city_id"])
    op.create_index(
        "ix_patient_next_transfusion", "patient", ["expected_next_transfusion_date"]
    )

    op.create_table(
        "donor",
        sa.Column("donor_id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("blood_group", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("donor_type", sa.String(length=32), nullable=False),
        sa.Column("preferred_lang", sa.String(length=8), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column("last_donation_date", sa.Date(), nullable=True),
        sa.Column("next_eligible_date", sa.Date(), nullable=True),
        sa.Column(
            "eligibility_status",
            sa.String(length=16),
            server_default="eligible",
            nullable=False,
        ),
        sa.Column(
            "donations_till_date", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("total_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "calls_to_donations_ratio",
            sa.Float(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("consent_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["consent_id"], ["consent.consent_id"]),
        sa.PrimaryKeyConstraint("donor_id"),
    )
    op.create_index(op.f("ix_donor_city_id"), "donor", ["city_id"])
    op.create_index("ix_donor_eligibility_status", "donor", ["eligibility_status"])
    op.create_index("ix_donor_next_eligible_date", "donor", ["next_eligible_date"])
    # GIST spatial index for PostGIS proximity ranking.
    op.create_index(
        "ix_donor_geom", "donor", ["geom"], postgresql_using="gist"
    )

    op.create_table(
        "subscription",
        sa.Column("subscription_id", sa.String(length=64), nullable=False),
        sa.Column("patient_id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("cadence_days", sa.Float(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.patient_id"]),
        sa.PrimaryKeyConstraint("subscription_id"),
    )
    op.create_index(op.f("ix_subscription_city_id"), "subscription", ["city_id"])
    op.create_index(
        op.f("ix_subscription_patient_id"), "subscription", ["patient_id"]
    )

    op.create_table(
        "slot",
        sa.Column("slot_id", sa.String(length=64), nullable=False),
        sa.Column("subscription_id", sa.String(length=64), nullable=False),
        sa.Column("patient_id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("window_expected", sa.Date(), nullable=False),
        sa.Column("units_needed", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="planned", nullable=False
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscription.subscription_id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.patient_id"]),
        sa.PrimaryKeyConstraint("slot_id"),
    )
    op.create_index(op.f("ix_slot_city_id"), "slot", ["city_id"])
    op.create_index(op.f("ix_slot_patient_id"), "slot", ["patient_id"])
    op.create_index(op.f("ix_slot_subscription_id"), "slot", ["subscription_id"])
    op.create_index("ix_slot_status", "slot", ["status"])

    op.create_table(
        "transfusion_record",
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("patient_id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("units_given", sa.Integer(), server_default="1", nullable=False),
        sa.Column("fulfilled_by_slot_id", sa.String(length=64), nullable=True),
        sa.Column("recorded_by", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.patient_id"]),
        sa.ForeignKeyConstraint(["fulfilled_by_slot_id"], ["slot.slot_id"]),
        sa.PrimaryKeyConstraint("record_id"),
    )
    op.create_index(
        op.f("ix_transfusion_record_city_id"), "transfusion_record", ["city_id"]
    )
    op.create_index(
        op.f("ix_transfusion_record_patient_id"), "transfusion_record", ["patient_id"]
    )

    op.create_table(
        "slot_assignment",
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("slot_id", sa.String(length=64), nullable=False),
        sa.Column("donor_id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("rank", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("offered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["slot_id"], ["slot.slot_id"]),
        sa.ForeignKeyConstraint(["donor_id"], ["donor.donor_id"]),
        sa.PrimaryKeyConstraint("assignment_id"),
    )
    op.create_index(op.f("ix_slot_assignment_city_id"), "slot_assignment", ["city_id"])
    op.create_index(op.f("ix_slot_assignment_donor_id"), "slot_assignment", ["donor_id"])
    op.create_index(op.f("ix_slot_assignment_slot_id"), "slot_assignment", ["slot_id"])
    op.create_index("ix_slot_assignment_status", "slot_assignment", ["status"])

    op.create_table(
        "notification",
        sa.Column("notification_id", sa.String(length=64), nullable=False),
        sa.Column("donor_id", sa.String(length=64), nullable=False),
        sa.Column("slot_id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="queued", nullable=False
        ),
        sa.Column("response", sa.String(length=16), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["donor_id"], ["donor.donor_id"]),
        sa.ForeignKeyConstraint(["slot_id"], ["slot.slot_id"]),
        sa.PrimaryKeyConstraint("notification_id"),
    )
    op.create_index(op.f("ix_notification_city_id"), "notification", ["city_id"])
    op.create_index(op.f("ix_notification_donor_id"), "notification", ["donor_id"])
    op.create_index(op.f("ix_notification_slot_id"), "notification", ["slot_id"])

    op.create_table(
        "reliability_score",
        sa.Column("donor_id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("acceptance_ratio", sa.Float(), server_default="0", nullable=False),
        sa.Column("recency_factor", sa.Float(), server_default="0", nullable=False),
        sa.Column("volume_factor", sa.Float(), server_default="0", nullable=False),
        sa.Column("call_efficiency", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["donor_id"], ["donor.donor_id"]),
        sa.PrimaryKeyConstraint("donor_id"),
    )
    op.create_index(
        op.f("ix_reliability_score_city_id"), "reliability_score", ["city_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reliability_score_city_id"), table_name="reliability_score")
    op.drop_table("reliability_score")

    op.drop_index(op.f("ix_notification_slot_id"), table_name="notification")
    op.drop_index(op.f("ix_notification_donor_id"), table_name="notification")
    op.drop_index(op.f("ix_notification_city_id"), table_name="notification")
    op.drop_table("notification")

    op.drop_index("ix_slot_assignment_status", table_name="slot_assignment")
    op.drop_index(op.f("ix_slot_assignment_slot_id"), table_name="slot_assignment")
    op.drop_index(op.f("ix_slot_assignment_donor_id"), table_name="slot_assignment")
    op.drop_index(op.f("ix_slot_assignment_city_id"), table_name="slot_assignment")
    op.drop_table("slot_assignment")

    op.drop_index(
        op.f("ix_transfusion_record_patient_id"), table_name="transfusion_record"
    )
    op.drop_index(
        op.f("ix_transfusion_record_city_id"), table_name="transfusion_record"
    )
    op.drop_table("transfusion_record")

    op.drop_index("ix_slot_status", table_name="slot")
    op.drop_index(op.f("ix_slot_subscription_id"), table_name="slot")
    op.drop_index(op.f("ix_slot_patient_id"), table_name="slot")
    op.drop_index(op.f("ix_slot_city_id"), table_name="slot")
    op.drop_table("slot")

    op.drop_index(op.f("ix_subscription_patient_id"), table_name="subscription")
    op.drop_index(op.f("ix_subscription_city_id"), table_name="subscription")
    op.drop_table("subscription")

    op.drop_index("ix_donor_geom", table_name="donor", postgresql_using="gist")
    op.drop_index("ix_donor_next_eligible_date", table_name="donor")
    op.drop_index("ix_donor_eligibility_status", table_name="donor")
    op.drop_index(op.f("ix_donor_city_id"), table_name="donor")
    op.drop_table("donor")

    op.drop_index("ix_patient_next_transfusion", table_name="patient")
    op.drop_index(op.f("ix_patient_city_id"), table_name="patient")
    op.drop_table("patient")

    op.drop_index(op.f("ix_audit_log_timestamp"), table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index(op.f("ix_contact_point_subject_id"), table_name="contact_point")
    op.drop_table("contact_point")

    op.drop_index("ix_consent_subject", table_name="consent")
    op.drop_table("consent")
