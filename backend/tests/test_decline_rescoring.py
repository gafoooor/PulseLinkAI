"""Tests for decline-driven re-scoring (Task 12.2, Requirements 6.6, 3.5).

Covers step 2 of the design's decline -> auto-promote -> re-score flow
(section 4.5 / Flow 3): when a donor declines, a ``donor.responded`` event is
published on the in-memory :class:`~pulselink.common.event_bus.InMemoryEventBus`
and a subscribed :class:`~pulselink.subscription.promote.ReliabilityRescorer`
consumes it to recompute the decliner's score + tier
(:func:`pulselink.reliability.scoring.on_donor_response`). Fully offline using
the in-memory slot/stats repos and event bus (no DB, no telephony):

* a decline recomputes the decliner's reliability with score ``<=`` prior
  (Requirements 3.5 / 6.6);
* publishing the ``donor.responded`` event triggers the reliability handler;
* a re-decline does not double-publish / double-count the offer history;
* without an event bus wired, the decline still works and reports ``rescored``
  ``False`` (task 12.1 backward compatibility);
* the recomputed score + tier are persisted through the stats repo.
"""

from __future__ import annotations

from datetime import date

import pytest

from pulselink.common.enums import AssignmentStatus, SlotResponse, SlotStatus
from pulselink.common.event_bus import InMemoryEventBus
from pulselink.common.models import Slot, SlotAssignment, Window
from pulselink.reliability.scoring import (
    DonorStats,
    build_reliability_score,
    on_donor_response,
)
from pulselink.subscription.promote import (
    DONOR_RESPONDED_EVENT,
    InMemoryDonorStatsRepo,
    InMemorySlotRepo,
    PromoteDeps,
    ReliabilityRescorer,
    handle_decline,
    wire_reliability,
)

TODAY = date(2024, 6, 1)

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


def _deps(
    slot: Slot,
    *,
    stats: dict[str, DonorStats] | None = None,
) -> tuple[PromoteDeps, InMemoryDonorStatsRepo, InMemoryEventBus]:
    """Wire a fully offline decline -> re-score harness.

    Returns the :class:`PromoteDeps`, the stats repo (to assert persisted state),
    and the event bus (to assert publish behavior).
    """
    bus = InMemoryEventBus()
    stats_repo = InMemoryDonorStatsRepo(stats)
    rescorer = wire_reliability(bus, stats_repo, today_provider=lambda: TODAY)
    deps = PromoteDeps(
        repo=InMemorySlotRepo([slot]),
        event_bus=bus,
        rescorer=rescorer,
    )
    return deps, stats_repo, bus


# --------------------------------------------------------------------------- #
# Requirement 6.6 / 3.5: a decline recomputes the decliner's reliability
# --------------------------------------------------------------------------- #
def test_decline_recomputes_reliability_score_not_above_prior() -> None:
    """A decline recomputes the decliner with score <= prior (Req 3.5 / 6.6)."""
    # A donor with a strong acceptance history so the prior score is well above
    # the floor and a single decline measurably constrains the ratio.
    prior_stats = DonorStats(
        accepted=9,
        offered=10,
        calls_to_donations_ratio=1.0,
        donations_till_date=8,
        last_donation_date=date(2024, 5, 1),
    )
    prior_score = build_reliability_score("d0", prior_stats, TODAY).score

    slot = _slot(_assignment("d0", 0), _assignment("d1", 1))
    deps, stats_repo, _bus = _deps(slot, stats={"d0": prior_stats})

    result = handle_decline("slot-1", "d0", deps)

    assert result.rescored is True
    assert result.new_reliability is not None
    # Requirement 3.5 / 6.6: a DECLINED response never raises the score.
    assert result.new_reliability.score <= prior_score
    # The recompute is persisted for later reads.
    assert stats_repo.get_score("d0") is not None
    assert stats_repo.get_score("d0").score == result.new_reliability.score


def test_decline_recomputes_tier() -> None:
    """The decliner's tier is recomputed and persisted (Requirement 6.6)."""
    prior_stats = DonorStats(accepted=9, offered=10, donations_till_date=8)
    slot = _slot(_assignment("d0", 0))
    deps, stats_repo, _bus = _deps(slot, stats={"d0": prior_stats})

    result = handle_decline("slot-1", "d0", deps)

    assert result.new_reliability is not None
    persisted = stats_repo.get_score("d0")
    assert persisted is not None
    assert persisted.tier == result.new_reliability.tier


def test_decline_persists_updated_stats() -> None:
    """The decline applies the response to the stored stats (offered += 1)."""
    prior_stats = DonorStats(accepted=4, offered=8)
    slot = _slot(_assignment("d0", 0))
    deps, stats_repo, _bus = _deps(slot, stats={"d0": prior_stats})

    handle_decline("slot-1", "d0", deps)

    updated = stats_repo.get_stats("d0")
    # A DECLINED response records an offer but no acceptance.
    assert updated.offered == 9
    assert updated.accepted == 4


def test_decline_for_donor_with_no_history_uses_neutral_prior() -> None:
    """A decliner with no stored stats is still recomputed (neutral prior)."""
    slot = _slot(_assignment("new-donor", 0))
    deps, stats_repo, _bus = _deps(slot)  # no seeded stats

    result = handle_decline("slot-1", "new-donor", deps)

    assert result.rescored is True
    assert result.new_reliability is not None
    # offered goes 0 -> 1, accepted stays 0 -> acceptance ratio 0 <= prior 0.5.
    assert stats_repo.get_stats("new-donor").offered == 1
    assert stats_repo.get_stats("new-donor").accepted == 0


# --------------------------------------------------------------------------- #
# Event publish/consume wiring
# --------------------------------------------------------------------------- #
def test_decline_publishes_donor_responded_event() -> None:
    """A decline publishes a single donor.responded event with a DECLINED payload."""
    received: list[dict] = []
    bus = InMemoryEventBus()
    bus.subscribe(DONOR_RESPONDED_EVENT, received.append)

    stats_repo = InMemoryDonorStatsRepo()
    rescorer = wire_reliability(bus, stats_repo, today_provider=lambda: TODAY)
    deps = PromoteDeps(
        repo=InMemorySlotRepo([_slot(_assignment("d0", 0))]),
        event_bus=bus,
        rescorer=rescorer,
    )

    handle_decline("slot-1", "d0", deps)

    assert len(received) == 1
    assert received[0]["donorId"] == "d0"
    assert received[0]["response"] == SlotResponse.DECLINED.value
    assert received[0]["slotId"] == "slot-1"


def test_published_event_triggers_reliability_handler() -> None:
    """The subscribed ReliabilityRescorer consumes the event and recomputes."""
    slot = _slot(_assignment("d0", 0))
    deps, _stats_repo, _bus = _deps(slot, stats={"d0": DonorStats(accepted=3, offered=4)})

    handle_decline("slot-1", "d0", deps)

    # The handler recorded a recompute for the decliner.
    assert deps.rescorer is not None
    recompute = deps.rescorer.last_recompute.get("d0")
    assert recompute is not None
    assert recompute.response is SlotResponse.DECLINED
    assert recompute.score.score <= recompute.previous_score.score


def test_rescorer_handler_directly_recomputes() -> None:
    """The handler, invoked directly with an event payload, recomputes correctly."""
    stats_repo = InMemoryDonorStatsRepo({"d0": DonorStats(accepted=5, offered=5)})
    rescorer = ReliabilityRescorer(stats_repo, today_provider=lambda: TODAY)

    recompute = rescorer.on_event(
        {"donorId": "d0", "response": SlotResponse.DECLINED.value}
    )

    # Matches a direct on_donor_response call on the same baseline.
    expected = on_donor_response(
        "d0", DonorStats(accepted=5, offered=5), SlotResponse.DECLINED, TODAY
    )
    assert recompute.score.score == expected.score.score
    assert recompute.score.score <= recompute.previous_score.score


# --------------------------------------------------------------------------- #
# Idempotency: a re-decline does not double-publish / double-count
# --------------------------------------------------------------------------- #
def test_redecline_does_not_double_publish() -> None:
    """Re-declining the same assignment publishes the event exactly once."""
    received: list[dict] = []
    bus = InMemoryEventBus()
    bus.subscribe(DONOR_RESPONDED_EVENT, received.append)

    stats_repo = InMemoryDonorStatsRepo({"d0": DonorStats(accepted=3, offered=5)})
    rescorer = wire_reliability(bus, stats_repo, today_provider=lambda: TODAY)
    deps = PromoteDeps(
        repo=InMemorySlotRepo([_slot(_assignment("d0", 0), _assignment("d1", 1))]),
        event_bus=bus,
        rescorer=rescorer,
    )

    handle_decline("slot-1", "d0", deps)
    second = handle_decline("slot-1", "d0", deps)

    # Exactly one event despite two decline calls.
    assert len(received) == 1
    # The redelivered decline is a no-op that does not re-score.
    assert second.rescored is False


def test_redecline_does_not_double_count_offer_history() -> None:
    """A re-decline must not increment the decliner's offered count twice."""
    slot = _slot(_assignment("d0", 0))
    deps, stats_repo, _bus = _deps(slot, stats={"d0": DonorStats(accepted=2, offered=4)})

    handle_decline("slot-1", "d0", deps)
    handle_decline("slot-1", "d0", deps)

    # offered counted exactly once (4 -> 5), not twice (would be 6).
    assert stats_repo.get_stats("d0").offered == 5
    assert stats_repo.get_stats("d0").accepted == 2


# --------------------------------------------------------------------------- #
# Backward compatibility (task 12.1 call sites without a bus)
# --------------------------------------------------------------------------- #
def test_decline_without_event_bus_still_marks_declined() -> None:
    """With no event bus wired the decline still marks declined, rescored=False."""
    slot = _slot(_assignment("d0", 0))
    deps = PromoteDeps(repo=InMemorySlotRepo([slot]))  # no bus / rescorer

    result = handle_decline("slot-1", "d0", deps)

    assert result.declined_assignment is not None
    assert result.declined_assignment.status is AssignmentStatus.DECLINED
    assert result.rescored is False
    assert result.new_reliability is None


def test_decline_with_bus_but_no_rescorer_reference_reports_rescored() -> None:
    """A bus without a rescorer reference still publishes; rescored is True."""
    bus = InMemoryEventBus()
    # External consumer (the score is recomputed elsewhere); deps holds no
    # rescorer reference, so new_reliability is not read back.
    stats_repo = InMemoryDonorStatsRepo()
    ReliabilityRescorer(stats_repo, today_provider=lambda: TODAY).subscribe(bus)
    deps = PromoteDeps(repo=InMemorySlotRepo([_slot(_assignment("d0", 0))]), event_bus=bus)

    result = handle_decline("slot-1", "d0", deps)

    assert result.rescored is True
    assert result.new_reliability is None
    # The external rescorer still recomputed and persisted a score.
    assert stats_repo.get_score("d0") is not None


# --------------------------------------------------------------------------- #
# Error handling carried over from 12.1
# --------------------------------------------------------------------------- #
def test_unknown_donor_raises() -> None:
    slot = _slot(_assignment("d0", 0))
    deps, _stats_repo, _bus = _deps(slot)
    with pytest.raises(KeyError):
        handle_decline("slot-1", "not-on-slot", deps)
