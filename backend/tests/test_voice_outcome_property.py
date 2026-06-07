"""Property-based test for voice outcome integrity (Task 10.8).

**Property 12: Voice outcome integrity** — every captured ``VoiceOutcome`` maps
to exactly one ``SlotResponse`` for exactly one slot, is published exactly once
(no double-count: the call lands in ``placed_calls`` XOR ``sms_fallbacks``,
never both and never twice), and a ``DECLINED`` outcome never raises the
donor's Reliability_Score.

**Validates: Requirements 13.7, 13.8, 13.9**

This exercises the real :class:`MockVoiceChannel` (no mocks beyond the offline
seams the demo ships with) behind an active ``contact_for_slots`` consent store
so every call proceeds, across a wide, intelligently constrained space of
*captured* voice responses:

* answered calls programmed via :meth:`MockVoiceChannel.program_response` —
  ``ACCEPTED`` / ``DECLINED`` captured by ``voice_intent`` or ``dtmf`` keypad
  (Requirement 13.7);
* unanswered / failed calls programmed via
  :meth:`MockVoiceChannel.program_unanswered` — which take the SMS-fallback path
  and yield a ``NO_RESPONSE`` outcome flagged ``captured_via="sms_fallback"`` /
  ``fallback_used=True`` (Requirement 13.6 path, exercised here only to confirm
  outcome integrity holds for it too).

For the ``DECLINED``-never-raises-score half (Requirement 13.8), the captured
``DECLINED`` :class:`SlotResponse` is fed through the real
:func:`pulselink.reliability.scoring.on_donor_response` recompute and the
recomputed score is asserted ``<=`` the pre-response score — linking the
captured voice outcome to the reliability loop.
"""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import ConsentScope, SlotResponse
from pulselink.common.models import ContactPoint
from pulselink.messaging.voice import (
    MockVoiceChannel,
    VoiceAudio,
    VoiceOutcome,
)
from pulselink.reliability.scoring import DonorStats, on_donor_response

CONSENT_VERSION = "v1"

# Non-empty id-like tokens for donor / slot / contact ids.
_ids = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=16,
)

# A captured-via channel for an *answered* call (Requirement 13.7).
_answered_captured_via = st.sampled_from(["voice_intent", "dtmf"])

# An answered call captures exactly one of these terminal responses.
_answered_response = st.sampled_from([SlotResponse.ACCEPTED, SlotResponse.DECLINED])

# Bounded, realistic date range for the reliability recompute.
_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31))
_optional_dates = st.none() | _dates


def _contact(contact_id: str, donor_id: str) -> ContactPoint:
    """Build a minimal consent-gated contact whose subject is the donor."""
    return ContactPoint(
        contact_id=contact_id,
        subject_id=donor_id,
        type="phone",
        value_encrypted="enc::+910000000000",
        preferred_lang="te",
    )


def _audio(lang: str = "te") -> VoiceAudio:
    return VoiceAudio(lang=lang, audio_uri="file://mock/te/1.wav", duration_ms=2000)


def _consenting_channel(donor_id: str) -> MockVoiceChannel:
    """A MockVoiceChannel whose consent store grants the donor an active scope.

    With an active ``contact_for_slots`` scope the consent gate passes and the
    call proceeds, so we exercise the capture / outcome path (not the gate).
    """
    consent = InMemoryConsentService()
    consent.grant(donor_id, "donor", [ConsentScope.CONTACT_FOR_SLOTS], CONSENT_VERSION)
    return MockVoiceChannel(consent_store=consent)


@st.composite
def _scenario(draw: st.DrawFn) -> dict:
    """A single captured-voice scenario for one consenting donor + one slot.

    Either an answered call (terminal ACCEPTED/DECLINED via voice_intent/dtmf)
    or an unanswered/failed call (SMS-fallback path → NO_RESPONSE).
    """
    donor_id = draw(_ids)
    slot_id = draw(_ids)
    contact_id = draw(_ids)
    answered = draw(st.booleans())
    if answered:
        return {
            "donor_id": donor_id,
            "slot_id": slot_id,
            "contact_id": contact_id,
            "answered": True,
            "response": draw(_answered_response),
            "captured_via": draw(_answered_captured_via),
        }
    return {
        "donor_id": donor_id,
        "slot_id": slot_id,
        "contact_id": contact_id,
        "answered": False,
        "response": SlotResponse.NO_RESPONSE,
        "captured_via": "sms_fallback",
    }


@given(scenario=_scenario())
def test_voice_outcome_integrity(scenario: dict) -> None:
    """Property 12: a captured VoiceOutcome maps to exactly one response for
    exactly one slot and is recorded exactly once (Requirements 13.7, 13.9)."""
    donor_id = scenario["donor_id"]
    slot_id = scenario["slot_id"]
    contact_id = scenario["contact_id"]

    channel = _consenting_channel(donor_id)
    if scenario["answered"]:
        channel.program_response(
            donor_id, slot_id, scenario["response"], scenario["captured_via"]
        )
    else:
        channel.program_unanswered(donor_id, slot_id)

    # Pre-state: nothing recorded yet (clean publish-once baseline).
    assert channel.placed_calls == []
    assert channel.sms_fallbacks == []

    outcome = channel.place_call(_contact(contact_id, donor_id), _audio())

    # --- maps to exactly one slot for exactly one donor (Req 13.7) -------- #
    assert isinstance(outcome, VoiceOutcome)
    assert outcome.donor_id == donor_id
    assert outcome.slot_id == slot_id

    # --- response is exactly one SlotResponse (Req 13.7) ------------------ #
    assert isinstance(outcome.response, SlotResponse)
    assert outcome.response is scenario["response"]

    # --- captured_via is consistent: sms_fallback IFF the call was
    #     unanswered (fallback_used); otherwise the answered intent/dtmf ---- #
    if scenario["answered"]:
        assert outcome.fallback_used is False
        assert outcome.captured_via == scenario["captured_via"]
        assert outcome.captured_via in ("voice_intent", "dtmf")
    else:
        assert outcome.fallback_used is True
        assert outcome.captured_via == "sms_fallback"
    assert (outcome.captured_via == "sms_fallback") is outcome.fallback_used

    # --- published exactly once / no double-count (Req 13.9) -------------- #
    # The call lands in placed_calls XOR sms_fallbacks: exactly one new record,
    # never both and never twice.
    total_records = len(channel.placed_calls) + len(channel.sms_fallbacks)
    assert total_records == 1
    if scenario["answered"]:
        assert len(channel.placed_calls) == 1
        assert len(channel.sms_fallbacks) == 0
        recorded = channel.placed_calls[0]
        assert recorded.outcome == outcome
        assert recorded.donor_id == donor_id
    else:
        assert len(channel.placed_calls) == 0
        assert len(channel.sms_fallbacks) == 1
        recorded = channel.sms_fallbacks[0]
        assert recorded.donor_id == donor_id
        assert recorded.slot_id == slot_id


@st.composite
def _donor_stats(draw: st.DrawFn) -> DonorStats:
    """DonorStats with ``offered >= accepted >= 0`` (the scoring precondition),
    spanning the neutral-prior case (``offered == 0``) and well-formed history."""
    offered = draw(st.integers(min_value=0, max_value=1_000))
    accepted = draw(st.integers(min_value=0, max_value=offered))
    return DonorStats(
        accepted=accepted,
        offered=offered,
        calls_to_donations_ratio=draw(
            st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)
        ),
        donations_till_date=draw(st.integers(min_value=0, max_value=10_000)),
        last_donation_date=draw(_optional_dates),
    )


@given(
    donor_id=_ids,
    slot_id=_ids,
    contact_id=_ids,
    captured_via=_answered_captured_via,
    stats=_donor_stats(),
    today=_dates,
)
def test_captured_decline_never_raises_score(
    donor_id: str,
    slot_id: str,
    contact_id: str,
    captured_via: str,
    stats: DonorStats,
    today: date,
) -> None:
    """Property 12 (Req 13.8): a captured DECLINED voice outcome, fed through the
    reliability recompute, never raises the donor's score above the prior."""
    channel = _consenting_channel(donor_id)
    channel.program_response(donor_id, slot_id, SlotResponse.DECLINED, captured_via)

    outcome = channel.place_call(_contact(contact_id, donor_id), _audio())

    # The captured outcome is exactly one DECLINED response for this donor/slot.
    assert outcome.response is SlotResponse.DECLINED
    assert outcome.donor_id == donor_id
    assert outcome.slot_id == slot_id

    # Linking the captured outcome to the reliability loop: recomputing on the
    # captured DECLINED response can only keep the score equal or lower it.
    result = on_donor_response(donor_id, stats, outcome.response, today)
    assert result.score.score <= result.previous_score.score
