"""Focused unit tests for parser normalization and failure paths (Task 3.4).

These complement the broader pipeline tests in ``test_parser_service.py`` (Task
3.2) with additional *edge cases* — extra blood-group/date variants, phone
normalization detail, threshold-boundary and reason-precedence flagging, the
one-retry-then-``parse_failed`` contract (including the retry prompt), and a
static guard that the parser core writes nothing to a database (Req 5.6).

Covers Requirements 5.3, 5.4, 5.5, 5.6, 5.7.
"""

from __future__ import annotations

import inspect
import io
import tokenize

import pytest

from pulselink.common.config import get_settings
from pulselink.common.enums import BloodGroup
from pulselink.parsing.llm_client import MockLlmClient, SchemaValidationError
from pulselink.parsing import service as service_module
from pulselink.parsing.service import (
    ParsedRecord,
    build_review_flags,
    normalize_blood_group,
    normalize_date,
    normalize_phone,
    parse_record,
)


# --------------------------------------------------------------------------- #
# Test doubles (recording variants, so we can inspect the retry behaviour)
# --------------------------------------------------------------------------- #
class _StaticClient:
    """Returns a fixed dict, bypassing schema validation, for targeted tests."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    def complete_structured(self, **kwargs) -> dict:
        self.calls += 1
        return self._payload


class _RecordingAlwaysInvalidClient:
    """Always raises, recording each call's ``system`` prompt."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete_structured(self, *, system, **kwargs) -> dict:
        self.systems.append(system)
        raise SchemaValidationError("invalid output")


class _RecordingInvalidThenValidClient:
    """Fails the first call then succeeds, recording each ``system`` prompt."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.systems: list[str] = []

    def complete_structured(self, *, system, **kwargs) -> dict:
        self.systems.append(system)
        if len(self.systems) == 1:
            raise SchemaValidationError("first attempt invalid")
        return self._payload


def _patient_payload(**overrides) -> dict:
    """A minimal schema-shaped payload with sensible, overridable defaults."""
    patient = {
        "blood_group": "B Positive",
        "cadence_days": 21,
        "last_transfusion_date": "2024-01-05",
        "quantity_required": 2,
    }
    patient.update(overrides.pop("patient", {}))
    payload = {
        "patient": patient,
        "donors": [],
        "field_confidence": {
            "patient.blood_group": 0.95,
            "patient.cadence_days": 0.95,
            "patient.last_transfusion_date": 0.95,
            "patient.quantity_required": 0.95,
        },
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Blood-group normalization — extra variants + unmappable -> None (Req 5.5, 5.4)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("A+", "A Positive"),
        ("a pos", "A Positive"),
        ("B neg", "B Negative"),
        ("o-", "O Negative"),
        ("AB-ve", "AB Negative"),
        ("ab pos", "AB Positive"),
        ("B  +ve", "B Positive"),  # extra internal whitespace tolerated
        ("Patient is O positive today", "O Positive"),  # embedded in prose
    ],
)
def test_normalize_blood_group_extra_variants(raw, expected):
    assert normalize_blood_group(raw) == expected
    assert expected in {bg.value for bg in BloodGroup}


@pytest.mark.parametrize("raw", ["C+", "123", "AB", "positive", "  ", "+ve", 3.5, []])
def test_normalize_blood_group_unmappable_returns_none(raw):
    # Unsupported tokens become None so the field is flagged, never invented.
    assert normalize_blood_group(raw) is None


# --------------------------------------------------------------------------- #
# Date normalization — more formats + unparseable -> None (Req 5.5, 5.4)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-03-15T09:30:00", "2024-03-15"),          # ISO datetime with 'T'
        ("2024-03-15 09:30:00.123456", "2024-03-15"),   # microsecond timestamp
        ("13-05-2024", "2024-05-13"),                   # dd-mm-yyyy
        ("5 January 2024", "2024-01-05"),               # full month name
        ("15 Mar 2024", "2024-03-15"),                  # abbreviated month
        ("2024-12-31T23:59:59 extra", "2024-12-31"),    # leading-token fallback
    ],
)
def test_normalize_date_extra_formats(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["32/13/2024", "yesterday", "2024/01/05", "soon", 20240105, 0, [], "  "],
)
def test_normalize_date_unparseable_returns_none(raw):
    assert normalize_date(raw) is None


# --------------------------------------------------------------------------- #
# Phone normalization — keep a single leading '+' (Req 5.5)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+91 98765 43210", "+919876543210"),     # leading + preserved
        ("98765-43210", "9876543210"),            # no +, digits only
        ("  +1 (800) 555-0100  ", "+18005550100"),  # punctuation stripped
        ("++91123", "+91123"),                    # only one leading + kept
        ("tel: 0408000000", "0408000000"),         # leading text dropped, no +
    ],
)
def test_normalize_phone_keeps_leading_plus(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "no-digits-here", None, 12345, []])
def test_normalize_phone_returns_none_when_no_digits(raw):
    assert normalize_phone(raw) is None


# --------------------------------------------------------------------------- #
# Review flags — threshold boundary + reason precedence (Req 5.3, 5.4)
# --------------------------------------------------------------------------- #
def test_low_confidence_threshold_is_inclusive_at_a_custom_threshold():
    # Confidence exactly at the threshold is NOT flagged; just below IS flagged.
    parsed = _patient_payload()
    parsed["field_confidence"] = {
        "patient.blood_group": 0.60,        # == threshold -> not flagged
        "patient.cadence_days": 0.5999,     # just below -> flagged
        "patient.last_transfusion_date": 0.9,
        "patient.quantity_required": 0.9,
    }
    by_path = {f.field_path: f.reason for f in build_review_flags(parsed, threshold=0.60)}
    assert "patient.blood_group" not in by_path
    assert by_path.get("patient.cadence_days") == "low_confidence"


def test_non_numeric_confidence_is_flagged_ambiguous():
    parsed = _patient_payload()
    parsed["field_confidence"] = {
        "patient.blood_group": "high",   # non-numeric -> ambiguous
        "patient.cadence_days": 0.95,
        "patient.last_transfusion_date": 0.95,
        "patient.quantity_required": 0.95,
    }
    by_path = {f.field_path: f.reason for f in build_review_flags(parsed, threshold=0.75)}
    assert by_path.get("patient.blood_group") == "ambiguous"


def test_missing_reason_takes_precedence_over_low_confidence():
    # A null leaf that is ALSO below threshold is reported as "missing".
    parsed = _patient_payload(patient={"cadence_days": None})
    parsed["field_confidence"]["patient.cadence_days"] = 0.1  # also low
    by_path = {f.field_path: f.reason for f in build_review_flags(parsed, threshold=0.75)}
    assert by_path.get("patient.cadence_days") == "missing"


def test_null_donor_leaves_flagged_missing():
    parsed = {
        "patient": None,
        "donors": [
            {
                "name_raw": None,
                "blood_group": "O Positive",
                "phone_raw": None,
                "preferred_lang": None,
            }
        ],
        "field_confidence": {"donors[0].blood_group": 0.9},
    }
    by_path = {f.field_path: f.reason for f in build_review_flags(parsed, threshold=0.75)}
    assert by_path.get("donors[0].name_raw") == "missing"
    assert by_path.get("donors[0].phone_raw") == "missing"
    assert by_path.get("donors[0].preferred_lang") == "missing"
    # The present, confident donor field is not flagged.
    assert "donors[0].blood_group" not in by_path


def test_unsupported_value_becomes_null_and_flagged_missing_through_pipeline():
    # An unmappable blood group is set to null + flagged, never invented (Req 5.4).
    payload = _patient_payload(patient={"blood_group": "rainbow"})
    record = parse_record("anything", None, _StaticClient(payload), threshold=0.75)
    assert record.patient["blood_group"] is None
    by_path = {f.field_path: f.reason for f in record.review_flags}
    assert by_path.get("patient.blood_group") == "missing"


# --------------------------------------------------------------------------- #
# One-retry-then-parse_failed contract (Req 5.7)
# --------------------------------------------------------------------------- #
def test_invalid_twice_returns_parse_failed_after_exactly_one_retry():
    client = _RecordingAlwaysInvalidClient()
    record = parse_record("hopeless", None, client, threshold=0.75)

    assert record.parse_failed is True
    assert record.patient is None
    assert record.donors == []
    assert record.field_confidence == {}
    assert record.review_flags == []
    assert record.model_version == service_module.MODEL_VERSION
    # Exactly one retry: two attempts total.
    assert len(client.systems) == 2
    # The retry prompt is stricter than the first attempt's prompt.
    assert "failed schema validation" not in client.systems[0].lower()
    assert "failed schema validation" in client.systems[1].lower()


def test_invalid_then_valid_uses_retry_result_without_parse_failed():
    payload = _patient_payload(patient={"blood_group": "ab+ve"})
    client = _RecordingInvalidThenValidClient(payload)
    record = parse_record("messy", None, client, threshold=0.75)

    assert record.parse_failed is False
    # The retried, valid payload was normalized and used.
    assert record.patient["blood_group"] == "AB Positive"
    assert len(client.systems) == 2
    # First attempt used the base prompt; the retry added the stricter reminder.
    assert "failed schema validation" not in client.systems[0].lower()
    assert "failed schema validation" in client.systems[1].lower()


# --------------------------------------------------------------------------- #
# No-persistence guarantee (Req 5.6)
# --------------------------------------------------------------------------- #
def _executable_identifiers(source: str) -> set[str]:
    """Collect NAME tokens from ``source``, dropping comments and string bodies.

    This inspects only *executable* code (not docstrings/comments, which may
    legitimately describe the no-persistence guarantee in prose), so a match is
    real code rather than documentation.
    """
    names: set[str] = set()
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in tokens:
        if tok.type == tokenize.NAME:
            names.add(tok.string)
    return names


def test_parser_core_contains_no_persistence_calls():
    # Req 5.6: the parser core must not write to any operational store. Assert
    # no database/session/ORM identifiers appear in the executable code.
    identifiers = _executable_identifiers(inspect.getsource(service_module))
    forbidden = {
        "sqlalchemy",
        "Session",
        "sessionmaker",
        "create_engine",
        "commit",
        "flush",
        "execute",
        "insert",
        "upsert",
        "persist",
    }
    leaked = forbidden & identifiers
    assert not leaked, f"parser core must not reference persistence: {sorted(leaked)}"


def test_parse_record_returns_pure_result_without_a_db_handle():
    # The module exposes no database/session handle, and a parse runs fully
    # from the injected client with no persistence side effect available.
    assert not hasattr(service_module, "db")
    assert not hasattr(service_module, "Session")
    assert not hasattr(service_module, "engine")
    record = parse_record("O+ve 2 units every 30 days", None, MockLlmClient())
    assert isinstance(record, ParsedRecord)


# --------------------------------------------------------------------------- #
# Default threshold wiring (Req 5.3)
# --------------------------------------------------------------------------- #
def test_parse_record_defaults_to_configured_review_threshold():
    # When no threshold is passed, the configured 0.75 review threshold applies:
    # a value below 0.75 is flagged low_confidence.
    expected_threshold = get_settings().parse_review_threshold
    payload = _patient_payload()
    payload["field_confidence"]["patient.cadence_days"] = expected_threshold - 0.01
    record = parse_record("anything", None, _StaticClient(payload))
    by_path = {f.field_path: f.reason for f in record.review_flags}
    assert by_path.get("patient.cadence_days") == "low_confidence"
