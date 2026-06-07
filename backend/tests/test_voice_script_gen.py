"""Unit + property tests for LLM-backed voice script generation (Task 10.2).

Covers script generation through the injected ``LlmClient`` (Requirement 13.1),
fully offline and deterministic:

* with an injected ``MockLlmClient``, ``generate_script`` returns a
  context-grounded ``VoiceScript`` in the requested language with no medical
  claims;
* on an LLM/schema failure (a client that errors or returns off-contract /
  medical-claim output), it falls back to the canned script;
* with no ``llm_client`` injected, the canned path of Task 10.1 still works.

Requirements: 13.1
"""

from __future__ import annotations

import pytest

from hypothesis import given
from hypothesis import strategies as st

from pulselink.parsing.llm_client import MockLlmClient
from pulselink.messaging.voice import (
    _CANNED_SCRIPT,
    _LLM_SCRIPT_MODEL_VERSION,
    _MEDICAL_CLAIM_TERMS,
    _MOCK_SCRIPT_MODEL_VERSION,
    MockVoiceChannel,
    VoiceCallContext,
    VoiceScript,
    VoiceScriptGenerationError,
    VoiceScriptGenerator,
)


# --------------------------------------------------------------------------- #
# Helpers / fakes
# --------------------------------------------------------------------------- #
def _ctx(
    lang: str = "te",
    donor_id: str = "d1",
    slot_id: str = "s1",
    bridge_context: str = "A patient you support needs blood soon.",
) -> VoiceCallContext:
    return VoiceCallContext(
        donor_id=donor_id,
        slot_id=slot_id,
        lang=lang,
        bridge_context=bridge_context,
    )


class _ScriptedLlmClient:
    """A non-mock ``LlmClient`` that returns a fixed structured response.

    Drives the *production* code path (it is NOT a ``MockLlmClient``), letting
    tests assert that a well-formed LLM script is used verbatim.
    """

    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[dict] = []

    def complete_structured(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return dict(self._response)


class _FailingLlmClient:
    """A non-mock ``LlmClient`` whose call raises — exercises the fallback."""

    def complete_structured(self, **kwargs) -> dict:
        raise RuntimeError("bedrock unavailable")


class _OffContractLlmClient:
    """A non-mock ``LlmClient`` returning output that violates the schema."""

    def complete_structured(self, **kwargs) -> dict:
        return {"not_a_script": 123}


# --------------------------------------------------------------------------- #
# Injected MockLlmClient: context-grounded, in requested lang, no medical claims
# --------------------------------------------------------------------------- #
def test_injected_mock_llm_produces_context_grounded_script():
    channel = MockVoiceChannel(llm_client=MockLlmClient())
    ctx = _ctx(lang="hi", bridge_context="Riya needs blood within three days.")

    script = channel.generate_script(ctx)

    assert isinstance(script, VoiceScript)
    assert script.lang == "hi"
    assert script.text
    # Grounded in the supplied bridge context (this is NOT in the canned text).
    assert "Riya needs blood within three days." in script.text
    # It is the LLM-backed path, not the plain canned fallback.
    assert script.model_version == _LLM_SCRIPT_MODEL_VERSION
    assert channel.scripts == [script]


@pytest.mark.parametrize("lang", ["en", "hi", "te", "ta", "xx"])
def test_injected_mock_llm_script_has_no_medical_claims(lang):
    channel = MockVoiceChannel(llm_client=MockLlmClient())

    script = channel.generate_script(_ctx(lang=lang))

    assert script.lang == lang
    lowered = script.text.lower()
    for term in _MEDICAL_CLAIM_TERMS:
        assert term not in lowered


def test_injected_mock_llm_script_keeps_accept_decline_instructions():
    channel = MockVoiceChannel(llm_client=MockLlmClient())

    script = channel.generate_script(_ctx(lang="en"))

    lowered = script.text.lower()
    assert "accept" in lowered and "decline" in lowered
    assert "1" in script.text and "2" in script.text


# --------------------------------------------------------------------------- #
# Production path: a well-formed LLM response is used verbatim
# --------------------------------------------------------------------------- #
def test_wellformed_llm_response_is_used():
    good = "Hello, please help a patient by giving blood. Say Accept or press 1; say Decline or press 2."
    client = _ScriptedLlmClient({"script": good, "lang": "en"})
    channel = MockVoiceChannel(llm_client=client)

    script = channel.generate_script(_ctx(lang="en"))

    assert script.text == good
    assert script.lang == "en"
    assert script.model_version == _LLM_SCRIPT_MODEL_VERSION
    # The constrained call was issued at a low temperature against the schema.
    assert client.calls and client.calls[0]["temperature"] <= 0.5


# --------------------------------------------------------------------------- #
# Fallback to canned on LLM / schema / medical-claim failure
# --------------------------------------------------------------------------- #
def test_falls_back_to_canned_on_llm_error():
    channel = MockVoiceChannel(llm_client=_FailingLlmClient())

    script = channel.generate_script(_ctx(lang="te"))

    assert script.text == _CANNED_SCRIPT["te"]
    assert script.model_version == _MOCK_SCRIPT_MODEL_VERSION


def test_falls_back_to_canned_on_off_contract_output():
    channel = MockVoiceChannel(llm_client=_OffContractLlmClient())

    script = channel.generate_script(_ctx(lang="en"))

    assert script.text == _CANNED_SCRIPT["en"]
    assert script.model_version == _MOCK_SCRIPT_MODEL_VERSION


def test_falls_back_to_canned_when_llm_emits_medical_claim():
    bad = {"script": "We will diagnose and treat your disease today.", "lang": "en"}
    channel = MockVoiceChannel(llm_client=_ScriptedLlmClient(bad))

    script = channel.generate_script(_ctx(lang="en"))

    # The medical-claim script is rejected; canned (claim-free) is used instead.
    assert script.text == _CANNED_SCRIPT["en"]
    assert script.model_version == _MOCK_SCRIPT_MODEL_VERSION


def test_generator_raises_on_empty_script():
    client = _ScriptedLlmClient({"script": "   ", "lang": "en"})
    with pytest.raises(VoiceScriptGenerationError):
        VoiceScriptGenerator(client).generate(_ctx(lang="en"))


# --------------------------------------------------------------------------- #
# No llm_client injected: Task 10.1 canned behaviour preserved
# --------------------------------------------------------------------------- #
def test_no_llm_client_uses_canned_script():
    channel = MockVoiceChannel()  # no client

    script = channel.generate_script(_ctx(lang="ta"))

    assert script.text == _CANNED_SCRIPT["ta"]
    assert script.model_version == _MOCK_SCRIPT_MODEL_VERSION


# --------------------------------------------------------------------------- #
# Property: generate_script always yields a non-empty, claim-free script in lang
# Validates: Requirements 13.1
# --------------------------------------------------------------------------- #
@given(
    lang=st.sampled_from(["en", "hi", "te", "ta", "xx", "kn"]),
    bridge_context=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Zs", "Po")),
        max_size=80,
    ),
)
def test_property_script_is_grounded_clean_and_in_lang(lang, bridge_context):
    # The bridge context is guaranteed claim-free by the upstream contract; the
    # generator must always return a non-empty script in ctx.lang with no
    # medical-claim terms, regardless of context content.
    ctx = _ctx(lang=lang, bridge_context=bridge_context)
    channel = MockVoiceChannel(llm_client=MockLlmClient())

    script = channel.generate_script(ctx)

    assert script.lang == lang
    assert script.text.strip()
    lowered = script.text.lower()
    for term in _MEDICAL_CLAIM_TERMS:
        assert term not in lowered
