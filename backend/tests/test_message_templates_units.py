"""Focused unit tests for offer rendering + language fallback (Task 9.5).

These complement — and deliberately avoid duplicating — the Task 9.2 suite in
``test_message_templates.py``. Where 9.2 establishes the baseline behaviour,
these cases tighten the corners around Requirement 9:

* 9.1 — render in the donor's ``preferred_lang`` from the reviewed catalog.
* 9.2 — fall back to the configured default language when no preferred language
  is recorded, when the recorded language is unsupported, and finally to
  English when the configured default is itself unsupported.
* 9.3 — every rendered offer carries the slot details (the transfusion window
  date range + units) and exactly one Accept + one Decline action with
  localized labels.

Plain, deterministic unit tests: no network, no clock, no mocks.

Requirements: 9.1, 9.2, 9.3
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from pulselink.common.config import Settings, get_settings
from pulselink.messaging.channel import LocalizedMessage
from pulselink.messaging.templates import (
    TEMPLATE_CATALOG,
    ULTIMATE_FALLBACK_LANG,
    OfferContext,
    render_offer,
    resolve_lang,
)

# A different window from the 9.2 suite so these assertions stand on their own.
WINDOW_START = date(2025, 11, 2)
WINDOW_END = date(2025, 11, 9)

CATALOG_LANGS = sorted(TEMPLATE_CATALOG)  # ["en", "hi", "ta", "te"]


def _context(units: int = 1) -> OfferContext:
    return OfferContext(
        bridge_id="bridge-9-5",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        units_needed=units,
    )


def _settings(default_lang: str) -> Settings:
    """Settings with a given default language, inheriting all other defaults."""
    return dataclasses.replace(get_settings(), default_lang=default_lang)


# --------------------------------------------------------------------------- #
# 9.1 / 9.3 — every catalog language carries window + units + localized actions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lang", CATALOG_LANGS)
def test_each_language_includes_full_window_range_and_units(lang):
    """Both window endpoints AND the units count appear in the rendered text (Req 9.3)."""
    message = render_offer(_context(units=5), preferred_lang=lang)

    assert message.lang == lang
    # The complete window date range — start AND end — is present.
    assert WINDOW_START.isoformat() in message.text
    assert WINDOW_END.isoformat() in message.text
    # The units count is rendered.
    assert "5" in message.text


@pytest.mark.parametrize("lang", CATALOG_LANGS)
def test_each_language_has_exactly_one_accept_and_decline_with_localized_labels(lang):
    """Exactly one accept + one decline, each labelled in that language (Req 9.1, 9.3)."""
    message = render_offer(_context(), preferred_lang=lang)
    template = TEMPLATE_CATALOG[lang]

    accepts = [a for a in message.actions if a.kind == "accept"]
    declines = [a for a in message.actions if a.kind == "decline"]

    assert len(accepts) == 1
    assert len(declines) == 1
    # Labels are drawn from the same language's reviewed template.
    assert accepts[0].label == template.accept_label
    assert declines[0].label == template.decline_label


@pytest.mark.parametrize("units", [1, 2, 7, 99])
def test_units_value_is_rendered_for_varied_counts(units):
    """The exact units value is reflected in the text across a range of counts (Req 9.3)."""
    message = render_offer(_context(units=units), preferred_lang="en")
    assert str(units) in message.text


# --------------------------------------------------------------------------- #
# 9.2 — fallback chain: preferred -> configured default -> English
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("preferred", [None, "", "   ", "\t", "\n  "])
def test_missing_or_blank_preferred_falls_back_to_configured_default(preferred):
    """None/empty/whitespace-only preferred languages use the configured default (Req 9.2)."""
    message = render_offer(
        _context(), preferred_lang=preferred, settings=_settings("ta")
    )
    assert message.lang == "ta"


@pytest.mark.parametrize("default_lang", CATALOG_LANGS)
def test_blank_preferred_resolves_to_each_supported_default(default_lang):
    """A blank preferred language resolves to whichever supported default is set (Req 9.2)."""
    assert resolve_lang("   ", _settings(default_lang)) == default_lang


@pytest.mark.parametrize("unsupported", ["fr", "es", "zz", "klingon"])
def test_unsupported_preferred_falls_back_to_configured_default(unsupported):
    """An unsupported preferred language falls back to the configured default (Req 9.2)."""
    message = render_offer(
        _context(), preferred_lang=unsupported, settings=_settings("hi")
    )
    assert message.lang == "hi"


@pytest.mark.parametrize("bad_default", ["fr", "", "   ", "zz", "\t"])
def test_unsupported_or_blank_default_falls_back_to_english(bad_default):
    """When preferred AND default are unusable, English is the ultimate fallback (Req 9.2)."""
    message = render_offer(
        _context(), preferred_lang="fr", settings=_settings(bad_default)
    )
    assert message.lang == ULTIMATE_FALLBACK_LANG == "en"


def test_default_used_only_when_preferred_unsupported_not_when_preferred_valid():
    """A valid preferred language is never overridden by the configured default (Req 9.1)."""
    # preferred is supported -> preferred wins even though default differs.
    assert resolve_lang("te", _settings("hi")) == "te"
    # preferred is unsupported -> default wins.
    assert resolve_lang("fr", _settings("hi")) == "hi"


# --------------------------------------------------------------------------- #
# Language-tag normalization (case / surrounding whitespace)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("EN", "en"),
        ("Te", "te"),
        ("  hi", "hi"),
        ("ta  ", "ta"),
        ("  HI  ", "hi"),
        ("\tTA\n", "ta"),
    ],
)
def test_preferred_lang_tag_is_normalized_before_lookup(raw, expected):
    """Case and surrounding whitespace in the preferred tag still resolve (Req 9.1)."""
    assert resolve_lang(raw, _settings("en")) == expected
    assert render_offer(_context(), preferred_lang=raw).lang == expected


@pytest.mark.parametrize("raw_default", ["  HI ", "Te", "TA"])
def test_default_lang_tag_is_also_normalized(raw_default):
    """A messily-cased configured default still resolves when preferred is missing (Req 9.2)."""
    message = render_offer(
        _context(), preferred_lang=None, settings=_settings(raw_default)
    )
    assert message.lang == raw_default.strip().lower()


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lang", CATALOG_LANGS)
def test_rendering_is_deterministic_across_all_languages(lang):
    """Same context + language always yields an identical message (no clock/network)."""
    context = _context(units=3)
    first = render_offer(context, preferred_lang=lang)
    second = render_offer(context, preferred_lang=lang)
    assert isinstance(first, LocalizedMessage)
    assert first.model_dump() == second.model_dump()


def test_fallback_path_is_deterministic():
    """Repeated renders through the fallback chain produce identical output."""
    settings = _settings("zz")  # unsupported default -> English
    first = render_offer(_context(), preferred_lang="fr", settings=settings)
    second = render_offer(_context(), preferred_lang="fr", settings=settings)
    assert first.model_dump() == second.model_dump()
    assert first.lang == "en"
