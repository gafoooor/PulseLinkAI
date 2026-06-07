"""Unit tests for backup auto-promotion (Task 12.3, Requirements 6.3/6.4/6.5).

Covers step 3 of the design's decline -> auto-promote -> re-score flow
(section 4.5 / Flow 3): when the primary donor declines,
:func:`pulselink.subscription.promote.handle_decline` promotes the
highest-ranked still-eligible backup to the active primary, leaves at most one
active primary, never re-offers the slot to the decliner, and instructs the
Messaging_Service to send the promoted backup a localized offer. Fully offline
using the in-memory :class:`InMemorySlotRepo` + :class:`RecordingMessaging`
seams (no DB, no telephony):

* declining the primary promotes the top eligible backup to active primary
  (Req 6.5) and reports it on the result;
* at most one assignment is an active primary afterwards (Req 6.3);
* the decliner is ``declined``, never becomes primary, and is never re-offered
  (Req 6.4);
* the messaging seam is called for the promoted backup with a localized offer
  (Req 6.5);
* when several backups exist the highest-ranked (lowest-rank) eligible one is
  chosen (Req 6.5);
* an ineligible / already-declined backup is skipped in favour of the next
  eligible one;
* with no eligible backup nothing is promoted (escalation is task 12.4).
"""

from __future__ import annotations

from datetime import date

from pulselink.common.enums import AssignmentStatus, SlotStatus
from pulselink.common.models import Slot, SlotAssignment, Window
from pulselink.subscription.generate import is_active_primary, is_single_active_primary
from pulselink.subscription.promote import (
    InMemorySlotRepo,
    PromoteDeps,
    RecordingMessaging,
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
def _assignment(
    donor_id: str, rank: int, status: AssignmentStatus = AssignmentStatus.ACTIVE
) -> SlotAssignment:
    return SlotAssignment(
        assignment_id=f"a-{donor_id}",
        slot_id="slot-1",
        donor_id=donor_id,
        rank=rank,
        status=status,
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


def _deps(slot: Slot) -> tuple[PromoteDeps, RecordingMessaging]:
    """Wire an offline promotion harness with a recording messaging seam."""
    messaging = RecordingMessaging()
    deps = PromoteDeps(repo=InMemorySlotRepo([slot]), messaging=messaging)
    return deps, messaging


def _of(slot: Slot, donor_id: str) -> SlotAssignment:
    return next(a for a in slot.assignments if a.donor_id == donor_id)


# --------------------------------------------------------------------------- #
# Req 6.5: the top eligible backup is promoted to active primary
# --------------------------------------------------------------------------- #
def test_decline_promotes_top_backup_to_active_primary() -> None:
    """Declining the primary promotes the rank-1 backup to active primary."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))
    deps, _messaging = _deps(slot)

    result = handle_decline("slot-1", "d0", deps)

    assert result.promoted is True
    assert result.promoted_donor_id == "d1"

    stored = deps.repo.get_slot("slot-1")
    promoted = _of(stored, "d1")
    assert promoted.status is AssignmentStatus.ACTIVE
    assert promoted.rank == 0
    assert is_active_primary(promoted)


def test_at_most_one_active_primary_after_promotion() -> None:
    """After a decline + promotion exactly one assignment is the active primary."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1), _assignment("d2", 2))
    deps, _messaging = _deps(slot)

    handle_decline("slot-1", "d0", deps)

    stored = deps.repo.get_slot("slot-1")
    active_primaries = [a for a in stored.assignments if is_active_primary(a)]
    assert len(active_primaries) == 1
    assert active_primaries[0].donor_id == "d1"
    assert is_single_active_primary(stored)


# --------------------------------------------------------------------------- #
# Req 6.4: the decliner is never primary and never re-offered
# --------------------------------------------------------------------------- #
def test_decliner_is_declined_and_never_primary() -> None:
    """The decliner stays declined and is never an active primary (Req 6.4)."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))
    deps, _messaging = _deps(slot)

    handle_decline("slot-1", "d0", deps)

    stored = deps.repo.get_slot("slot-1")
    decliner = _of(stored, "d0")
    assert decliner.status is AssignmentStatus.DECLINED
    assert decliner.rank != 0
    assert not is_active_primary(decliner)


def test_decliner_is_not_re_offered() -> None:
    """The Messaging_Service is never asked to offer the slot to the decliner."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))
    deps, messaging = _deps(slot)

    handle_decline("slot-1", "d0", deps)

    offered_donors = [offer.donor_id for offer in messaging.offers]
    assert "d0" not in offered_donors


# --------------------------------------------------------------------------- #
# Req 6.5: the promoted backup receives a localized offer
# --------------------------------------------------------------------------- #
def test_messaging_seam_called_for_promoted_backup_with_localized_offer() -> None:
    """The promoted backup is sent exactly one localized offer (Req 6.5)."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))
    deps, messaging = _deps(slot)

    handle_decline("slot-1", "d0", deps)

    assert len(messaging.offers) == 1
    offer = messaging.offers[0]
    assert offer.donor_id == "d1"
    assert offer.slot_id == "slot-1"
    # The offer is genuinely localized: a language plus rendered body + actions.
    assert offer.message.lang
    assert offer.message.text
    kinds = {action.kind for action in offer.message.actions}
    assert kinds == {"accept", "decline"}


def test_promotion_works_without_messaging_wired() -> None:
    """Promotion still occurs when no messaging seam is injected (optional)."""
    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))
    deps = PromoteDeps(repo=InMemorySlotRepo([slot]))  # no messaging

    result = handle_decline("slot-1", "d0", deps)

    assert result.promoted is True
    assert result.promoted_donor_id == "d1"
    assert is_active_primary(_of(deps.repo.get_slot("slot-1"), "d1"))


# --------------------------------------------------------------------------- #
# Req 6.5: highest-ranked eligible backup is chosen; ineligible ones skipped
# --------------------------------------------------------------------------- #
def test_highest_ranked_eligible_backup_is_chosen() -> None:
    """With multiple backups the lowest-rank (highest-priority) one is promoted."""
    slot = _slot(
        _assignment("d0", 0),
        _assignment("d1", 1),
        _assignment("d2", 2),
        _assignment("d3", 3),
    )
    deps, messaging = _deps(slot)

    result = handle_decline("slot-1", "d0", deps)

    assert result.promoted_donor_id == "d1"
    assert [offer.donor_id for offer in messaging.offers] == ["d1"]
    stored = deps.repo.get_slot("slot-1")
    assert _of(stored, "d1").rank == 0


def test_skips_ineligible_backup_and_promotes_next_eligible() -> None:
    """A declined/expired backup is skipped; the next eligible one is promoted."""
    slot = _slot(
        _assignment("d0", 0),
        _assignment("d1", 1, status=AssignmentStatus.DECLINED),
        _assignment("d2", 2, status=AssignmentStatus.EXPIRED),
        _assignment("d3", 3),
    )
    deps, messaging = _deps(slot)

    result = handle_decline("slot-1", "d0", deps)

    # d1 (declined) and d2 (expired) are skipped; d3 is the top eligible backup.
    assert result.promoted_donor_id == "d3"
    assert [offer.donor_id for offer in messaging.offers] == ["d3"]
    stored = deps.repo.get_slot("slot-1")
    assert _of(stored, "d3").rank == 0
    assert is_active_primary(_of(stored, "d3"))
    # The skipped backups keep their original statuses.
    assert _of(stored, "d1").status is AssignmentStatus.DECLINED
    assert _of(stored, "d2").status is AssignmentStatus.EXPIRED


def test_no_eligible_backup_does_not_promote() -> None:
    """With no eligible backup nothing is promoted (escalation is task 12.4)."""
    slot = _slot(
        _assignment("d0", 0),
        _assignment("d1", 1, status=AssignmentStatus.DECLINED),
    )
    deps, messaging = _deps(slot)

    result = handle_decline("slot-1", "d0", deps)

    assert result.promoted is False
    assert result.promoted_donor_id is None
    assert messaging.offers == []
