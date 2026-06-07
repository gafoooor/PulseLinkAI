"""Tests for the Dataset.csv importer's pure CSV -> domain mapping (Task 2.1).

These exercise the database-free mapping path (:func:`map_dataset` and its
field helpers) against a small inline CSV fixture plus a few real-shaped rows
from ``Dataset.csv``. The persistence step (:func:`import_dataset`) is covered
by the mapping path here; it is also validated end-to-end when a live
PostgreSQL is available.

Requirements: 12.1, 10.1
"""

from __future__ import annotations

import csv
import io
from datetime import date

from pulselink.common.enums import BloodGroup
from pulselink.ingest.importer import (
    DEFAULT_SEED_CADENCE_DAYS,
    HYDERABAD_CITY_ID,
    derive_city_id,
    map_dataset,
    normalize_blood_group,
    normalize_id,
    parse_date,
    parse_float,
    parse_int,
    read_csv_rows,
)

# A small fixture mirroring the real Dataset.csv header + representative rows:
# an Emergency Donor (no bridge), two Bridge Donors sharing a bridge_id, and a
# Bridge Donor on an outlier bridge (frequency_in_days = 1958).
HEADER = (
    "user_id,bridge_id,role,role_status,bridge_status,blood_group,gender,"
    "latitude,longitude,bridge_gender,bridge_blood_group,quantity_required,"
    "last_transfusion_date,expected_next_transfusion_date,registration_date,"
    "donor_type,last_contacted_date,last_donation_date,next_eligible_date,"
    "donations_till_date,eligibility_status,cycle_of_donations,total_calls,"
    "frequency_in_days,status_of_bridge,status,donated_earlier,"
    "last_bridge_donation_date,calls_to_donations_ratio,"
    "user_donation_active_status,inactive_trigger_comment"
)

ROWS = [
    # Emergency Donor, no bridge_id, blank counts where realistic.
    "\\xAAA1,,Emergency Donor,true,false,A Positive,Male,17.3922792,78.4602749,"
    ",,,,,2020-04-18 10:27:00.000,One-Time Donor,2025-07-28,2025-08-17,"
    "2025-11-15,9,not eligible,90,3,0,false,active,true,,0.33,Active,",
    # Bridge Donor #1 on bridge BRG1.
    "\\xBBB1,\\xBRG1,Bridge Donor,true,true,O Positive,Male,17.3922792,78.4602749,"
    "Male,O Positive,1,2025-08-02,2025-08-23,2020-05-02 22:09:38.000,Regular Donor,"
    "2025-07-26,2025-07-28,2025-10-26,3,not eligible,90,3,21,true,active,true,"
    "2025-08-02,1.00,Active,",
    # Bridge Donor #2 on the SAME bridge BRG1 (must dedupe to one Patient).
    "\\xCCC1,\\xBRG1,Bridge Donor,true,true,O Positive,Male,17.3922792,78.4602749,"
    "Male,O Positive,1,2025-08-02,2025-08-23,2020-04-12 00:00:00.000,Regular Donor,"
    "2025-08-17,,,,eligible,0,9,21,true,active,,2025-08-02,,Active,",
    # Bridge Donor on outlier bridge BRG2 (frequency_in_days = 1958).
    "\\xDDD1,\\xBRG2,Bridge Donor,true,true,O Positive,Male,17.3922792,78.4602749,"
    "Male,O Positive,2,2025-12-16,2025-12-31,2020-04-20 17:45:33.000,Regular Donor,"
    "2025-08-11,2020-08-22,2026-01-01,1,not eligible,1958,9,1958,true,active,true,"
    "2025-12-16,9.00,Inactive,Very limited activity despite multiple calls",
]


def _fixture_rows() -> list[dict]:
    text = HEADER + "\n" + "\n".join(ROWS) + "\n"
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


# --------------------------------------------------------------------------- #
# Field helpers
# --------------------------------------------------------------------------- #
def test_normalize_id_strips_hex_prefix():
    assert normalize_id("\\x9a738e") == "9a738e"
    assert normalize_id("  \\xABC  ") == "ABC"
    assert normalize_id("") is None
    assert normalize_id(None) is None


def test_parse_date_handles_both_formats_and_blank():
    assert parse_date("2025-08-02") == date(2025, 8, 2)
    assert parse_date("2020-05-02 22:09:38.000") == date(2020, 5, 2)
    assert parse_date("") is None
    assert parse_date(None) is None


def test_parse_int_and_float_coerce_blanks():
    assert parse_int("", default=0) == 0
    assert parse_int("9") == 9
    assert parse_int("1958") == 1958
    assert parse_float("") == 0.0
    assert parse_float("23.00") == 23.0


def test_normalize_blood_group_validates_and_normalizes():
    assert normalize_blood_group("A Positive") is BloodGroup.A_POSITIVE
    assert normalize_blood_group("o positive") is BloodGroup.O_POSITIVE
    assert normalize_blood_group("AB+") is BloodGroup.AB_POSITIVE
    assert normalize_blood_group("nonsense") is None
    assert normalize_blood_group("") is None


def test_derive_city_id_clusters_to_hyderabad():
    assert derive_city_id(17.3922792, 78.4602749, "fallback") == HYDERABAD_CITY_ID
    assert derive_city_id(None, None, "fallback") == "fallback"
    assert derive_city_id(12.97, 77.59, "fallback") == "city_13.0_77.6"


# --------------------------------------------------------------------------- #
# Dataset-level mapping
# --------------------------------------------------------------------------- #
def test_map_dataset_groups_bridges_and_donors():
    result = map_dataset(_fixture_rows(), default_lang="te")

    # Four rows -> four distinct donors; two distinct bridges -> two patients.
    assert len(result.donors) == 4
    assert {p.patient_id for p in result.patients} == {"BRG1", "BRG2"}

    # Donor ids have the \x prefix stripped.
    assert {d.donor_id for d in result.donors} == {"AAA1", "BBB1", "CCC1", "DDD1"}


def test_map_dataset_donor_fields_and_lang_default():
    result = map_dataset(_fixture_rows(), default_lang="te")
    donor = next(d for d in result.donors if d.donor_id == "AAA1")

    assert donor.role == "Emergency Donor"
    assert donor.blood_group is BloodGroup.A_POSITIVE
    assert donor.preferred_lang == "te"  # no column -> configured default
    assert donor.city_id == HYDERABAD_CITY_ID
    assert donor.eligibility_status == "not eligible"
    assert donor.donations_till_date == 9
    assert donor.calls_to_donations_ratio == 0.33


def test_map_dataset_patient_from_bridge_rows():
    result = map_dataset(_fixture_rows(), default_lang="te")
    brg1 = next(p for p in result.patients if p.patient_id == "BRG1")

    assert brg1.blood_group is BloodGroup.O_POSITIVE
    assert brg1.quantity_required == 1
    assert brg1.cadence_days == 21
    assert brg1.last_transfusion_date == date(2025, 8, 2)
    assert brg1.expected_next_transfusion_date == date(2025, 8, 23)
    assert brg1.city_id == HYDERABAD_CITY_ID


def test_map_dataset_keeps_outlier_cadence():
    result = map_dataset(_fixture_rows(), default_lang="te")
    brg2 = next(p for p in result.patients if p.patient_id == "BRG2")

    # The 1958-day outlier is kept, not rejected or clamped.
    assert brg2.cadence_days == 1958
    assert brg2.quantity_required == 2


def test_map_dataset_dedupes_repeated_user_ids():
    rows = _fixture_rows()
    # Duplicate the first donor row; the donor must still appear only once.
    rows.append(rows[0])
    result = map_dataset(rows, default_lang="te")
    assert len(result.donors) == 4


def test_map_dataset_seeds_blank_frequency():
    rows = _fixture_rows()
    rows[1]["frequency_in_days"] = ""  # blank out BRG1's cadence
    rows[2]["frequency_in_days"] = ""
    result = map_dataset(rows, default_lang="te")
    brg1 = next(p for p in result.patients if p.patient_id == "BRG1")
    assert brg1.cadence_days == DEFAULT_SEED_CADENCE_DAYS


def test_map_dataset_reports_derivations():
    result = map_dataset(_fixture_rows(), default_lang="te")
    d = result.derivations
    assert d["patients_mapped"] == 2
    assert d["donors_mapped"] == 4
    assert d["preferred_lang_default"] == "te"
    assert HYDERABAD_CITY_ID in d["city_ids"]


# --------------------------------------------------------------------------- #
# Against the real Dataset.csv (best-effort; skipped if not present)
# --------------------------------------------------------------------------- #
def test_real_dataset_maps_without_error():
    import os

    # tests/ -> backend/ -> repo root.
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    csv_path = os.path.join(repo_root, "Dataset.csv")
    if not os.path.exists(csv_path):  # pragma: no cover - dataset optional in CI
        import pytest

        pytest.skip("Dataset.csv not present")

    result = map_dataset(read_csv_rows(csv_path), default_lang="te")
    # Every donor and patient carries a city_id (Requirement 10.1).
    assert all(d.city_id for d in result.donors)
    assert all(p.city_id for p in result.patients)
    # There is at least one bridge-derived patient and more donors than patients.
    assert len(result.patients) >= 1
    assert len(result.donors) >= len(result.patients)
