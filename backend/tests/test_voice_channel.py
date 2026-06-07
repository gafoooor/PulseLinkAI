"""Unit tests for the VoiceChannel seam and MockVoiceChannel (Task 10.1).

Covers the offline seam + mock only (no telephony, fully deterministic):
* ``MockVoiceChannel`` satisfies both ``MessageChannel`` (has ``send``) and the
  ``VoiceChannel`` extension shape.
* ``generate_script`` returns a ``VoiceScript`` in the requested language.
* ``synthesize`` returns a ``VoiceAudio`` artifact with a positive duration.
* ``place_call`` maps a pre-programmed ACCEPT/DECLINE to a ``VoiceOutcome`` with
  the right ``SlotResponse`` and ``captured_via``.
* the in-memory logs (scripts / synthesized / placed_calls / sent) are
  inspectable.
* ``get_voice_channel`` returns a mock under default/mock config and fails
  loudly for the not-yet-implemented ``connect`` provider.

Requirements: 13.1, 13.10, 12.2
"""

from __future__ import annotations

import dataclasses

import pytest

from pulselink.common.config import Settings, VoiceProvider, get_settings
from pulselink.common.enums import SlotResponse
from pulselink.common.models import ContactPoint
from pulselink.messaging.channel import (
    LocalizedMessage,
    MessageAction,
    MessageChannel,
)
from pulselink.messaging.voice import (
    MockVoiceChannel,
    VoiceAudio,
    VoiceCallContext,
    VoiceChannel,
    VoiceOutcome,
    VoiceScript,
    get_voice_channel,
)


def _contact(contact_id: str = "c1", donor_id: str = "d1") -> ContactPoint:
    return ContactPoint(
        contact_id=contact_id,
        subject_id=donor_id,
        type="phone",
        value_encrypted="enc::+910000000000",
        preferred_lang="te",
    )


def _ctx(lang: str = "te", donor_id: str = "d1", slot_id: str = "s1") -> VoiceCallContext:
    return VoiceCallContext(
        donor_id=donor_id,
        slot_id=slot_id,
        lang=lang,
        bridge_context="A patient you support needs blood soon.",
    )


def _offer(lang: str = "te") -> LocalizedMessage:
    return LocalizedMessage(
        lang=lang,
        text="Blood slot offer",
        actions=[
            MessageAction(kind="accept", label="Accept", keyword="ACCEPT"),
            MessageAction(kind="decline", label="Decline", keyword="DECLINE"),
        ],
    )


def _settings(provider: VoiceProvider) -> Settings:
    """Build settings with the given voice provider, inheriting all defaults."""
    return dataclasses.replace(get_settings(), voice_provider=provider)


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #
def test_mock_voice_channel_is_a_message_channel():
    """A VoiceChannel is a MessageChannel — it satisfies the base seam too."""
    assert isinstance(MockVoiceChannel(), MessageChannel)


def test_mock_voice_channel_satisfies_voice_channel_shape():
    """MockVoiceChannel structurally satisfies the VoiceChannel extension."""
    assert isinstance(MockVoiceChannel(), VoiceChannel)


# --------------------------------------------------------------------------- #
# generate_script
# --------------------------------------------------------------------------- #
def test_generate_script_returns_script_in_requested_language():
    channel = MockVoiceChannel()

    script = channel.generate_script(_ctx(lang="hi"))

    assert isinstance(script, VoiceScript)
    assert script.lang == "hi"
    assert script.text  # non-empty, canned
    assert script.model_version
    # No medical claims sneak in via the canned templates.
    assert "diagnos" not in script.text.lower()
    assert channel.scripts == [script]


def test_generate_script_falls_back_to_english_text_for_unknown_lang():
    channel = MockVoiceChannel()

    script = channel.generate_script(_ctx(lang="xx"))

    # The requested language is preserved on the script even when the canned
    # text falls back to the English template.
    assert script.lang == "xx"
    assert script.text == channel.generate_script(_ctx(lang="en")).text


# --------------------------------------------------------------------------- #
# synthesize
# --------------------------------------------------------------------------- #
def test_synthesize_returns_audio_artifact_with_duration():
    channel = MockVoiceChannel()
    script = channel.generate_script(_ctx(lang="te"))

    audio = channel.synthesize(script, "te")

    assert isinstance(audio, VoiceAudio)
    assert audio.lang == "te"
    assert audio.audio_uri
    assert audio.duration_ms >= 1000
    assert channel.synthesized[-1].audio == audio
    assert channel.synthesized[-1].script == script


# --------------------------------------------------------------------------- #
# place_call
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "response, captured_via",
    [
        (SlotResponse.ACCEPTED, "voice_intent"),
        (SlotResponse.DECLINED, "dtmf"),
    ],
)
def test_place_call_maps_programmed_response_to_outcome(response, captured_via):
    channel = MockVoiceChannel()
    contact = _contact(contact_id="c7", donor_id="d7")
    channel.program_response("d7", "s7", response, captured_via=captured_via)

    script = channel.generate_script(_ctx(lang="te", donor_id="d7", slot_id="s7"))
    audio = channel.synthesize(script, "te")
    outcome = channel.place_call(contact, audio)

    assert isinstance(outcome, VoiceOutcome)
    assert outcome.donor_id == "d7"
    assert outcome.slot_id == "s7"
    assert outcome.response is response
    assert outcome.captured_via == captured_via
    assert outcome.fallback_used is False

    # The call is recorded in the inspectable log.
    assert len(channel.placed_calls) == 1
    recorded = channel.placed_calls[0]
    assert recorded.donor_id == "d7"
    assert recorded.to_contact_id == "c7"
    assert recorded.outcome == outcome


def test_place_call_without_programmed_response_raises():
    channel = MockVoiceChannel()
    audio = channel.synthesize(channel.generate_script(_ctx()), "te")
    with pytest.raises(KeyError):
        channel.place_call(_contact(donor_id="unprogrammed"), audio)


# --------------------------------------------------------------------------- #
# send (plain MessageChannel behaviour) + log inspectability
# --------------------------------------------------------------------------- #
def test_send_records_offer_and_returns_voice_receipt():
    channel = MockVoiceChannel()
    contact = _contact()

    receipt = channel.send(contact, _offer())

    assert receipt.status == "delivered"
    assert receipt.channel == "voice"
    assert receipt.to_contact_id == contact.contact_id
    assert receipt.message_id
    assert len(channel.sent) == 1
    assert channel.sent[0].receipt.message_id == receipt.message_id


def test_logs_return_copies():
    """Mutating a returned log must not affect the channel's internal state."""
    channel = MockVoiceChannel()
    channel.program_response("d1", "s1", SlotResponse.ACCEPTED)
    audio = channel.synthesize(channel.generate_script(_ctx()), "te")
    channel.place_call(_contact(), audio)

    channel.placed_calls.clear()
    channel.scripts.clear()
    channel.synthesized.clear()

    assert len(channel.placed_calls) == 1
    assert len(channel.scripts) == 1
    assert len(channel.synthesized) == 1


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def test_factory_returns_mock_under_default_config():
    channel = get_voice_channel(_settings(VoiceProvider.MOCK))
    assert isinstance(channel, MockVoiceChannel)


def test_factory_uses_ambient_settings_when_none_passed():
    channel = get_voice_channel()
    assert isinstance(channel, MockVoiceChannel)


def test_factory_connect_provider_not_yet_implemented():
    with pytest.raises(NotImplementedError):
        get_voice_channel(_settings(VoiceProvider.CONNECT))
