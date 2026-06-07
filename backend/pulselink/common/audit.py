"""Append-only audit logging seam (Task 13.4).

PulseLink records an audit entry on every **parse confirmation**, **data
share**, and **notification** so data handling is traceable and accountable
(Requirements 11.1, 11.2). This module is the decoupled API the other services
call; it deliberately edits no other service's files. The parser-confirm flow
(Task 3.x), the messaging flow (Task 9.3), and the voice flow (Task 10.x) wire
these helpers in from their own tasks — this module only provides the seam.

Design decisions encoded here:

* **Append-only at the API level.** The audit sink Protocol exposes only
  ``record`` (write) and ``entries`` (read). There is deliberately *no*
  ``update`` / ``delete`` / ``edit`` method anywhere in this module, so the
  append-only guarantee (Requirement 11.2) is enforced by the shape of the API
  itself, not just by table conventions. The DB-backed implementation issues
  ``INSERT`` only.
* **Service-set timestamps.** The timestamp is stamped by the service at
  ``record`` time (UTC now); callers cannot pass or backdate a timestamp. This
  keeps the log an honest chronological record (Requirement 11.1).
* **System of record, separate from operational data.** Entries are written to
  the append-only ``audit_log`` table, which lives apart from the
  operational/matching tables (Requirement 11.2). In production CloudWatch
  provides operational observability, but this append-only log remains the
  system of record.
* **No PII / no plaintext contact values.** The ``detail`` field is for
  non-PII context only — ids and action metadata such as a channel name. A raw
  phone / WhatsApp value (or any ``ContactPoint`` plaintext) MUST NEVER be
  written to ``actor``, ``action``, ``detail``, or any other audit field
  (Requirement 7.4). The convenience helpers below take ids and metadata only,
  never a contact value, to make that easy to honour at call sites.

Requirements: 11.1, 11.2 (privacy cross-check: 7.4)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from pulselink.common.db_models import AuditLog as AuditLogORM

# --------------------------------------------------------------------------- #
# Canonical action strings
# --------------------------------------------------------------------------- #
# The three required auditable events (Requirement 11.1) use these fixed action
# strings so the log is queryable with stable, consistent identifiers. Helpers
# below set them; callers never have to remember the exact spelling.
ACTION_PARSE_CONFIRMED = "parse.confirmed"
ACTION_DATA_SHARED = "data.shared"
ACTION_NOTIFICATION_SENT = "notification.sent"


# --------------------------------------------------------------------------- #
# The audit entry value object
# --------------------------------------------------------------------------- #
class AuditEntry(BaseModel):
    """An immutable record of one auditable action.

    Carries the actor, the action, and the service-set ``timestamp`` required
    by Requirement 11.1, plus optional non-PII context. ``model_config``
    freezes the instance so a recorded entry cannot be mutated after the fact,
    mirroring the append-only guarantee at the value-object level.
    """

    model_config = {"frozen": True}

    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    timestamp: datetime
    subject_id: Optional[str] = None
    subject_type: Optional[str] = None
    detail: Optional[str] = None


# --------------------------------------------------------------------------- #
# The append-only audit sink seam
# --------------------------------------------------------------------------- #
@runtime_checkable
class AuditSink(Protocol):
    """Append-only audit seam (Requirements 11.1, 11.2).

    Implementations expose exactly two capabilities: append one entry
    (``record``) and read the entries back (``entries``). There is no update or
    delete path — that omission is the append-only guarantee.
    """

    def record(
        self,
        actor: str,
        action: str,
        *,
        subject_id: Optional[str] = None,
        subject_type: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> AuditEntry:
        """Append an audit entry, stamping the timestamp with UTC now."""
        ...

    def entries(self) -> list[AuditEntry]:
        """Return the recorded entries in chronological (append) order."""
        ...


def _new_entry(
    actor: str,
    action: str,
    subject_id: Optional[str],
    subject_type: Optional[str],
    detail: Optional[str],
) -> AuditEntry:
    """Build an :class:`AuditEntry` with a service-set UTC timestamp.

    Centralised so every implementation stamps the timestamp identically and
    callers can never supply one.
    """

    return AuditEntry(
        actor=actor,
        action=action,
        timestamp=datetime.now(timezone.utc),
        subject_id=subject_id,
        subject_type=subject_type,
        detail=detail,
    )


# --------------------------------------------------------------------------- #
# In-memory implementation (offline tests / demo)
# --------------------------------------------------------------------------- #
class InMemoryAuditLog:
    """Offline ``AuditSink`` that appends entries to an in-process list.

    Used by tests and the offline demo. Like the DB-backed sink it exposes no
    mutation or delete path: :meth:`record` appends, :meth:`entries` reads, and
    there is nothing else. The returned list is always a copy so callers cannot
    reach in and rewrite history.
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(
        self,
        actor: str,
        action: str,
        *,
        subject_id: Optional[str] = None,
        subject_type: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> AuditEntry:
        """Append an entry (UTC-now timestamp) and return it."""
        entry = _new_entry(actor, action, subject_id, subject_type, detail)
        self._entries.append(entry)
        return entry

    def entries(self) -> list[AuditEntry]:
        """A copy of the recorded entries in append order (oldest first)."""
        return list(self._entries)


# --------------------------------------------------------------------------- #
# DB-backed implementation (system of record)
# --------------------------------------------------------------------------- #
class DbAuditLog:
    """``AuditSink`` that inserts append-only rows into the ``audit_log`` table.

    INSERT only: there is no method here that updates or deletes an audit row,
    enforcing the append-only guarantee (Requirement 11.2) at the API level.
    The append-only ``audit_log`` table lives apart from the operational data,
    keeping it the system of record.

    A SQLAlchemy ``Session`` is supplied by the caller (matching the pattern
    used by :func:`pulselink.ingest.importer.import_dataset`). By default
    :meth:`record` flushes the insert so the row's autoincrement ``audit_id`` is
    populated; transaction commit remains the caller's responsibility.
    """

    def __init__(self, session, *, flush: bool = True) -> None:
        if session is None:
            raise ValueError("DbAuditLog requires a SQLAlchemy session")
        self._session = session
        self._flush = flush

    def record(
        self,
        actor: str,
        action: str,
        *,
        subject_id: Optional[str] = None,
        subject_type: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> AuditEntry:
        """Insert one append-only audit row and return the recorded entry."""
        entry = _new_entry(actor, action, subject_id, subject_type, detail)
        self._session.add(
            AuditLogORM(
                actor=entry.actor,
                action=entry.action,
                timestamp=entry.timestamp,
                subject_id=entry.subject_id,
                subject_type=entry.subject_type,
                detail=entry.detail,
            )
        )
        if self._flush:
            self._session.flush()
        return entry

    def entries(self) -> list[AuditEntry]:
        """Read back all audit rows in chronological order (oldest first)."""
        rows = (
            self._session.query(AuditLogORM)
            .order_by(AuditLogORM.timestamp.asc(), AuditLogORM.audit_id.asc())
            .all()
        )
        return [
            AuditEntry(
                actor=row.actor,
                action=row.action,
                timestamp=row.timestamp,
                subject_id=row.subject_id,
                subject_type=row.subject_type,
                detail=row.detail,
            )
            for row in rows
        ]


# --------------------------------------------------------------------------- #
# Convenience helpers for the three required auditable events
# --------------------------------------------------------------------------- #
# Each helper sets the canonical ``action`` string consistently and takes ids /
# metadata only — never a contact value — so call sites stay PII-safe (Req 7.4).
def audit_parse_confirmed(
    sink: AuditSink,
    actor: str,
    subject_id: str,
    *,
    subject_type: Optional[str] = None,
    detail: Optional[str] = None,
) -> AuditEntry:
    """Record that a coordinator confirmed a parsed record (Requirement 11.1).

    ``subject_id`` is the confirmed patient/donor id; ``detail`` is optional
    non-PII context (e.g. ``"patient"`` / a model version). No contact value is
    ever passed here.
    """
    return sink.record(
        actor,
        ACTION_PARSE_CONFIRMED,
        subject_id=subject_id,
        subject_type=subject_type,
        detail=detail,
    )


def audit_data_shared(
    sink: AuditSink,
    actor: str,
    subject_id: str,
    *,
    subject_type: Optional[str] = None,
    detail: Optional[str] = None,
) -> AuditEntry:
    """Record that PulseLink shared a subject's data (Requirement 11.1).

    ``detail`` carries only non-PII context such as the sharing scope or
    recipient role — never a contact value.
    """
    return sink.record(
        actor,
        ACTION_DATA_SHARED,
        subject_id=subject_id,
        subject_type=subject_type,
        detail=detail,
    )


def audit_notification_sent(
    sink: AuditSink,
    actor: str,
    donor_id: str,
    channel: str,
    *,
    detail: Optional[str] = None,
) -> AuditEntry:
    """Record that a notification was sent to a donor (Requirement 11.1).

    Takes the ``donor_id`` and the ``channel`` name (``sms`` / ``whatsapp`` /
    ``voice`` / ``mock``) only — the recipient's phone / WhatsApp value is a
    ``ContactPoint`` plaintext and MUST NEVER be written to the audit log
    (Requirement 7.4). When supplied, ``detail`` is prefixed with the channel
    so the log keeps consistent, queryable, PII-free context.
    """
    channel_detail = f"channel={channel}"
    if detail is not None:
        channel_detail = f"{channel_detail}; {detail}"
    return sink.record(
        actor,
        ACTION_NOTIFICATION_SENT,
        subject_id=donor_id,
        subject_type="donor",
        detail=channel_detail,
    )
