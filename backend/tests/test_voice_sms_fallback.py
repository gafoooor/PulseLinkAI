"""Unit tests for the PulseLink Voice SMS fallback (Task 10.5).

These cover the unanswered/failed-call SMS fallback on
:meth:`MockVoiceChannel.place_call` (Requirement 13.6), fully offline and
deterministic:

* An unanswered call for a donor with an active ``contact_for_slots`` scope
  triggers an SMS fallback: the SMS is recorded (in :attr:`sms_fallbacks` and on
  the injected sender) and the returned :class:`VoiceOutcome` carries
  ``fallback_used=True`` / ``captured_via="sms_fallback"``.
* An answered call does NOT trigger the fallback (no SMS recorded).
* The fallback is blocked when consent is missing or revoked — no SMS is
  recorded and :class:`VoiceConsentError` is raised (consistent with Task 10.4):
  a non-consenting donor gets neither the call nor the SMS.

Requirements: 13.6 (cross-checks: 13.4, 13.5)
"""

from __future__ import annotations

import pytest

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import ConsentScope, SlotResponse
from pulselink.common.models import ContactPoint
from pulselink.messaging.voice import (
    MockSmsSender,
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
# Unanswered call + active consent → SMS fallback fires
# --------------------------------------------------------------------------- #
def test_unanswered_call_triggers_sms_fallback():
    consent = InMemoryConsentService()
    _grant_contact_scope(consent, "d1")
    sms = MockSmsSender()
    channel = MockVoiceChannel(consent_store=consent, sms_sender=sms)
    channel.program_unanswered("d1", "s1")

    audio = _audio(channel, "d1", "s1")
    outcome = channel.place_call(_contact(donor_id="d1"), audio)

    # The outcome reflects the SMS-fallback path.
    assert isinstance(outcome, VoiceOutcome)
    assert outcome.fallback_used is True
    assert outcome.captured_via == "sms_fallback"
    assert outcome.response is SlotResponse.NO_RESPONSE
    assert outcome.donor_id == "d1"
    assert outcome.slot_id == "s1"

    # Exactly one SMS fallback recorded on the channel...
    assert len(channel.sms_fallbacks) == 1
    fallback = channel.sms_fallbacks[0]
    assert fallback.donor_id == "d1"
    assert fallback.slot_id == "s1"
    assert fallback.to_contact_id == "c1"
    assert fallback.lang == "te"
    assert fallback.body  # a non-empty localized offer
    # ...and on the injected SMS sender.
    assert len(sms.sent) == 1
    assert sms.sent[0].to_contact_id == "c1"


def test_unanswered_call_uses_default_mock_sms_sender():
    """With no sms_sender injected, the channel still records the fallback."""
    consent = InMemoryConsentService()
    _grant_contact_scope(consent, "d2")
    channel = MockVoiceChannel(consent_store=consent)  # default MockSmsSender
    channel.program_unanswered("d2", "s2")

    audio = _audio(channel, "d2", "s2")
    outcome = channel.place_call(_contact(contact_id="c2", donor_id="d2"), audio)

    assert outcome.fallback_used is True
    assert outcome.captured_via == "sms_fallback"
    assert len(channel.sms_fallbacks) == 1


# --------------------------------------------------------------------------- #
# Answered call → NO fallback
# --------------------------------------------------------------------------- #
def test_answered_call_does_not_trigger_sms_fallback():
    consent = InMemoryConsentService()
    _grant_contact_scope(consent, "d3")
    sms = MockSmsSender()
    channel = MockVoiceChannel(consent_store=consent, sms_sender=sms)
    channel.program_response("d3", "s3", SlotResponse.ACCEPTED, captured_via="dtmf")

    audio = _audio(channel, "d3", "s3")
    outcome = channel.place_call(_contact(contact_id="c3", donor_id="d3"), audio)

    assert outcome.response is SlotResponse.ACCEPTED
    assert outcome.fallback_used is False
    assert outcome.captured_via == "dtmf"
    # No SMS fallback for an answered call.
    assert channel.sms_fallbacks == []
    assert sms.sent == []
    # The call was recorded as a normal placed call.
    assert len(channel.placed_calls) == 1


# --------------------------------------------------------------------------- #
# Missing / revoked consent → neither call nor SMS fallback
# --------------------------------------------------------------------------- #
def test_sms_fallback_blocked_when_consent_missing():
    consent = InMemoryConsentService()  # donor "d4" never granted anything
    sms = MockSmsSender()
    channel = MockVoiceChannel(consent_store=consent, sms_sender=sms)
    channel.program_unanswered("d4", "s4")

    audio = _audio(channel, "d4", "s4")

    with pytest.raises(VoiceConsentError):
        channel.place_call(_contact(contact_id="c4", donor_id="d4"), audio)

    # Neither the call nor the SMS fallback happened.
    assert channel.sms_fallbacks == []
    assert sms.sent == []
    assert channel.placed_calls == []


def test_sms_fallback_blocked_after_revocation():
    consent = InMemoryConsentService()
    _grant_contact_scope(consent, "d5")
    sms = MockSmsSender()
    channel = MockVoiceChannel(consent_store=consent, sms_sender=sms)
    channel.program_unanswered("d5", "s5")
    audio = _audio(channel, "d5", "s5")

    # Revoke before the (unanswered) call is attempted.
    consent.revoke("d5")

    with pytest.raises(VoiceConsentError):
        channel.place_call(_contact(contact_id="c5", donor_id="d5"), audio)

    assert channel.sms_fallbacks == []
    assert sms.sent == []


def test_sms_fallback_blocked_with_wrong_scope_only():
    """A donor who granted some *other* scope gets no call and no SMS."""
    consent = InMemoryConsentService()
    consent.grant(
        "d6",
        "donor",
        [ConsentScope.USE_IN_FORECASTING],  # not contact_for_slots
        version="v1",
    )
    sms = MockSmsSender()
    channel = MockVoiceChannel(consent_store=consent, sms_sender=sms)
    channel.program_unanswered("d6", "s6")
    audio = _audio(channel, "d6", "s6")

    with pytest.raises(VoiceConsentError):
        channel.place_call(_contact(contact_id="c6", donor_id="d6"), audio)

    assert channel.sms_fallbacks == []
    assert sms.sent == []


# --------------------------------------------------------------------------- #
# Unanswered programming takes precedence over a programmed voice response
# --------------------------------------------------------------------------- #
def test_unanswered_takes_precedence_over_programmed_response():
    consent = InMemoryConsentService()
    _grant_contact_scope(consent, "d7")
    channel = MockVoiceChannel(consent_store=consent)
    # Program both: unanswered must win.
    channel.program_response("d7", "s7", SlotResponse.ACCEPTED)
    channel.program_unanswered("d7", "s7")

    audio = _audio(channel, "d7", "s7")
    outcome = channel.place_call(_contact(contact_id="c7", donor_id="d7"), audio)

    assert outcome.fallback_used is True
    assert outcome.captured_via == "sms_fallback"
    assert len(channel.sms_fallbacks) == 1
