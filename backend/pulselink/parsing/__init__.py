"""LLM Parsing Service package.

Task 3.1 establishes the pluggable ``LlmClient`` seam, its mock/Bedrock
adapters, the shared ``PARSE_SCHEMA``, and the schema-validation helper. The
parser core (prompt, normalization, confidence, review flags) is added in
Task 3.2 and builds on these exports.
"""

from pulselink.parsing.llm_client import (
    BedrockClaudeClient,
    LlmClient,
    MockLlmClient,
    SchemaValidationError,
    get_llm_client,
    validate_against_schema,
)
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
