"""Multilingual slot-offer template rendering (Task 9.2).

This module renders a donor slot offer into a :class:`LocalizedMessage` in the
donor's preferred language, drawn from a small *reviewed* translation template
catalog. It is the language seam for Requirement 9:

* **9.1** — render the offer in the donor's ``preferred_lang`` from the reviewed
  translation template catalog.
* **9.2** — when no ``preferred_lang`` is recorded (``None``/empty) *or* the
  recorded language is not in the catalog, fall back to the configured default
  language (``settings.default_lang``), and finally to English.
* **9.3** — every rendered offer includes the slot details (the transfusion
  window date range and the units needed) **and** both the Accept and Decline
  actions as :class:`MessageAction` entries.

Design constraints:
* Pure, deterministic, and fully offline — no network, no clock reads. The same
  inputs always produce the same :class:`LocalizedMessage`.
* The returned message is ready to hand to any ``MessageChannel.send()``.
* Strings are short, empathetic, and carry **no medical claims**. They are
  clearly marked as *demo* translations: in production these would be replaced
  by professionally reviewed copy (hence "reviewed translation template
  catalog").

Scope note (Task 9.2 only): this module owns template rendering. The
``MessageChannel`` seam itself lives in ``channel.py`` (Task 9.1); the consent
gate and ``notify_slot`` / ``handle_inbound`` wiring are Tasks 9.3 / 9.4, and
the voice channel is Task 10.x. This file intentionally does not touch
``channel.py`` so concurrent work there does not collide.

Requirements: 9.1, 9.2, 9.3
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from pulselink.common.config import Settings, get_settings
from pulselink.messaging.channel import LocalizedMessage, MessageAction

# The hard final fallback when neither the donor's preferred language nor the
# configured default language is present in the catalog.
ULTIMATE_FALLBACK_LANG = "en"

# Default channel-agnostic reply keywords for the two donor actions. A channel
# may render these as SMS reply words or map the deep links to buttons.
DEFAULT_ACCEPT_KEYWORD = "ACCEPT"
DEFAULT_DECLINE_KEYWORD = "DECLINE"


# --------------------------------------------------------------------------- #
# Offer context (the slot details handed to the renderer)
# --------------------------------------------------------------------------- #
class OfferContext(BaseModel):
    """The first-name-free slot details rendered into a localized offer.

    Deliberately carries **no patient PII** (no names) — only the bridge anchor,
    the transfusion window, the units needed, and the action keywords/links.
    This keeps rendering privacy-safe and consistent with the consent-gated
    contact model elsewhere in PulseLink.
    """

    bridge_id: str = Field(min_length=1)
    window_start: date
    window_end: date
    units_needed: int = Field(ge=1)
    # Channel-agnostic reply keywords (defaulted) and optional install-free
    # Donor_Interface deep links for the Accept / Decline actions.
    accept_keyword: str = DEFAULT_ACCEPT_KEYWORD
    decline_keyword: str = DEFAULT_DECLINE_KEYWORD
    accept_deep_link: Optional[str] = None
    decline_deep_link: Optional[str] = None


# --------------------------------------------------------------------------- #
# Reviewed translation template catalog
# --------------------------------------------------------------------------- #
class OfferTemplate(BaseModel):
    """One language's reviewed offer template: body + the two action labels.

    ``body`` is a ``str.format`` template with the named placeholders
    ``{window_start}``, ``{window_end}``, and ``{units}``. ``accept_label`` and
    ``decline_label`` are the localized button/keyword labels.
    """

    body: str = Field(min_length=1)
    accept_label: str = Field(min_length=1)
    decline_label: str = Field(min_length=1)


# NOTE: These are DEMO translations for the offline hackathon flow. In
# production they would be replaced by professionally reviewed, localized copy.
# They are intentionally short, warm, and contain NO medical claims.
TEMPLATE_CATALOG: dict[str, OfferTemplate] = {
    "en": OfferTemplate(
        body=(
            "Hello, a patient in your area may need {units} unit(s) of blood "
            "between {window_start} and {window_end}. Could you help? "
            "Reply to accept or decline — thank you for being there."
        ),
        accept_label="Accept",
        decline_label="Decline",
    ),
    "te": OfferTemplate(
        # Telugu (demo translation, to be professionally reviewed in prod).
        body=(
            "నమస్కారం, మీ ప్రాంతంలో ఒక రోగికి {window_start} నుండి "
            "{window_end} మధ్య {units} యూనిట్(ల) రక్తం అవసరం కావచ్చు. "
            "మీరు సహాయం చేయగలరా? అంగీకరించడానికి లేదా తిరస్కరించడానికి "
            "ప్రత్యుత్తరం ఇవ్వండి — మీ సహకారానికి ధన్యవాదాలు."
        ),
        accept_label="అంగీకరించు",
        decline_label="తిరస్కరించు",
    ),
    "hi": OfferTemplate(
        # Hindi (demo translation, to be professionally reviewed in prod).
        body=(
            "नमस्ते, आपके क्षेत्र के एक मरीज़ को {window_start} और "
            "{window_end} के बीच {units} यूनिट रक्त की आवश्यकता हो सकती है। "
            "क्या आप मदद कर सकते हैं? स्वीकार या अस्वीकार करने के लिए "
            "उत्तर दें — आपके साथ होने के लिए धन्यवाद।"
        ),
        accept_label="स्वीकार करें",
        decline_label="अस्वीकार करें",
    ),
    "ta": OfferTemplate(
        # Tamil (demo translation, to be professionally reviewed in prod).
        body=(
            "வணக்கம், உங்கள் பகுதியில் உள்ள ஒரு நோயாளிக்கு {window_start} "
            "முதல் {window_end} வரை {units} யூனிட் இரத்தம் தேவைப்படலாம். "
            "நீங்கள் உதவ முடியுமா? ஏற்க அல்லது மறுக்க பதிலளிக்கவும் — "
            "உங்கள் ஆதரவுக்கு நன்றி."
        ),
        accept_label="ஏற்கிறேன்",
        decline_label="மறுக்கிறேன்",
    ),
}


# --------------------------------------------------------------------------- #
# Language resolution + rendering
# --------------------------------------------------------------------------- #
def resolve_lang(preferred_lang: Optional[str], settings: Optional[Settings] = None) -> str:
    """Resolve which catalog language to render in (Requirements 9.1, 9.2).

    Resolution order:
    1. the donor's ``preferred_lang`` when recorded and present in the catalog;
    2. otherwise the configured default language (``settings.default_lang``)
       when present in the catalog;
    3. otherwise the ultimate English fallback.

    Language tags are normalized (trimmed, lower-cased) before lookup so
    ``"TE"`` / ``" te "`` resolve to ``"te"``.
    """
    settings = settings or get_settings()

    candidate = _normalize_lang(preferred_lang)
    if candidate and candidate in TEMPLATE_CATALOG:
        return candidate

    default = _normalize_lang(settings.default_lang)
    if default and default in TEMPLATE_CATALOG:
        return default

    return ULTIMATE_FALLBACK_LANG


def render_offer(
    context: OfferContext,
    preferred_lang: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> LocalizedMessage:
    """Render a slot offer into a :class:`LocalizedMessage`.

    The message is rendered in the donor's ``preferred_lang`` from the reviewed
    template catalog, falling back to the configured default language when none
    is recorded or the recorded language is unsupported (Requirements 9.1, 9.2).
    The rendered message always includes the slot details (window date range and
    units) and both the Accept and Decline actions (Requirement 9.3).

    Pure and deterministic: the same ``context`` + language always yield the
    same message, with no network or clock access.
    """
    lang = resolve_lang(preferred_lang, settings)
    template = TEMPLATE_CATALOG[lang]

    text = template.body.format(
        window_start=context.window_start.isoformat(),
        window_end=context.window_end.isoformat(),
        units=context.units_needed,
    )

    actions = [
        MessageAction(
            kind="accept",
            label=template.accept_label,
            keyword=context.accept_keyword,
            deep_link=context.accept_deep_link,
        ),
        MessageAction(
            kind="decline",
            label=template.decline_label,
            keyword=context.decline_keyword,
            deep_link=context.decline_deep_link,
        ),
    ]

    return LocalizedMessage(lang=lang, text=text, actions=actions)


def _normalize_lang(lang: Optional[str]) -> Optional[str]:
    """Trim and lower-case a language tag; return ``None`` for empty/``None``."""
    if lang is None:
        return None
    normalized = lang.strip().lower()
    return normalized or None
