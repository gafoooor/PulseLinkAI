"""Dataset.csv importer (Task 2.1).

Reads ``Dataset.csv`` from the local filesystem and maps each row onto the
shared PulseLink domain models, then persists the result to local PostgreSQL
scoped by ``city_id``. This mirrors the production S3 import column-to-field
mapping (bridge -> Patient, user -> Donor) but is a simple local/Lambda
importer with **no AWS Glue**.

Design seam (testable without a live database)
----------------------------------------------
The pure CSV -> domain-object mapping is kept completely separate from the
persistence step:

* :func:`read_csv_rows`  -- read the CSV into a list of ``dict`` rows.
* :func:`map_dataset`    -- pure function: rows -> ``MappingResult`` (Patient /
  Donor domain objects + derivation notes). No database required.
* :func:`import_dataset` -- read + map + persist via the ORM models in
  :mod:`pulselink.common.db_models`, scoped by ``city_id``.

Dataset realities handled here (see the module-level derivation notes returned
by :func:`map_dataset`)
-----------------------------------------------------------------------------
* The CSV is **donor-centric**. ``bridge_id`` is empty for Emergency Donors /
  Volunteers and populated for Bridge Donors. A Patient (bridge) is derived by
  grouping the rows that share a non-empty ``bridge_id`` and de-duplicating to
  one Patient per ``bridge_id``.
* ``user_id`` / ``bridge_id`` are hex strings prefixed with ``\\x``. The ``\\x``
  prefix is stripped so the remaining 64 hex characters form a valid key that
  fits the ``VARCHAR(64)`` id columns; nothing else is altered.
* There is no explicit ``city`` or ``preferred_lang`` column. ``city_id`` is
  derived from the latitude/longitude cluster (the seed data sits at
  ~17.39, 78.46 = Hyderabad); ``preferred_lang`` defaults to the configured
  default language.
* Blood groups are already canonical ("A Positive" ...); they are validated
  against :class:`~pulselink.common.enums.BloodGroup` and normalised if needed.
* Dates are ``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS.sss``; blanks become
  ``None``. Blank counts coerce to ``0``; outliers (e.g. ``frequency_in_days``
  of ``1958``) are kept, not rejected.

Requirements: 12.1, 10.1
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from pulselink.common.config import get_settings
from pulselink.common.enums import BloodGroup
from pulselink.common.models import Donor, Patient

# --------------------------------------------------------------------------- #
# Derivation defaults (documented seed-time choices)
# --------------------------------------------------------------------------- #

#: City assigned to the dataset's lat/lng cluster (~17.39, 78.46 == Hyderabad).
HYDERABAD_CITY_ID = "hyderabad"
HYDERABAD_LAT = 17.39
HYDERABAD_LNG = 78.46
#: Degrees of slack around the Hyderabad centroid that still count as Hyderabad.
CITY_CLUSTER_RADIUS_DEG = 0.5

#: Seed cadence used when a bridge's ``frequency_in_days`` is blank/zero. The
#: domain model requires ``cadence_days > 0``; real outliers are kept as-is.
DEFAULT_SEED_CADENCE_DAYS = 21

#: Default units per transfusion when ``quantity_required`` is blank/zero.
DEFAULT_QUANTITY_REQUIRED = 1

# Canonical blood-group lookup keyed by a normalised (lower, single-spaced) form.
_BLOOD_GROUP_BY_NORM = {bg.value.lower(): bg for bg in BloodGroup}
# A few tolerant aliases for short forms, in case the seed source varies.
_BLOOD_GROUP_ALIASES = {
    "a+": BloodGroup.A_POSITIVE,
    "a-": BloodGroup.A_NEGATIVE,
    "b+": BloodGroup.B_POSITIVE,
    "b-": BloodGroup.B_NEGATIVE,
    "ab+": BloodGroup.AB_POSITIVE,
    "ab-": BloodGroup.AB_NEGATIVE,
    "o+": BloodGroup.O_POSITIVE,
    "o-": BloodGroup.O_NEGATIVE,
}

_VALID_ROLES = {"Bridge Donor", "Emergency Donor", "Volunteer"}
_VALID_DONOR_TYPES = {"Regular Donor", "One-Time Donor", "Other"}


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MappingResult:
    """The pure mapping output: domain objects plus derivation notes."""

    patients: list[Patient]
    donors: list[Donor]
    derivations: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ImportCounts:
    """Counts returned after persisting an import."""

    patients: int
    donors: int


# --------------------------------------------------------------------------- #
# Field-level parsing helpers (robust to blanks / mixed formats)
# --------------------------------------------------------------------------- #
def _clean(raw: Optional[str]) -> str:
    """Strip surrounding whitespace; treat ``None`` as empty."""
    return (raw or "").strip()


def normalize_id(raw: Optional[str]) -> Optional[str]:
    """Normalise a dataset hex id.

    The seed ids look like ``\\x9a738e...`` (a literal backslash-x prefix plus
    64 hex characters). The ``\\x`` prefix is stripped so the remaining key fits
    the ``VARCHAR(64)`` id columns; an empty value yields ``None``.
    """
    value = _clean(raw)
    if not value:
        return None
    if value.startswith("\\x"):
        value = value[2:]
    return value or None


def parse_date(raw: Optional[str]) -> Optional[date]:
    """Parse ``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS[.fff]``; blank -> ``None``."""
    value = _clean(raw)
    if not value:
        return None
    # Date-only fast path.
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    # Datetime forms (optionally with fractional seconds).
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    # Last resort: take the leading date token.
    token = value.split(" ", 1)[0].split("T", 1)[0]
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def parse_int(raw: Optional[str], default: int = 0) -> int:
    """Parse an integer; blank or unparseable -> ``default``."""
    value = _clean(raw)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return default


def parse_float(raw: Optional[str], default: float = 0.0) -> float:
    """Parse a float; blank or unparseable -> ``default``."""
    value = _clean(raw)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def normalize_blood_group(raw: Optional[str]) -> Optional[BloodGroup]:
    """Validate/normalise a blood group against :class:`BloodGroup`.

    Returns ``None`` when the value cannot be mapped to a canonical group.
    """
    value = _clean(raw)
    if not value:
        return None
    norm = " ".join(value.split()).lower()
    if norm in _BLOOD_GROUP_BY_NORM:
        return _BLOOD_GROUP_BY_NORM[norm]
    compact = norm.replace(" ", "")
    if compact in _BLOOD_GROUP_ALIASES:
        return _BLOOD_GROUP_ALIASES[compact]
    return None


def derive_city_id(lat: Optional[float], lng: Optional[float], default_city: str) -> str:
    """Derive a ``city_id`` from coordinates.

    The seed dataset clusters at Hyderabad (~17.39, 78.46); coordinates within
    ``CITY_CLUSTER_RADIUS_DEG`` of that centroid map to ``HYDERABAD_CITY_ID``.
    Other coordinates are bucketed into a stable ``city_<lat>_<lng>`` id by
    rounding to one decimal place. Missing coordinates fall back to
    ``default_city``.
    """
    if lat is None or lng is None:
        return default_city
    if (
        abs(lat - HYDERABAD_LAT) <= CITY_CLUSTER_RADIUS_DEG
        and abs(lng - HYDERABAD_LNG) <= CITY_CLUSTER_RADIUS_DEG
    ):
        return HYDERABAD_CITY_ID
    return f"city_{round(lat, 1)}_{round(lng, 1)}"


# --------------------------------------------------------------------------- #
# Row -> domain object mapping
# --------------------------------------------------------------------------- #
def map_donor_row(
    row: dict,
    *,
    default_lang: str,
    default_city: str,
) -> Optional[Donor]:
    """Map a single CSV row to a :class:`Donor`, or ``None`` if not mappable.

    Every row (Bridge Donor, Emergency Donor, Volunteer) is a donor. A row is
    skipped only when it lacks a ``user_id`` or a recognisable blood group.
    """
    donor_id = normalize_id(row.get("user_id"))
    if donor_id is None:
        return None

    blood_group = normalize_blood_group(row.get("blood_group"))
    if blood_group is None:
        return None

    role = _clean(row.get("role")) or "Volunteer"
    if role not in _VALID_ROLES:
        role = "Volunteer"

    donor_type = _clean(row.get("donor_type")) or "Other"
    if donor_type not in _VALID_DONOR_TYPES:
        donor_type = "Other"

    lat = parse_float(row.get("latitude"), default=None) if _clean(row.get("latitude")) else None
    lng = parse_float(row.get("longitude"), default=None) if _clean(row.get("longitude")) else None

    eligibility = _clean(row.get("eligibility_status")).lower()
    eligibility_status = "eligible" if eligibility == "eligible" else "not eligible"

    return Donor(
        donor_id=donor_id,
        city_id=derive_city_id(lat, lng, default_city),
        blood_group=blood_group,
        role=role,
        donor_type=donor_type,
        preferred_lang=default_lang,
        lat=lat,
        lng=lng,
        last_donation_date=parse_date(row.get("last_donation_date")),
        next_eligible_date=parse_date(row.get("next_eligible_date")),
        eligibility_status=eligibility_status,
        donations_till_date=max(0, parse_int(row.get("donations_till_date"), default=0)),
        total_calls=max(0, parse_int(row.get("total_calls"), default=0)),
        calls_to_donations_ratio=max(
            0.0, parse_float(row.get("calls_to_donations_ratio"), default=0.0)
        ),
        # Consent records are created in a later task; synthesise a stable
        # placeholder so the domain model validates. Persistence stores NULL.
        consent_id=f"consent-donor-{donor_id}",
    )


def _map_bridge_to_patient(
    bridge_id: str,
    rows: list[dict],
    *,
    default_city: str,
) -> Optional[Patient]:
    """Build one :class:`Patient` from the rows sharing a ``bridge_id``."""

    def first_nonempty(column: str) -> str:
        for r in rows:
            value = _clean(r.get(column))
            if value:
                return value
        return ""

    # Bridge blood group, falling back to the row's donor blood group.
    blood_group = normalize_blood_group(first_nonempty("bridge_blood_group"))
    if blood_group is None:
        blood_group = normalize_blood_group(first_nonempty("blood_group"))
    if blood_group is None:
        return None

    quantity_required = parse_int(first_nonempty("quantity_required"), default=0)
    if quantity_required < DEFAULT_QUANTITY_REQUIRED:
        quantity_required = DEFAULT_QUANTITY_REQUIRED

    cadence_days = parse_int(first_nonempty("frequency_in_days"), default=0)
    if cadence_days <= 0:
        cadence_days = DEFAULT_SEED_CADENCE_DAYS

    # Bridge activity: ``status_of_bridge`` is a boolean-ish flag in the seed.
    status_flag = first_nonempty("status_of_bridge").lower()
    bridge_status = "inactive" if status_flag in {"false", "0", "no"} else "active"

    # City from the first row that carries coordinates.
    lat = lng = None
    for r in rows:
        if _clean(r.get("latitude")) and _clean(r.get("longitude")):
            lat = parse_float(r.get("latitude"), default=None)
            lng = parse_float(r.get("longitude"), default=None)
            break

    return Patient(
        patient_id=bridge_id,
        city_id=derive_city_id(lat, lng, default_city),
        blood_group=blood_group,
        quantity_required=quantity_required,
        cadence_days=cadence_days,
        last_transfusion_date=parse_date(first_nonempty("last_transfusion_date")),
        expected_next_transfusion_date=parse_date(
            first_nonempty("expected_next_transfusion_date")
        ),
        bridge_status=bridge_status,
        consent_id=f"consent-patient-{bridge_id}",
    )


def map_dataset(
    rows: Iterable[dict],
    *,
    default_lang: str = "te",
    default_city: str = HYDERABAD_CITY_ID,
) -> MappingResult:
    """Pure mapping: CSV rows -> ``MappingResult`` (no database access).

    * Every row maps to one :class:`Donor` (de-duplicated by ``user_id``,
      keeping the first occurrence).
    * Rows are grouped by non-empty ``bridge_id`` and each group de-duplicates
      to one :class:`Patient`.
    """
    rows = list(rows)

    donors: list[Donor] = []
    seen_donor_ids: set[str] = set()
    skipped_donor_rows = 0

    bridge_rows: dict[str, list[dict]] = {}

    for row in rows:
        donor = map_donor_row(row, default_lang=default_lang, default_city=default_city)
        if donor is None:
            skipped_donor_rows += 1
        elif donor.donor_id not in seen_donor_ids:
            seen_donor_ids.add(donor.donor_id)
            donors.append(donor)

        bridge_id = normalize_id(row.get("bridge_id"))
        if bridge_id is not None:
            bridge_rows.setdefault(bridge_id, []).append(row)

    patients: list[Patient] = []
    skipped_bridges = 0
    for bridge_id, group in bridge_rows.items():
        patient = _map_bridge_to_patient(bridge_id, group, default_city=default_city)
        if patient is None:
            skipped_bridges += 1
        else:
            patients.append(patient)

    city_ids = sorted(
        {p.city_id for p in patients} | {d.city_id for d in donors}
    )

    derivations = {
        "total_rows": len(rows),
        "patients_mapped": len(patients),
        "donors_mapped": len(donors),
        "skipped_donor_rows": skipped_donor_rows,
        "skipped_bridges": skipped_bridges,
        "city_ids": city_ids,
        "preferred_lang_default": default_lang,
        "default_city": default_city,
        "notes": [
            "bridge_id grouping: one Patient per distinct non-empty bridge_id.",
            "user_id de-duplicated: first occurrence wins.",
            "ids: leading '\\x' prefix stripped to fit VARCHAR(64) keys.",
            "preferred_lang has no column; defaulted to the configured default.",
            "city_id derived from lat/lng cluster (Hyderabad seed).",
            f"blank/zero frequency_in_days seeded to {DEFAULT_SEED_CADENCE_DAYS} days; "
            "real outliers (e.g. 1958) kept.",
        ],
    }

    return MappingResult(patients=patients, donors=donors, derivations=derivations)


# --------------------------------------------------------------------------- #
# CSV reading + persistence
# --------------------------------------------------------------------------- #
def read_csv_rows(csv_path: str) -> list[dict]:
    """Read ``Dataset.csv`` into a list of ``dict`` rows (header-keyed)."""
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _point_wkt(lat: Optional[float], lng: Optional[float]):
    """Build a PostGIS POINT element from lat/lng, or ``None`` when missing."""
    if lat is None or lng is None:
        return None
    try:
        from geoalchemy2 import WKTElement

        return WKTElement(f"POINT({lng} {lat})", srid=4326)
    except Exception:  # pragma: no cover - geom is optional; lat/lng suffice
        return None


def import_dataset(
    csv_path: Optional[str] = None,
    session=None,
    *,
    default_lang: Optional[str] = None,
    default_city: str = HYDERABAD_CITY_ID,
    flush: bool = True,
) -> ImportCounts:
    """Read ``Dataset.csv``, map it, and persist scoped by ``city_id``.

    The pure mapping (:func:`map_dataset`) is reused so behaviour is identical
    with or without a database. Entities are upserted via ``session.merge`` so
    re-running the import is idempotent. ``consent_id`` is persisted as ``NULL``
    (consent records are created in a later task) to avoid FK violations.
    """
    if session is None:
        raise ValueError("import_dataset requires a SQLAlchemy session")

    settings = get_settings()
    lang = default_lang or settings.default_lang
    path = csv_path or settings.dataset_csv_path

    # Import ORM models lazily so the pure mapping path has no ORM/DB import cost.
    from pulselink.common.db_models import Donor as DonorORM
    from pulselink.common.db_models import Patient as PatientORM

    rows = read_csv_rows(path)
    result = map_dataset(rows, default_lang=lang, default_city=default_city)

    for patient in result.patients:
        session.merge(
            PatientORM(
                patient_id=patient.patient_id,
                city_id=patient.city_id,
                blood_group=patient.blood_group.value,
                quantity_required=patient.quantity_required,
                cadence_days=patient.cadence_days,
                last_transfusion_date=patient.last_transfusion_date,
                expected_next_transfusion_date=patient.expected_next_transfusion_date,
                bridge_status=patient.bridge_status,
                consent_id=None,
            )
        )

    for donor in result.donors:
        session.merge(
            DonorORM(
                donor_id=donor.donor_id,
                city_id=donor.city_id,
                blood_group=donor.blood_group.value,
                role=donor.role,
                donor_type=donor.donor_type,
                preferred_lang=donor.preferred_lang,
                lat=donor.lat,
                lng=donor.lng,
                geom=_point_wkt(donor.lat, donor.lng),
                last_donation_date=donor.last_donation_date,
                next_eligible_date=donor.next_eligible_date,
                eligibility_status=donor.eligibility_status,
                donations_till_date=donor.donations_till_date,
                total_calls=donor.total_calls,
                calls_to_donations_ratio=donor.calls_to_donations_ratio,
                consent_id=None,
            )
        )

    if flush:
        session.flush()

    return ImportCounts(patients=len(result.patients), donors=len(result.donors))
