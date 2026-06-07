"""The LLM parser core (Task 3.2).

This module turns one messy, multilingual, WhatsApp-style record into a clean,
validated, *structured* :class:`ParsedRecord` — the meaningful AI use at the
heart of PulseLink. It builds on the pluggable ``LlmClient`` seam (Task 3.1):

Pipeline (design §4.1, Component 1):

1. Call ``llm.complete_structured`` with the fixed :data:`PARSE_SCHEMA`, the
   strict few-shot system prompt, and ``temperature=0`` for deterministic
   extraction.
2. Defensively re-validate the response against the schema (the client already
   validates, but the parser never trusts raw model output) — Req 5.1.
3. :func:`normalize` the record: dates to ISO 8601 and blood groups to the
   canonical eight-value set; values that cannot be mapped become ``null``
   rather than invented — Req 5.4, 5.5.
4. :func:`build_review_flags` flags every leaf field whose confidence is below
   the review threshold (default 0.75) and every ``null`` leaf field for human
   review — Req 5.2, 5.3, 5.4.
5. On a schema-validation failure that survives **one** retry, return an empty
   :class:`ParsedRecord` carrying a ``parse_failed`` flag so the coordinator can
   enter data manually — Req 5.7.

Privacy guarantee (Req 5.6): this module performs **no** persistence. It has no
database session, no ORM import, and never writes to the operational store. The
``ParsedRecord`` it returns is reviewed and confirmed by a coordinator before
anything is saved — that save happens elsewhere.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from pulselink.common.config import get_settings
from pulselink.common.enums import BloodGroup
from pulselink.parsing.llm_client import (
    LlmClient,
    SchemaValidationError,
    validate_against_schema,
)
from pulselink.parsing.schema import PARSE_SCHEMA

__all__ = [
    "ParseRequest",
    "ReviewFlag",
    "ParsedRecord",
    "LlmParsingService",
    "parse_record",
    "normalize",
    "build_review_flags",
    "SYSTEM_PROMPT",
    "MODEL_VERSION",
]

# Bumped whenever the prompt or normalization contract changes; surfaced on
# every ``ParsedRecord`` for auditability.
MODEL_VERSION = "pulselink-parser-1.0"

# Default temperature / token budget for deterministic structured extraction.
_TEMPERATURE = 0.0
_MAX_OUTPUT_TOKENS = 800


# --------------------------------------------------------------------------- #
# Prompt (design §4.1)
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are a strict medical-logistics data extraction engine.
Extract patient and donor fields from a messy WhatsApp message.
Rules:
- Output ONLY JSON valid against the provided schema.
- Never invent a value the text does not support; use null and lower its confidence.
- Normalize dates to ISO 8601 and blood groups to the canonical set
  (e.g. 'B+ve' -> 'B Positive').
- Preserve names in their original language/script.
- For every leaf field, emit a confidence in field_confidence (0..1)."""

# Three to five messy, multilingual, code-mixed examples paired with ideal JSON
# (Telugu/Hindi/English mixing, abbreviations like "B+ve", relative dates). These
# are appended to the system prompt so any structured-output provider sees the
# same few-shot grounding. The MockLlmClient ignores them (it is rules-based and
# deterministic); the Bedrock provider uses them to anchor extraction.
FEW_SHOT_EXAMPLES = """
Examples (input -> ideal JSON):

INPUT: "Patient B+ve, 2 units every 21 days, last transfusion 2024-01-05"
JSON: {"patient": {"blood_group": "B Positive", "cadence_days": 21,
"last_transfusion_date": "2024-01-05", "quantity_required": 2},
"donors": [], "field_confidence": {"patient.blood_group": 0.97,
"patient.cadence_days": 0.95, "patient.last_transfusion_date": 0.95,
"patient.quantity_required": 0.93}}

INPUT: "Donor Ramesh O negative ph 9876543210, Telugu pref"
JSON: {"patient": null, "donors": [{"name_raw": "Ramesh",
"blood_group": "O Negative", "phone_raw": "9876543210",
"preferred_lang": "te"}], "field_confidence": {"donors[0].name_raw": 0.9,
"donors[0].blood_group": 0.96, "donors[0].phone_raw": 0.95,
"donors[0].preferred_lang": 0.8}}

INPUT: "rogi ko AB+ chahiye, har 28 din me 1 unit"
JSON: {"patient": {"blood_group": "AB Positive", "cadence_days": 28,
"last_transfusion_date": null, "quantity_required": 1}, "donors": [],
"field_confidence": {"patient.blood_group": 0.92, "patient.cadence_days": 0.9,
"patient.last_transfusion_date": 0.1, "patient.quantity_required": 0.88}}

INPUT: "thanks"
JSON: {"patient": null, "donors": [], "field_confidence": {}}
"""

# A stricter reminder appended on the single retry after a schema failure.
_RETRY_REMINDER = (
    "\n\nIMPORTANT: Your previous output failed schema validation. "
    "Return ONLY a JSON object valid against the provided schema, with the "
    "required keys 'donors' (array) and 'field_confidence' (object). Do not "
    "include any prose."
)


def _full_system_prompt(retry: bool = False) -> str:
    prompt = SYSTEM_PROMPT + "\n" + FEW_SHOT_EXAMPLES
    if retry:
        prompt += _RETRY_REMINDER
    return prompt


# --------------------------------------------------------------------------- #
# Result shape (design Component 1 interface)
# --------------------------------------------------------------------------- #
ReviewReason = Literal["low_confidence", "ambiguous", "missing", "conflicting"]


class ReviewFlag(BaseModel):
    """A single leaf field routed to a human for review."""

    field_path: str = Field(min_length=1)
    reason: ReviewReason


class ParsedRecord(BaseModel):
    """The validated, normalized extraction result for one raw record.

    ``patient`` and each donor are *partial* (only the fields the parser
    targets), so they are plain dicts rather than full domain models — the
    coordinator completes and confirms them before persistence (Req 5.6).
    """

    patient: Optional[dict] = None
    donors: list[dict] = Field(default_factory=list)
    field_confidence: dict[str, float] = Field(default_factory=dict)
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    model_version: str = MODEL_VERSION
    parse_failed: bool = False


class ParseRequest(BaseModel):
    """Input to the parser: the raw text plus optional language/city context."""

    raw_text: str
    source_lang: Optional[str] = None
    city_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# Normalization helpers (Req 5.5)
# --------------------------------------------------------------------------- #
# ABO group + Rh sign anywhere in a value, tolerating "B+ve", "B+", "B Positive".
_BLOOD_GROUP_RE = re.compile(
    r"(AB|A|B|O)\s*(positive|negative|pos|neg|\+ve|-ve|\+|-)",
    re.IGNORECASE,
)
_POSITIVE_SIGNS = {"positive", "pos", "+ve", "+"}

# Leaf field names per section, used to walk the record for flags.
_PATIENT_LEAVES = (
    "blood_group",
    "cadence_days",
    "last_transfusion_date",
    "quantity_required",
)
_DONOR_LEAVES = ("name_raw", "blood_group", "phone_raw", "preferred_lang")

_VALID_BLOOD_GROUPS = {bg.value for bg in BloodGroup}


def normalize_blood_group(raw: object) -> Optional[str]:
    """Map a messy blood-group token to a canonical value, else ``None``.

    Returns one of the eight :class:`BloodGroup` values (e.g. ``"B Positive"``)
    or ``None`` when the input cannot be mapped — the caller then treats the
    field as unsupported (null + flag) rather than inventing a value (Req 5.4).
    """
    if not isinstance(raw, str):
        return None
    match = _BLOOD_GROUP_RE.search(raw)
    if not match:
        return None
    abo = match.group(1).upper()
    sign = match.group(2).lower()
    rh = "Positive" if sign in _POSITIVE_SIGNS else "Negative"
    canonical = f"{abo} {rh}"
    return canonical if canonical in _VALID_BLOOD_GROUPS else None


def normalize_date(raw: object) -> Optional[str]:
    """Convert a date value to an ISO-8601 ``YYYY-MM-DD`` string, else ``None``.

    Handles ISO dates, common datetime forms, and a few day/month/year layouts.
    Unparseable values (including unresolved relative dates) return ``None`` so
    the field is flagged rather than guessed (Req 5.4, 5.5).
    """
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        # ISO date fast path.
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%m/%d/%Y",
            "%d %b %Y",
            "%d %B %Y",
        ):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue
        # Last resort: leading date token of a datetime string.
        token = value.split(" ", 1)[0].split("T", 1)[0]
        try:
            return date.fromisoformat(token).isoformat()
        except ValueError:
            return None
    return None


def normalize_phone(raw: object) -> Optional[str]:
    """Light phone normalization: keep digits and a leading ``+``; else ``None``.

    Phone normalization is best-effort (design notes it as optional). The raw
    contact value is never persisted by the parser — it is handed to the
    coordinator for review and consent-gated storage downstream (Req 5.6).
    """
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    has_plus = stripped.startswith("+")
    digits = re.sub(r"\D", "", stripped)
    if not digits:
        return None
    return ("+" + digits) if has_plus else digits


def normalize(parsed: dict) -> dict:
    """Canonicalize dates, blood groups, and phones in a validated record.

    Mutates and returns ``parsed``. Any value that cannot be canonicalized is
    set to ``None`` so it is flagged for review rather than persisted in a
    non-canonical form (Req 5.4, 5.5).
    """
    patient = parsed.get("patient")
    if isinstance(patient, dict):
        if "blood_group" in patient:
            patient["blood_group"] = normalize_blood_group(patient.get("blood_group"))
        if "last_transfusion_date" in patient:
            patient["last_transfusion_date"] = normalize_date(
                patient.get("last_transfusion_date")
            )

    donors = parsed.get("donors")
    if isinstance(donors, list):
        for donor in donors:
            if not isinstance(donor, dict):
                continue
            if "blood_group" in donor:
                donor["blood_group"] = normalize_blood_group(donor.get("blood_group"))
            if "phone_raw" in donor:
                donor["phone_raw"] = normalize_phone(donor.get("phone_raw"))

    return parsed


# --------------------------------------------------------------------------- #
# Review flags (Req 5.3, 5.4)
# --------------------------------------------------------------------------- #
def _null_leaf_paths(parsed: dict) -> set[str]:
    """Dotted paths of leaf fields whose value is ``None`` (missing)."""
    paths: set[str] = set()
    patient = parsed.get("patient")
    if isinstance(patient, dict):
        for leaf in _PATIENT_LEAVES:
            if patient.get(leaf) is None:
                paths.add(f"patient.{leaf}")
    donors = parsed.get("donors")
    if isinstance(donors, list):
        for index, donor in enumerate(donors):
            if isinstance(donor, dict):
                for leaf in _DONOR_LEAVES:
                    if donor.get(leaf) is None:
                        paths.add(f"donors[{index}].{leaf}")
    return paths


def build_review_flags(parsed: dict, threshold: float) -> list[ReviewFlag]:
    """Flag low-confidence and missing (null) leaf fields for human review.

    * Any leaf field with a confidence below ``threshold`` is flagged
      ``low_confidence`` (Req 5.3).
    * Any null leaf field is flagged ``missing`` so an unsupported value is
      surfaced rather than silently accepted (Req 5.4). ``missing`` takes
      precedence when both would apply for the same path.
    """
    reason_by_path: dict[str, ReviewReason] = {}

    confidences = parsed.get("field_confidence", {})
    if isinstance(confidences, dict):
        for path, conf in confidences.items():
            try:
                if float(conf) < threshold:
                    reason_by_path[path] = "low_confidence"
            except (TypeError, ValueError):
                # A non-numeric confidence is itself a review-worthy ambiguity.
                reason_by_path[path] = "ambiguous"

    # Null leaf fields are always flagged; "missing" overrides "low_confidence".
    for path in _null_leaf_paths(parsed):
        reason_by_path[path] = "missing"

    return [
        ReviewFlag(field_path=path, reason=reason)
        for path, reason in sorted(reason_by_path.items())
    ]


# --------------------------------------------------------------------------- #
# Parser core
# --------------------------------------------------------------------------- #
def _empty_failed_record() -> ParsedRecord:
    """The empty record returned when parsing fails after one retry (Req 5.7)."""
    return ParsedRecord(
        patient=None,
        donors=[],
        field_confidence={},
        review_flags=[],
        model_version=MODEL_VERSION,
        parse_failed=True,
    )


def _attempt(llm: LlmClient, raw_text: str, *, retry: bool) -> dict:
    """One structured-output call + defensive schema re-validation.

    Raises :class:`SchemaValidationError` if the client (or the defensive
    re-validation) rejects the output.
    """
    resp = llm.complete_structured(
        system=_full_system_prompt(retry=retry),
        user=raw_text,
        json_schema=PARSE_SCHEMA,
        temperature=_TEMPERATURE,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
    )
    # The parser never trusts raw model text: re-validate before use (Req 5.1).
    return validate_against_schema(resp, PARSE_SCHEMA)


def parse_record(
    raw_text: str,
    source_lang: Optional[str],
    llm: LlmClient,
    *,
    threshold: Optional[float] = None,
) -> ParsedRecord:
    """Parse one messy record into a validated, normalized :class:`ParsedRecord`.

    ``source_lang`` is an optional hint preserved for the caller; the LLM is
    multilingual and does not require it. ``threshold`` defaults to the
    configured ``parse_review_threshold`` (0.75).

    This function performs NO persistence (Req 5.6): it only returns the
    structured result for coordinator review.
    """
    if threshold is None:
        threshold = get_settings().parse_review_threshold

    # Schema-valid extraction with exactly one retry on failure (Req 5.7).
    try:
        parsed = _attempt(llm, raw_text, retry=False)
    except SchemaValidationError:
        try:
            parsed = _attempt(llm, raw_text, retry=True)
        except SchemaValidationError:
            return _empty_failed_record()

    # Canonicalize, then route low-confidence / missing fields to review.
    parsed = normalize(parsed)
    review_flags = build_review_flags(parsed, threshold=threshold)

    return ParsedRecord(
        patient=parsed.get("patient"),
        donors=parsed.get("donors", []),
        field_confidence=parsed.get("field_confidence", {}),
        review_flags=review_flags,
        model_version=MODEL_VERSION,
        parse_failed=False,
    )


class LlmParsingService:
    """Object-oriented entry point matching the design's ``LlmParsingService``.

    Wraps :func:`parse_record` around a configured :class:`LlmClient`. Holds no
    database handle and writes nothing to the operational store (Req 5.6).
    """

    def __init__(self, llm: LlmClient, *, threshold: Optional[float] = None) -> None:
        self._llm = llm
        self._threshold = (
            threshold
            if threshold is not None
            else get_settings().parse_review_threshold
        )

    def parse(self, request: ParseRequest) -> ParsedRecord:
        return parse_record(
            request.raw_text,
            request.source_lang,
            self._llm,
            threshold=self._threshold,
        )
