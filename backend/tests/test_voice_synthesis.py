"""Unit + property tests for voice speech synthesis (Task 10.3).

Covers synthesizing a script to speech in the donor's preferred language, with
the configured-default fallback, fully offline and deterministic (no AWS calls):

* the donor's ``preferred_lang`` is used when it is a supported voice language
  (Requirement 13.2);
* the configured default language is used when the preferred language is
  ``None`` / empty / unsupported (Requirement 13.3);
* the produced ``VoiceAudio`` carries the **resolved** (effective) language, a
  positive duration, and a stored artifact URI;
* the lang → Polly neural VoiceId map covers en/hi/te/ta;
* the production ``PollySynthesizer`` is constructible offline and raises a
  clear error only when actually invoked without ``boto3``.

Requirements: 13.2, 13.3
"""

from __future__ import annotations

import dataclasses
import importlib.util

import pytest

from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.config import Settings, get_settings
from pulselink.messaging.voice import (
    POLLY_VOICE_IDS,
    VOICE_FALLBACK_LANG,
    MockSpeechSynthesizer,
    MockVoiceChannel,
    PollySynthesizer,
    SpeechSynthesisError,
    SpeechSynthesisResult,
    VoiceAudio,
    VoiceCallContext,
    VoiceScript,
    polly_voice_id_for,
    resolve_voice_lang,
)

_SUPPORTED = ("en", "hi", "te", "ta")
_BOTO3_AVAILABLE = importlib.util.find_spec("boto3") is not None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _settings(default_lang: str = "en") -> Settings:
    """Settings with the given configured default language, else defaults."""
    return dataclasses.replace(get_settings(), default_lang=default_lang)


def _script(lang: str = "te", text: str = "Hello, please help by giving blood.") -> VoiceScript:
    return VoiceScript(lang=lang, text=text, model_version="test-v0")


def _ctx(lang: str) -> VoiceCallContext:
    return VoiceCallContext(donor_id="d1", slot_id="s1", lang=lang, bridge_context="")


# --------------------------------------------------------------------------- #
# resolve_voice_lang — preferred used when supported (Req 13.2)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lang", list(_SUPPORTED))
def test_resolve_uses_preferred_when_supported(lang):
    assert resolve_voice_lang(lang, _settings(default_lang="en")) == lang


def test_resolve_normalizes_preferred_tag():
    assert resolve_voice_lang("  HI ", _settings(default_lang="en")) == "hi"


# --------------------------------------------------------------------------- #
# resolve_voice_lang — fallback to configured default (Req 13.3)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("preferred", [None, "", "   ", "xx", "kn", "fr"])
def test_resolve_falls_back_to_configured_default(preferred):
    # None / empty / unsupported preferred → configured default language.
    assert resolve_voice_lang(preferred, _settings(default_lang="ta")) == "ta"


def test_resolve_falls_back_to_english_when_default_unsupported():
    # When neither preferred nor the configured default is supported, the
    # ultimate English fallback is used.
    assert resolve_voice_lang("zz", _settings(default_lang="zz")) == VOICE_FALLBACK_LANG
    assert VOICE_FALLBACK_LANG == "en"


# --------------------------------------------------------------------------- #
# lang -> Polly neural VoiceId map covers en/hi/te/ta
# --------------------------------------------------------------------------- #
def test_polly_voice_map_covers_supported_langs():
    assert set(POLLY_VOICE_IDS) == set(_SUPPORTED)
    for lang in _SUPPORTED:
        assert POLLY_VOICE_IDS[lang]  # non-empty VoiceId


@pytest.mark.parametrize("lang", list(_SUPPORTED))
def test_polly_voice_id_for_returns_mapped_voice(lang):
    assert polly_voice_id_for(lang) == POLLY_VOICE_IDS[lang]


def test_polly_voice_id_for_unknown_lang_falls_back_to_english_voice():
    assert polly_voice_id_for("zz") == POLLY_VOICE_IDS["en"]


# --------------------------------------------------------------------------- #
# MockSpeechSynthesizer — deterministic artifact + duration
# --------------------------------------------------------------------------- #
def test_mock_synthesizer_is_deterministic_and_positive_duration():
    synth = MockSpeechSynthesizer()

    first = synth.synthesize("hello world", "hi")
    second = synth.synthesize("hello world", "hi")

    assert isinstance(first, SpeechSynthesisResult)
    assert first.duration_ms >= 1000
    assert first.audio_uri.startswith("file://")
    assert "/hi/" in first.audio_uri
    # Distinct sequential artifacts; duration only depends on text length.
    assert first.audio_uri != second.audio_uri
    assert first.duration_ms == second.duration_ms


# --------------------------------------------------------------------------- #
# MockVoiceChannel.synthesize — VoiceAudio carries the resolved lang (Req 13.2/13.3)
# --------------------------------------------------------------------------- #
def test_synthesize_uses_preferred_lang_when_supported():
    channel = MockVoiceChannel(settings=_settings(default_lang="en"))
    script = _script(lang="hi")

    audio = channel.synthesize(script, "hi")

    assert isinstance(audio, VoiceAudio)
    assert audio.lang == "hi"  # preferred, supported
    assert audio.duration_ms > 0
    assert audio.audio_uri
    assert "/hi/" in audio.audio_uri
    assert channel.synthesized[-1].audio == audio


@pytest.mark.parametrize("preferred", [None, "", "xx"])
def test_synthesize_falls_back_to_default_lang(preferred):
    channel = MockVoiceChannel(settings=_settings(default_lang="ta"))
    script = _script(lang="ta")

    audio = channel.synthesize(script, preferred)

    # Effective language is the configured default when preferred is missing
    # or unsupported (Requirement 13.3).
    assert audio.lang == "ta"
    assert audio.duration_ms > 0
    assert "/ta/" in audio.audio_uri


def test_synthesize_carries_resolved_lang_not_requested_lang():
    # Requesting an unsupported language must NOT leak onto the artifact; the
    # resolved/effective language is recorded instead.
    channel = MockVoiceChannel(settings=_settings(default_lang="en"))

    audio = channel.synthesize(_script(lang="xx"), "xx")

    assert audio.lang == "en"
    assert "/xx/" not in audio.audio_uri


# --------------------------------------------------------------------------- #
# PollySynthesizer — constructible offline, clear error without boto3
# --------------------------------------------------------------------------- #
def test_polly_synthesizer_constructs_offline_without_creds():
    # Construction must not import boto3 or require credentials.
    synth = PollySynthesizer(s3_bucket="pulselink-audio")
    assert isinstance(synth, PollySynthesizer)


@pytest.mark.skipif(_BOTO3_AVAILABLE, reason="boto3 is installed in this env")
def test_polly_synthesizer_raises_clear_error_without_boto3():
    synth = PollySynthesizer(s3_bucket="pulselink-audio")
    with pytest.raises(SpeechSynthesisError):
        synth.synthesize("hello", "hi")


# --------------------------------------------------------------------------- #
# Property: synthesized audio is always in a supported lang with positive
# duration and a non-empty artifact uri, for any preferred-language input.
# Validates: Requirements 13.2, 13.3
# --------------------------------------------------------------------------- #
@given(
    preferred=st.one_of(
        st.none(),
        st.sampled_from(["en", "hi", "te", "ta", "", "  ", "xx", "kn", "EN", " te "]),
    ),
    default_lang=st.sampled_from(list(_SUPPORTED)),
    text=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Zs")),
        min_size=1,
        max_size=60,
    ),
)
def test_property_synthesis_resolves_to_supported_lang(preferred, default_lang, text):
    channel = MockVoiceChannel(settings=_settings(default_lang=default_lang))

    audio = channel.synthesize(_script(lang="en", text=text), preferred)

    # Effective language is always a supported voice language with a mapped voice.
    assert audio.lang in POLLY_VOICE_IDS
    assert polly_voice_id_for(audio.lang)
    # And a usable artifact: positive duration + non-empty uri tagged by lang.
    assert audio.duration_ms > 0
    assert audio.audio_uri
    assert f"/{audio.lang}/" in audio.audio_uri
