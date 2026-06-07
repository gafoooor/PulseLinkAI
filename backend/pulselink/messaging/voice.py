"""PulseLink Voice — the ``VoiceChannel`` seam and ``MockVoiceChannel`` (Task 10.1).

PulseLink Voice replaces manual coordinator phone calls with an automated,
empathetic, local-language voice call that offers a donor a slot. It is built
as an *extension* of the existing pluggable :class:`MessageChannel`
(``pulselink.messaging.channel``) so the Messaging Service can treat a voice
call exactly like any other channel (SMS / WhatsApp / Mock).

This module establishes **only the seam + the offline mock** (Task 10.1):

* :class:`VoiceChannel` — a ``Protocol`` that *extends* ``MessageChannel`` with
  the three voice-specific steps from the design:
  ``generate_script`` → ``synthesize`` → ``place_call``. Because it still
  carries ``MessageChannel.send``, a ``VoiceChannel`` is a drop-in channel.
* the supporting Pydantic types: :class:`VoiceCallContext`, :class:`VoiceScript`,
  :class:`VoiceAudio`, :class:`VoiceOutcome`.
* :class:`MockVoiceChannel` — a fully offline, deterministic implementation for
  the demo. It "plays" generated audio locally (records an artifact), and lets a
  test/demo *pre-program* the donor's Accept/Decline (captured via voice intent
  or DTMF keypad) so ``place_call`` is reproducible without telephony.
* :func:`get_voice_channel` — a config-selectable factory returning the mock for
  the offline demo (``VOICE_PROVIDER=mock``); the real Amazon Connect + Lex /
  Transcribe + Polly + SNS stack (``VOICE_PROVIDER=connect``) is wired in later.

Scope note (Tasks 10.1 + 10.2 — clean extension points are left for later tasks):

* LLM-backed call-script generation via ``LlmClient`` / Amazon Bedrock — Task 10.2
  (implemented here). When a ``MockVoiceChannel`` is constructed with an
  ``llm_client``, :meth:`MockVoiceChannel.generate_script` routes through
  :class:`VoiceScriptGenerator`, which produces a short, empathetic,
  context-grounded, no-medical-claims script (deterministic offline via
  ``MockLlmClient``; Amazon Bedrock / Claude Haiku in prod) and falls back to
  the canned template on any generation/validation failure. With no
  ``llm_client`` injected, generation stays canned exactly as in Task 10.1.
* Amazon Polly speech synthesis specifics — Task 10.3. Here ``synthesize``
  records a local placeholder artifact + a deterministic duration.
* The ``contact_for_slots`` consent gate on ``place_call`` — Task 10.4
  (implemented here). When a :class:`MockVoiceChannel` is constructed with a
  ``consent_store``, :meth:`MockVoiceChannel.place_call` asserts the donor holds
  an active ``contact_for_slots`` scope *before* placing the call, raising
  :class:`VoiceConsentError` and recording nothing when it does not. With no
  ``consent_store`` injected, the gate is a no-op (Task 10.1 behaviour). The gate
  is exposed via :meth:`MockVoiceChannel._assert_contact_allowed` so the SMS
  fallback (Task 10.5) inherits the same check.
* SMS fallback on unanswered / failed calls — Task 10.5 (implemented here).
  When a call is unanswered/failed, :meth:`MockVoiceChannel.place_call` routes
  to an injected SMS-sender seam (:class:`SmsSender`; a deterministic
  :class:`MockSmsSender` offline, Amazon SNS via :class:`SnsSmsSender` in prod)
  instead of capturing a voice/DTMF response — *after* the same
  ``contact_for_slots`` consent gate has passed, so a non-consenting donor gets
  neither the call nor the SMS. The resulting :class:`VoiceOutcome` carries
  ``fallback_used=True`` / ``captured_via="sms_fallback"`` and each send is
  recorded in :attr:`MockVoiceChannel.sms_fallbacks`. A call is simulated as
  unanswered via :meth:`MockVoiceChannel.program_unanswered`.
* Mapping the outcome to an event and the publish-once reliability loop — Task 10.6
  (implemented here). After a call is placed (answered or routed to the SMS
  fallback), :meth:`MockVoiceChannel.record_outcome` maps the resulting
  :class:`VoiceOutcome` to exactly one donor-response event for exactly one slot
  and publishes it **exactly once** to an injected ``event_bus`` seam (the
  in-memory bus for the demo; Amazon EventBridge in prod). The event reuses the
  existing ``donor.responded`` name/shape the Reliability_Service already
  consumes, so a ``DECLINED`` outcome drives the same reliability recompute that
  never raises the donor's score (Requirement 13.8). Publishing is idempotent
  per ``(donor_id, slot_id)`` so a redelivered/duplicate capture is never
  double-counted (Requirement 13.9). A ``NO_RESPONSE`` (unanswered call → SMS
  fallback) is not yet a donor response and is never published. With no
  ``event_bus`` injected the publish step is a no-op, preserving the behaviour
  of Tasks 10.1-10.5.

Requirements: 13.1, 13.6, 13.7, 13.8, 13.9, 13.10, 12.2
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from pulselink.common.config import Settings, VoiceProvider, get_settings
from pulselink.common.consent import ConsentStore
from pulselink.common.enums import ConsentScope, SlotResponse
from pulselink.common.event_bus import EventBus
from pulselink.common.models import ContactPoint, ReliabilityScore
from pulselink.messaging.channel import (
    DeliveryReceipt,
    LocalizedMessage,
    MessageChannel,
)
from pulselink.parsing.llm_client import (
    LlmClient,
    MockLlmClient,
    SchemaValidationError,
    validate_against_schema,
)

# The donor-response event name + payload shape the Reliability_Service already
# subscribes to (defined by the decline -> re-score flow). A captured voice
# outcome is published as this same event so the existing ReliabilityRescorer
# recomputes the donor — voice does NOT invent a divergent event (Task 10.6 /
# Requirement 13.9). Imported here so the name/shape stay a single source of
# truth; ``ReliabilityRescorer`` is referenced only as an optional read-back
# seam for the recomputed score (the recompute itself happens via the bus
# subscription, keeping voice loosely coupled to reliability).
from pulselink.subscription.promote import DONOR_RESPONDED_EVENT, ReliabilityRescorer

# How a donor's Accept/Decline was captured during (or after) a voice call.
# ``sms_fallback`` is reserved for the Task 10.5 unanswered/failed-call path.
CapturedVia = Literal["voice_intent", "dtmf", "sms_fallback"]


# --------------------------------------------------------------------------- #
# Supporting voice types (Pydantic, mirroring the design's Component 7)
# --------------------------------------------------------------------------- #
class VoiceCallContext(BaseModel):
    """Everything the voice agent needs to craft and place one call.

    ``bridge_context`` is the patient/bridge framing handed to the script
    generator; it MUST contain no medical claims (enforced when the real
    LLM-backed generator lands in Task 10.2).
    """

    donor_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    lang: str = Field(min_length=1)
    bridge_context: str = Field(default="")


class VoiceScript(BaseModel):
    """A short, empathetic, local-language call script.

    In the demo this is produced deterministically by
    :meth:`MockVoiceChannel.generate_script`; in production it is generated via
    the ``LlmClient`` (Amazon Bedrock / Claude Haiku) in Task 10.2.
    """

    lang: str = Field(min_length=1)
    text: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


class VoiceAudio(BaseModel):
    """Synthesized speech for a script.

    ``audio_uri`` points at the stored artifact — an S3 object in production
    (Amazon Polly), a local placeholder path in the offline demo.
    """

    lang: str = Field(min_length=1)
    audio_uri: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)


class VoiceOutcome(BaseModel):
    """The result of placing one voice call for one slot.

    Maps the captured Accept/Decline to exactly one :class:`SlotResponse` for
    exactly one slot. ``fallback_used`` flags the SMS-fallback path (wired in
    Task 10.5); it is ``False`` for a directly-answered call.
    """

    donor_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    response: SlotResponse
    captured_via: CapturedVia
    fallback_used: bool = False


# Captured responses that represent a terminal donor *decision* worth publishing
# as a donor-responded event (Task 10.6). A ``NO_RESPONSE`` — an unanswered/
# failed call routed to the SMS fallback — is not yet a donor response, so it is
# never published (publishing it would prematurely penalize a donor who may
# still reply to the SMS). Only ``ACCEPTED`` / ``DECLINED`` are published.
_PUBLISHABLE_RESPONSES: tuple[SlotResponse, ...] = (
    SlotResponse.ACCEPTED,
    SlotResponse.DECLINED,
)


class OutcomePublication(BaseModel):
    """The result of mapping one :class:`VoiceOutcome` to a domain event and
    publishing it to the event bus (Task 10.6, Requirements 13.7, 13.8, 13.9).

    Returned by :meth:`MockVoiceChannel.record_outcome` so callers/tests can see
    exactly what happened without inspecting the bus:

    * ``outcome`` — the captured outcome that was mapped (exactly one
      :class:`SlotResponse` for exactly one slot, Requirement 13.7).
    * ``published`` — whether *this* call published an event. ``True`` only on
      the first publish of a publishable (``ACCEPTED`` / ``DECLINED``) outcome
      when an ``event_bus`` is wired; ``False`` when no bus is wired, when the
      response is not publishable (``NO_RESPONSE``), or on an idempotent repeat
      of an already-published ``(donor_id, slot_id)`` (Requirement 13.9).
    * ``event_type`` — the published event name (``donor.responded``) when
      ``published`` is ``True``, else ``None``.
    * ``rescored`` — whether a ``DECLINED`` outcome was published to drive the
      reliability recompute (Requirement 13.8). ``False`` for non-declines and
      idempotent repeats.
    * ``new_reliability`` — the donor's recomputed score+tier read back from an
      injected :class:`~pulselink.subscription.promote.ReliabilityRescorer`
      after a published ``DECLINED`` (the synchronous in-memory bus has already
      recomputed by the time :meth:`record_outcome` returns); ``None`` when no
      rescorer is wired or the outcome was not a published decline. Guaranteed
      ``<=`` the pre-decline score (Requirement 13.8 / design Property 4).
    """

    outcome: VoiceOutcome
    published: bool
    event_type: Optional[str] = None
    rescored: bool = False
    new_reliability: Optional[ReliabilityScore] = None


# --------------------------------------------------------------------------- #
# The VoiceChannel seam — a MessageChannel extension
# --------------------------------------------------------------------------- #
@runtime_checkable
class VoiceChannel(MessageChannel, Protocol):
    """A :class:`MessageChannel` that places voice calls (Requirement 13.x).

    Because it *extends* ``MessageChannel`` it still satisfies ``send``, so the
    Messaging Service routes a slot offer to it just like SMS / WhatsApp / Mock.
    On top of ``send`` it exposes the three voice-specific steps from the
    design's PulseLink Voice component:

    1. :meth:`generate_script` — empathetic, local-language script
       (Amazon Bedrock / Claude Haiku in prod; canned in the demo).
    2. :meth:`synthesize` — script → speech in the donor's language
       (Amazon Polly in prod; local artifact in the demo).
    3. :meth:`place_call` — place the call and capture Accept/Decline via voice
       intent or DTMF (Amazon Connect + Lex / Transcribe in prod; simulated in
       the demo).
    """

    def generate_script(self, ctx: VoiceCallContext) -> VoiceScript:
        """Produce a short, empathetic, local-language script for ``ctx``."""
        ...

    def synthesize(self, script: VoiceScript, lang: str) -> VoiceAudio:
        """Synthesize ``script`` to speech in ``lang`` and store the artifact."""
        ...

    def place_call(self, to: ContactPoint, audio: VoiceAudio) -> VoiceOutcome:
        """Place the call to ``to``, play ``audio``, and capture the response."""
        ...


# --------------------------------------------------------------------------- #
# MockVoiceChannel — the offline, deterministic demo implementation
# --------------------------------------------------------------------------- #
class SynthesizedAudio(BaseModel):
    """An inspectable record of one ``synthesize`` call by the mock channel."""

    script: VoiceScript
    audio: VoiceAudio


class PlacedCall(BaseModel):
    """An inspectable record of one ``place_call`` simulated by the mock channel."""

    to_contact_id: str = Field(min_length=1)
    donor_id: str = Field(min_length=1)
    audio: VoiceAudio
    outcome: VoiceOutcome
    placed_at: datetime


class SentVoiceMessage(BaseModel):
    """An inspectable record of one ``send`` (channel-agnostic offer) on voice."""

    to: ContactPoint
    body: LocalizedMessage
    receipt: DeliveryReceipt


class _ProgrammedResponse(BaseModel):
    """A pre-programmed donor response used to make ``place_call`` deterministic."""

    slot_id: str = Field(min_length=1)
    response: SlotResponse
    captured_via: CapturedVia


# A canned, no-medical-claims script template per supported language. Task 10.2
# replaces this with an LlmClient-generated, context-grounded script.
_CANNED_SCRIPT: dict[str, str] = {
    "en": (
        "Hello, this is a PulseLink call. A patient you support needs blood "
        "soon. If you can help, please say Accept or press 1. To decline, say "
        "Decline or press 2. Thank you for being a lifesaver."
    ),
    "hi": (
        "Namaste, yah PulseLink ki call hai. Aapke dwara samarthit ek mareez "
        "ko jald rakt ki zaroorat hai. Madad ke liye 'Accept' kahein ya 1 "
        "dabayein, mana karne ke liye 'Decline' kahein ya 2 dabayein. Dhanyavaad."
    ),
    "te": (
        "Namaskaram, idi PulseLink call. Meeru sahaayam chese rogiki tvaralo "
        "raktam avasaram. Sahaayapadithe 'Accept' anandi leda 1 nokkandi, "
        "venakku 'Decline' anandi leda 2 nokkandi. Dhanyavaadalu."
    ),
    "ta": (
        "Vanakkam, idhu PulseLink alaippu. Neengal udavum oru noyaalikku "
        "viraivil ratham thevai. Udava 'Accept' enru sollungal alladhu 1 "
        "azhuthungal, marukka 'Decline' enru sollungal alladhu 2 azhuthungal. "
        "Nandri."
    ),
}

_MOCK_SCRIPT_MODEL_VERSION = "mock-voice-script-v0"

# Model version recorded on an LLM-backed script (Task 10.2). The mock path is
# deterministic and offline; the production path is Amazon Bedrock / Claude Haiku.
_LLM_SCRIPT_MODEL_VERSION = "voice-script-llm-v1"


# --------------------------------------------------------------------------- #
# LLM-backed script generation (Task 10.2)
# --------------------------------------------------------------------------- #
# The tiny structured-output contract for a voice script: a script string in a
# language. Every provider's output is validated against this before use, so a
# malformed or off-contract response is rejected and we fall back to canned.
VOICE_SCRIPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "script": {"type": "string"},
        "lang": {"type": "string"},
    },
    "required": ["script", "lang"],
}

# Substrings that signal a *medical claim*. A generated script must contain none
# of these (case-insensitive). PulseLink Voice invites a donor to give blood; it
# never diagnoses, treats, prescribes, or otherwise makes a clinical claim
# (Requirement 13.1). Kept as substrings to also catch inflected forms
# (e.g. "diagnos" → diagnose/diagnosis, "prescri" → prescribe/prescription).
_MEDICAL_CLAIM_TERMS: tuple[str, ...] = (
    "diagnos",
    "cure",
    "treat",
    "dosage",
    "dose",
    "prescri",
    "symptom",
    "disease",
    "therapy",
    "medicine",
    "medication",
    "medical",
)

# A constrained system prompt: short, warm, local-language, NO medical claims,
# grounded in the supplied context, and ending with the spoken/keypad
# Accept(1)/Decline(2) instructions. The model must return JSON matching
# VOICE_SCRIPT_SCHEMA.
_VOICE_SCRIPT_SYSTEM_PROMPT = (
    "You write a very short, warm, empathetic voice-call script that invites a "
    "registered blood donor to accept an upcoming donation slot for a patient "
    "they already support. Rules: (1) Write the script in the requested "
    "language code. (2) Keep it to 2-4 short spoken sentences. (3) Ground it "
    "ONLY in the provided context; invent no facts. (4) Make NO medical claims "
    "of any kind: no diagnosis, treatment, dosage, prognosis, or disease "
    "statements. (5) End with clear response instructions: to accept, say "
    "Accept or press 1; to decline, say Decline or press 2. (6) Be respectful "
    "and grateful. Return ONLY JSON matching the schema with keys 'script' and "
    "'lang'."
)


class VoiceScriptGenerationError(RuntimeError):
    """Raised when an LLM-backed script cannot be produced or fails validation.

    Callers (e.g. :meth:`MockVoiceChannel.generate_script`) treat this as the
    signal to fall back to the deterministic canned script.
    """


def _contains_medical_claim(text: str) -> bool:
    """Return ``True`` if ``text`` contains any medical-claim term."""
    lowered = text.lower()
    return any(term in lowered for term in _MEDICAL_CLAIM_TERMS)


class VoiceScriptGenerator:
    """Produce a :class:`VoiceScript` from a :class:`VoiceCallContext` via an
    :class:`~pulselink.parsing.llm_client.LlmClient` (Task 10.2, Requirement 13.1).

    Two paths, one contract:

    * **Offline / demo** — when the injected client is a
      :class:`~pulselink.parsing.llm_client.MockLlmClient` (a rules-based
      *parser* extractor, not a script writer), the generator produces a
      deterministic, context-grounded script in-process with no network call.
    * **Production** — for any other ``LlmClient`` (e.g. Amazon Bedrock / Claude
      Haiku), it issues a single constrained structured-output call against
      :data:`VOICE_SCRIPT_SCHEMA` at a low temperature.

    Either way the result is validated to be non-empty, in ``ctx.lang``, and
    free of medical claims; any failure raises :class:`VoiceScriptGenerationError`
    so the caller can fall back to the canned template.
    """

    def __init__(self, llm_client: LlmClient) -> None:
        self._llm = llm_client

    def generate(self, ctx: VoiceCallContext) -> VoiceScript:
        """Return a validated, context-grounded :class:`VoiceScript` for ``ctx``.

        Raises :class:`VoiceScriptGenerationError` on an empty script, a
        schema/transport failure, or a script that contains a medical claim.
        """
        if isinstance(self._llm, MockLlmClient):
            text = self._mock_script_text(ctx)
        else:
            text = self._llm_script_text(ctx)

        text = (text or "").strip()
        if not text:
            raise VoiceScriptGenerationError("generated voice script was empty")
        if _contains_medical_claim(text):
            raise VoiceScriptGenerationError(
                "generated voice script contained a medical claim"
            )
        return VoiceScript(
            lang=ctx.lang,
            text=text,
            model_version=_LLM_SCRIPT_MODEL_VERSION,
        )

    # ----- production path: a single constrained structured call ---------- #
    def _llm_script_text(self, ctx: VoiceCallContext) -> str:
        try:
            resp = self._llm.complete_structured(
                system=_VOICE_SCRIPT_SYSTEM_PROMPT,
                user=self._build_user_prompt(ctx),
                json_schema=VOICE_SCRIPT_SCHEMA,
                temperature=0.2,
                max_output_tokens=400,
            )
            # Defensive: re-validate even though providers self-validate.
            validate_against_schema(resp, VOICE_SCRIPT_SCHEMA)
        except SchemaValidationError as exc:
            raise VoiceScriptGenerationError(str(exc)) from exc
        except VoiceScriptGenerationError:
            raise
        except Exception as exc:  # transport / provider error → fall back
            raise VoiceScriptGenerationError(
                f"voice script generation failed: {exc}"
            ) from exc
        return str(resp.get("script", ""))

    @staticmethod
    def _build_user_prompt(ctx: VoiceCallContext) -> str:
        context = ctx.bridge_context.strip() or "A patient you support needs blood soon."
        return (
            f"Language code: {ctx.lang}\n"
            f"Donor id: {ctx.donor_id}\n"
            f"Slot id: {ctx.slot_id}\n"
            f"Context (no medical claims): {context}\n"
            "Write the call script now."
        )

    # ----- offline path: deterministic, context-grounded, no network ------ #
    @staticmethod
    def _mock_script_text(ctx: VoiceCallContext) -> str:
        """Deterministically weave the context into the canned local-language
        script so the offline demo produces a *grounded* script without a call.
        """
        context = ctx.bridge_context.strip()
        base = _CANNED_SCRIPT.get(ctx.lang, _CANNED_SCRIPT["en"])
        if context:
            return f"{context} {base}"
        return base


class VoiceConsentError(RuntimeError):
    """Raised when a voice call (or its SMS fallback) is attempted for a donor
    who does not hold an active ``contact_for_slots`` consent scope.

    This is the **voice consent gate** (Requirements 13.4, 13.5 / design
    Property 11). :meth:`MockVoiceChannel.place_call` raises it *before* placing
    the call when a consent store is configured and the donor's scope is missing
    or revoked, so no call is ever placed (and nothing is recorded) without
    consent. The SMS fallback (Task 10.5) inherits the same gate.
    """


class MockVoiceChannel:
    """Offline, deterministic :class:`VoiceChannel` for the demo and tests.

    The full call flow runs in-process with no telephony (Requirement 13.10):

    * :meth:`generate_script` returns a canned, no-medical-claims script in the
      requested language (LLM-backed generation is Task 10.2).
    * :meth:`synthesize` records a local audio artifact and returns a
      :class:`VoiceAudio` with a deterministic duration (Polly is Task 10.3).
    * :meth:`place_call` simulates the call: a test/demo first *pre-programs* the
      donor's Accept/Decline via :meth:`program_response`, and ``place_call``
      maps that to a :class:`VoiceOutcome` and records it.
    * :meth:`send` lets the channel act as a plain ``MessageChannel`` (records
      the offer and returns a ``voice`` receipt).

    Every action is appended to an inspectable in-memory log
    (:attr:`scripts`, :attr:`synthesized`, :attr:`placed_calls`, :attr:`sent`)
    so tests/demos can assert exactly what happened — mirroring ``MockChannel``.

    NOTE: the ``contact_for_slots`` consent gate IS enforced on
    :meth:`place_call` when a ``consent_store`` is injected (Task 10.4); with no
    store configured it is a no-op, preserving the Task 10.1 behaviour.
    """

    CHANNEL = "voice"

    def __init__(
        self,
        llm_client: Optional[LlmClient] = None,
        settings: Optional[Settings] = None,
        synthesizer: Optional["SpeechSynthesizer"] = None,
        consent_store: Optional[ConsentStore] = None,
        sms_sender: Optional["SmsSender"] = None,
        event_bus: Optional[EventBus] = None,
        rescorer: Optional[ReliabilityRescorer] = None,
    ) -> None:
        # ``llm_client`` enables LLM-backed script generation (Task 10.2). When
        # provided, :meth:`generate_script` routes through VoiceScriptGenerator
        # and falls back to the canned template on any failure. When ``None``,
        # generation stays canned exactly as in Task 10.1.
        self._llm_client = llm_client
        # ``settings`` supply the configured default language used to resolve the
        # effective voice language (Task 10.3 / Requirement 13.3). Resolved
        # lazily via :func:`resolve_voice_lang` so the offline default still
        # works when ``None`` is passed.
        self._settings = settings
        # ``synthesizer`` is the speech-synthesis seam (Task 10.3). The offline
        # demo uses a deterministic :class:`MockSpeechSynthesizer`; production
        # injects a :class:`PollySynthesizer` (Amazon Polly neural voices + S3).
        self._synthesizer: "SpeechSynthesizer" = synthesizer or MockSpeechSynthesizer()
        # ``consent_store`` enables the ``contact_for_slots`` voice consent gate
        # (Task 10.4 / Requirements 13.4, 13.5). When provided, :meth:`place_call`
        # asserts the donor holds an active scope before placing the call (and the
        # SMS fallback in Task 10.5 inherits the same check). When ``None`` the
        # gate is a no-op, keeping the Task 10.1 behaviour intact.
        self._consent_store = consent_store
        # ``sms_sender`` is the SMS-fallback seam (Task 10.5 / Requirement 13.6).
        # When a voice call is unanswered or fails, the channel sends an SMS
        # offer to the same donor through this sender — but only after the SAME
        # ``contact_for_slots`` consent gate has passed (see :meth:`place_call`).
        # The offline demo defaults to a deterministic :class:`MockSmsSender`
        # (records in-process, no network); production injects an
        # :class:`SnsSmsSender` (Amazon SNS). Every fallback send is also
        # appended to the inspectable :attr:`sms_fallbacks` log.
        self._sms_sender: "SmsSender" = sms_sender or MockSmsSender()
        # ``event_bus`` is the publish-once outcome seam (Task 10.6 /
        # Requirements 13.7-13.9). When wired, :meth:`record_outcome` (invoked
        # automatically by :meth:`place_call`) publishes the captured outcome as
        # a ``donor.responded`` event so the Reliability_Service recomputes; a
        # subscribed :class:`~pulselink.subscription.promote.ReliabilityRescorer`
        # consumes it. In-memory for the demo, Amazon EventBridge in prod. When
        # ``None`` the publish step is a no-op, preserving Tasks 10.1-10.5.
        self._event_bus = event_bus
        # ``rescorer`` is an optional read-back seam: when wired (and subscribed
        # to the SAME bus) :meth:`record_outcome` reads the freshly recomputed
        # score off it after publishing a DECLINED outcome, purely to surface it
        # on :class:`OutcomePublication`. The recompute itself happens through
        # the bus subscription, not a direct call, so voice stays decoupled.
        self._rescorer = rescorer
        self._scripts: list[VoiceScript] = []
        self._synthesized: list[SynthesizedAudio] = []
        self._placed_calls: list[PlacedCall] = []
        self._sent: list[SentVoiceMessage] = []
        self._sms_fallbacks: list[SmsFallback] = []
        self._programmed: dict[str, _ProgrammedResponse] = {}
        # Publish-once ledger (Task 10.6 / Requirement 13.9): the set of
        # ``(donor_id, slot_id)`` outcomes already published. A repeat capture
        # of the same outcome is NOT republished, so a redelivered/duplicate
        # response can never double-count the donor's reliability history.
        self._published_outcomes: set[tuple[str, str]] = set()
        # Donors whose *next* call is simulated as unanswered/failed → SMS
        # fallback. Maps donor_id → slot_id (see :meth:`program_unanswered`).
        self._unanswered: dict[str, str] = {}
        self._counter: int = 0

    # ----- VoiceChannel: script generation -------------------------------- #
    def generate_script(self, ctx: VoiceCallContext) -> VoiceScript:
        """Return a short, empathetic, no-medical-claims script in ``ctx.lang``.

        When an ``llm_client`` was injected (Task 10.2), the script is generated
        through :class:`VoiceScriptGenerator` — deterministically offline via
        ``MockLlmClient`` or via Amazon Bedrock / Claude Haiku in prod — grounded
        in ``ctx.bridge_context`` and validated to contain no medical claims. On
        any generation/validation failure, or when no client was injected
        (Task 10.1 behaviour), it falls back to the canned local-language
        template (English when the language has no canned entry).
        """
        if self._llm_client is not None:
            try:
                script = VoiceScriptGenerator(self._llm_client).generate(ctx)
                self._scripts.append(script)
                return script
            except VoiceScriptGenerationError:
                # Fall through to the deterministic canned template below.
                pass

        text = _CANNED_SCRIPT.get(ctx.lang, _CANNED_SCRIPT["en"])
        script = VoiceScript(
            lang=ctx.lang,
            text=text,
            model_version=_MOCK_SCRIPT_MODEL_VERSION,
        )
        self._scripts.append(script)
        return script

    # ----- VoiceChannel: synthesis (Polly seam; local artifact for demo) -- #
    def synthesize(self, script: VoiceScript, lang: str) -> VoiceAudio:
        """Synthesize ``script`` to speech in the donor's effective language.

        The requested ``lang`` (the donor's ``preferred_lang``) is resolved via
        :func:`resolve_voice_lang`, falling back to the configured default
        language when it is ``None``/empty/unsupported (Requirement 13.3). The
        produced :class:`VoiceAudio` therefore always carries the **effective**
        (resolved) language (Requirement 13.2).

        The actual synthesis runs through the injected :class:`SpeechSynthesizer`
        seam: the offline demo's :class:`MockSpeechSynthesizer` records a
        deterministic local artifact + duration (so tests are reproducible);
        production's :class:`PollySynthesizer` calls Amazon Polly neural voices
        and stores the mp3 to S3.
        """
        effective_lang = resolve_voice_lang(lang, self._settings)
        result = self._synthesizer.synthesize(script.text, effective_lang)
        audio = VoiceAudio(
            lang=effective_lang,
            audio_uri=result.audio_uri,
            duration_ms=result.duration_ms,
        )
        self._synthesized.append(SynthesizedAudio(script=script, audio=audio))
        return audio

    # ----- VoiceChannel: place call (simulated in 10.1) ------------------- #
    def _assert_contact_allowed(self, subject_id: str) -> None:
        """Enforce the ``contact_for_slots`` voice consent gate (Task 10.4).

        When a ``consent_store`` is configured, raise :class:`VoiceConsentError`
        unless ``subject_id`` (the donor) holds an active ``contact_for_slots``
        scope right now — so a missing or revoked scope blocks the call (and,
        from Task 10.5, the SMS fallback) before anything is placed or recorded.
        When no consent store is configured the gate is a no-op, preserving the
        Task 10.1 behaviour (Requirements 13.4, 13.5).
        """
        if self._consent_store is None:
            return
        if not self._consent_store.has_active_scope(
            subject_id, ConsentScope.CONTACT_FOR_SLOTS
        ):
            raise VoiceConsentError(
                f"voice contact blocked for subject_id={subject_id!r}: no active "
                "contact_for_slots consent scope (Requirements 13.4, 13.5)"
            )

    def program_response(
        self,
        donor_id: str,
        slot_id: str,
        response: SlotResponse,
        captured_via: CapturedVia = "voice_intent",
    ) -> None:
        """Pre-program how ``donor_id`` will respond on their next call.

        This is the offline hook that makes :meth:`place_call` deterministic:
        the test/demo states up front whether the donor accepts or declines and
        whether it was captured by spoken intent or DTMF keypad.
        """
        self._programmed[donor_id] = _ProgrammedResponse(
            slot_id=slot_id,
            response=response,
            captured_via=captured_via,
        )

    def program_unanswered(self, donor_id: str, slot_id: str) -> None:
        """Pre-program ``donor_id``'s next call as unanswered / failed.

        This is the offline hook for the Task 10.5 SMS-fallback path: it states
        up front that the next :meth:`place_call` for this donor will NOT be
        answered (no voice/DTMF response is captured). When that call is placed,
        the channel instead sends an SMS offer to the same donor — subject to
        the same ``contact_for_slots`` consent gate — and returns a
        :class:`VoiceOutcome` with ``fallback_used=True`` and
        ``captured_via="sms_fallback"`` (Requirement 13.6).

        An unanswered programming takes precedence over any
        :meth:`program_response` for the same donor.
        """
        self._unanswered[donor_id] = slot_id

    def place_call(self, to: ContactPoint, audio: VoiceAudio) -> VoiceOutcome:
        """Simulate placing the call and capturing the donor's response.

        Resolves the donor from ``to.subject_id`` and maps their pre-programmed
        response (see :meth:`program_response`) to a :class:`VoiceOutcome`,
        recording the call in :attr:`placed_calls`. Raises ``KeyError`` if no
        response was programmed for the donor — the offline equivalent of "we
        never told the mock what would happen on this call".

        The **voice consent gate** runs first (Task 10.4 / Requirements 13.4,
        13.5): when a ``consent_store`` is configured and the donor lacks an
        active ``contact_for_slots`` scope, :class:`VoiceConsentError` is raised
        *before* anything is placed or recorded, so a non-consenting donor is
        never called. With no consent store the gate is a no-op (Task 10.1).

        The unanswered/failed-call SMS fallback (Task 10.5 / Requirement 13.6):
        when the donor's next call has been programmed as unanswered/failed via
        :meth:`program_unanswered`, this method does NOT capture a voice/DTMF
        response. Instead — only after the consent gate above has passed — it
        sends an SMS offer to the same donor through the injected ``sms_sender``
        (a :class:`MockSmsSender` offline; Amazon SNS in prod), records it in
        :attr:`sms_fallbacks`, and returns a :class:`VoiceOutcome` with
        ``fallback_used=True`` and ``captured_via="sms_fallback"``. Because the
        consent gate runs first, a donor without an active ``contact_for_slots``
        scope gets neither the call nor the SMS fallback (Requirement 13.5).
        """
        # --- The voice consent gate (Requirement 13.4 / Property 11) ------- #
        # Checked first so no call is placed (and nothing recorded) for a donor
        # without an active contact_for_slots scope. The SMS fallback (10.5)
        # reuses this same assertion — so a non-consenting donor is never
        # called *or* texted.
        self._assert_contact_allowed(to.subject_id)

        donor_id = to.subject_id

        # --- Unanswered / failed call → SMS fallback (Requirement 13.6) ---- #
        # The call is attempted but not answered: capture no voice/DTMF
        # response and instead send the SMS offer to the same (consenting)
        # donor. Takes precedence over any programmed voice response.
        if donor_id in self._unanswered:
            outcome = self._send_sms_fallback(to, audio, donor_id)
            self.record_outcome(outcome)
            return outcome

        programmed = self._programmed.get(donor_id)
        if programmed is None:
            raise KeyError(
                f"no programmed voice response for donor_id={donor_id!r}; call "
                "program_response(...) first (offline demo is deterministic)"
            )
        outcome = VoiceOutcome(
            donor_id=donor_id,
            slot_id=programmed.slot_id,
            response=programmed.response,
            captured_via=programmed.captured_via,
            fallback_used=False,
        )
        self._placed_calls.append(
            PlacedCall(
                to_contact_id=to.contact_id,
                donor_id=donor_id,
                audio=audio,
                outcome=outcome,
                placed_at=datetime.now(timezone.utc),
            )
        )
        # Task 10.6: map the captured outcome to exactly one donor-response
        # event and publish it once (no-op when no event bus is wired).
        self.record_outcome(outcome)
        return outcome

    def _send_sms_fallback(
        self, to: ContactPoint, audio: VoiceAudio, donor_id: str
    ) -> VoiceOutcome:
        """Send the SMS fallback for an unanswered/failed call (Task 10.5).

        Called by :meth:`place_call` only *after* the ``contact_for_slots``
        consent gate has passed, so this never sends an SMS to a donor who has
        not consented (Requirement 13.5). Renders a localized SMS offer in the
        audio's effective language, delivers it through the injected
        ``sms_sender`` (Amazon SNS in prod; a :class:`MockSmsSender` offline),
        records the send in :attr:`sms_fallbacks`, and returns a
        :class:`VoiceOutcome` flagged ``fallback_used=True`` /
        ``captured_via="sms_fallback"`` with a ``NO_RESPONSE`` outcome (the
        donor has not yet replied to the SMS).
        """
        slot_id = self._unanswered[donor_id]
        lang = audio.lang
        body = _render_fallback_sms(lang)
        receipt = self._sms_sender.send_sms(to, body, lang)
        self._sms_fallbacks.append(
            SmsFallback(
                message_id=receipt.message_id,
                to_contact_id=to.contact_id,
                donor_id=donor_id,
                slot_id=slot_id,
                lang=lang,
                body=body,
                sent_at=receipt.sent_at,
            )
        )
        return VoiceOutcome(
            donor_id=donor_id,
            slot_id=slot_id,
            response=SlotResponse.NO_RESPONSE,
            captured_via="sms_fallback",
            fallback_used=True,
        )

    # ----- VoiceChannel: map outcome -> event, publish once (Task 10.6) --- #
    def record_outcome(self, outcome: VoiceOutcome) -> OutcomePublication:
        """Map ``outcome`` to one donor-response event and publish it once.

        This is the publish-once reliability loop (Task 10.6 / Requirements
        13.7, 13.8, 13.9). A :class:`VoiceOutcome` already encodes exactly one
        :class:`SlotResponse` for exactly one slot (Requirement 13.7); this
        method maps it to the existing ``donor.responded`` event the
        Reliability_Service consumes and publishes it to the injected
        ``event_bus`` (the in-memory bus for the demo; Amazon EventBridge in
        prod) so the donor is recomputed.

        Publish-once / idempotency (Requirement 13.9): the event is published at
        most once per ``(donor_id, slot_id)``. A repeat call for an
        already-published outcome (e.g. a redelivered capture, or because
        :meth:`place_call` already published it) is a no-op that returns
        ``published=False`` — so a duplicate response can never double-count the
        donor's reliability history.

        Decline → re-score (Requirement 13.8): a published ``DECLINED`` outcome
        drives the subscribed
        :class:`~pulselink.subscription.promote.ReliabilityRescorer`, whose
        recompute (via :func:`pulselink.reliability.scoring.on_donor_response`)
        is guaranteed never to raise the donor's score. Because the in-memory
        bus is synchronous, that recompute has already run by the time
        :meth:`publish` returns; when a ``rescorer`` is also injected the
        recomputed score is read back onto the result's ``new_reliability``.

        A ``NO_RESPONSE`` outcome (an unanswered/failed call routed to the SMS
        fallback) is not yet a donor response and is never published. With no
        ``event_bus`` wired the method is a no-op (returns ``published=False``),
        preserving the behaviour of Tasks 10.1-10.5.
        """
        not_published = OutcomePublication(outcome=outcome, published=False)

        # No bus wired → publish is a no-op (Tasks 10.1-10.5 behaviour intact).
        if self._event_bus is None:
            return not_published

        # Only a terminal ACCEPTED/DECLINED decision is published; a pending
        # NO_RESPONSE (SMS fallback) is not yet a donor response.
        if outcome.response not in _PUBLISHABLE_RESPONSES:
            return not_published

        # Publish-once: never republish the same (donor, slot) outcome.
        key = (outcome.donor_id, outcome.slot_id)
        if key in self._published_outcomes:
            return not_published

        # Map to exactly one donor-response event for exactly one slot and
        # publish it exactly once (Requirements 13.7, 13.9). Reuses the event
        # name/shape the Reliability_Service already subscribes to.
        self._event_bus.publish(
            DONOR_RESPONDED_EVENT,
            {
                "donorId": outcome.donor_id,
                "response": outcome.response.value,
                "slotId": outcome.slot_id,
            },
        )
        self._published_outcomes.add(key)

        # On a DECLINED outcome the publish above has (synchronously, for the
        # in-memory bus) driven the reliability recompute (Requirement 13.8).
        rescored = outcome.response is SlotResponse.DECLINED
        new_reliability: Optional[ReliabilityScore] = None
        if rescored and self._rescorer is not None:
            recompute = self._rescorer.last_recompute.get(outcome.donor_id)
            if recompute is not None:
                new_reliability = recompute.score

        return OutcomePublication(
            outcome=outcome,
            published=True,
            event_type=DONOR_RESPONDED_EVENT,
            rescored=rescored,
            new_reliability=new_reliability,
        )

    # ----- MessageChannel: plain send ------------------------------------- #
    def send(self, to: ContactPoint, body: LocalizedMessage) -> DeliveryReceipt:
        """Act as a plain ``MessageChannel``: record the offer, return a receipt.

        Lets the Messaging Service hand a localized offer to the voice channel
        through the same seam as every other channel. Nothing leaves the
        process; the offer is recorded in :attr:`sent`.
        """
        self._counter += 1
        receipt = DeliveryReceipt(
            message_id=f"voice-msg-{self._counter}",
            channel=self.CHANNEL,
            status="delivered",
            to_contact_id=to.contact_id,
            lang=body.lang,
            sent_at=datetime.now(timezone.utc),
        )
        self._sent.append(SentVoiceMessage(to=to, body=body, receipt=receipt))
        return receipt

    # ----- Inspectable logs ----------------------------------------------- #
    @property
    def scripts(self) -> list[VoiceScript]:
        """A copy of the generated-script log (most recent last)."""
        return list(self._scripts)

    @property
    def synthesized(self) -> list[SynthesizedAudio]:
        """A copy of the synthesized-audio log (most recent last)."""
        return list(self._synthesized)

    @property
    def placed_calls(self) -> list[PlacedCall]:
        """A copy of the placed-call log (most recent last)."""
        return list(self._placed_calls)

    @property
    def sent(self) -> list[SentVoiceMessage]:
        """A copy of the plain-``send`` log (most recent last)."""
        return list(self._sent)

    @property
    def sms_fallbacks(self) -> list["SmsFallback"]:
        """A copy of the SMS-fallback log (most recent last) — Task 10.5.

        Each entry records an SMS offer sent because a voice call was
        unanswered/failed (Requirement 13.6). The list stays empty when no
        fallback occurred.
        """
        return list(self._sms_fallbacks)


# --------------------------------------------------------------------------- #
# Config-selectable factory
# --------------------------------------------------------------------------- #
def get_voice_channel(settings: Optional[Settings] = None) -> VoiceChannel:
    """Return the configured :class:`VoiceChannel` (Requirements 12.2, 13.10).

    Defaults to :class:`MockVoiceChannel` for the fully offline demo
    (``VOICE_PROVIDER=mock``). Selecting ``VOICE_PROVIDER=connect`` (Amazon
    Connect + Lex / Transcribe + Polly + SNS) raises ``NotImplementedError``
    here — those adapters are wired in by later tasks, so the seam fails loudly
    rather than silently falling back.
    """
    settings = settings or get_settings()
    provider = settings.voice_provider

    if provider is VoiceProvider.MOCK:
        return MockVoiceChannel()

    if provider is VoiceProvider.CONNECT:
        raise NotImplementedError(
            "VOICE_PROVIDER='connect' selects the Amazon Connect + Lex/"
            "Transcribe + Polly + SNS voice stack, which is implemented in "
            "later tasks. Use 'mock' for the offline demo."
        )

    raise NotImplementedError(  # pragma: no cover - defensive, enum is exhaustive
        f"Unsupported VOICE_PROVIDER={provider!r}"
    )


# --------------------------------------------------------------------------- #
# Speech synthesis (Task 10.3) — language resolution + the SpeechSynthesizer seam
# --------------------------------------------------------------------------- #
# The ultimate hard fallback when neither the donor's preferred language nor the
# configured default language is a supported voice language.
VOICE_FALLBACK_LANG = "en"

# Map each supported voice language to an Amazon Polly **neural** VoiceId used in
# production (the offline demo never calls Polly). These are documented prod
# hints, not load-bearing for the demo:
#
# * ``en`` / ``hi`` → "Kajal", Amazon Polly's bilingual en-IN / hi-IN *neural*
#   voice — a natural Indian-accent voice covering both English and Hindi.
# * ``te`` (Telugu) / ``ta`` (Tamil) → Amazon Polly currently ships **no**
#   native neural voice for these languages, so they are mapped to the closest
#   available Indian neural voice ("Kajal") as a documented stand-in. Revisit if
#   Polly adds native Telugu/Tamil neural voices (the seam needs no other
#   change — only this table).
#
# The keys of this table are the canonical set of supported voice languages
# (en/hi/te/ta), mirroring the canned-script catalog.
POLLY_VOICE_IDS: dict[str, str] = {
    "en": "Kajal",
    "hi": "Kajal",
    "te": "Kajal",
    "ta": "Kajal",
}

# The Polly engine used for the mapped voices above.
_POLLY_ENGINE = "neural"


def _normalize_voice_lang(lang: Optional[str]) -> Optional[str]:
    """Trim and lower-case a language tag; return ``None`` for empty/``None``."""
    if lang is None:
        return None
    normalized = lang.strip().lower()
    return normalized or None


def resolve_voice_lang(
    preferred_lang: Optional[str], settings: Optional[Settings] = None
) -> str:
    """Resolve the effective voice language (Requirements 13.2, 13.3).

    Resolution order:
    1. the donor's ``preferred_lang`` when recorded and a supported voice
       language (present in :data:`POLLY_VOICE_IDS`);
    2. otherwise the configured default language (``settings.default_lang``)
       when supported — this is the Requirement 13.3 fallback for a donor with
       no recorded preferred language (``None``/empty) or an unsupported one;
    3. otherwise the ultimate :data:`VOICE_FALLBACK_LANG` (English).

    Language tags are normalized (trimmed, lower-cased) before lookup so
    ``"TE"`` / ``" te "`` resolve to ``"te"``.
    """
    settings = settings or get_settings()

    candidate = _normalize_voice_lang(preferred_lang)
    if candidate and candidate in POLLY_VOICE_IDS:
        return candidate

    default = _normalize_voice_lang(settings.default_lang)
    if default and default in POLLY_VOICE_IDS:
        return default

    return VOICE_FALLBACK_LANG


def polly_voice_id_for(lang: str) -> str:
    """Return the Amazon Polly neural VoiceId mapped to ``lang``.

    Falls back to the English voice for any unmapped language so a caller that
    somehow passes an unsupported tag still gets a usable voice.
    """
    normalized = _normalize_voice_lang(lang) or VOICE_FALLBACK_LANG
    return POLLY_VOICE_IDS.get(normalized, POLLY_VOICE_IDS[VOICE_FALLBACK_LANG])


class SpeechSynthesisError(RuntimeError):
    """Raised when speech synthesis cannot be performed (e.g. the production
    :class:`PollySynthesizer` was invoked without ``boto3`` installed)."""


class SpeechSynthesisResult(BaseModel):
    """The product of one synthesis call: a stored artifact + its duration.

    ``audio_uri`` points at the persisted artifact — an ``s3://`` object in
    production (Amazon Polly), a ``file://`` placeholder path in the offline
    demo. ``duration_ms`` is the spoken length in milliseconds.
    """

    audio_uri: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)


@runtime_checkable
class SpeechSynthesizer(Protocol):
    """The pluggable speech-synthesis seam used by :class:`MockVoiceChannel`.

    Mirrors the ``MessageChannel`` / ``LlmClient`` pattern: the demo injects a
    :class:`MockSpeechSynthesizer` (offline, deterministic), production injects a
    :class:`PollySynthesizer` (Amazon Polly neural voices + S3). The contract is
    intentionally tiny — synthesize ``text`` in ``lang`` and return where the
    audio was stored plus how long it runs.
    """

    def synthesize(self, text: str, lang: str) -> SpeechSynthesisResult:
        """Synthesize ``text`` in ``lang`` to a stored audio artifact."""
        ...


class MockSpeechSynthesizer:
    """Offline, deterministic :class:`SpeechSynthesizer` for the demo and tests.

    Records a local placeholder artifact (``file://...``) instead of calling a
    cloud TTS service, and derives a reproducible duration from the text length
    (≈60 ms per character, floored at 1 s) so synthesized audio is stable across
    runs. This is the Task 10.1 behaviour, now factored behind the seam.
    """

    def __init__(self, artifact_root: str = "file://mock-voice-audio") -> None:
        self._artifact_root = artifact_root.rstrip("/")
        self._counter = 0

    def synthesize(self, text: str, lang: str) -> SpeechSynthesisResult:
        self._counter += 1
        duration_ms = max(1000, len(text) * 60)
        return SpeechSynthesisResult(
            audio_uri=f"{self._artifact_root}/{lang}/{self._counter}.wav",
            duration_ms=duration_ms,
        )


class PollySynthesizer:
    """Production :class:`SpeechSynthesizer` backed by Amazon Polly + S3.

    Synthesizes ``text`` with the neural VoiceId mapped from ``lang`` (see
    :data:`POLLY_VOICE_IDS`) and stores the resulting mp3 to S3, returning its
    ``s3://`` URI. ``boto3`` is imported lazily at call time so this class can be
    constructed (and the module imported) in the offline demo / tests with no
    AWS SDK and no credentials present; it raises :class:`SpeechSynthesisError`
    with a clear message only when :meth:`synthesize` is actually invoked
    without ``boto3``.
    """

    def __init__(
        self,
        s3_bucket: str,
        s3_prefix: str = "voice-audio",
        settings: Optional[Settings] = None,
    ) -> None:
        self._s3_bucket = s3_bucket
        self._s3_prefix = s3_prefix.strip("/")
        self._settings = settings
        self._counter = 0

    def synthesize(self, text: str, lang: str) -> SpeechSynthesisResult:
        try:
            import boto3  # deferred: no SDK/creds needed at import or in tests
        except ImportError as exc:  # pragma: no cover - exercised only sans boto3
            raise SpeechSynthesisError(
                "PollySynthesizer requires the 'boto3' package to call Amazon "
                "Polly; install boto3 or use MockSpeechSynthesizer for the "
                "offline demo."
            ) from exc

        settings = self._settings or get_settings()
        voice_id = polly_voice_id_for(lang)

        polly = boto3.client("polly", region_name=settings.aws_region)
        response = polly.synthesize_speech(
            Text=text,
            OutputFormat="mp3",
            VoiceId=voice_id,
            Engine=_POLLY_ENGINE,
        )

        # Persist the mp3 stream to S3 and return its canonical URI.
        self._counter += 1
        key = f"{self._s3_prefix}/{lang}/{voice_id}-{self._counter}.mp3"
        audio_stream = response["AudioStream"].read()
        s3 = boto3.client("s3", region_name=settings.aws_region)
        s3.put_object(Bucket=self._s3_bucket, Key=key, Body=audio_stream)

        # Polly's synthesize_speech does not return a duration; estimate it from
        # the synthesized text so the artifact still carries a positive runtime.
        duration_ms = max(1000, len(text) * 60)
        return SpeechSynthesisResult(
            audio_uri=f"s3://{self._s3_bucket}/{key}",
            duration_ms=duration_ms,
        )


# --------------------------------------------------------------------------- #
# SMS fallback on unanswered / failed calls (Task 10.5 / Requirement 13.6)
# --------------------------------------------------------------------------- #
# A short, no-medical-claims SMS offer per supported language, mirroring the
# canned voice-script catalog. Sent to the same donor when a voice call goes
# unanswered or fails. The Donor_Interface deep link / Accept-Decline handling
# lives in the messaging service (Task 9.x); this is just the fallback offer
# text. English is the hard fallback for any unlisted language.
_FALLBACK_SMS: dict[str, str] = {
    "en": (
        "PulseLink: we tried to call you. A patient you support needs blood "
        "soon. Reply ACCEPT to help or DECLINE to pass. Thank you."
    ),
    "hi": (
        "PulseLink: humne aapko call kiya tha. Aapke dwara samarthit ek mareez "
        "ko jald rakt chahiye. Madad ke liye ACCEPT bhejein, mana karne ke liye "
        "DECLINE bhejein. Dhanyavaad."
    ),
    "te": (
        "PulseLink: memu mీku call chesamu. Meeru sahaayam chese rogiki tvaralo "
        "raktam avasaram. Sahaayapadithe ACCEPT pampandi, ventane venakku "
        "DECLINE pampandi. Dhanyavaadalu."
    ),
    "ta": (
        "PulseLink: ungalai azhaikka muyandrom. Neengal udavum oru noyaalikku "
        "viraivil ratham thevai. Udava ACCEPT anuppungal, marukka DECLINE "
        "anuppungal. Nandri."
    ),
}

# Model version stamped on a mock SMS-fallback send. Production SNS sends carry
# the provider's own message id.
_MOCK_SMS_MODEL_VERSION = "mock-sms-fallback-v0"


def _render_fallback_sms(lang: str) -> str:
    """Return the localized SMS-fallback offer for ``lang`` (English fallback)."""
    normalized = _normalize_voice_lang(lang) or VOICE_FALLBACK_LANG
    return _FALLBACK_SMS.get(normalized, _FALLBACK_SMS[VOICE_FALLBACK_LANG])


class SmsFallback(BaseModel):
    """An inspectable record of one SMS-fallback offer (Requirement 13.6).

    Recorded by :class:`MockVoiceChannel` whenever a voice call is
    unanswered/failed and the SMS offer is sent to the same (consenting) donor.
    Carries ids / channel metadata and the rendered body only — never a raw
    contact value (Requirement 7.4).
    """

    message_id: str = Field(min_length=1)
    to_contact_id: str = Field(min_length=1)
    donor_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    lang: str = Field(min_length=1)
    body: str = Field(min_length=1)
    sent_at: datetime


class SmsDeliveryReceipt(BaseModel):
    """The result of handing an SMS-fallback offer to an :class:`SmsSender`."""

    message_id: str = Field(min_length=1)
    to_contact_id: str = Field(min_length=1)
    lang: str = Field(min_length=1)
    sent_at: datetime


@runtime_checkable
class SmsSender(Protocol):
    """The pluggable SMS-fallback seam used by :class:`MockVoiceChannel`.

    Mirrors the ``MessageChannel`` / ``SpeechSynthesizer`` pattern: the demo
    injects a :class:`MockSmsSender` (offline, deterministic, records in
    process), production injects an :class:`SnsSmsSender` (Amazon SNS). The
    contract is intentionally tiny — send ``body`` to ``to`` in ``lang`` and
    return where/when it was delivered.
    """

    def send_sms(self, to: ContactPoint, body: str, lang: str) -> SmsDeliveryReceipt:
        """Send ``body`` to the contact ``to`` in ``lang`` and return a receipt."""
        ...


class MockSmsSender:
    """Offline, deterministic :class:`SmsSender` for the demo and tests.

    "Sends" an SMS by recording it in an in-memory log and returning a
    deliverable receipt with a deterministic, monotonically increasing
    ``message_id``. Nothing leaves the process. The recorded log is inspectable
    via :attr:`sent` so tests/demos can assert exactly what was delivered.
    """

    def __init__(self) -> None:
        self._sent: list[SmsDeliveryReceipt] = []
        self._counter: int = 0

    def send_sms(self, to: ContactPoint, body: str, lang: str) -> SmsDeliveryReceipt:
        self._counter += 1
        receipt = SmsDeliveryReceipt(
            message_id=f"{_MOCK_SMS_MODEL_VERSION}-{self._counter}",
            to_contact_id=to.contact_id,
            lang=lang,
            sent_at=datetime.now(timezone.utc),
        )
        self._sent.append(receipt)
        return receipt

    @property
    def sent(self) -> list[SmsDeliveryReceipt]:
        """A copy of the recorded SMS log (most recent last)."""
        return list(self._sent)


class SnsSmsSender:
    """Production :class:`SmsSender` backed by Amazon SNS.

    Publishes the SMS-fallback offer via Amazon SNS. ``boto3`` is imported
    lazily at call time so this class can be constructed (and the module
    imported) in the offline demo / tests with no AWS SDK and no credentials
    present; it raises :class:`SmsSendError` with a clear message only when
    :meth:`send_sms` is actually invoked without ``boto3``.

    The SNS publish needs the donor's real phone number, which lives encrypted
    in the :class:`~pulselink.common.models.ContactPoint` (consent-gated). A
    ``decryptor`` callable is injected to turn ``value_encrypted`` into the E.164
    number SNS requires, keeping decryption out of this transport class.
    """

    def __init__(
        self,
        decryptor,
        settings: Optional[Settings] = None,
    ) -> None:
        self._decryptor = decryptor
        self._settings = settings
        self._counter = 0

    def send_sms(self, to: ContactPoint, body: str, lang: str) -> SmsDeliveryReceipt:
        try:
            import boto3  # deferred: no SDK/creds needed at import or in tests
        except ImportError as exc:  # pragma: no cover - exercised only sans boto3
            raise SmsSendError(
                "SnsSmsSender requires the 'boto3' package to publish via Amazon "
                "SNS; install boto3 or use MockSmsSender for the offline demo."
            ) from exc

        settings = self._settings or get_settings()
        phone_number = self._decryptor(to.value_encrypted)

        sns = boto3.client("sns", region_name=settings.aws_region)
        response = sns.publish(PhoneNumber=phone_number, Message=body)

        self._counter += 1
        return SmsDeliveryReceipt(
            message_id=str(response.get("MessageId", f"sns-{self._counter}")),
            to_contact_id=to.contact_id,
            lang=lang,
            sent_at=datetime.now(timezone.utc),
        )


class SmsSendError(RuntimeError):
    """Raised when an SMS-fallback offer cannot be sent (e.g. the production
    :class:`SnsSmsSender` was invoked without ``boto3`` installed)."""
