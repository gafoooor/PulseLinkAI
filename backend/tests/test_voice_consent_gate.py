"""Unit tests for the PulseLink Voice consent gate (Task 10.4).

These cover the ``contact_for_slots`` consent gate on
:meth:`MockVoiceChannel.place_call` (Requirements 13.4, 13.5 / design
Property 11), fully offline and deterministic:

* With a consent store AND an active ``contact_for_slots`` scope, ``place_call``
  proceeds and returns the programmed :class:`VoiceOutcome` (and records it).
* With the scope missing, or after it is revoked, ``place_call`` is blocked —
  it raises :class:`VoiceConsentError` and records NO placed call.
* With NO consent store configured, behaviour is unchanged from Task 10.1
  (the gate is a no-op): ``place_call`` proceeds without any consent check.

Requirements: 13.4, 13.5
"""

from __future__ import annotations

import pytest

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import ConsentScope, SlotResponse
from pulselink.common.models import ContactPoint
from pulselink.messaging.voice import (
    MockVoiceChannel,
    VoiceCallContext,
    VoiceConsentError,
    VoiceOutcome,
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


def _audio(channel: MockVoiceChannel, donor_id: str, slot_id: str):
    """Run the offline script→synthesize steps to get a VoiceAudio to place."""
    script = channel.generate_script(_ctx(donor_id=donor_id, slot_id=slot_id))
    return channel.synthesize(script, "te")


def _grant_contact_scope(consent: InMemoryConsentService, donor_id: str) -> None:
    consent.grant(
        donor_id,
        "donor",
        [ConsentScope.CONTACT_FOR_SLOTS],
        version="v1",
    )


# --------------------------------------------------------------------------- #
# Active scope → call proceeds
# --------------------------------------------------------------------------- #
def test_place_call_proceeds_with_active_consent():
    consent = InMemoryConsentService()
    _grant_contact_scope(consent, "d7")
    channel = MockVoiceChannel(consent_store=consent)
    channel.program_response("d7", "s7", SlotResponse.ACCEPTED, captured_via="dtmf")

    audio = _audio(channel, "d7", "s7")
    outcome = channel.place_call(_contact(contact_id="c7", donor_id="d7"), audio)

    assert isinstance(outcome, VoiceOutcome)
    assert outcome.donor_id == "d7"
    assert outcome.slot_id == "s7"
    assert outcome.response is SlotResponse.ACCEPTED
    # The call is recorded exactly once.
    assert len(channel.placed_calls) == 1
    assert channel.placed_calls[0].donor_id == "d7"


# --------------------------------------------------------------------------- #
# Missing scope → blocked, nothing recorded
# --------------------------------------------------------------------------- #
def test_place_call_blocked_when_scope_missing():
    consent = InMemoryConsentService()  # donor "d8" was never granted anything
    channel = MockVoiceChannel(consent_store=consent)
    channel.program_response("d8", "s8", SlotResponse.ACCEPTED)

    audio = _audio(channel, "d8", "s8")

    with pytest.raises(VoiceConsentError):
        channel.place_call(_contact(contact_id="c8", donor_id="d8"), audio)

    # No call was placed for a non-consenting donor.
    assert channel.placed_calls == []


def test_place_call_blocked_with_wrong_scope_only():
    """A donor who granted some *other* scope is still blocked from contact."""
    consent = InMemoryConsentService()
    consent.grant(
        "d9",
        "donor",
        [ConsentScope.USE_IN_FORECASTING],  # not contact_for_slots
        version="v1",
    )
    channel = MockVoiceChannel(consent_store=consent)
    channel.program_response("d9", "s9", SlotResponse.DECLINED)
    audio = _audio(channel, "d9", "s9")

    with pytest.raises(VoiceConsentError):
        channel.place_call(_contact(contact_id="c9", donor_id="d9"), audio)

    assert channel.placed_calls == []


# --------------------------------------------------------------------------- #
# Revoked scope → blocked, nothing recorded
# --------------------------------------------------------------------------- #
def test_place_call_blocked_after_revocation():
    consent = InMemoryConsentService()
    _grant_contact_scope(consent, "d10")
    channel = MockVoiceChannel(consent_store=consent)
    channel.program_response("d10", "s10", SlotResponse.ACCEPTED)
    audio = _audio(channel, "d10", "s10")

    # First call proceeds while consent is active.
    channel.place_call(_contact(contact_id="c10", donor_id="d10"), audio)
    assert len(channel.placed_calls) == 1

    # After revocation the donor must never be called again.
    consent.revoke("d10")
    with pytest.raises(VoiceConsentError):
        channel.place_call(_contact(contact_id="c10", donor_id="d10"), audio)

    # No new call recorded — still just the pre-revocation one.
    assert len(channel.placed_calls) == 1


# --------------------------------------------------------------------------- #
# No consent store → unchanged Task 10.1 behaviour (gate is a no-op)
# --------------------------------------------------------------------------- #
def test_place_call_no_consent_store_is_a_no_op_gate():
    channel = MockVoiceChannel()  # no consent_store: 10.1 compatibility
    channel.program_response("d11", "s11", SlotResponse.ACCEPTED)
    audio = _audio(channel, "d11", "s11")

    outcome = channel.place_call(_contact(contact_id="c11", donor_id="d11"), audio)

    assert outcome.response is SlotResponse.ACCEPTED
    assert len(channel.placed_calls) == 1
