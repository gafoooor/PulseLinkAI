"""Decline -> auto-promote-backup -> re-score flow (design section 4.5).

This module is the **promotion layer** of the Subscription_Generator (design
Component 4 / Flow 3 — "the live demo"). It is built incrementally across tasks
12.1-12.4; this file currently implements **tasks 12.1, 12.2, and 12.3**: marking
the declining donor's :class:`SlotAssignment` as ``declined`` (Requirement 6.2),
re-scoring that donor by publishing a donor-response event that the
Reliability_Service consumes (Requirements 6.6, 3.5), and auto-promoting the
highest-ranked eligible backup to the active primary while instructing the
Messaging_Service to send that backup a localized offer (Requirements 6.3, 6.4,
6.5).

The remaining steps are deliberately left as clear seams/TODOs so later tasks
can build on this structure without reshaping it:

* **12.2 — decline-driven re-scoring.** *(implemented here)* On a decline the
  handler publishes a ``donor.responded`` event on the injected
  :class:`~pulselink.common.event_bus.EventBus` (Amazon EventBridge in prod; the
  in-memory bus for the demo). A subscribed :class:`ReliabilityRescorer` consumes
  it and recomputes the decliner's score + tier via
  :func:`pulselink.reliability.scoring.on_donor_response`, which guarantees the
  recomputed score is ``<=`` the pre-decline score (Requirement 3.5). The event
  is published **exactly once per decline**: an already-declined assignment is a
  no-op and never republishes, so a redelivered/duplicate decline cannot
  double-count the donor's offer history.
* **12.3 — backup auto-promotion.** *(implemented here)* Promote the
  highest-ranked eligible backup to primary (rank 0, ACTIVE), leave at most one
  active primary, never re-offer the slot to the decliner, and have the
  Messaging_Service send the backup a localized offer. Wires through
  ``PromoteDeps.messaging`` and extends :class:`PromotionResult`
  (``promoted=True``, ``promoted_donor_id=...``).
* **12.4 — coordinator escalation.** When no eligible backup exists, keep the
  slot unconfirmed and escalate to the coordinator (``escalated=True``).

**In-process (demo) vs. Step Functions (prod).** :func:`handle_decline` runs
synchronously in-process so the demo works offline with no DB or telephony. In
production this same handler is the core of an **AWS Step Functions** state
machine (decline -> promote -> re-score) whose steps invoke Lambda functions
(design section 4.5 note). To keep that seam clean and the logic testable
offline, persistence is an injected :class:`SlotRepo` Protocol with an in-memory
implementation, mirroring the pattern used by
:mod:`pulselink.forecasting.relearn`.

**Idempotency.** Declining an assignment that is already ``declined`` is a
no-op: the design's post-conditions require the operation to be idempotent on
repeated decline events for the same assignment (a real bus may deliver a
donor-response event more than once).

Requirements: 6.2, 6.3, 6.4, 6.5, 6.6, 3.5
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from pulselink.common.enums import AssignmentStatus, SlotResponse
from pulselink.common.event_bus import EventBus
from pulselink.common.models import ReliabilityScore, Slot, SlotAssignment, Window
from pulselink.messaging.channel import LocalizedMessage
from pulselink.messaging.templates import OfferContext, render_offer
from pulselink.reliability.scoring import (
    DonorStats,
    ResponseRecompute,
    on_donor_response,
)

# Event type published on a decline and consumed by the Reliability_Service.
# Matches the in-memory-bus convention used elsewhere in the codebase; maps to
# an Amazon EventBridge detail-type in the budget production profile.
DONOR_RESPONDED_EVENT = "donor.responded"


# --------------------------------------------------------------------------- #
# Persistence seam (injected; in-memory for the demo, RDS in prod)
# --------------------------------------------------------------------------- #
@runtime_checkable
class SlotRepo(Protocol):
    """Persistence seam for reading a slot and updating an assignment's status.

    The offline demo uses :class:`InMemorySlotRepo`; production maps this to the
    RDS ``slot`` / ``slot_assignment`` tables behind a Step Functions task. Only
    the two methods the decline handler needs are part of the contract; tasks
    12.3/12.4 will widen it (e.g. ``promote_rank``, ``set_slot_status``,
    ``get_donor``) as those steps are implemented.
    """

    def get_slot(self, slot_id: str) -> Slot:
        """Return the slot, including its assignments. Raises if unknown."""
        ...

    def set_assignment_status(
        self, slot_id: str, donor_id: str, status: AssignmentStatus
    ) -> SlotAssignment:
        """Set the ``(slot_id, donor_id)`` assignment's status and return it."""
        ...

    def set_assignment_rank(
        self, slot_id: str, donor_id: str, rank: int
    ) -> SlotAssignment:
        """Set the ``(slot_id, donor_id)`` assignment's rank and return it.

        Used by backup auto-promotion (task 12.3): the promoted backup takes the
        decliner's rank (0 = primary) and the decliner is demoted to the backup's
        vacated rank, so ranks stay distinct and exactly one assignment sits at
        rank 0. Mirrors the design's ``promote_rank`` step (section 4.5).
        """
        ...


class InMemorySlotRepo:
    """Synchronous in-memory :class:`SlotRepo` for tests and the offline demo.

    Holds :class:`Slot` objects keyed by ``slot_id``. ``set_assignment_status``
    mutates the matching assignment in place (Pydantic models are mutable by
    default) so the stored slot always reflects the latest assignment statuses.
    """

    def __init__(self, slots: Optional[list[Slot]] = None) -> None:
        self._slots: dict[str, Slot] = {s.slot_id: s for s in (slots or [])}

    def add_slot(self, slot: Slot) -> None:
        """Register (or replace) a slot in the repository."""
        self._slots[slot.slot_id] = slot

    def get_slot(self, slot_id: str) -> Slot:
        try:
            return self._slots[slot_id]
        except KeyError:
            raise KeyError(f"unknown slot_id: {slot_id!r}") from None

    def set_assignment_status(
        self, slot_id: str, donor_id: str, status: AssignmentStatus
    ) -> SlotAssignment:
        slot = self.get_slot(slot_id)
        assignment = _find_assignment(slot, donor_id)
        if assignment is None:
            raise KeyError(
                f"no assignment for donor {donor_id!r} on slot {slot_id!r}"
            )
        assignment.status = status
        return assignment

    def set_assignment_rank(
        self, slot_id: str, donor_id: str, rank: int
    ) -> SlotAssignment:
        slot = self.get_slot(slot_id)
        assignment = _find_assignment(slot, donor_id)
        if assignment is None:
            raise KeyError(
                f"no assignment for donor {donor_id!r} on slot {slot_id!r}"
            )
        assignment.rank = rank
        return assignment


# --------------------------------------------------------------------------- #
# Reliability re-scoring seam (Task 12.2, Requirements 6.6, 3.5)
# --------------------------------------------------------------------------- #
@runtime_checkable
class DonorStatsRepo(Protocol):
    """Persistence seam for a donor's reliability stats + latest score.

    The offline demo uses :class:`InMemoryDonorStatsRepo`; production maps this
    to the RDS donor/reliability tables. The Reliability_Service reads a donor's
    :class:`~pulselink.reliability.scoring.DonorStats`, recomputes after a
    response, and persists both the updated stats and the new
    :class:`~pulselink.common.models.ReliabilityScore`.
    """

    def get_stats(self, donor_id: str) -> DonorStats:
        """Return the donor's stats (a neutral default if none are stored)."""
        ...

    def set_stats(self, donor_id: str, stats: DonorStats) -> None:
        """Persist the donor's updated stats."""
        ...

    def get_score(self, donor_id: str) -> Optional[ReliabilityScore]:
        """Return the donor's latest persisted score, or ``None``."""
        ...

    def set_score(self, donor_id: str, score: ReliabilityScore) -> None:
        """Persist the donor's latest recomputed score."""
        ...


class InMemoryDonorStatsRepo:
    """Synchronous in-memory :class:`DonorStatsRepo` for tests and the demo.

    Stores a :class:`DonorStats` and the latest :class:`ReliabilityScore` per
    donor. Unknown donors return a neutral, empty ``DonorStats`` (the same
    no-history state the scorer treats with the 0.5 acceptance prior), so a
    decline for a never-before-seen donor is still well-defined.
    """

    def __init__(self, stats: Optional[dict[str, DonorStats]] = None) -> None:
        self._stats: dict[str, DonorStats] = dict(stats or {})
        self._scores: dict[str, ReliabilityScore] = {}

    def get_stats(self, donor_id: str) -> DonorStats:
        return self._stats.get(donor_id, DonorStats())

    def set_stats(self, donor_id: str, stats: DonorStats) -> None:
        self._stats[donor_id] = stats

    def get_score(self, donor_id: str) -> Optional[ReliabilityScore]:
        return self._scores.get(donor_id)

    def set_score(self, donor_id: str, score: ReliabilityScore) -> None:
        self._scores[donor_id] = score


class ReliabilityRescorer:
    """Reliability_Service handler that recomputes a donor on a response event.

    This is the consumer side of the event-driven re-learning described in the
    design's "event-driven re-learning" principle and Flow 3: it subscribes to
    the ``donor.responded`` event and, on each event, recomputes the donor's
    score + tier via :func:`pulselink.reliability.scoring.on_donor_response`
    (Requirement 6.6). Because that recompute is built on ``apply_response``
    (an offer is always recorded, only ``ACCEPTED`` bumps ``accepted``), a
    ``DECLINED`` response can never raise the score (Requirement 3.5).

    The handler persists the updated stats and score back through the injected
    :class:`DonorStatsRepo`, and records the most recent
    :class:`~pulselink.reliability.scoring.ResponseRecompute` per donor so the
    decline handler can read the freshly recomputed score for its result.

    ``today_provider`` defaults to :meth:`date.today` and is injectable so tests
    stay deterministic. The handler is idempotency-neutral: it recomputes once
    per event it receives, so publishing exactly once per decline (the decline
    handler's responsibility) is what prevents double-counting.
    """

    def __init__(
        self,
        stats_repo: DonorStatsRepo,
        today_provider: Optional[Callable[[], date]] = None,
    ) -> None:
        self._stats_repo = stats_repo
        self._today_provider = today_provider or date.today
        self.last_recompute: dict[str, ResponseRecompute] = {}

    def on_event(self, payload: dict[str, Any]) -> ResponseRecompute:
        """Consume a ``donor.responded`` event and recompute the donor.

        The payload carries ``donorId`` and ``response`` (a
        :class:`~pulselink.common.enums.SlotResponse` value). The donor's current
        stats are read, the response is applied + scored, and the updated stats
        and new score are persisted.
        """
        donor_id = payload["donorId"]
        response = SlotResponse(payload["response"])
        stats = self._stats_repo.get_stats(donor_id)

        recompute = on_donor_response(
            donor_id, stats, response, self._today_provider()
        )

        self._stats_repo.set_stats(donor_id, recompute.stats)
        self._stats_repo.set_score(donor_id, recompute.score)
        self.last_recompute[donor_id] = recompute
        return recompute

    def subscribe(self, bus: EventBus) -> None:
        """Subscribe this handler to ``donor.responded`` on ``bus``."""
        bus.subscribe(DONOR_RESPONDED_EVENT, self.on_event)


def wire_reliability(
    bus: EventBus,
    stats_repo: DonorStatsRepo,
    today_provider: Optional[Callable[[], date]] = None,
) -> ReliabilityRescorer:
    """Create a :class:`ReliabilityRescorer` and subscribe it to ``bus``.

    Convenience wiring for the decline -> re-score flow: returns the rescorer so
    it can be passed to :class:`PromoteDeps` (``event_bus=bus, rescorer=...``).
    """
    rescorer = ReliabilityRescorer(stats_repo, today_provider)
    rescorer.subscribe(bus)
    return rescorer


# --------------------------------------------------------------------------- #
# Messaging seam (Task 12.3 — Requirement 6.5)
# --------------------------------------------------------------------------- #
@runtime_checkable
class PromotionMessaging(Protocol):
    """Seam the promotion layer uses to offer a slot to a promoted backup.

    On a decline, once the highest-ranked eligible backup is promoted to
    primary, :func:`handle_decline` instructs the Messaging_Service to send that
    backup a **localized** slot offer (Requirement 6.5). This narrow Protocol is
    all the promotion layer needs from messaging; the full
    :class:`~pulselink.messaging.service.MessagingService` (with its consent gate
    and contact lookup) is the production implementation, while
    :class:`RecordingMessaging` serves the offline demo and tests.

    ``notify_slot`` renders/sends the offer for ``donor_id`` and returns the
    :class:`~pulselink.messaging.channel.LocalizedMessage` that was offered, so
    callers and tests can confirm a localized offer was produced.
    """

    def notify_slot(
        self, slot: Slot, donor_id: str, *, lang: Optional[str] = None
    ) -> LocalizedMessage:
        """Send ``donor_id`` a localized offer for ``slot`` and return it."""
        ...


@dataclass
class PromotionOffer:
    """An inspectable record of one localized offer the recorder "sent"."""

    slot_id: str
    donor_id: str
    message: LocalizedMessage


class RecordingMessaging:
    """Offline :class:`PromotionMessaging` that records localized offers.

    Renders the slot offer into a :class:`LocalizedMessage` via
    :func:`pulselink.messaging.templates.render_offer` (so the offer is genuinely
    localized — falling back to the configured default language when no ``lang``
    is given) and appends it to an in-memory log instead of contacting anyone.
    No network, no consent lookup, fully deterministic — the consent gate is
    exercised by the real :class:`MessagingService` and its own tests. The
    recorded log is inspectable via :attr:`offers` so tests can assert the
    promoted backup was offered the slot in their language.
    """

    def __init__(self) -> None:
        self._offers: list[PromotionOffer] = []

    def notify_slot(
        self, slot: Slot, donor_id: str, *, lang: Optional[str] = None
    ) -> LocalizedMessage:
        message = render_offer(
            OfferContext(
                bridge_id=slot.patient_id,
                window_start=slot.window.start,
                window_end=slot.window.end,
                units_needed=slot.units_needed,
            ),
            preferred_lang=lang,
        )
        self._offers.append(
            PromotionOffer(slot_id=slot.slot_id, donor_id=donor_id, message=message)
        )
        return message

    @property
    def offers(self) -> list[PromotionOffer]:
        """A copy of the recorded localized offers (most recent last)."""
        return list(self._offers)


# --------------------------------------------------------------------------- #
# Coordinator escalation seam (Task 12.4 — Requirement 6.7)
# --------------------------------------------------------------------------- #
@runtime_checkable
class EscalationSink(Protocol):
    """Seam the promotion layer uses to escalate a slot to the coordinator.

    When a donor declines and no eligible backup exists (Requirement 6.7), the
    slot is kept unconfirmed and escalated to the coordinator. In production
    this would push a notification to the coordinator dashboard / an SNS topic;
    in the offline demo :class:`RecordingEscalation` logs the escalation.
    """

    def escalate(self, slot: Slot, reason: str) -> None:
        """Escalate ``slot`` to the coordinator with a human-readable reason."""
        ...


@dataclass
class EscalationRecord:
    """An inspectable record of one escalation the recorder logged."""

    slot_id: str
    patient_id: str
    reason: str


class RecordingEscalation:
    """Offline :class:`EscalationSink` that records escalations in memory.

    The recorded log is inspectable via :attr:`escalations` so tests can assert
    a slot was escalated when no backup existed.
    """

    def __init__(self) -> None:
        self._escalations: list[EscalationRecord] = []

    def escalate(self, slot: Slot, reason: str) -> None:
        self._escalations.append(
            EscalationRecord(
                slot_id=slot.slot_id,
                patient_id=slot.patient_id,
                reason=reason,
            )
        )

    @property
    def escalations(self) -> list[EscalationRecord]:
        """A copy of the recorded escalations (most recent last)."""
        return list(self._escalations)


@dataclass
class PromoteDeps:
    """Injected dependencies for the decline -> promote -> re-score flow.

    ``repo`` (12.1) is always required. ``event_bus`` + ``rescorer`` (12.2) wire
    decline-driven re-scoring; both are optional so task 12.1's call sites keep
    working unchanged (when ``event_bus`` is ``None`` no event is published and
    the result is simply ``rescored=False``). The remaining collaborators are
    kept as documented seams (added in tasks 12.3-12.4) so :func:`handle_decline`
    can grow without changing its signature:

    * ``event_bus`` (12.2) — the pub/sub seam a decline publishes the
      ``donor.responded`` event on (in-memory for the demo, Amazon EventBridge in
      prod). A subscribed :class:`ReliabilityRescorer` consumes it to recompute
      the decliner's score (see :func:`wire_reliability`).
    * ``rescorer`` (12.2) — the reliability handler subscribed to the bus. It is
      referenced on ``deps`` only so :func:`handle_decline` can read back the
      freshly recomputed score for the :class:`PromotionResult`; the actual
      recompute happens through the bus subscription, not a direct call.
    * ``messaging`` (12.3/12.4) — send the promoted backup a localized offer, or
      escalate the slot to the coordinator when no backup is available.
    """

    repo: SlotRepo
    event_bus: Optional[EventBus] = None
    rescorer: Optional["ReliabilityRescorer"] = None
    messaging: Optional["PromotionMessaging"] = None
    escalation: Optional["EscalationSink"] = None


# --------------------------------------------------------------------------- #
# Result object (fields kept stable so 12.2-12.4 extend, not reshape)
# --------------------------------------------------------------------------- #
@dataclass
class PromotionResult:
    """Outcome of :func:`handle_decline`.

    Mirrors the design's ``PromotionResult`` and carries the updated state so
    callers (and tests) can inspect the result without re-reading the repo:

    * ``promoted`` — whether a backup was promoted to primary (task 12.3). ``True``
      when an eligible backup existed and was promoted to the active primary.
    * ``escalated`` — whether the slot was escalated to the coordinator (task
      12.4). ``True`` when no eligible backup exists and an ``EscalationSink``
      is wired.
    * ``promoted_donor_id`` — the backup promoted to primary (task 12.3); ``None``
      when no eligible backup existed or on an idempotent re-decline.
    * ``slot`` — the slot after the decline was applied.
    * ``declined_assignment`` — the assignment that was set to ``declined`` (the
      decliner's), so callers can confirm who declined.
    * ``rescored`` (12.2) — whether the decline triggered a reliability recompute
      of the declining donor (i.e. a ``donor.responded`` event was published and
      consumed). ``False`` on an idempotent re-decline or when no event bus is
      wired.
    * ``new_reliability`` (12.2) — the decliner's recomputed score+tier when a
      :class:`ReliabilityRescorer` is wired, else ``None``. Guaranteed ``<=`` the
      pre-decline score (Requirement 3.5).
    """

    promoted: bool
    escalated: bool
    promoted_donor_id: Optional[str] = None
    slot: Optional[Slot] = None
    declined_assignment: Optional[SlotAssignment] = None
    rescored: bool = False
    new_reliability: Optional[ReliabilityScore] = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _find_assignment(slot: Slot, donor_id: str) -> Optional[SlotAssignment]:
    """Return the slot's assignment for ``donor_id`` (``None`` if not present)."""
    return next((a for a in slot.assignments if a.donor_id == donor_id), None)


# Statuses that disqualify a backup from being promoted (Req 6.5). A backup that
# already declined or whose offer expired can never become the new primary.
INELIGIBLE_BACKUP_STATUSES = (AssignmentStatus.DECLINED, AssignmentStatus.EXPIRED)


def is_still_eligible(assignment: SlotAssignment, window: Window) -> bool:
    """True iff ``assignment`` is a backup still eligible to be promoted (Req 6.5).

    Consistent with design section 4.5: a backup must not already have declined
    or expired. ``window`` is accepted (and reserved) so this helper can grow to
    re-check blood-compatibility, consent, and ``next_eligible_date`` against the
    slot's :class:`~pulselink.common.models.Window` as those signals are wired in;
    today the structural status check is the authoritative gate.
    """
    return assignment.status not in INELIGIBLE_BACKUP_STATUSES


def _next_eligible_backup(
    slot: Slot, declining_donor_id: str
) -> Optional[SlotAssignment]:
    """Pick the highest-ranked eligible backup, never the decliner (Req 6.5/6.4).

    Considers every assignment that is **not** the declining donor and is still
    eligible (:func:`is_still_eligible`), then returns the one with the lowest
    ``rank`` (highest priority). Returns ``None`` when no eligible backup remains
    — the no-backup/escalation path handled by task 12.4.
    """
    candidates = [
        a
        for a in slot.assignments
        if a.donor_id != declining_donor_id and is_still_eligible(a, slot.window)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda a: a.rank)


# --------------------------------------------------------------------------- #
# Decline handling (Task 12.1, Requirement 6.2)
# --------------------------------------------------------------------------- #
def handle_decline(
    slot_id: str, declining_donor_id: str, deps: PromoteDeps
) -> PromotionResult:
    """Mark the decline and re-score the decliner (Requirements 6.2, 6.6, 3.5).

    Steps 1-3 of the design's decline flow (section 4.5):

    1. **Mark declined (12.1, Req 6.2).** Locate the declining donor's
       :class:`SlotAssignment` and set its status to
       :attr:`AssignmentStatus.DECLINED`.
    2. **Re-score (12.2, Req 6.6/3.5).** Publish a ``donor.responded`` event on
       ``deps.event_bus`` (when wired). A subscribed :class:`ReliabilityRescorer`
       consumes it and recomputes the decliner's score + tier via
       :func:`pulselink.reliability.scoring.on_donor_response`, which guarantees
       the recomputed score is ``<=`` the pre-decline score (Req 3.5). The
       recomputed score is surfaced on the result as ``new_reliability``.
    3. **Auto-promote backup (12.3, Req 6.3/6.4/6.5).** Promote the highest-ranked
       still-eligible backup to the active primary (rank 0), swapping the
       decliner down to the backup's vacated rank so **at most one** assignment
       is an active primary (Req 6.3). The decliner is ``declined`` and is never
       promoted or re-offered (Req 6.4). When ``deps.messaging`` is wired, the
       promoted backup is sent a localized slot offer (Req 6.5). When no eligible
       backup exists, ``promoted`` stays ``False`` and the slot is escalated to
       the coordinator (task 12.4, Req 6.7).

    Coordinator escalation (task 12.4, Req 6.7): when no eligible backup exists
    after a decline, the slot is kept unconfirmed and escalated to the
    coordinator through the injected ``EscalationSink``. The returned
    :class:`PromotionResult` carries ``escalated=True`` when this occurs.

    Idempotent: if the assignment is already ``declined`` the call is a no-op —
    it does **not** re-publish the donor-response event, so a redelivered decline
    cannot double-count the donor's offer history (``rescored=False``). Other
    assignments on the slot are never touched.

    Args:
        slot_id: The slot the donor is declining.
        declining_donor_id: The donor who declined.
        deps: Injected dependencies; ``deps.repo`` marks the decline and (when
            wired) ``deps.event_bus`` / ``deps.rescorer`` drive re-scoring.

    Returns:
        A :class:`PromotionResult` carrying the updated slot, the declining
        assignment, and (when a bus/rescorer are wired) the recomputed score.

    Raises:
        KeyError: if the slot is unknown or the donor has no assignment on it.
    """
    slot = deps.repo.get_slot(slot_id)
    assignment = _find_assignment(slot, declining_donor_id)
    if assignment is None:
        raise KeyError(
            f"no assignment for donor {declining_donor_id!r} on slot {slot_id!r}"
        )

    # Idempotency: an already-declined assignment is a no-op (the bus may
    # redeliver the same donor-response event). It must NOT republish the event,
    # otherwise the decliner's offer history would be double-counted. Per design
    # post-conditions.
    if assignment.status is AssignmentStatus.DECLINED:
        return PromotionResult(
            promoted=False,
            escalated=False,
            slot=slot,
            declined_assignment=assignment,
            rescored=False,
        )

    declined = deps.repo.set_assignment_status(
        slot_id, declining_donor_id, AssignmentStatus.DECLINED
    )

    # 12.2: publish the donor-response event exactly once per decline so the
    # Reliability_Service recomputes the decliner's score + tier (Req 6.6). The
    # in-memory bus is synchronous, so any subscribed ReliabilityRescorer has
    # already recomputed by the time publish() returns; read the result back for
    # the PromotionResult. on_donor_response guarantees new score <= prior (3.5).
    rescored = False
    new_reliability: Optional[ReliabilityScore] = None
    if deps.event_bus is not None:
        deps.event_bus.publish(
            DONOR_RESPONDED_EVENT,
            {
                "donorId": declining_donor_id,
                "response": SlotResponse.DECLINED.value,
                "slotId": slot_id,
            },
        )
        rescored = True
        if deps.rescorer is not None:
            recompute = deps.rescorer.last_recompute.get(declining_donor_id)
            if recompute is not None:
                new_reliability = recompute.score

    # 12.3: promote the highest-ranked eligible backup to primary (Req 6.5),
    # leaving at most one active primary (Req 6.3) and never re-offering the slot
    # to the decliner (Req 6.4). Re-read the slot so the candidate scan sees the
    # decliner's freshly-DECLINED status (so it is excluded by is_still_eligible).
    promoted = False
    promoted_donor_id: Optional[str] = None
    current = deps.repo.get_slot(slot_id)
    backup = _next_eligible_backup(current, declining_donor_id)
    if backup is not None:
        # Swap ranks: the backup takes the decliner's rank (0 = primary) and the
        # decliner is demoted to the backup's vacated rank, so ranks stay distinct
        # and exactly one assignment sits at rank 0. The decliner is DECLINED so it
        # is never an *active* primary and is never re-offered (Req 6.3 / 6.4).
        decliner_rank = declined.rank
        backup_rank = backup.rank
        deps.repo.set_assignment_status(
            slot_id, backup.donor_id, AssignmentStatus.ACTIVE
        )
        deps.repo.set_assignment_rank(slot_id, backup.donor_id, decliner_rank)
        deps.repo.set_assignment_rank(slot_id, declining_donor_id, backup_rank)
        promoted = True
        promoted_donor_id = backup.donor_id

        # Instruct the Messaging_Service to send the promoted backup a localized
        # offer (Req 6.5). Optional so 12.1/12.2 call sites without messaging wired
        # still promote; they simply skip the outbound offer.
        if deps.messaging is not None:
            deps.messaging.notify_slot(
                deps.repo.get_slot(slot_id), backup.donor_id
            )

    # 12.4: if no eligible backup exists, keep the slot unconfirmed and escalate
    # to the coordinator (Requirement 6.7). The slot stays in its current status
    # (not promoted to CONFIRMED) and the coordinator is notified so they can
    # manually intervene.
    escalated = False
    if backup is None and not promoted:
        if deps.escalation is not None:
            deps.escalation.escalate(
                deps.repo.get_slot(slot_id),
                reason=f"Donor {declining_donor_id} declined slot {slot_id} "
                       f"and no eligible backup exists.",
            )
            escalated = True

    return PromotionResult(
        promoted=promoted,
        escalated=escalated,
        promoted_donor_id=promoted_donor_id,
        slot=deps.repo.get_slot(slot_id),
        declined_assignment=declined,
        rescored=rescored,
        new_reliability=new_reliability,
    )
