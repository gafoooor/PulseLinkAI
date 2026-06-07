"""Unit tests for decline handling (Task 12.1, Requirement 6.2).

Covers the first step of the design's decline -> auto-promote -> re-score flow
(section 4.5): :func:`pulselink.subscription.promote.handle_decline` setting the
declining donor's :class:`SlotAssignment` to ``declined``. Fully offline using
the in-memory :class:`InMemorySlotRepo` seam (no DB, no telephony):

* declining sets the matching assignment to ``DECLINED`` (Requirement 6.2);
* the declining donor is the one recorded as declined (returned on the result);
* re-declining the same assignment is idempotent (no-op, still ``DECLINED``);
* other assignments on the slot are left untouched;
* promotion / escalation are NOT performed yet (deferred to 12.3 / 12.4);
* unknown slot / unknown donor raise ``KeyError``.
"""

from __future__ import annotations

from datetime import date

import pytest

from pulselink.common.enums import AssignmentStatus, SlotStatus
from pulselink.common.models import Slot, SlotAssignment, Window
from pulselink.subscription.promote import (
    InMemorySlotRepo,
    PromoteDeps,
    PromotionResult,
    handle_decline,
)

WINDOW = Window(
    start=date(2024, 1, 10),
    expected=date(2024, 1, 13),
    end=date(2024, 1, 16),
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _assignment(donor_id: str, rank: int) -> SlotAssignment:
    return SlotAssignment(
        assignment_id=f"a-{donor_id}",
        slot_id="slot-1",
        donor_id=donor_id,
        rank=rank,
        status=AssignmentStatus.ACTIVE,
    )


def _slot(*assignments: SlotAssignment) -> Slot:
    return Slot(
        slot_id="slot-1",
        subscription_id="sub-1",
        patient_id="p1",
        window=WINDOW,
        units_needed=2,
        status=SlotStatus.OFFERED,
        assignments=list(assignments),
    )


def _deps(slot: Slot) -> PromoteDeps:
    return PromoteDeps(repo=InMemorySlotRepo([slot]))


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_decline_sets_assignment_to_declined() -> None:
    """The declining donor's assignment is set to DECLINED (Requirement 6.2)."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))
    deps = _deps(slot)

    result = handle_decline("slot-1", "d0", deps)

    stored = deps.repo.get_slot("slot-1")
    declined = next(a for a in stored.assignments if a.donor_id == "d0")
    assert declined.status is AssignmentStatus.DECLINED


def test_declining_donor_is_recorded() -> None:
    """The result records exactly the donor who declined."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))

    result = handle_decline("slot-1", "d0", _deps(slot))

    assert isinstance(result, PromotionResult)
    assert result.declined_assignment is not None
    assert result.declined_assignment.donor_id == "d0"
    assert result.declined_assignment.status is AssignmentStatus.DECLINED


def test_other_assignments_unaffected() -> None:
    """Declining one donor never changes the other assignments on the slot."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1), _assignment("d2", 2))
    deps = _deps(slot)

    handle_decline("slot-1", "d0", deps)

    stored = deps.repo.get_slot("slot-1")
    untouched = {a.donor_id: a.status for a in stored.assignments if a.donor_id != "d0"}
    assert untouched == {"d1": AssignmentStatus.ACTIVE, "d2": AssignmentStatus.ACTIVE}


def test_redecline_is_idempotent() -> None:
    """Declining an already-declined assignment is a no-op (still DECLINED)."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))
    deps = _deps(slot)

    first = handle_decline("slot-1", "d0", deps)
    second = handle_decline("slot-1", "d0", deps)

    stored = deps.repo.get_slot("slot-1")
    declined = [a for a in stored.assignments if a.status is AssignmentStatus.DECLINED]
    # Exactly one declined assignment, unchanged across the repeated call.
    assert [a.donor_id for a in declined] == ["d0"]
    assert second.declined_assignment is not None
    assert second.declined_assignment.donor_id == "d0"
    assert second.declined_assignment.status is AssignmentStatus.DECLINED


def test_backup_is_promoted_when_one_exists() -> None:
    """With an eligible backup, declining the primary now promotes it (12.3)."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))

    result = handle_decline("slot-1", "d0", _deps(slot))

    # Promotion now happens (12.3); escalation is still deferred to 12.4.
    assert result.promoted is True
    assert result.promoted_donor_id == "d1"
    assert result.escalated is False


def test_unknown_slot_raises() -> None:
    slot = _slot(_assignment("d0", 0))
    with pytest.raises(KeyError):
        handle_decline("missing-slot", "d0", _deps(slot))


def test_unknown_donor_raises() -> None:
    slot = _slot(_assignment("d0", 0))
    with pytest.raises(KeyError):
        handle_decline("slot-1", "not-on-slot", _deps(slot))
