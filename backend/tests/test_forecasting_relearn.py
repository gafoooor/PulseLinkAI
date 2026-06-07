"""Unit tests for the forecasting re-learn event handler (Task 4.2).

Covers:
* appending a transfusion recomputes the stored cadence to equal
  ``estimate_cadence`` over the complete history (Reqs 2.4, 2.5),
* the sample count is non-decreasing across successive records (Req 2.6),
* publishing the ``transfusion_recorded`` event on the in-memory bus triggers
  the handler and updates the stored cadence (event-driven re-learn).

Requirements: 2.4, 2.5, 2.6
"""

from __future__ import annotations

from datetime import date, timedelta

from pulselink.common.enums import BloodGroup
from pulselink.common.event_bus import InMemoryEventBus
from pulselink.common.models import Patient, TransfusionRecord
from pulselink.forecasting.engine import estimate_cadence
from pulselink.forecasting.relearn import (
    TRANSFUSION_RECORDED,
    InMemoryTransfusionRepo,
    record_transfusion_and_relearn,
    sample_count,
    subscribe_relearn,
)

SEED = 21.0


def _patient(patient_id: str = "bridge-1", cadence: float = SEED) -> Patient:
    return Patient(
        patient_id=patient_id,
        city_id="hyd",
        blood_group=BloodGroup.O_POSITIVE,
        quantity_required=2,
        cadence_days=cadence,
        consent_id="consent-1",
    )


def _record(patient_id: str, when: date, n: int) -> TransfusionRecord:
    return TransfusionRecord(
        record_id=f"{patient_id}-rec-{n}",
        patient_id=patient_id,
        date=when,
        units_given=2,
    )


def test_recording_appends_record_to_history():
    repo = InMemoryTransfusionRepo()
    patient = _patient()
    rec = _record(patient.patient_id, date(2024, 1, 1), 0)

    record_transfusion_and_relearn(patient, rec, repo)

    assert repo.get_transfusion_dates(patient.patient_id) == [date(2024, 1, 1)]


def test_stored_cadence_equals_estimate_over_complete_history():
    repo = InMemoryTransfusionRepo()
    patient = _patient()
    dates = [date(2024, 1, 1), date(2024, 1, 22), date(2024, 2, 12)]

    for n, d in enumerate(dates):
        est = record_transfusion_and_relearn(patient, _record(patient.patient_id, d, n), repo)

    # Stored cadence reflects the complete history (Req 2.5).
    history = repo.get_transfusion_dates(patient.patient_id)
    expected = estimate_cadence(history, seed_cadence=patient.cadence_days)
    assert est.cadence_days == expected.cadence_days
    assert repo.get_cadence(patient.patient_id) == expected.cadence_days


def test_first_record_uses_seed_cadence():
    repo = InMemoryTransfusionRepo()
    patient = _patient()

    est = record_transfusion_and_relearn(
        patient, _record(patient.patient_id, date(2024, 1, 1), 0), repo
    )

    # A single date yields no observed gaps -> seed cadence, zero samples.
    assert est.samples == 0
    assert est.cadence_days == SEED
    assert repo.get_cadence(patient.patient_id) == SEED


def test_sample_count_is_non_decreasing_across_records():
    repo = InMemoryTransfusionRepo()
    patient = _patient()
    dates = [date(2024, 1, 1) + timedelta(days=21 * i) for i in range(5)]

    prev = sample_count(repo, patient.patient_id)
    assert prev == 0
    for n, d in enumerate(dates):
        est = record_transfusion_and_relearn(patient, _record(patient.patient_id, d, n), repo)
        after = sample_count(repo, patient.patient_id)
        # Req 2.6: sample count never decreases, and matches the estimate.
        assert after >= prev
        assert est.samples == after
        prev = after
    # 5 dates -> 4 observed gaps.
    assert prev == 4


def test_publishing_event_triggers_relearn_handler():
    repo = InMemoryTransfusionRepo()
    bus = InMemoryEventBus()
    patient = _patient()
    subscribe_relearn(bus, repo)

    bus.publish(
        TRANSFUSION_RECORDED,
        {"patient": patient, "record": _record(patient.patient_id, date(2024, 1, 1), 0)},
    )
    bus.publish(
        TRANSFUSION_RECORDED,
        {"patient": patient, "record": _record(patient.patient_id, date(2024, 1, 22), 1)},
    )

    history = repo.get_transfusion_dates(patient.patient_id)
    assert history == [date(2024, 1, 1), date(2024, 1, 22)]
    # The event-driven path persisted the recomputed cadence (single 21-day gap).
    assert repo.get_cadence(patient.patient_id) == 21


def test_event_payload_for_unsubscribed_type_is_ignored():
    repo = InMemoryTransfusionRepo()
    bus = InMemoryEventBus()
    patient = _patient()
    subscribe_relearn(bus, repo)

    # An unrelated event type must not trigger the re-learn handler.
    bus.publish("donor_responded", {"patient": patient})

    assert repo.get_transfusion_dates(patient.patient_id) == []
    assert repo.get_cadence(patient.patient_id) is None
