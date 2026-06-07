"""The pluggable ``LlmClient`` seam and its adapters (Task 3.1).

PulseLink calls every LLM through a single narrow interface so the provider can
be swapped by configuration without touching business logic (Requirement 12.3):

* :class:`LlmClient` — the structural ``Protocol`` every provider implements.
* :class:`MockLlmClient` — deterministic, fully offline; drives the demo and
  property tests. Returns schema-valid structured JSON with no network access.
* :class:`BedrockClaudeClient` — production adapter for Amazon Bedrock
  (Anthropic Claude Haiku) using the Messages API with tool/JSON-schema
  enforced structured output.

Every provider validates its own response against the supplied JSON schema via
:func:`validate_against_schema` before returning, so callers can trust that a
returned dict is schema-valid regardless of which provider produced it
(Requirement 5.1).

The parser core (Task 3.2) builds on this seam; this module only establishes
the seam, the schema-validation helper, and the provider factory.

Requirements: 12.3, 5.1
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Optional, Protocol, runtime_checkable

from pulselink.common.config import LlmProvider, Settings, get_settings
from pulselink.parsing.schema import PARSE_SCHEMA

__all__ = [
    "LlmClient",
    "MockLlmClient",
    "BedrockClaudeClient",
    "SchemaValidationError",
    "validate_against_schema",
    "get_llm_client",
    "PARSE_SCHEMA",
]


# --------------------------------------------------------------------------- #
# The seam
# --------------------------------------------------------------------------- #
@runtime_checkable
class LlmClient(Protocol):
    """The single interface through which services reach the configured LLM.

    Providers are constrained to *structured* completion only: the model never
    returns free prose, only JSON validated against ``json_schema``.
    """

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        temperature: float,
        max_output_tokens: int,
    ) -> dict:
        """Return a dict that is valid against ``json_schema``."""
        ...


# --------------------------------------------------------------------------- #
# Shared JSON-schema validation (applied by every provider)
# --------------------------------------------------------------------------- #
class SchemaValidationError(ValueError):
    """Raised when an LLM response does not satisfy the fixed JSON schema."""


# Map JSON-schema primitive type names to Python type checks. ``bool`` is
# deliberately excluded from the numeric types because ``True``/``False`` are
# ``int`` subclasses in Python and must not pass as integers/numbers.
def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    # Unknown type keywords are treated permissively.
    return True


def _validate_node(value: Any, schema: dict, path: str) -> None:
    """Recursively validate ``value`` against a (subset) JSON ``schema``."""
    expected = schema.get("type")
    if expected is not None:
        type_names = [expected] if isinstance(expected, str) else list(expected)
        if not any(_matches_type(value, t) for t in type_names):
            raise SchemaValidationError(
                f"{path or '<root>'}: expected type {type_names}, "
                f"got {type(value).__name__}"
            )

    # Object: check required keys and recurse into declared properties.
    if isinstance(value, dict):
        for required_key in schema.get("required", []):
            if required_key not in value:
                raise SchemaValidationError(
                    f"{path or '<root>'}: missing required property "
                    f"{required_key!r}"
                )
        properties = schema.get("properties", {})
        for key, sub_schema in properties.items():
            if key in value:
                child_path = f"{path}.{key}" if path else key
                _validate_node(value[key], sub_schema, child_path)

    # Array: recurse into each item against the items schema.
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_node(item, item_schema, f"{path}[{index}]")


def validate_against_schema(resp: dict, schema: dict) -> dict:
    """Validate ``resp`` against ``schema`` and return it unchanged.

    Uses the ``jsonschema`` library when it is installed (production), and
    otherwise falls back to a minimal built-in validator that covers the subset
    of JSON-schema features PulseLink relies on (types, ``required``,
    ``properties``, ``items``). Raises :class:`SchemaValidationError` on any
    violation so no unvalidated model output ever reaches business logic.
    """
    if not isinstance(resp, dict):
        raise SchemaValidationError(
            f"LLM response must be a JSON object, got {type(resp).__name__}"
        )

    try:  # Prefer the full validator when available.
        import jsonschema  # type: ignore
    except Exception:  # pragma: no cover - exercised only when lib is absent
        jsonschema = None

    if jsonschema is not None:
        try:
            jsonschema.validate(instance=resp, schema=schema)
        except jsonschema.ValidationError as exc:  # type: ignore[attr-defined]
            raise SchemaValidationError(str(exc)) from exc
        return resp

    _validate_node(resp, schema, path="")
    return resp


# --------------------------------------------------------------------------- #
# Deterministic offline provider
# --------------------------------------------------------------------------- #
# Canonical blood-group normalization for the mock's rules-based extractor.
_BLOOD_GROUP_PATTERN = re.compile(
    r"\b(AB|A|B|O)\s*(positive|negative|pos|neg|\+ve|-ve|\+|-)(?!\w)",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\s-]?){10,13}")
_ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DMY_DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_NATURAL_DATE_PATTERN = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\b",
    re.IGNORECASE,
)
_DAYS_AGO_PATTERN = re.compile(r"\b(\d{1,3})\s*days?\s*ago\b", re.IGNORECASE)
_CADENCE_PATTERN = re.compile(r"(?:every|in)\s+(\d{1,3})\s*days?", re.IGNORECASE)
_QUANTITY_PATTERN = re.compile(r"(\d{1,2})\s*units?\b", re.IGNORECASE)

_POSITIVE_TOKENS = {"positive", "pos", "+ve", "+"}

_MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _normalize_blood_group(abo: str, sign: str) -> str:
    rh = "Positive" if sign.lower() in _POSITIVE_TOKENS else "Negative"
    return f"{abo.upper()} {rh}"


class MockLlmClient:
    """A deterministic, offline ``LlmClient`` for the demo and tests.

    It runs a small rules-based extractor over the user text and returns
    schema-valid structured JSON. The output is a pure function of the input
    text and schema (independent of ``temperature``), so the same input always
    yields the same result — and that result always passes
    :func:`validate_against_schema`.
    """

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        temperature: float,
        max_output_tokens: int,
    ) -> dict:
        resp = self._extract(user or "")
        # Enforce JSON-schema validation regardless of provider (Req 5.1).
        return validate_against_schema(resp, json_schema)

    @staticmethod
    def _extract(text: str) -> dict:
        field_confidence: dict[str, float] = {}

        # --- Patient fields ---------------------------------------------- #
        patient_blood = MockLlmClient._first_blood_group(text)
        field_confidence["patient.blood_group"] = 0.92 if patient_blood else 0.2

        cadence_match = _CADENCE_PATTERN.search(text)
        cadence_days = int(cadence_match.group(1)) if cadence_match else None
        field_confidence["patient.cadence_days"] = 0.9 if cadence_days else 0.2

        last_transfusion = MockLlmClient._parse_any_date(text)
        field_confidence["patient.last_transfusion_date"] = (
            0.9 if last_transfusion else 0.2
        )

        quantity_match = _QUANTITY_PATTERN.search(text)
        quantity_required = int(quantity_match.group(1)) if quantity_match else None
        field_confidence["patient.quantity_required"] = 0.85 if quantity_required else 0.2

        patient = {
            "blood_group": patient_blood,
            "cadence_days": cadence_days,
            "last_transfusion_date": last_transfusion,
            "quantity_required": quantity_required,
        }

        # --- Donor field (single best-effort donor from a phone hint) ----- #
        donors: list[dict] = []
        phone_match = _PHONE_PATTERN.search(text)
        if phone_match:
            phone_raw = phone_match.group(0).strip()
            donor = {
                "name_raw": None,
                "blood_group": patient_blood,
                "phone_raw": phone_raw,
                "preferred_lang": None,
            }
            donors.append(donor)
            field_confidence["donors[0].phone_raw"] = 0.8
            field_confidence["donors[0].blood_group"] = 0.6 if patient_blood else 0.2
            field_confidence["donors[0].name_raw"] = 0.2
            field_confidence["donors[0].preferred_lang"] = 0.2

        return {
            "patient": patient,
            "donors": donors,
            "field_confidence": field_confidence,
        }

    @staticmethod
    def _parse_any_date(text: str) -> Optional[str]:
        """Try multiple date formats; return ISO string or None."""
        from datetime import date, timedelta

        # ISO: 2025-01-24
        m = _ISO_DATE_PATTERN.search(text)
        if m:
            return MockLlmClient._safe_iso(m.group(1))

        # Natural: "24th January 2025", "24 Jan 2025", etc.
        m = _NATURAL_DATE_PATTERN.search(text)
        if m:
            day, month_str, year = m.group(1), m.group(2), m.group(3)
            month = _MONTH_MAP.get(month_str.lower())
            if month:
                try:
                    return date(int(year), month, int(day)).isoformat()
                except ValueError:
                    pass

        # DD-MM-YYYY or DD/MM/YYYY
        m = _DMY_DATE_PATTERN.search(text)
        if m:
            return MockLlmClient._safe_iso(f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}")

        # Relative: "12 days ago"
        m = _DAYS_AGO_PATTERN.search(text)
        if m:
            return (date.today() - timedelta(days=int(m.group(1)))).isoformat()

        return None

    @staticmethod
    def _first_blood_group(text: str) -> Optional[str]:
        match = _BLOOD_GROUP_PATTERN.search(text)
        if not match:
            return None
        return _normalize_blood_group(match.group(1), match.group(2))

    @staticmethod
    def _safe_iso(raw: str) -> Optional[str]:
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return None


# --------------------------------------------------------------------------- #
# Production provider: Amazon Bedrock / Anthropic Claude Haiku
# --------------------------------------------------------------------------- #
class BedrockClaudeClient:
    """Production ``LlmClient`` backed by Amazon Bedrock (Claude Haiku).

    The ``boto3`` import is deferred to call time so this module imports cleanly
    in environments without ``boto3`` or AWS credentials (e.g. the offline demo
    and the test suite). A clear error is raised only when the client is
    actually invoked without ``boto3`` available.

    Structured output is enforced via the Anthropic Messages API ``tools``
    feature: the JSON schema is supplied as a tool's ``input_schema`` and the
    model is forced to call that tool, so the response is guaranteed parseable
    and is then re-validated locally (Req 5.1).
    """

    _TOOL_NAME = "emit_parsed_record"

    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        client: Any | None = None,
        anthropic_version: str = "bedrock-2023-05-31",
    ) -> None:
        self.model_id = model_id
        self.region = region
        self._client = client  # allow injection (tests / reuse); else lazy-init
        self._anthropic_version = anthropic_version

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "BedrockClaudeClient requires the 'boto3' package and AWS "
                "credentials to make live calls. Install boto3 or set "
                "LLM_PROVIDER=mock for the offline demo."
            ) from exc
        self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        temperature: float,
        max_output_tokens: int,
    ) -> dict:
        client = self._get_client()

        body = {
            "anthropic_version": self._anthropic_version,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [
                {
                    "name": self._TOOL_NAME,
                    "description": (
                        "Return the extracted record as JSON matching the schema."
                    ),
                    "input_schema": json_schema,
                }
            ],
            # Force the model to emit structured output via the tool.
            "tool_choice": {"type": "tool", "name": self._TOOL_NAME},
        }

        response = client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(body),
        )
        parsed = self._extract_tool_input(response)
        # Enforce JSON-schema validation regardless of provider (Req 5.1).
        return validate_against_schema(parsed, json_schema)

    def _extract_tool_input(self, response: Any) -> dict:
        """Pull the tool-call JSON payload out of a Bedrock invoke_model reply."""
        raw = response["body"].read()
        payload = json.loads(raw)
        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == self._TOOL_NAME:
                tool_input = block.get("input")
                if isinstance(tool_input, dict):
                    return tool_input
                raise SchemaValidationError(
                    "Bedrock tool_use 'input' was not a JSON object"
                )
        raise SchemaValidationError(
            "Bedrock response contained no structured tool_use output"
        )


# --------------------------------------------------------------------------- #
# Provider factory
# --------------------------------------------------------------------------- #
def get_llm_client(settings: Optional[Settings] = None) -> LlmClient:
    """Return the configured ``LlmClient`` (mock by default for the demo).

    ``LLM_PROVIDER=mock`` (the default) yields a fully offline
    :class:`MockLlmClient`; ``LLM_PROVIDER=bedrock`` yields a
    :class:`BedrockClaudeClient` configured from settings. The Bedrock client
    makes no network call (and needs no credentials) until it is invoked.
    """
    settings = settings or get_settings()
    if settings.llm_provider is LlmProvider.BEDROCK:
        return BedrockClaudeClient(
            model_id=settings.bedrock_model_id,
            region=settings.aws_region,
        )
    return MockLlmClient()
