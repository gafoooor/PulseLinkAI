"""Property test for forecasting re-learn monotonicity (Task 4.4).

**Property 2: Re-learning monotonicity of information** — for every patient,
recording a new transfusion never decreases ``based_on_samples`` (the observed
sample count), and after each record the stored cadence equals
``estimate_cadence(complete_history_so_far, seed)``.

**Validates: Requirements 2.2**

Strategy: generate a bounded, unsorted sequence of transfusion dates plus a
positive seed cadence, then feed the records one at a time through
``record_transfusion_and_relearn``. The engine sorts internally, so the dates
need not be ordered. After each step we assert the two halves of the property.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from pulselink.common.enums import BloodGroup
from pulselink.common.models import Patient, TransfusionRecord
from pulselink.forecasting.engine import estimate_cadence
from pulselink.forecasting.relearn import (
    InMemoryTransfusionRepo,
    record_transfusion_and_relearn,
    sample_count,
)

# A base date well in the past so generated dates never fall in the future
# (TransfusionRecord rejects future dates).
_BASE = date(2000, 1, 1)

# Bounded day-offsets keep the date sequence small and the test fast while
# still exercising arbitrary ordering, duplicates, and gap sizes.
_day_offsets = st.lists(
    st.integers(min_value=0, max_value=2000),
    min_size=1,
    max_size=12,
)

# Positive seed cadence (the patient's current cadence_days), kept in a sane band.
_seed_cadence = st.floats(
    min_value=1.0, max_value=60.0, allow_nan=False, allow_infinity=False
)


def _patient(seed: float) -> Patient:
    return Patient(
        patient_id="bridge-prop",
        city_id="hyd",
        blood_group=BloodGroup.O_POSITIVE,
        quantity_required=2,
        cadence_days=seed,
        consent_id="consent-1",
    )


@settings(max_examples=200)
@given(offsets=_day_offsets, seed=_seed_cadence)
def test_relearn_is_monotonic_and_cadence_matches_estimate(
    offsets: list[int], seed: float
) -> None:
    repo = InMemoryTransfusionRepo()
    patient = _patient(seed)

    dates = [_BASE + timedelta(days=off) for off in offsets]

    prev_samples = sample_count(repo, patient.patient_id)
    assert prev_samples == 0  # empty history starts at zero observed gaps

    for n, when in enumerate(dates):
        record = TransfusionRecord(
            record_id=f"{patient.patient_id}-rec-{n}",
            patient_id=patient.patient_id,
            date=when,
            units_given=2,
        )
        est = record_transfusion_and_relearn(patient, record, repo)

        after_samples = sample_count(repo, patient.patient_id)

        # --- Monotonicity of information: sample count never decreases. --- #
        assert after_samples >= prev_samples
        assert est.samples == after_samples

        # --- Stored cadence equals estimate_cadence over the full history. --- #
        history = repo.get_transfusion_dates(patient.patient_id)
        expected = estimate_cadence(history, seed_cadence=patient.cadence_days)
        assert est.cadence_days == expected.cadence_days
        assert repo.get_cadence(patient.patient_id) == expected.cadence_days

        prev_samples = after_samples
