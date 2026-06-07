"""Fixed JSON schema for LLM parsing output (shared seam).

This is the single source of truth for the structured-output contract the
``LlmClient`` providers must satisfy. It mirrors the design document's
``PARSE_SCHEMA`` (design.md §4.1) exactly. The parser core (Task 3.2) imports
``PARSE_SCHEMA`` from here so the schema definition lives in one place.

The schema is intentionally minimal at the seam stage: it pins the top-level
shape (a nullable ``patient`` object, a ``donors`` array, and a
``field_confidence`` map) so every provider — mock or Bedrock — returns output
that passes the same validation.

Requirements: 5.1, 12.3
"""

from __future__ import annotations

# Fixed structured-output schema. Every LlmClient response is validated against
# this regardless of provider (see ``validate_against_schema``).
PARSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "patient": {
            "type": ["object", "null"],
            "properties": {
                "blood_group": {"type": ["string", "null"]},
                "cadence_days": {"type": ["integer", "null"]},
                "last_transfusion_date": {"type": ["string", "null"]},  # ISO 8601
                "quantity_required": {"type": ["integer", "null"]},
            },
        },
        "donors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name_raw": {"type": ["string", "null"]},
                    "blood_group": {"type": ["string", "null"]},
                    "phone_raw": {"type": ["string", "null"]},
                    "preferred_lang": {"type": ["string", "null"]},
                },
            },
        },
        # Dotted-path leaf field -> confidence in [0, 1].
        "field_confidence": {"type": "object"},
    },
    "required": ["donors", "field_confidence"],
}
