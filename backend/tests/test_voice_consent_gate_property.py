"""Property-based test for the PulseLink Voice consent gate (Task 10.7).

**Property 11: Voice consent gate** — ``VoiceChannel`` (and its SMS fallback)
never places a call or SMS to a donor without an active ``contact_for_slots``
consent scope at call time.

**Validates: Requirements 13.4, 13.5**

This exercises the real :meth:`MockVoiceChannel.place_call` implementation (no
mocks of the channel itself) against a real ``InMemoryConsentService`` and a
real ``MockSmsSender`` across a wide, intelligently constrained consent space:

* donors with an active ``contact_for_slots`` scope (alone or alongside other
  scopes) — the contact-allowed region,
* donors with no consent record at all,
* donors who consented only to *other* scopes (``store_contact`` etc.) but not
  ``contact_for_slots``,
* donors whose consent (whatever its scopes) was subsequently *revoked*.

It also randomizes the *call mode*: an answered call (pre-programmed via
:meth:`MockVoiceChannel.program_response`, captured by voice intent or DTMF) or
an unanswered/failed call that routes to the SMS fallback (pre-programmed via
:meth:`MockVoiceChannel.program_unanswered`).

The property is an IFF: *something is placed/sent* — a voice call recorded in
``channel.placed_calls`` OR an SMS fallback recorded in ``channel.sms_fallbacks``
(and in the injected ``sender.sent``) — **if and only if** the donor held an
active ``contact_for_slots`` scope at call time. Otherwise ``place_call`` raises
:class:`VoiceConsentError` and NEITHER ``placed_calls`` NOR ``sms_fallbacks`` has
any entry. The universal safety direction is asserted explicitly: if anything
was placed/sent, the active scope held.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import ConsentScope, SlotResponse
from pulselink.common.models import ContactPoint
from pulselink.messaging.voice import (
    MockSmsSender,
    MockVoiceChannel,
    VoiceCallContext,
    VoiceConsentError,
)

DONOR_ID = "donor-1"
SLOT_ID = "slot-1"
CONSENT_VERSION = "v1"

# The full scope vocabulary, used to build varied grants — some including the
# gating CONTACT_FOR_SLOTS scope, some deliberately excluding it.
_ALL_SCOPES = list(ConsentScope)


# --------------------------------------------------------------------------- #
# Minimal valid domain builders
# --------------------------------------------------------------------------- #
def _contact() -> ContactPoint:
    return ContactPoint(
        contact_id="contact-1",
        subject_id=DONOR_ID,
        type="phone",
        value_encrypted="enc::+910000000000",
        preferred_lang="te",
    )


def _ctx() -> VoiceCallContext:
    return VoiceCallContext(
        donor_id=DONOR_ID,
        slot_id=SLOT_ID,
        lang="te",
        bridge_context="A patient you support needs blood soon.",
    )


# --------------------------------------------------------------------------- #
# Consent-state strategy
# --------------------------------------------------------------------------- #
@st.composite
def _consent_state(draw: st.DrawFn) -> tuple[InMemoryConsentService, bool]:
    """Build a consent store for the donor and report the expected gate result.

    Returns ``(consent_service, expected_active)`` where ``expected_active`` is
    True iff the donor holds an active ``contact_for_slots`` scope at call time
    — i.e. consent was granted, includes that scope, and was not revoked.
    """
    consent = InMemoryConsentService()

    has_record = draw(st.booleans())
    if not has_record:
        # No consent on file at all → gate must block.
        return consent, False

    scopes = draw(
        st.lists(st.sampled_from(_ALL_SCOPES), unique=True, max_size=len(_ALL_SCOPES))
    )
    consent.grant(DONOR_ID, "donor", scopes, CONSENT_VERSION)

    revoked = draw(st.booleans())
    if revoked:
        consent.revoke(DONOR_ID)

    expected_active = (ConsentScope.CONTACT_FOR_SLOTS in scopes) and not revoked
    return consent, expected_active


# Captured-via options for an answered call (irrelevant to the gate, varied for
# coverage).
_captured_via = st.sampled_from(["voice_intent", "dtmf"])

# Donor's pre-programmed Accept/Decline on an answered call (also gate-neutral).
_responses = st.sampled_from([SlotResponse.ACCEPTED, SlotResponse.DECLINED])


def _build_channel(
    consent: InMemoryConsentService, sender: MockSmsSender
) -> MockVoiceChannel:
    """A voice channel wired to the consent store + SMS-fallback sender."""
    return MockVoiceChannel(consent_store=consent, sms_sender=sender)


def _audio(channel: MockVoiceChannel):
    """Run the offline script→synthesize steps to obtain a placeable VoiceAudio."""
    script = channel.generate_script(_ctx())
    return channel.synthesize(script, "te")


# --------------------------------------------------------------------------- #
# Property 11 — the IFF
# --------------------------------------------------------------------------- #
@given(
    state=_consent_state(),
    unanswered=st.booleans(),
    response=_responses,
    captured_via=_captured_via,
)
def test_voice_consent_gate_iff_active_scope(
    state: tuple[InMemoryConsentService, bool],
    unanswered: bool,
    response: SlotResponse,
    captured_via: str,
) -> None:
    """Property 11: a call/SMS is placed IFF the donor has an active scope at call time."""
    consent, expected_active = state
    sender = MockSmsSender()
    channel = _build_channel(consent, sender)

    # Randomize the call mode: unanswered → SMS fallback, else an answered call.
    if unanswered:
        channel.program_unanswered(DONOR_ID, SLOT_ID)
    else:
        channel.program_response(DONOR_ID, SLOT_ID, response, captured_via=captured_via)

    audio = _audio(channel)

    # Ground truth: what the authoritative consent check reports at call time.
    assert (
        consent.has_active_scope(DONOR_ID, ConsentScope.CONTACT_FOR_SLOTS)
        is expected_active
    )

    if expected_active:
        # Contact-allowed region: exactly one thing is placed/sent, and it is
        # the mode we programmed (a recorded call, or an SMS fallback).
        outcome = channel.place_call(_contact(), audio)

        placed = channel.placed_calls
        fallbacks = channel.sms_fallbacks
        # Exactly one of the two channels fired (never both, never neither).
        assert (len(placed) + len(fallbacks)) == 1

        if unanswered:
            assert len(fallbacks) == 1
            assert channel.placed_calls == []
            assert fallbacks[0].donor_id == DONOR_ID
            assert fallbacks[0].slot_id == SLOT_ID
            # The fallback was actually delivered through the injected sender.
            assert len(sender.sent) == 1
            assert sender.sent[0].to_contact_id == "contact-1"
            assert outcome.fallback_used is True
            assert outcome.captured_via == "sms_fallback"
            assert outcome.response is SlotResponse.NO_RESPONSE
        else:
            assert len(placed) == 1
            assert channel.sms_fallbacks == []
            assert sender.sent == []
            assert placed[0].donor_id == DONOR_ID
            assert outcome.fallback_used is False
            assert outcome.response is response
    else:
        # Blocked region: the gate raises before anything is placed or sent,
        # and NEITHER log has an entry (no call, no SMS fallback).
        with pytest.raises(VoiceConsentError):
            channel.place_call(_contact(), audio)

        assert channel.placed_calls == []
        assert channel.sms_fallbacks == []
        assert sender.sent == []


@given(
    state=_consent_state(),
    unanswered=st.booleans(),
    response=_responses,
    captured_via=_captured_via,
)
def test_anything_placed_or_sent_implies_active_scope(
    state: tuple[InMemoryConsentService, bool],
    unanswered: bool,
    response: SlotResponse,
    captured_via: str,
) -> None:
    """Property 11 (the universal direction): if anything was placed/sent, the scope was active.

    This is the core safety direction — it must be impossible for a voice call
    to land in ``placed_calls`` or an SMS to land in ``sms_fallbacks`` /
    ``sender.sent`` unless the donor held an active ``contact_for_slots`` scope
    at the moment of the call.
    """
    consent, _ = state
    sender = MockSmsSender()
    channel = _build_channel(consent, sender)

    if unanswered:
        channel.program_unanswered(DONOR_ID, SLOT_ID)
    else:
        channel.program_response(DONOR_ID, SLOT_ID, response, captured_via=captured_via)

    audio = _audio(channel)

    # The gate either lets the call through or raises; swallow the raise so we
    # can inspect the logs afterwards regardless of the path taken.
    try:
        channel.place_call(_contact(), audio)
    except VoiceConsentError:
        pass

    if channel.placed_calls or channel.sms_fallbacks or sender.sent:
        assert consent.has_active_scope(DONOR_ID, ConsentScope.CONTACT_FOR_SLOTS)
