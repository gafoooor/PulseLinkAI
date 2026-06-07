"""Additional unit tests for the Dataset.csv importer (Task 2.2).

These complement ``test_importer.py`` (Task 2.1) with deeper, exhaustive
coverage of the pure CSV -> domain mapping:

* exhaustive blood-group normalization (all 8 canonical groups, common short
  aliases, and unmappable input -> ``None``);
* date parsing across both dataset formats (date-only and full timestamp) plus
  blanks;
* int/float blank/garbage coercion;
* preservation of the real ``frequency_in_days`` outlier (e.g. ``1958``) all the
  way into ``Patient.cadence_days``;
* donor de-duplication by ``user_id``;
* bridge grouping to exactly one ``Patient`` per ``bridge_id``;
* ``city_id`` derivation from the lat/lng cluster; and
* the ``preferred_lang`` default (there is no dataset column for it).

Where useful these run against a handful of real rows from the actual
``Dataset.csv`` and skip gracefully when it is not present.

Requirements: 12.1
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date

import pytest

from pulselink.common.enums import BloodGroup
from pulselink.ingest.importer import (
    DEFAULT_QUANTITY_REQUIRED,
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

# The real Dataset.csv column order, reused to build inline fixtures whose rows
# line up exactly with the production header.
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

_COLUMNS = HEADER.split(",")


def _row(**overrides) -> dict:
    """Build a single CSV-shaped row dict, blank except for the overrides."""
    row = {col: "" for col in _COLUMNS}
    row.update(overrides)
    return row


def _rows_from_csv(*lines: str) -> list[dict]:
    """Parse inline CSV ``lines`` (under :data:`HEADER`) into dict rows."""
    text = HEADER + "\n" + "\n".join(lines) + "\n"
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


# --------------------------------------------------------------------------- #
# Blood-group normalization (exhaustive)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("group", list(BloodGroup))
def test_normalize_blood_group_accepts_every_canonical_value(group: BloodGroup):
    # Canonical "A Positive"-style strings round-trip to their enum member.
    assert normalize_blood_group(group.value) is group


@pytest.mark.parametrize("group", list(BloodGroup))
def test_normalize_blood_group_is_case_and_space_insensitive(group: BloodGroup):
    noisy = f"  {group.value.upper()}   "  # extra spaces + upper-cased
    assert normalize_blood_group(noisy) is group


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("A+", BloodGroup.A_POSITIVE),
        ("a-", BloodGroup.A_NEGATIVE),
        ("B+", BloodGroup.B_POSITIVE),
        ("b-", BloodGroup.B_NEGATIVE),
        ("AB+", BloodGroup.AB_POSITIVE),
        ("ab-", BloodGroup.AB_NEGATIVE),
        ("O+", BloodGroup.O_POSITIVE),
        ("o-", BloodGroup.O_NEGATIVE),
        (" AB+ ", BloodGroup.AB_POSITIVE),
    ],
)
def test_normalize_blood_group_accepts_short_aliases(alias, expected):
    assert normalize_blood_group(alias) is expected


@pytest.mark.parametrize("bad", ["", "   ", None, "nonsense", "C Positive", "AB", "++"])
def test_normalize_blood_group_returns_none_for_unmappable(bad):
    assert normalize_blood_group(bad) is None


def test_normalize_blood_group_returns_all_eight_distinct_members():
    # Sanity: the canonical set is exactly the 8 expected groups (no collisions).
    mapped = {normalize_blood_group(g.value) for g in BloodGroup}
    assert mapped == set(BloodGroup)
    assert len(mapped) == 8


# --------------------------------------------------------------------------- #
# normalize_id
# --------------------------------------------------------------------------- #
def test_normalize_id_strips_prefix_and_handles_blanks():
    long_hex = "9a738e" * 10  # 60 chars, no prefix
    assert normalize_id("\\x" + long_hex) == long_hex
    assert normalize_id(long_hex) == long_hex  # already-clean value untouched
    assert normalize_id("  \\xABC  ") == "ABC"
    assert normalize_id("\\x") is None  # prefix only -> empty -> None
    assert normalize_id("") is None
    assert normalize_id("   ") is None
    assert normalize_id(None) is None


# --------------------------------------------------------------------------- #
# Date parsing (both dataset formats + blanks)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2025-08-02", date(2025, 8, 2)),  # date-only
        ("2020-05-02 22:09:38.000", date(2020, 5, 2)),  # timestamp w/ fraction
        ("2020-04-12 00:00:00.000", date(2020, 4, 12)),  # midnight timestamp
        ("2020-04-18 10:27:00", date(2020, 4, 18)),  # timestamp w/o fraction
        ("  2025-08-17  ", date(2025, 8, 17)),  # surrounding whitespace
    ],
)
def test_parse_date_handles_dataset_formats(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_parse_date_blank_is_none(blank):
    assert parse_date(blank) is None


def test_parse_date_unparseable_is_none():
    assert parse_date("not-a-date") is None
    assert parse_date("2025/08/02") is None  # wrong separator


# --------------------------------------------------------------------------- #
# Numeric coercion
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [("", 0), ("   ", 0), (None, 0), ("9", 9), ("1958", 1958), ("23", 23)],
)
def test_parse_int_blank_and_values(raw, expected):
    assert parse_int(raw) == expected


def test_parse_int_default_and_float_strings():
    assert parse_int("", default=21) == 21  # blank uses the supplied default
    assert parse_int("3.0") == 3  # float-looking int string coerces
    assert parse_int("garbage", default=7) == 7  # unparseable -> default


@pytest.mark.parametrize(
    "raw,expected",
    [("", 0.0), ("   ", 0.0), (None, 0.0), ("0.33", 0.33), ("23.00", 23.0), ("1", 1.0)],
)
def test_parse_float_blank_and_values(raw, expected):
    assert parse_float(raw) == expected


def test_parse_float_unparseable_uses_default():
    assert parse_float("n/a", default=1.5) == 1.5


# --------------------------------------------------------------------------- #
# city_id derivation
# --------------------------------------------------------------------------- #
def test_derive_city_id_clusters_and_falls_back():
    # Seed cluster (~17.39, 78.46) maps to Hyderabad.
    assert derive_city_id(17.3922792, 78.4602749, "fallback") == HYDERABAD_CITY_ID
    # Just inside the radius is still Hyderabad; well outside is bucketed.
    assert derive_city_id(17.8, 78.9, "fallback") == HYDERABAD_CITY_ID
    assert derive_city_id(12.97, 77.59, "fallback") == "city_13.0_77.6"
    # Missing coordinates fall back to the default city.
    assert derive_city_id(None, None, "fallback") == "fallback"
    assert derive_city_id(17.39, None, "fallback") == "fallback"


# --------------------------------------------------------------------------- #
# Column-to-field mapping (donor)
# --------------------------------------------------------------------------- #
def test_donor_column_to_field_mapping_is_complete():
    rows = _rows_from_csv(
        "\\xUSER1,,Emergency Donor,true,false,B Negative,Female,17.39,78.46,"
        ",,,,,2020-04-18 10:27:00.000,One-Time Donor,2025-07-28,2025-07-28,"
        "2025-11-15,9,not eligible,90,3,0,false,active,true,,0.33,Active,"
    )
    result = map_dataset(rows, default_lang="hi")
    assert len(result.donors) == 1
    donor = result.donors[0]

    assert donor.donor_id == "USER1"  # user_id, \x stripped
    assert donor.blood_group is BloodGroup.B_NEGATIVE  # blood_group
    assert donor.role == "Emergency Donor"  # role
    assert donor.donor_type == "One-Time Donor"  # donor_type
    assert donor.preferred_lang == "hi"  # default (no column)
    assert donor.lat == 17.39 and donor.lng == 78.46  # latitude/longitude
    assert donor.last_donation_date == date(2025, 7, 28)  # last_donation_date
    assert donor.next_eligible_date == date(2025, 11, 15)  # next_eligible_date
    assert donor.eligibility_status == "not eligible"  # eligibility_status
    assert donor.donations_till_date == 9  # donations_till_date
    assert donor.total_calls == 3  # total_calls
    assert donor.calls_to_donations_ratio == 0.33  # calls_to_donations_ratio
    assert donor.city_id == HYDERABAD_CITY_ID  # derived from lat/lng


def test_donor_unknown_role_and_type_default_safely():
    rows = _rows_from_csv(
        "\\xUSER2,,Captain,true,false,O Negative,Male,17.39,78.46,"
        ",,,,,,Mystery Donor,,,,0,eligible,0,0,0,true,active,,,0,Active,"
    )
    donor = map_dataset(rows).donors[0]
    assert donor.role == "Volunteer"  # unknown role -> Volunteer
    assert donor.donor_type == "Other"  # unknown type -> Other


def test_donor_row_without_user_id_or_blood_group_is_skipped():
    rows = _rows_from_csv(
        # No user_id -> skipped.
        ",,Emergency Donor,true,false,A Positive,Male,17.39,78.46,"
        ",,,,,,One-Time Donor,,,,0,eligible,0,0,0,true,active,,,0,Active,",
        # Unmappable blood group -> skipped.
        "\\xUSER3,,Emergency Donor,true,false,Rh-null,Male,17.39,78.46,"
        ",,,,,,One-Time Donor,,,,0,eligible,0,0,0,true,active,,,0,Active,",
    )
    result = map_dataset(rows)
    assert result.donors == []
    assert result.derivations["skipped_donor_rows"] == 2


# --------------------------------------------------------------------------- #
# Bridge grouping -> one Patient per bridge_id
# --------------------------------------------------------------------------- #
def test_three_rows_one_bridge_yield_single_patient():
    bridge = "\\xBRIDGE_A"
    rows = _rows_from_csv(
        f"\\xD1,{bridge},Bridge Donor,true,true,O Positive,Male,17.39,78.46,"
        "Male,O Positive,2,2025-08-02,2025-08-23,2020-05-02 22:09:38.000,"
        "Regular Donor,2025-07-26,2025-07-28,2025-10-26,3,not eligible,90,3,"
        "21,true,active,true,2025-08-02,1.00,Active,",
        f"\\xD2,{bridge},Bridge Donor,true,true,O Positive,Male,17.39,78.46,"
        "Male,O Positive,2,2025-08-02,2025-08-23,2020-04-12 00:00:00.000,"
        "Regular Donor,2025-08-17,,,,eligible,0,9,21,true,active,,2025-08-02,,Active,",
        f"\\xD3,{bridge},Bridge Donor,true,true,O Positive,Male,17.39,78.46,"
        "Male,O Positive,2,2025-08-02,2025-08-23,2020-04-12 00:00:00.000,"
        "Regular Donor,2025-08-17,,,,eligible,0,9,21,true,active,,2025-08-02,,Active,",
    )
    result = map_dataset(rows)

    # Three distinct donors, but exactly one Patient for the shared bridge_id.
    assert len(result.donors) == 3
    assert len(result.patients) == 1
    patient = result.patients[0]
    assert patient.patient_id == "BRIDGE_A"
    assert patient.blood_group is BloodGroup.O_POSITIVE
    assert patient.quantity_required == 2
    assert patient.cadence_days == 21
    assert patient.last_transfusion_date == date(2025, 8, 2)
    assert patient.expected_next_transfusion_date == date(2025, 8, 23)
    assert patient.city_id == HYDERABAD_CITY_ID


def test_distinct_bridges_yield_distinct_patients():
    rows = _rows_from_csv(
        "\\xD1,\\xBRG_X,Bridge Donor,true,true,O Positive,Male,17.39,78.46,"
        "Male,O Positive,1,,,,Regular Donor,,,,0,eligible,0,0,21,true,active,,,0,Active,",
        "\\xD2,\\xBRG_Y,Bridge Donor,true,true,A Positive,Male,17.39,78.46,"
        "Male,A Positive,1,,,,Regular Donor,,,,0,eligible,0,0,30,true,active,,,0,Active,",
    )
    result = map_dataset(rows)
    assert {p.patient_id for p in result.patients} == {"BRG_X", "BRG_Y"}


def test_bridge_status_flag_maps_to_inactive():
    rows = _rows_from_csv(
        "\\xD1,\\xBRG_Z,Bridge Donor,true,false,O Positive,Male,17.39,78.46,"
        "Male,O Positive,1,,,,Regular Donor,,,,0,eligible,0,0,21,false,active,,,0,Active,",
    )
    patient = map_dataset(rows).patients[0]
    assert patient.bridge_status == "inactive"


def test_bridge_blood_group_falls_back_to_donor_group():
    # bridge_blood_group blank but the donor's blood_group is present.
    rows = _rows_from_csv(
        "\\xD1,\\xBRG_F,Bridge Donor,true,true,AB Negative,Male,17.39,78.46,"
        "Male,,1,,,,Regular Donor,,,,0,eligible,0,0,21,true,active,,,0,Active,",
    )
    patient = map_dataset(rows).patients[0]
    assert patient.blood_group is BloodGroup.AB_NEGATIVE


# --------------------------------------------------------------------------- #
# Outlier handling + seeding
# --------------------------------------------------------------------------- #
def test_outlier_frequency_preserved_into_cadence_days():
    rows = _rows_from_csv(
        "\\xD1,\\xBRG_OUT,Bridge Donor,true,true,O Positive,Male,17.39,78.46,"
        "Male,O Positive,1,2025-12-16,2025-12-31,2020-04-20 17:45:33.000,"
        "Regular Donor,2025-08-11,2020-08-22,2026-01-01,1,not eligible,1958,9,"
        "1958,true,active,true,2025-12-16,9.00,Inactive,Very limited activity",
    )
    patient = map_dataset(rows).patients[0]
    # The 1958-day outlier is kept verbatim, not clamped or rejected.
    assert patient.cadence_days == 1958


def test_blank_or_zero_frequency_seeded_and_blank_quantity_floored():
    rows = _rows_from_csv(
        "\\xD1,\\xBRG_S,Bridge Donor,true,true,O Positive,Male,17.39,78.46,"
        "Male,O Positive,,,,,Regular Donor,,,,0,eligible,0,0,0,true,active,,,0,Active,",
    )
    patient = map_dataset(rows).patients[0]
    assert patient.cadence_days == DEFAULT_SEED_CADENCE_DAYS  # 0 -> seed
    assert patient.quantity_required == DEFAULT_QUANTITY_REQUIRED  # blank -> 1


# --------------------------------------------------------------------------- #
# De-duplication by user_id
# --------------------------------------------------------------------------- #
def test_duplicate_user_ids_keep_first_occurrence_only():
    rows = _rows_from_csv(
        "\\xDUP,,Emergency Donor,true,false,A Positive,Male,17.39,78.46,"
        ",,,,,,One-Time Donor,,,,5,eligible,0,1,0,false,active,,,0.5,Active,",
        # Same user_id, different stats: must be ignored (first wins).
        "\\xDUP,,Emergency Donor,true,false,A Positive,Male,17.39,78.46,"
        ",,,,,,One-Time Donor,,,,99,eligible,0,9,0,false,active,,,9.0,Active,",
    )
    result = map_dataset(rows)
    assert len(result.donors) == 1
    assert result.donors[0].donations_till_date == 5  # first row's value


# --------------------------------------------------------------------------- #
# preferred_lang default
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lang", ["te", "hi", "en", "ta"])
def test_preferred_lang_defaults_to_configured_value(lang):
    rows = _rows_from_csv(
        "\\xD1,,Emergency Donor,true,false,O Positive,Male,17.39,78.46,"
        ",,,,,,One-Time Donor,,,,0,eligible,0,0,0,false,active,,,0,Active,",
    )
    donor = map_dataset(rows, default_lang=lang).donors[0]
    assert donor.preferred_lang == lang
    # And it is reported in the derivation notes.
    assert map_dataset(rows, default_lang=lang).derivations["preferred_lang_default"] == lang


# --------------------------------------------------------------------------- #
# Real Dataset.csv (best-effort; skipped if absent)
# --------------------------------------------------------------------------- #
def _real_dataset_path() -> str:
    # tests/ -> backend/ -> repo root.
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(repo_root, "Dataset.csv")


def test_real_dataset_first_rows_map_as_expected():
    csv_path = _real_dataset_path()
    if not os.path.exists(csv_path):  # pragma: no cover - dataset optional in CI
        pytest.skip("Dataset.csv not present")

    rows = read_csv_rows(csv_path)
    result = map_dataset(rows[:7], default_lang="te")

    # The first row is an Emergency Donor with no bridge_id.
    first = next(d for d in result.donors if d.donations_till_date == 9 and d.role == "Emergency Donor")
    assert first.blood_group is BloodGroup.A_POSITIVE
    assert first.calls_to_donations_ratio == 0.33
    assert first.preferred_lang == "te"
    assert first.city_id == HYDERABAD_CITY_ID

    # Rows 2 and 3 share one bridge_id -> exactly one Patient with cadence 21.
    assert len(result.patients) >= 1
    shared = next(
        (p for p in result.patients if p.cadence_days == 21 and p.blood_group is BloodGroup.O_POSITIVE),
        None,
    )
    assert shared is not None
    assert shared.last_transfusion_date == date(2025, 8, 2)

    # Every mapped entity carries a city_id, and donors outnumber patients.
    assert all(d.city_id for d in result.donors)
    assert all(p.city_id for p in result.patients)
    assert len(result.donors) > len(result.patients)
