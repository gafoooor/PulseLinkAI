"""Subscription generation — slot planning + primary/backup matching (Task 8.1).

This is the **Subscription_Generator** of the design (Component 4 / Flow 2). It
turns a patient's transfusion cadence into a forward-looking recurring plan: a
:class:`~pulselink.common.models.Subscription` of
:class:`~pulselink.common.models.Slot` objects, each covering a predicted
transfusion :class:`~pulselink.common.models.Window`, with a **primary donor at
rank 0** plus **ranked backups at ascending ranks** drawn from the
Matching_Service.

It implements two acceptance criteria:

* **Requirement 4.4 — horizon coverage + primary/backup ranking.**
  :func:`generate` produces slots covering the requested ``horizon_days`` and,
  per slot, assigns one primary donor at rank 0 and ranked backups at ascending
  ranks ``1..n`` in the matcher's reliability order.
* **Requirement 4.5 — single active primary (structural).** Each slot is built
  with **at most one** rank-0 PRIMARY assignment (the top-ranked donor). The
  enforcement guard that keeps this invariant true through declines/promotions
  is task 8.2; :func:`generate` only establishes the initial structure so 8.2
  and ``regenerate_from_window`` (task 8.3) can build on it.

**In-process pipeline (demo) vs. Step Functions (prod).** :func:`generate` runs
the whole forecast → match → assign pipeline synchronously in-process so the
demo works offline with no DB. In production this same sequence maps to an AWS
Step Functions state machine invoking Lambda functions (design Component 4
note). To stay testable offline, every external capability is an injected seam:

* ``estimate_cadence_fn`` / ``predict_window_fn`` — the Forecasting Engine
  functions (defaulting to :mod:`pulselink.forecasting.engine`).
* ``rank_fn`` — the Matching_Service entry point (defaulting to
  :func:`pulselink.matching.match.rank_donors_for_slot`).

The caller passes the candidate donor list and the consent store straight
through to the matcher, exactly as the real wiring will.

Requirements: 4.4, 4.5
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import Callable, Iterable, Optional, Protocol

from pulselink.common.consent import ConsentStore
from pulselink.common.enums import AssignmentStatus, SlotStatus
from pulselink.common.models import (
    Patient,
    Slot,
    SlotAssignment,
    Subscription,
    Window,
)
from pulselink.forecasting.engine import (
    CadenceEstimate,
    WindowPrediction,
    estimate_cadence,
    predict_window,
)
from pulselink.matching.match import MatchCandidate, RankedDonor

# Default number of backup donors to assign per slot (rank 1..DEFAULT_BACKUPS).
# Fewer are assigned when the matcher returns fewer eligible donors; the primary
# (rank 0) is in addition to these backups.
DEFAULT_BACKUPS = 2


# --------------------------------------------------------------------------- #
# Injected seams (so generation is testable offline with no DB)
# --------------------------------------------------------------------------- #
class RankFn(Protocol):
    """The Matching_Service ranking seam used per slot (Requirements 4.1-4.3).

    Mirrors :func:`pulselink.matching.match.rank_donors_for_slot`'s keyword
    signature so the real matcher is the drop-in default and tests can inject a
    stub returning a fixed ranked list.
    """

    def __call__(
        self,
        *,
        patient_blood_group,
        window: Window,
        city_id: str,
        candidates: Iterable[MatchCandidate],
        consent_store: ConsentStore,
        patient_lat: Optional[float],
        patient_lng: Optional[float],
        today: date,
    ) -> list[RankedDonor]:
        ...


EstimateCadenceFn = Callable[[list[date], float], CadenceEstimate]
PredictWindowFn = Callable[[date, CadenceEstimate], WindowPrediction]


@dataclass(frozen=True)
class _SlotPlan:
    """An internal, donor-free slot skeleton: just its predicted window."""

    index: int
    window: Window


# --------------------------------------------------------------------------- #
# Window planning
# --------------------------------------------------------------------------- #
def _plan_windows(
    *,
    anchor: date,
    horizon_days: int,
    estimate: CadenceEstimate,
    first_prediction: WindowPrediction,
    today: Optional[date] = None,
) -> list[Window]:
    """Walk forward in cadence-day steps to cover ``horizon_days``.

    The first window comes straight from the Forecasting Engine
    (``first_prediction``); its half-width (``expected - start``) is reused for
    every subsequent slot so all windows share the cadence-derived shape. Slot
    ``k`` (1-indexed) is centred on ``anchor + k * cadence`` where ``cadence``
    is the rounded cadence estimate (at least 1 day).

    The number of slots covers the horizon: ``ceil(horizon_days / cadence)``,
    and always at least one slot, so a subscription is never empty and the plan
    extends across the whole requested horizon.

    When ``today`` is supplied and ``anchor`` is in the past, ``start_k`` is
    advanced so the first generated window lands on or after ``today``, ensuring
    reminders and alerts always reference future slots.
    """
    cadence = max(1, round(estimate.cadence_days))
    half_width = (first_prediction.window.expected - first_prediction.window.start).days

    n_slots = max(1, math.ceil(horizon_days / cadence))

    # If anchor is historical, jump to the first cycle that is >= today.
    start_k = 1
    if today is not None and anchor < today:
        days_gap = (today - anchor).days
        start_k = max(1, math.ceil(days_gap / cadence))

    windows: list[Window] = []
    for k in range(start_k, start_k + n_slots):
        if k == 1:
            # Reuse the engine's first prediction verbatim.
            windows.append(first_prediction.window)
            continue
        expected = anchor + timedelta(days=k * cadence)
        windows.append(
            Window(
                start=expected - timedelta(days=half_width),
                expected=expected,
                end=expected + timedelta(days=half_width),
            )
        )
    return windows


def _build_assignments(slot_id: str, ranked: list[RankedDonor]) -> list[SlotAssignment]:
    """Build slot assignments from a ranked donor list (Requirement 4.4).

    The top-ranked donor becomes the **primary at rank 0** with ACTIVE status;
    each remaining donor becomes a backup at the next ascending rank
    (``1..n``), preserving the matcher's reliability order. Backups are also
    ACTIVE but, being rank > 0, are not primary — so the slot carries **at most
    one** rank-0 primary (Requirement 4.5, structural). When ``ranked`` is empty
    the slot is left uncovered (no assignments) rather than crashing.
    """
    assignments: list[SlotAssignment] = []
    for rank, donor in enumerate(ranked):
        assignments.append(
            SlotAssignment(
                assignment_id=f"{slot_id}-r{rank}",
                slot_id=slot_id,
                donor_id=donor.donor_id,
                rank=rank,
                status=AssignmentStatus.ACTIVE,
            )
        )
    return assignments


# --------------------------------------------------------------------------- #
# The generator
# --------------------------------------------------------------------------- #
def generate(
    patient: Patient,
    horizon_days: int,
    *,
    candidates: Iterable[MatchCandidate],
    consent_store: ConsentStore,
    today: date,
    transfusion_dates: Optional[list[date]] = None,
    seed_cadence: Optional[float] = None,
    max_backups: int = DEFAULT_BACKUPS,
    patient_lat: Optional[float] = None,
    patient_lng: Optional[float] = None,
    subscription_id: Optional[str] = None,
    rank_fn: RankFn = None,  # type: ignore[assignment]
    estimate_cadence_fn: EstimateCadenceFn = estimate_cadence,
    predict_window_fn: PredictWindowFn = predict_window,
    now: Optional[datetime] = None,
) -> Subscription:
    """Generate a recurring :class:`Subscription` for ``patient`` (Req 4.4, 4.5).

    Pipeline (in-process for the demo; Step Functions + Lambda in prod):

    1. **Forecast.** Estimate cadence from the patient's transfusion history
       (``transfusion_dates``, seeded by ``seed_cadence`` / ``cadence_days``)
       and predict the first window from the last transfusion date. Windows are
       then walked forward in cadence-day steps to cover ``horizon_days``.
    2. **Match.** For each slot, call the injected Matching_Service
       (``rank_fn``) to rank eligible donors, passing the candidate list and
       consent store straight through.
    3. **Assign.** Build one primary assignment at rank 0 plus up to
       ``max_backups`` backups at ascending ranks per slot. A slot with no
       eligible donors is left uncovered (no assignments).

    Args:
        patient: The patient to plan for (supplies blood group, city, cadence,
            last transfusion date, and units required).
        horizon_days: How far ahead to generate slots (must be >= 1).
        candidates: Candidate donors (with stats) passed through to the matcher.
        consent_store: Consent gate passed through to the matcher.
        today: The "current" date used for forecasting/matching (offline-safe).
        transfusion_dates: Optional transfusion history for cadence estimation;
            falls back to ``[last_transfusion_date]`` (or empty) when omitted.
        seed_cadence: Seed cadence in days; defaults to ``patient.cadence_days``.
        max_backups: Desired backups per slot beyond the primary (default 2).
        patient_lat, patient_lng: Patient coordinates for the distance tie-break.
        subscription_id: Optional stable id; one is derived from the patient id
            when omitted.
        rank_fn: Matching seam; defaults to ``rank_donors_for_slot``.
        estimate_cadence_fn, predict_window_fn: Forecasting seams.
        now: Optional creation timestamp (defaults to UTC now).

    Returns:
        A :class:`Subscription` whose slots cover the horizon, each carrying a
        cadence-derived window and ranked primary/backup assignments.
    """
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")

    # Resolve the matching seam lazily so the default is the real matcher
    # without importing it at module import time creating a hard dependency in
    # callers that always inject their own.
    if rank_fn is None:
        from pulselink.matching.match import rank_donors_for_slot

        rank_fn = rank_donors_for_slot

    seed = seed_cadence if seed_cadence is not None else patient.cadence_days

    # 1. Forecast: cadence estimate + first window.
    if transfusion_dates is None:
        transfusion_dates = (
            [patient.last_transfusion_date]
            if patient.last_transfusion_date is not None
            else []
        )
    estimate = estimate_cadence_fn(list(transfusion_dates), seed)

    # Anchor the walk-forward at the last transfusion date when known, else at
    # ``today`` so a brand-new patient still gets a plan.
    anchor = patient.last_transfusion_date or today
    first_prediction = predict_window_fn(anchor, estimate)

    windows = _plan_windows(
        anchor=anchor,
        horizon_days=horizon_days,
        estimate=estimate,
        first_prediction=first_prediction,
        today=today,
    )

    # Materialize candidates once so the same list is reused for every slot.
    candidate_list = list(candidates)

    sub_id = subscription_id or f"sub-{patient.patient_id}"
    created = now or datetime.now(timezone.utc)

    # 2 + 3. Match + assign per slot.
    desired = max(0, max_backups) + 1  # primary + backups
    slots: list[Slot] = []
    for index, window in enumerate(windows):
        slot_id = f"{sub_id}-slot-{index}"
        ranked = rank_fn(
            patient_blood_group=patient.blood_group,
            window=window,
            city_id=patient.city_id,
            candidates=candidate_list,
            consent_store=consent_store,
            patient_lat=patient_lat,
            patient_lng=patient_lng,
            today=today,
        )
        assignments = _build_assignments(slot_id, ranked[:desired])
        slots.append(
            Slot(
                slot_id=slot_id,
                subscription_id=sub_id,
                patient_id=patient.patient_id,
                window=window,
                units_needed=patient.quantity_required,
                status=SlotStatus.PLANNED,
                assignments=assignments,
            )
        )

    return Subscription(
        subscription_id=sub_id,
        patient_id=patient.patient_id,
        cadence_days=estimate.cadence_days,
        horizon_days=horizon_days,
        slots=slots,
        created_at=created,
        updated_at=created,
    )


# --------------------------------------------------------------------------- #
# Single-active-primary invariant (Task 8.2 — Requirement 4.5)
# --------------------------------------------------------------------------- #
# Requirement 4.5: "WHILE a slot is not fulfilled, THE Subscription_Generator
# SHALL maintain at most one Slot_Assignment with primary status for that slot
# at any time."
#
# Chosen convention (documented so the invariant is well-defined):
#   * **Primary** means **rank 0**. Task 8.1 builds exactly one rank-0
#     assignment per slot and ranks backups at 1..n, so "primary" is structural.
#   * **Active primary** means a rank-0 assignment whose status is ACTIVE or
#     CONFIRMED — i.e. it is the donor currently committed (or being committed)
#     to the slot. ACTIVE is the freshly-offered primary; CONFIRMED is the
#     committed primary after the donor accepts.
#   * **Backups (rank > 0) are never primaries**, regardless of status. A backup
#     that is ACTIVE is merely an available stand-in, not the active primary.
#   * A primary whose status is DECLINED / PROMOTED / EXPIRED is no longer an
#     *active* primary (it has been superseded), so it does not count.
#
# The invariant only constrains a slot **while it is unfulfilled**: a FULFILLED
# slot is terminal and outside the invariant's scope.

PRIMARY_RANK = 0

# Statuses that make a rank-0 assignment the *active* primary.
ACTIVE_PRIMARY_STATUSES = (AssignmentStatus.ACTIVE, AssignmentStatus.CONFIRMED)


def is_active_primary(assignment: SlotAssignment) -> bool:
    """True iff ``assignment`` is the active primary (rank 0 + ACTIVE/CONFIRMED).

    Backups (rank > 0) are never primaries; a rank-0 assignment that has been
    declined, promoted away, or expired is no longer an *active* primary.
    """
    return (
        assignment.rank == PRIMARY_RANK
        and assignment.status in ACTIVE_PRIMARY_STATUSES
    )


def active_primary(slot: Slot) -> Optional[SlotAssignment]:
    """Return the slot's single active primary assignment, or ``None``.

    When the slot is well-formed there is at most one active primary and it is
    returned. If the slot is malformed with several active primaries (an
    invariant violation), the first one in assignment order is returned so
    callers always get a deterministic "current" primary; use
    :func:`is_single_active_primary` to detect the violation.
    """
    for assignment in slot.assignments:
        if is_active_primary(assignment):
            return assignment
    return None


def is_single_active_primary(slot: Slot) -> bool:
    """True iff the slot honors Requirement 4.5's single-active-primary rule.

    Returns ``True`` when **at most one** assignment is an active primary while
    the slot is unfulfilled. A FULFILLED slot is terminal and always satisfies
    the invariant vacuously. Backups (rank > 0) are not primaries, so any number
    of active backups is fine.
    """
    if slot.status is SlotStatus.FULFILLED:
        return True
    active_primaries = sum(1 for a in slot.assignments if is_active_primary(a))
    return active_primaries <= 1


def enforce_single_active_primary(slot: Slot) -> Slot:
    """Normalize ``slot`` so at most one assignment is the active primary (Req 4.5).

    Pure: returns a corrected **copy** of the slot and never mutates the input.

    When several assignments are active primaries (multiple ACTIVE/CONFIRMED at
    rank 0), the **first in assignment order is kept** as the sole primary and
    every other duplicate is **demoted to a backup** — re-ranked to the next
    available rank past the current maximum, preserving its order and status, so
    no donor is dropped. A slot that already satisfies the invariant (including a
    FULFILLED slot) is returned as an unchanged copy.
    """
    if is_single_active_primary(slot):
        return slot.model_copy(deep=True)

    # More than one active primary: keep the first, demote the rest to backups.
    kept_index: Optional[int] = None
    for index, assignment in enumerate(slot.assignments):
        if is_active_primary(assignment):
            kept_index = index
            break

    max_rank = max((a.rank for a in slot.assignments), default=PRIMARY_RANK)
    next_backup_rank = max_rank + 1

    new_assignments: list[SlotAssignment] = []
    for index, assignment in enumerate(slot.assignments):
        if index != kept_index and is_active_primary(assignment):
            # Demote this duplicate primary to a backup at a fresh rank; it stays
            # ACTIVE/CONFIRMED but, being rank > 0, is no longer a primary.
            new_assignments.append(
                assignment.model_copy(update={"rank": next_backup_rank})
            )
            next_backup_rank += 1
        else:
            new_assignments.append(assignment.model_copy(deep=True))

    return slot.model_copy(update={"assignments": new_assignments})


# --------------------------------------------------------------------------- #
# Regenerate from an updated forecast window (Task 8.3 — Requirement 4.4)
# --------------------------------------------------------------------------- #
# Requirement 4.4 — the Subscription_Generator refreshes a patient's plan from
# the latest forecast window. This is the ``regenerateFromWindow(patientId)``
# entry point of design Component 4.
#
# Wiring (design "Event-driven re-learning" + Flow 2):
#   1. A transfusion is recorded → the Forecasting Engine's re-learn handler
#      (:func:`pulselink.forecasting.relearn.record_transfusion_and_relearn`)
#      appends the record and recomputes the patient's cadence, persisting the
#      new ``cadence_days``.
#   2. That publishes a ``transfusion_recorded`` event on the bus (in-memory for
#      the demo, Amazon EventBridge in the budget production profile).
#   3. The Subscription_Generator consumes it and calls
#      :func:`regenerate_from_window` with the **updated** patient (current
#      ``cadence_days`` / ``last_transfusion_date``) so the refreshed
#      subscription's first window reflects the latest forecast.
#
# :func:`regenerate_from_window` is a thin, side-effect-free wrapper around
# :func:`generate`: it re-runs the same forecast → match → assign pipeline with
# the patient's current cadence and reuses every injected seam, so it stays
# fully offline-testable. When the prior subscription is supplied, its
# ``subscription_id`` (and original ``created_at``) are preserved so the refresh
# updates the same plan in place rather than spawning a new one.


def regenerate_from_window(
    patient: Patient,
    horizon_days: Optional[int] = None,
    *,
    candidates: Iterable[MatchCandidate],
    consent_store: ConsentStore,
    today: date,
    existing_subscription: Optional[Subscription] = None,
    transfusion_dates: Optional[list[date]] = None,
    seed_cadence: Optional[float] = None,
    max_backups: int = DEFAULT_BACKUPS,
    patient_lat: Optional[float] = None,
    patient_lng: Optional[float] = None,
    subscription_id: Optional[str] = None,
    rank_fn: RankFn = None,  # type: ignore[assignment]
    estimate_cadence_fn: EstimateCadenceFn = estimate_cadence,
    predict_window_fn: PredictWindowFn = predict_window,
    now: Optional[datetime] = None,
) -> Subscription:
    """Refresh ``patient``'s subscription from their latest forecast (Req 4.4).

    Re-runs the full forecast → match → assign pipeline via :func:`generate`
    using the patient's **current** cadence and last transfusion date (i.e. the
    post-re-learn state), so the refreshed subscription's first window reflects
    the latest forecast. This is the ``regenerateFromWindow`` entry point of
    design Component 4; in production it is triggered by the
    ``transfusion_recorded`` re-learn event (see
    :mod:`pulselink.forecasting.relearn`).

    All injected seams (``rank_fn``, ``estimate_cadence_fn``,
    ``predict_window_fn``, ``candidates``, ``consent_store``) are passed straight
    through to :func:`generate`, so regeneration is as offline-testable as
    generation. The function is pure: it neither mutates ``patient`` nor
    ``existing_subscription`` and performs no persistence of its own.

    Args:
        patient: The (updated) patient to refresh; ``patient.cadence_days`` and
            ``patient.last_transfusion_date`` are expected to already reflect
            the latest re-learn.
        horizon_days: How far ahead to regenerate. Defaults to the prior
            subscription's ``horizon_days`` when ``existing_subscription`` is
            given; required otherwise.
        candidates: Candidate donors passed through to the matcher.
        consent_store: Consent gate passed through to the matcher.
        today: The "current" date used for forecasting/matching (offline-safe).
        existing_subscription: The prior subscription being refreshed. When
            supplied, its ``subscription_id`` is preserved (so the refresh
            updates the same plan) and its original ``created_at`` is carried
            forward while ``updated_at`` advances to ``now``.
        transfusion_dates: Optional transfusion history for cadence estimation;
            falls back to ``[last_transfusion_date]`` (or empty) when omitted.
        seed_cadence: Seed cadence in days; defaults to ``patient.cadence_days``.
        max_backups: Desired backups per slot beyond the primary (default 2).
        patient_lat, patient_lng: Patient coordinates for the distance tie-break.
        subscription_id: Explicit id override; ignored when
            ``existing_subscription`` is supplied (its id wins).
        rank_fn: Matching seam; defaults to ``rank_donors_for_slot``.
        estimate_cadence_fn, predict_window_fn: Forecasting seams.
        now: Optional refresh timestamp (defaults to UTC now).

    Returns:
        A fresh :class:`Subscription` whose slots reflect the patient's current
        cadence/forecast, preserving the prior ``subscription_id`` and
        ``created_at`` when ``existing_subscription`` is provided.

    Raises:
        ValueError: If neither ``horizon_days`` nor ``existing_subscription`` is
            provided (the horizon would be unknown), or if ``horizon_days < 1``.
    """
    # Resolve the horizon: explicit value wins, else inherit the prior plan's.
    if horizon_days is None:
        if existing_subscription is None:
            raise ValueError(
                "horizon_days is required when no existing_subscription is given"
            )
        horizon_days = existing_subscription.horizon_days

    # Preserve the prior subscription's id so the refresh updates the same plan.
    resolved_sub_id = (
        existing_subscription.subscription_id
        if existing_subscription is not None
        else subscription_id
    )

    refreshed_at = now or datetime.now(timezone.utc)

    subscription = generate(
        patient,
        horizon_days,
        candidates=candidates,
        consent_store=consent_store,
        today=today,
        transfusion_dates=transfusion_dates,
        seed_cadence=seed_cadence,
        max_backups=max_backups,
        patient_lat=patient_lat,
        patient_lng=patient_lng,
        subscription_id=resolved_sub_id,
        rank_fn=rank_fn,
        estimate_cadence_fn=estimate_cadence_fn,
        predict_window_fn=predict_window_fn,
        now=refreshed_at,
    )

    # When refreshing an existing plan, carry its original creation time forward
    # and advance only ``updated_at`` so the lifecycle reads as an update.
    if existing_subscription is not None:
        subscription = subscription.model_copy(
            update={
                "created_at": existing_subscription.created_at,
                "updated_at": refreshed_at,
            }
        )

    return subscription
