"""Forecasting re-learn event handler (Task 4.2).

This module implements the *re-learn* step of the Forecasting Engine described
in design section 4.2: when a transfusion is recorded for a patient, the engine
appends the record, recomputes the patient's cadence from the **complete**
transfusion history (so the stored cadence always equals
``estimate_cadence(full_history)`` — Requirement 2.5), persists the updated
cadence, and reports a sample count that never decreases (Requirement 2.6).

It is kept as a sibling of :mod:`pulselink.forecasting.engine` so the pure
functions in ``engine.py`` (task 4.1) stay small and untouched; this module only
imports from them.

Event-driven wiring (design "Event-driven re-learning" principle):
    Recording a transfusion publishes a ``transfusion_recorded`` event on the
    bus. The forecasting engine subscribes to that event and runs
    :func:`record_transfusion_and_relearn`. In the offline demo the bus is the
    in-memory :class:`~pulselink.common.event_bus.InMemoryEventBus`; in the
    budget production profile this maps to **Amazon EventBridge** (pay-per-event)
    and the handler runs as a Lambda that loads history from RDS. The seam keeps
    the decline/re-score and re-learn flows loosely coupled.

Requirements: 2.4, 2.5, 2.6
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Protocol, runtime_checkable

from pulselink.common.event_bus import EventBus
from pulselink.common.models import Patient, TransfusionRecord
from pulselink.forecasting.engine import CadenceEstimate, estimate_cadence

# Domain event type consumed by the forecasting engine. Maps to an Amazon
# EventBridge detail-type in the budget production profile.
TRANSFUSION_RECORDED = "transfusion_recorded"


@runtime_checkable
class TransfusionRepo(Protocol):
    """Persistence seam for transfusion history + the stored cadence.

    The offline demo uses :class:`InMemoryTransfusionRepo`; production maps this
    to the RDS ``transfusion_record`` table plus the ``patient.cadence_days``
    column. Only the three methods the re-learn handler needs are part of the
    contract.
    """

    def append_transfusion(self, patient_id: str, record: TransfusionRecord) -> None:
        """Append ``record`` to the patient's stored transfusion history."""
        ...

    def get_transfusion_dates(self, patient_id: str) -> list[date]:
        """Return the patient's complete transfusion dates (any order)."""
        ...

    def update_cadence(self, patient_id: str, cadence_days: float) -> None:
        """Persist the patient's recomputed cadence in days."""
        ...


class InMemoryTransfusionRepo:
    """Synchronous in-memory :class:`TransfusionRepo` for tests and the demo."""

    def __init__(self) -> None:
        self._records: dict[str, list[TransfusionRecord]] = defaultdict(list)
        self._cadence: dict[str, float] = {}

    def append_transfusion(self, patient_id: str, record: TransfusionRecord) -> None:
        self._records[patient_id].append(record)

    def get_transfusion_dates(self, patient_id: str) -> list[date]:
        return [r.date for r in self._records[patient_id]]

    def update_cadence(self, patient_id: str, cadence_days: float) -> None:
        self._cadence[patient_id] = cadence_days

    # --- convenience observers (not part of the TransfusionRepo contract) --- #
    def get_cadence(self, patient_id: str) -> float | None:
        """Return the last-persisted cadence for a patient (``None`` if unset)."""
        return self._cadence.get(patient_id)

    def get_records(self, patient_id: str) -> list[TransfusionRecord]:
        """Return the patient's stored records in insertion order."""
        return list(self._records[patient_id])


def sample_count(repo: TransfusionRepo, patient_id: str) -> int:
    """Observe the current re-learn sample count for a patient.

    The sample count is the number of observed inter-transfusion gaps,
    ``max(0, len(history) - 1)`` — the same value reported as
    :attr:`CadenceEstimate.samples`. Useful for asserting the monotonic
    non-decreasing property (Requirement 2.6) before vs. after a record.
    """
    return max(0, len(repo.get_transfusion_dates(patient_id)) - 1)


def record_transfusion_and_relearn(
    patient: Patient, new_record: TransfusionRecord, repo: TransfusionRepo
) -> CadenceEstimate:
    """Append a transfusion and re-learn the patient's cadence (design 4.2).

    Steps (event handler for ``transfusion_recorded``):

    1. Append ``new_record`` to the patient's history.
    2. Recompute cadence from the **complete** history with
       :func:`~pulselink.forecasting.engine.estimate_cadence`, seeded by the
       patient's current ``cadence_days``.
    3. Persist the recomputed cadence so the stored value equals
       ``estimate_cadence(full_history)`` (Requirement 2.5).

    Because the history only ever grows, the returned ``est.samples`` is
    non-decreasing across successive records (Requirement 2.6).

    Args:
        patient: The patient the record belongs to; ``patient.cadence_days`` is
            the seed used when the history is still sparse.
        new_record: The transfusion being recorded.
        repo: The persistence seam (in-memory for the demo, RDS in prod).

    Returns:
        The recomputed :class:`CadenceEstimate` over the complete history.
    """
    repo.append_transfusion(patient.patient_id, new_record)
    history = repo.get_transfusion_dates(patient.patient_id)
    est = estimate_cadence(history, seed_cadence=patient.cadence_days)
    repo.update_cadence(patient.patient_id, est.cadence_days)
    return est


def subscribe_relearn(bus: EventBus, repo: TransfusionRepo) -> None:
    """Subscribe the re-learn handler to ``transfusion_recorded`` on the bus.

    Publishing a ``transfusion_recorded`` event with a payload of
    ``{"patient": Patient, "record": TransfusionRecord}`` triggers
    :func:`record_transfusion_and_relearn`, keeping the re-learn flow loosely
    coupled from whoever records the transfusion. Maps to an Amazon EventBridge
    rule + Lambda target in the budget production profile.

    Args:
        bus: The event bus to subscribe on (in-memory for the demo).
        repo: The transfusion repository the handler reads/writes.
    """

    def _handler(payload: dict[str, Any]) -> None:
        patient = payload["patient"]
        record = payload["record"]
        record_transfusion_and_relearn(patient, record, repo)

    bus.subscribe(TRANSFUSION_RECORDED, _handler)
