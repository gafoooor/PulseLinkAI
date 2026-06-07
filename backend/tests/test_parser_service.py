"""Unit tests for the LLM parser core (Task 3.2).

Exercises the full pipeline offline through the deterministic ``MockLlmClient``
and small injected clients — no network calls, no database (Req 5.6).

Covers (Requirements 5.1-5.7):
* a normal messy message yields normalized fields + per-leaf confidences;
* low-confidence and null fields are routed to review flags;
* dates and blood groups are normalized to ISO 8601 / the canonical set;
* a client returning schema-invalid output twice yields ``parse_failed``;
* a retry that succeeds is used (no parse_failed);
* the parser persists nothing (the module imports no DB layer).
"""

from __future__ import annotations

import inspect

import pytest

from pulselink.common.enums import BloodGroup
from pulselink.parsing.llm_client import MockLlmClient, SchemaValidationError
from pulselink.parsing import service as service_module
from pulselink.parsing.service import (
    LlmParsingService,
    ParsedRecord,
    ParseRequest,
    ReviewFlag,
    build_review_flags,
    normalize,
    normalize_blood_group,
    normalize_date,
    parse_record,
)


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class _StaticClient:
    """Returns a fixed dict (bypassing schema validation) for targeted tests."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    def complete_structured(self, **kwargs) -> dict:  # noqa: D401
        self.calls += 1
        return self._payload


class _AlwaysInvalidClient:
    """Raises SchemaValidationError on every call (simulates a bad model)."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_structured(self, **kwargs) -> dict:
        self.calls += 1
        raise SchemaValidationError("invalid output")


class _InvalidThenValidClient:
    """Fails the first call, succeeds on the retry."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    def complete_structured(self, **kwargs) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise SchemaValidationError("first attempt invalid")
        return self._payload


# --------------------------------------------------------------------------- #
# Normalizer units (Req 5.5)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("B+ve", "B Positive"),
        ("b positive", "B Positive"),
        ("O-ve", "O Negative"),
        ("AB+", "AB Positive"),
        ("a negative", "A Negative"),
        ("AB Negative", "AB Negative"),
    ],
)
def test_normalize_blood_group_canonicalizes(raw, expected):
    assert normalize_blood_group(raw) == expected
    assert expected in {bg.value for bg in BloodGroup}


@pytest.mark.parametrize("raw", ["", "unknown", None, 5, "Z+"])
def test_normalize_blood_group_returns_none_when_unmappable(raw):
    assert normalize_blood_group(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-01-05", "2024-01-05"),
        ("2024-01-05 13:30:00", "2024-01-05"),
        ("05/01/2024", "2024-01-05"),  # d/m/Y
        ("5 Jan 2024", "2024-01-05"),
    ],
)
def test_normalize_date_to_iso(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw", ["next Tuesday", "", None, "not-a-date"])
def test_normalize_date_returns_none_when_unparseable(raw):
    assert normalize_date(raw) is None


# --------------------------------------------------------------------------- #
# Happy path through the MockLlmClient (Req 5.1, 5.2, 5.5)
# --------------------------------------------------------------------------- #
def test_parse_record_normal_message_yields_normalized_fields():
    record = parse_record(
        "Patient needs B+ve blood, 2 units, every 21 days. last 2024-01-05",
        source_lang="en",
        llm=MockLlmClient(),
    )
    assert isinstance(record, ParsedRecord)
    assert record.parse_failed is False
    assert record.patient is not None
    # Blood group normalized to the canonical set.
    assert record.patient["blood_group"] == "B Positive"
    assert record.patient["cadence_days"] == 21
    assert record.patient["quantity_required"] == 2
    # Date already ISO 8601 and preserved.
    assert record.patient["last_transfusion_date"] == "2024-01-05"
    assert record.model_version == service_module.MODEL_VERSION


def test_parse_record_attaches_confidence_to_every_leaf_in_unit_interval():
    record = parse_record(
        "Patient needs B+ve blood, 2 units, every 21 days. last 2024-01-05",
        source_lang=None,
        llm=MockLlmClient(),
    )
    assert record.field_confidence  # non-empty
    for path, conf in record.field_confidence.items():
        assert 0.0 <= conf <= 1.0, f"{path} out of range: {conf}"


# --------------------------------------------------------------------------- #
# Review flags: low confidence + missing (Req 5.3, 5.4)
# --------------------------------------------------------------------------- #
def test_low_confidence_fields_get_review_flags():
    # The mock assigns low confidence (~0.2) to fields it cannot find.
    record = parse_record(
        "patient B positive",  # no cadence / quantity / date present
        source_lang=None,
        llm=MockLlmClient(),
    )
    flagged = {f.field_path for f in record.review_flags}
    # Unsupported patient fields are flagged for review.
    assert "patient.cadence_days" in flagged
    assert "patient.last_transfusion_date" in flagged
    assert "patient.quantity_required" in flagged
    # The confidently-extracted blood group is NOT flagged.
    assert "patient.blood_group" not in flagged


def test_null_fields_flagged_missing_not_invented():
    # Confidence high but value null -> still flagged (reason "missing").
    payload = {
        "patient": {
            "blood_group": "B Positive",
            "cadence_days": None,
            "last_transfusion_date": None,
            "quantity_required": 2,
        },
        "donors": [],
        "field_confidence": {
            "patient.blood_group": 0.95,
            "patient.cadence_days": 0.95,  # high, but value is null
            "patient.last_transfusion_date": 0.95,
            "patient.quantity_required": 0.95,
        },
    }
    record = parse_record("anything", None, _StaticClient(payload), threshold=0.75)
    by_path = {f.field_path: f.reason for f in record.review_flags}
    assert by_path.get("patient.cadence_days") == "missing"
    assert by_path.get("patient.last_transfusion_date") == "missing"
    # Non-null high-confidence fields are not flagged.
    assert "patient.blood_group" not in by_path
    assert "patient.quantity_required" not in by_path


def test_build_review_flags_threshold_boundary():
    parsed = {
        "patient": {"blood_group": "B Positive", "cadence_days": 21,
                    "last_transfusion_date": "2024-01-05", "quantity_required": 2},
        "donors": [],
        "field_confidence": {
            "patient.blood_group": 0.75,   # == threshold -> NOT flagged
            "patient.cadence_days": 0.74,  # < threshold -> flagged
            "patient.last_transfusion_date": 0.9,
            "patient.quantity_required": 0.9,
        },
    }
    flags = build_review_flags(parsed, threshold=0.75)
    by_path = {f.field_path: f.reason for f in flags}
    assert "patient.blood_group" not in by_path
    assert by_path.get("patient.cadence_days") == "low_confidence"


# --------------------------------------------------------------------------- #
# Normalization inside the pipeline (Req 5.5)
# --------------------------------------------------------------------------- #
def test_normalize_canonicalizes_messy_blood_group_and_date():
    payload = {
        "patient": {
            "blood_group": "ab -ve",
            "cadence_days": 30,
            "last_transfusion_date": "05/01/2024",
            "quantity_required": 1,
        },
        "donors": [{"name_raw": "Sita", "blood_group": "o+ve",
                    "phone_raw": "+91 98765-43210", "preferred_lang": "te"}],
        "field_confidence": {"patient.blood_group": 0.9},
    }
    record = parse_record("anything", None, _StaticClient(payload), threshold=0.75)
    assert record.patient["blood_group"] == "AB Negative"
    assert record.patient["last_transfusion_date"] == "2024-01-05"
    assert record.donors[0]["blood_group"] == "O Positive"
    assert record.donors[0]["phone_raw"] == "+919876543210"


def test_unmappable_blood_group_becomes_null_and_flagged():
    payload = {
        "patient": {"blood_group": "purple", "cadence_days": 21,
                    "last_transfusion_date": "2024-01-05", "quantity_required": 2},
        "donors": [],
        "field_confidence": {"patient.blood_group": 0.9},
    }
    record = parse_record("anything", None, _StaticClient(payload), threshold=0.75)
    assert record.patient["blood_group"] is None
    by_path = {f.field_path: f.reason for f in record.review_flags}
    assert by_path.get("patient.blood_group") == "missing"


# --------------------------------------------------------------------------- #
# Failure path + retry (Req 5.7)
# --------------------------------------------------------------------------- #
def test_schema_invalid_twice_yields_parse_failed_empty_record():
    client = _AlwaysInvalidClient()
    record = parse_record("hopeless input", None, client, threshold=0.75)
    assert record.parse_failed is True
    assert record.patient is None
    assert record.donors == []
    assert record.field_confidence == {}
    assert record.review_flags == []
    # Exactly one retry: two attempts total.
    assert client.calls == 2


def test_retry_succeeds_after_one_failure():
    payload = {
        "patient": {"blood_group": "B Positive", "cadence_days": 21,
                    "last_transfusion_date": "2024-01-05", "quantity_required": 2},
        "donors": [],
        "field_confidence": {"patient.blood_group": 0.95},
    }
    client = _InvalidThenValidClient(payload)
    record = parse_record("messy", None, client, threshold=0.75)
    assert record.parse_failed is False
    assert record.patient["blood_group"] == "B Positive"
    assert client.calls == 2


# --------------------------------------------------------------------------- #
# Service wrapper + no-persistence guarantee (Req 5.6)
# --------------------------------------------------------------------------- #
def test_service_parse_delegates_and_returns_record():
    service = LlmParsingService(MockLlmClient(), threshold=0.75)
    record = service.parse(
        ParseRequest(raw_text="O-ve donor 9876543210 every 30 days", city_id="hyd")
    )
    assert isinstance(record, ParsedRecord)
    assert record.parse_failed is False


def test_parser_module_performs_no_persistence():
    # Req 5.6: the parser core never touches the DB. Assert the module imports
    # no database/session/ORM layer and invokes no persistence call. Inspect the
    # executable lines (imports + calls), ignoring docstrings/comments which may
    # legitimately discuss the no-persistence guarantee in prose.
    code_lines = [
        line.split("#", 1)[0]
        for line in inspect.getsource(service_module).splitlines()
        if line.lstrip().startswith(("import ", "from "))
    ]
    code = "\n".join(code_lines)
    for forbidden in ("db_models", "db_session", "sqlalchemy", "common.db", "engine"):
        assert forbidden not in code, f"parser must not import {forbidden!r}"
    # The module exposes no database/session handle either.
    assert not hasattr(service_module, "db")
    assert not hasattr(service_module, "Session")


def test_parse_record_is_deterministic_at_temperature_zero():
    text = "Patient AB+ve 3 units every 28 days last 2024-02-10"
    first = parse_record(text, None, MockLlmClient(), threshold=0.75)
    second = parse_record(text, None, MockLlmClient(), threshold=0.75)
    assert first.model_dump() == second.model_dump()
