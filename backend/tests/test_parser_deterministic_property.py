"""Property-based test for deterministic LLM parsing (Task 3.3).

**Property 10: Deterministic parsing** — for any raw input text, the parser
(running over the deterministic offline ``MockLlmClient``) returns a result that
is schema-valid against :data:`PARSE_SCHEMA` and in which *every* leaf field
carries a confidence within the unit interval ``[0, 1]``. Parsing the same text
twice yields an identical result.

**Validates: Requirements 5.1**

This exercises the real ``parse_record`` + ``MockLlmClient`` pipeline (no mocks,
no network, no database) across a wide, intelligently constrained input space
covering blood-group tokens, phone numbers, ISO dates, cadence phrases,
multilingual / code-mixed text, garbage, and the empty string.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from pulselink.parsing.llm_client import (
    MockLlmClient,
    validate_against_schema,
)
from pulselink.parsing.schema import PARSE_SCHEMA
from pulselink.parsing.service import ParsedRecord, parse_record


# --------------------------------------------------------------------------- #
# Smart generators: build varied, realistic-but-messy raw records.
# --------------------------------------------------------------------------- #
# Blood-group tokens in the messy forms the extractor must tolerate.
_blood_tokens = st.sampled_from(
    ["B+ve", "B positive", "O-ve", "AB+", "a negative", "AB Negative",
     "o+", "A+ve", "purple", ""]
)
# Phone-like fragments (some valid 10-13 digit, some not).
_phone_tokens = st.sampled_from(
    ["9876543210", "+91 98765-43210", "+919876543210", "12345", ""]
)
# ISO and non-ISO date fragments.
_date_tokens = st.sampled_from(
    ["2024-01-05", "2023-12-31", "05/01/2024", "next Tuesday", ""]
)
# Cadence phrases.
_cadence_tokens = st.sampled_from(
    ["every 21 days", "every 28 days", "har 28 din", "every 1958 days", ""]
)
# Quantity phrases.
_quantity_tokens = st.sampled_from(["1 unit", "2 units", "3 units", ""])
# Multilingual / code-mixed / garbage filler.
_filler_tokens = st.sampled_from(
    ["rogi ko chahiye", "Telugu pref", "thanks", "patient needs blood",
     "డోనర్", "मरीज", "!!!???", "", "   "]
)


@st.composite
def _raw_texts(draw: st.DrawFn) -> str:
    """Assemble a messy record from a random selection of fragments.

    Fragments are shuffled and joined so blood-group tokens, phones, dates,
    cadence phrases, and multilingual/garbage text appear in varied orders —
    plus a non-trivial chance of the empty string.
    """
    parts = [
        draw(_blood_tokens),
        draw(_phone_tokens),
        draw(_date_tokens),
        draw(_cadence_tokens),
        draw(_quantity_tokens),
        draw(_filler_tokens),
    ]
    # Keep only some fragments so we also generate sparse / empty inputs.
    keep = draw(st.lists(st.booleans(), min_size=len(parts), max_size=len(parts)))
    chosen = [p for p, k in zip(parts, keep) if k and p]
    return " ".join(chosen)


# Either an assembled messy record or an arbitrary free-text string (incl. empty).
_inputs = st.one_of(_raw_texts(), st.text(max_size=120))


def _to_schema_dict(record: ParsedRecord) -> dict:
    """Reconstruct the raw structured dict the schema describes from a record."""
    return {
        "patient": record.patient,
        "donors": record.donors,
        "field_confidence": record.field_confidence,
    }


@given(raw_text=_inputs)
def test_parse_is_schema_valid_with_bounded_confidences(raw_text: str) -> None:
    """Property 10: output is schema-valid and every confidence is in [0, 1]."""
    record = parse_record(raw_text, None, MockLlmClient())

    # The reconstructed structured payload validates against the fixed schema
    # (raises SchemaValidationError otherwise). This is the schema-validity half
    # of the property (Req 5.1).
    validate_against_schema(_to_schema_dict(record), PARSE_SCHEMA)

    # Every leaf field carries a confidence within the unit interval.
    assert isinstance(record.field_confidence, dict)
    for path, conf in record.field_confidence.items():
        assert isinstance(conf, (int, float)) and not isinstance(conf, bool)
        assert 0.0 <= conf <= 1.0, f"{path} confidence out of range: {conf}"


@given(raw_text=_inputs)
def test_parse_is_deterministic(raw_text: str) -> None:
    """Property 10 (determinism): same input always yields the same result."""
    first = parse_record(raw_text, None, MockLlmClient())
    second = parse_record(raw_text, None, MockLlmClient())
    assert first.model_dump() == second.model_dump()
