"""Provider-selection configuration seam.

This module centralises the runtime switches that let PulseLink swap offline
mocks (used by the demo) for real cloud providers (used in production) WITHOUT
touching business logic:

* ``LLM_PROVIDER``      mock | bedrock                  (Requirement 12.3)
* ``MESSAGE_CHANNEL``   mock | sms | whatsapp | voice   (Requirement 12.2)
* ``VOICE_PROVIDER``    mock | connect                  (Requirements 12.2, 13.x)
* ``EVENT_BUS``         memory | eventbridge

The actual adapter implementations are provided in later tasks. This file only
establishes the seam and reads the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

try:  # Load a local .env if python-dotenv is installed (optional at scaffold time).
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional for scaffolding
    pass


class LlmProvider(str, Enum):
    """Pluggable LLM client provider (Requirement 12.3)."""

    MOCK = "mock"
    BEDROCK = "bedrock"


class MessageChannelProvider(str, Enum):
    """Pluggable message channel provider (Requirement 12.2)."""

    MOCK = "mock"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    VOICE = "voice"


class VoiceProvider(str, Enum):
    """Pluggable PulseLink Voice provider (Requirements 12.2, 13.x)."""

    MOCK = "mock"
    CONNECT = "connect"


class EventBusProvider(str, Enum):
    """Event-bus backend. In-memory for the demo, EventBridge in production."""

    MEMORY = "memory"
    EVENTBRIDGE = "eventbridge"


def _enum_from_env(env_var: str, enum_cls: type[Enum], default: Enum) -> Enum:
    raw = os.getenv(env_var)
    if raw is None or raw == "":
        return default
    try:
        return enum_cls(raw.strip().lower())
    except ValueError as exc:  # pragma: no cover - defensive
        valid = ", ".join(e.value for e in enum_cls)
        raise ValueError(
            f"Invalid {env_var}={raw!r}; expected one of: {valid}"
        ) from exc


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration, read once from the environment."""

    llm_provider: LlmProvider
    message_channel: MessageChannelProvider
    voice_provider: VoiceProvider
    event_bus: EventBusProvider
    default_lang: str
    parse_review_threshold: float
    database_url: str
    contact_encryption_key: str
    dataset_csv_path: str
    # Amazon Bedrock settings (used only when llm_provider == BEDROCK; the
    # offline demo uses the MockLlmClient and never reads these). These carry
    # sensible defaults so building Settings directly or via
    # ``dataclasses.replace(get_settings(), ...)`` never requires them. As the
    # trailing fields they may safely hold defaults without breaking ordering.
    bedrock_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"
    aws_region: str = "us-east-1"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_provider=_enum_from_env("LLM_PROVIDER", LlmProvider, LlmProvider.MOCK),
            message_channel=_enum_from_env(
                "MESSAGE_CHANNEL", MessageChannelProvider, MessageChannelProvider.MOCK
            ),
            voice_provider=_enum_from_env(
                "VOICE_PROVIDER", VoiceProvider, VoiceProvider.MOCK
            ),
            event_bus=_enum_from_env(
                "EVENT_BUS", EventBusProvider, EventBusProvider.MEMORY
            ),
            default_lang=os.getenv("DEFAULT_LANG", "en"),
            parse_review_threshold=float(os.getenv("PARSE_REVIEW_THRESHOLD", "0.75")),
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://pulselink:pulselink@localhost:5432/pulselink",
            ),
            contact_encryption_key=os.getenv(
                "CONTACT_ENCRYPTION_KEY", "change-me-demo-only-32byte-key!!"
            ),
            dataset_csv_path=os.getenv("DATASET_CSV_PATH", "./Dataset.csv"),
            # Default to Claude Haiku on Bedrock (recommended for cost); the
            # specific model id and region remain configurable.
            bedrock_model_id=os.getenv(
                "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
            ),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
        )


def get_settings() -> Settings:
    """Return runtime settings resolved from the environment."""

    return Settings.from_env()
