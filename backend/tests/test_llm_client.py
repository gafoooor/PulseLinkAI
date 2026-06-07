"""Unit tests for the LlmClient seam, adapters, and schema validation (Task 3.1).

Covers (Requirements 12.3, 5.1):
* MockLlmClient returns schema-valid output, deterministically and offline.
* validate_against_schema rejects malformed output.
* get_llm_client returns MockLlmClient under the default/mock config.
* BedrockClaudeClient imports cleanly and only touches boto3 when invoked.

No network calls are made anywhere in this module.
"""

from __future__ import annotations

import pytest

from pulselink.common.config import (
    EventBusProvider,
    LlmProvider,
    MessageChannelProvider,
    Settings,
    VoiceProvider,
)
from pulselink.parsing.llm_client import (
    BedrockClaudeClient,
    LlmClient,
    MockLlmClient,
    SchemaValidationError,
    get_llm_client,
    validate_against_schema,
)
from pulselink.parsing.schema import PARSE_SCHEMA


def _settings(provider: LlmProvider) -> Settings:
    """Build a Settings instance with a chosen LLM provider (others defaulted)."""
    return Settings(
        llm_provider=provider,
        message_channel=MessageChannelProvider.MOCK,
        voice_provider=VoiceProvider.MOCK,
        event_bus=EventBusProvider.MEMORY,
        default_lang="en",
        parse_review_threshold=0.75,
        database_url="postgresql://localhost/test",
        contact_encryption_key="x" * 32,
        dataset_csv_path="./Dataset.csv",
        bedrock_model_id="anthropic.claude-3-haiku-20240307-v1:0",
        aws_region="us-east-1",
    )


# --------------------------------------------------------------------------- #
# MockLlmClient
# --------------------------------------------------------------------------- #
def test_mock_client_satisfies_protocol():
    assert isinstance(MockLlmClient(), LlmClient)


def test_mock_client_returns_schema_valid_output():
    client = MockLlmClient()
    resp = client.complete_structured(
        system="sys",
        user="Patient needs B+ve blood, 2 units, every 21 days. last 2024-01-05",
        json_schema=PARSE_SCHEMA,
        temperature=0,
        max_output_tokens=800,
    )
    # Required keys present and correctly typed.
    assert isinstance(resp["donors"], list)
    assert isinstance(resp["field_confidence"], dict)
    # Re-validate explicitly (the call already validates internally).
    validate_against_schema(resp, PARSE_SCHEMA)


def test_mock_client_extracts_expected_patient_fields():
    client = MockLlmClient()
    resp = client.complete_structured(
        system="sys",
        user="Patient needs B+ve blood, 2 units, every 21 days. last 2024-01-05",
        json_schema=PARSE_SCHEMA,
        temperature=0,
        max_output_tokens=800,
    )
    patient = resp["patient"]
    assert patient["blood_group"] == "B Positive"
    assert patient["cadence_days"] == 21
    assert patient["quantity_required"] == 2
    assert patient["last_transfusion_date"] == "2024-01-05"


def test_mock_client_is_deterministic_at_temperature_zero():
    client = MockLlmClient()
    text = "O-ve donor 9876543210 needed every 30 days"
    first = client.complete_structured(
        system="s", user=text, json_schema=PARSE_SCHEMA,
        temperature=0, max_output_tokens=800,
    )
    second = client.complete_structured(
        system="s", user=text, json_schema=PARSE_SCHEMA,
        temperature=0, max_output_tokens=800,
    )
    assert first == second


def test_mock_client_every_leaf_confidence_in_unit_interval():
    client = MockLlmClient()
    resp = client.complete_structured(
        system="s",
        user="messy text A negative 5 units 1234567890",
        json_schema=PARSE_SCHEMA,
        temperature=0,
        max_output_tokens=800,
    )
    for path, conf in resp["field_confidence"].items():
        assert 0.0 <= conf <= 1.0, f"{path} confidence out of range: {conf}"


def test_mock_client_handles_empty_text_with_valid_output():
    client = MockLlmClient()
    resp = client.complete_structured(
        system="s", user="", json_schema=PARSE_SCHEMA,
        temperature=0, max_output_tokens=800,
    )
    assert resp["donors"] == []
    validate_against_schema(resp, PARSE_SCHEMA)


# --------------------------------------------------------------------------- #
# validate_against_schema
# --------------------------------------------------------------------------- #
def test_validate_accepts_well_formed_record():
    record = {"patient": None, "donors": [], "field_confidence": {}}
    assert validate_against_schema(record, PARSE_SCHEMA) is record


def test_validate_rejects_missing_required_key():
    with pytest.raises(SchemaValidationError):
        validate_against_schema({"donors": []}, PARSE_SCHEMA)  # no field_confidence


def test_validate_rejects_wrong_type_for_donors():
    bad = {"donors": "not-a-list", "field_confidence": {}}
    with pytest.raises(SchemaValidationError):
        validate_against_schema(bad, PARSE_SCHEMA)


def test_validate_rejects_non_object_response():
    with pytest.raises(SchemaValidationError):
        validate_against_schema(["not", "an", "object"], PARSE_SCHEMA)  # type: ignore[arg-type]


def test_validate_rejects_bad_nested_donor_item():
    bad = {"donors": ["should-be-object"], "field_confidence": {}}
    with pytest.raises(SchemaValidationError):
        validate_against_schema(bad, PARSE_SCHEMA)


def test_validate_rejects_bool_as_integer():
    # booleans must not satisfy an integer-typed field.
    bad = {
        "patient": {"cadence_days": True},
        "donors": [],
        "field_confidence": {},
    }
    with pytest.raises(SchemaValidationError):
        validate_against_schema(bad, PARSE_SCHEMA)


# --------------------------------------------------------------------------- #
# get_llm_client factory
# --------------------------------------------------------------------------- #
def test_factory_returns_mock_under_default_config():
    client = get_llm_client(_settings(LlmProvider.MOCK))
    assert isinstance(client, MockLlmClient)


def test_factory_returns_bedrock_under_bedrock_config():
    client = get_llm_client(_settings(LlmProvider.BEDROCK))
    assert isinstance(client, BedrockClaudeClient)
    assert client.model_id == "anthropic.claude-3-haiku-20240307-v1:0"
    assert client.region == "us-east-1"


# --------------------------------------------------------------------------- #
# BedrockClaudeClient (structured, not live-tested)
# --------------------------------------------------------------------------- #
def test_bedrock_client_constructs_without_credentials():
    # Constructing must not require boto3 or credentials.
    client = BedrockClaudeClient(model_id="m", region="us-east-1")
    assert client.model_id == "m"


def test_bedrock_client_uses_injected_client_and_validates():
    """A fake bedrock-runtime client exercises the structured-output path offline."""

    class _FakeBody:
        def read(self) -> bytes:
            import json

            return json.dumps(
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": BedrockClaudeClient._TOOL_NAME,
                            "input": {
                                "patient": None,
                                "donors": [],
                                "field_confidence": {"patient.blood_group": 0.5},
                            },
                        }
                    ]
                }
            ).encode()

    class _FakeClient:
        def invoke_model(self, modelId, body):  # noqa: N803 - boto3 naming
            return {"body": _FakeBody()}

    client = BedrockClaudeClient(
        model_id="m", region="us-east-1", client=_FakeClient()
    )
    resp = client.complete_structured(
        system="s", user="u", json_schema=PARSE_SCHEMA,
        temperature=0, max_output_tokens=800,
    )
    assert resp["donors"] == []
    assert resp["field_confidence"]["patient.blood_group"] == 0.5


def test_bedrock_client_rejects_response_without_tool_use():
    class _FakeBody:
        def read(self) -> bytes:
            import json

            return json.dumps({"content": [{"type": "text", "text": "hi"}]}).encode()

    class _FakeClient:
        def invoke_model(self, modelId, body):  # noqa: N803
            return {"body": _FakeBody()}

    client = BedrockClaudeClient(
        model_id="m", region="us-east-1", client=_FakeClient()
    )
    with pytest.raises(SchemaValidationError):
        client.complete_structured(
            system="s", user="u", json_schema=PARSE_SCHEMA,
            temperature=0, max_output_tokens=800,
        )
