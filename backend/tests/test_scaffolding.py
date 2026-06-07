"""Placeholder tests verifying the scaffolding and the provider-selection seam.

These confirm the toolchain works (pytest + hypothesis import and run) and that
the config seam defaults to the offline mock providers used by the demo.
Service logic is tested in later tasks.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.config import (
    EventBusProvider,
    LlmProvider,
    MessageChannelProvider,
    Settings,
    VoiceProvider,
)
from pulselink.common.event_bus import InMemoryEventBus


def test_config_defaults_to_offline_mocks(monkeypatch):
    """With no env overrides, the seam selects offline/mock providers (Req 12.2, 12.3)."""
    for var in ("LLM_PROVIDER", "MESSAGE_CHANNEL", "VOICE_PROVIDER", "EVENT_BUS"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings.from_env()

    assert settings.llm_provider is LlmProvider.MOCK
    assert settings.message_channel is MessageChannelProvider.MOCK
    assert settings.voice_provider is VoiceProvider.MOCK
    assert settings.event_bus is EventBusProvider.MEMORY


def test_config_reads_provider_overrides(monkeypatch):
    """Providers are swappable by configuration without code changes (Req 12.2, 12.3)."""
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("MESSAGE_CHANNEL", "voice")
    monkeypatch.setenv("VOICE_PROVIDER", "connect")
    monkeypatch.setenv("EVENT_BUS", "eventbridge")

    settings = Settings.from_env()

    assert settings.llm_provider is LlmProvider.BEDROCK
    assert settings.message_channel is MessageChannelProvider.VOICE
    assert settings.voice_provider is VoiceProvider.CONNECT
    assert settings.event_bus is EventBusProvider.EVENTBRIDGE


def test_in_memory_event_bus_delivers_to_subscribers():
    bus = InMemoryEventBus()
    received: list[dict] = []
    bus.subscribe("donor.responded", received.append)

    bus.publish("donor.responded", {"donorId": "d1", "response": "DECLINED"})

    assert received == [{"donorId": "d1", "response": "DECLINED"}]


@given(st.text())
def test_hypothesis_toolchain_runs(value: str):
    """Trivial property confirming the hypothesis runner is wired up."""
    assert value == value
