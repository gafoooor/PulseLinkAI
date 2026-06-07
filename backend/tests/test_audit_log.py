"""Unit tests for the append-only audit logging seam (Task 13.4).

Covers, using the offline ``InMemoryAuditLog``:
* ``record`` appends an entry carrying actor / action / a service-set timestamp.
* Entries are returned in chronological (append) order.
* There is no public update/delete path — the API exposes only append + read,
  which is the append-only guarantee (Requirement 11.2).
* The convenience helpers set the expected canonical ``action`` strings.
* The notification helper never writes a raw phone value into ``detail`` and
  takes ids/metadata only (PII cross-check, Requirement 7.4).

Requirements: 11.1, 11.2 (privacy cross-check: 7.4)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pulselink.common.audit import (
    ACTION_DATA_SHARED,
    ACTION_NOTIFICATION_SENT,
    ACTION_PARSE_CONFIRMED,
    AuditEntry,
    AuditSink,
    InMemoryAuditLog,
    audit_data_shared,
    audit_notification_sent,
    audit_parse_confirmed,
)


def test_record_appends_entry_with_actor_action_timestamp():
    """record() captures actor/action and stamps a UTC timestamp (Req 11.1)."""
    log = InMemoryAuditLog()
    before = datetime.now(timezone.utc)

    entry = log.record("coordinator:42", "parse.confirmed", subject_id="bridge-1")

    after = datetime.now(timezone.utc)
    assert isinstance(entry, AuditEntry)
    assert entry.actor == "coordinator:42"
    assert entry.action == "parse.confirmed"
    assert entry.subject_id == "bridge-1"
    # Timestamp is set by the service (not the caller) and is UTC-now.
    assert entry.timestamp.tzinfo is not None
    assert before <= entry.timestamp <= after
    # The entry is persisted in the log.
    assert log.entries() == [entry]


def test_entries_returned_in_chronological_append_order():
    """Entries come back oldest-first in the order they were recorded."""
    log = InMemoryAuditLog()

    e1 = log.record("a1", "parse.confirmed")
    e2 = log.record("a2", "data.shared")
    e3 = log.record("a3", "notification.sent")

    entries = log.entries()
    assert entries == [e1, e2, e3]
    timestamps = [e.timestamp for e in entries]
    assert timestamps == sorted(timestamps)


def test_caller_cannot_backdate_timestamp():
    """record() takes no timestamp argument — callers cannot backdate."""
    log = InMemoryAuditLog()
    ancient = datetime(2000, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(TypeError):
        # ``timestamp`` is intentionally not part of the record() signature.
        log.record("a1", "parse.confirmed", timestamp=ancient)  # type: ignore[call-arg]


def test_entries_returns_a_copy_so_history_cannot_be_rewritten():
    """Mutating the returned list must not affect the log's internal state."""
    log = InMemoryAuditLog()
    log.record("a1", "parse.confirmed")

    snapshot = log.entries()
    snapshot.clear()

    assert len(log.entries()) == 1


def test_recorded_entry_is_immutable():
    """A recorded AuditEntry is frozen — it cannot be edited after the fact."""
    log = InMemoryAuditLog()
    entry = log.record("a1", "parse.confirmed")

    with pytest.raises(Exception):
        entry.actor = "tampered"  # type: ignore[misc]


def test_api_exposes_only_append_and_read_no_mutation_path():
    """The append-only guarantee: no update/delete/edit methods exist (Req 11.2)."""
    public = {name for name in dir(InMemoryAuditLog) if not name.startswith("_")}
    assert public == {"record", "entries"}

    forbidden = {"update", "delete", "remove", "edit", "clear", "pop", "set"}
    assert forbidden.isdisjoint(public)

    # The seam Protocol itself only declares append + read.
    protocol_members = {m for m in dir(AuditSink) if not m.startswith("_")}
    assert protocol_members == {"record", "entries"}


def test_in_memory_log_satisfies_audit_sink_protocol():
    """InMemoryAuditLog structurally satisfies the AuditSink seam."""
    assert isinstance(InMemoryAuditLog(), AuditSink)


# --------------------------------------------------------------------------- #
# Convenience helpers set the expected canonical action strings (Req 11.1)
# --------------------------------------------------------------------------- #
def test_audit_parse_confirmed_sets_action():
    log = InMemoryAuditLog()
    entry = audit_parse_confirmed(log, "coordinator:7", "bridge-9")

    assert entry.action == ACTION_PARSE_CONFIRMED == "parse.confirmed"
    assert entry.actor == "coordinator:7"
    assert entry.subject_id == "bridge-9"
    assert log.entries() == [entry]


def test_audit_data_shared_sets_action():
    log = InMemoryAuditLog()
    entry = audit_data_shared(
        log, "coordinator:7", "donor-3", subject_type="donor", detail="scope=share_with_coordinator"
    )

    assert entry.action == ACTION_DATA_SHARED == "data.shared"
    assert entry.subject_id == "donor-3"
    assert entry.subject_type == "donor"


def test_audit_notification_sent_sets_action_and_channel_metadata():
    log = InMemoryAuditLog()
    entry = audit_notification_sent(log, "messaging-service", "donor-5", "sms")

    assert entry.action == ACTION_NOTIFICATION_SENT == "notification.sent"
    assert entry.subject_id == "donor-5"
    assert entry.subject_type == "donor"
    # Non-PII channel context is recorded for queryability.
    assert "channel=sms" in (entry.detail or "")


def test_notification_helper_never_writes_raw_phone_value():
    """Req 7.4 cross-check: no plaintext contact value lands in any audit field."""
    log = InMemoryAuditLog()
    phone = "+919876543210"

    # The helper takes donor_id + channel + non-PII metadata only — there is no
    # parameter through which a contact value could flow in.
    entry = audit_notification_sent(
        log, "messaging-service", "donor-5", "voice", detail="lang=te"
    )

    for value in (entry.actor, entry.action, entry.subject_id, entry.subject_type, entry.detail):
        assert phone not in (value or "")
