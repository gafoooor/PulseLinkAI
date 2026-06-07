"""Unit tests for multilingual slot-offer template rendering (Task 9.2).

Covers Requirement 9:
* 9.1 — rendering in each catalog language (en/te/hi/ta) uses that language and
  includes the transfusion window + both Accept/Decline actions.
* 9.2 — a missing ``preferred_lang`` (None/empty) falls back to the configured
  default language; an unsupported language also falls back.
* 9.3 — the rendered message includes the slot details (window dates + units)
  and exactly one Accept and one Decline action.

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

WINDOW_START = date(2025, 3, 10)
WINDOW_END = date(2025, 3, 17)


def _context(units: int = 2) -> OfferContext:
    return OfferContext(
        bridge_id="bridge-123",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        units_needed=units,
        accept_deep_link="https://pulse.link/s/abc/accept",
        decline_deep_link="https://pulse.link/s/abc/decline",
    )


def _settings(default_lang: str) -> Settings:
    """Build settings with the given default language, inheriting other defaults."""
    return dataclasses.replace(get_settings(), default_lang=default_lang)


def _action_kinds(message: LocalizedMessage) -> list[str]:
    return [action.kind for action in message.actions]


@pytest.mark.parametrize("lang", ["en", "te", "hi", "ta"])
def test_renders_each_catalog_language_with_window_and_actions(lang):
    """Each supported language renders in that language with window + actions (Req 9.1, 9.3)."""
    message = render_offer(_context(units=3), preferred_lang=lang)

    # Rendered in the requested language.
    assert message.lang == lang

    # Slot details (window date range + units) are present (Req 9.3).
    assert WINDOW_START.isoformat() in message.text
    assert WINDOW_END.isoformat() in message.text
    assert "3" in message.text

    # Exactly one Accept and one Decline action (Req 9.3).
    assert _action_kinds(message) == ["accept", "decline"]
    assert sum(a.kind == "accept" for a in message.actions) == 1
    assert sum(a.kind == "decline" for a in message.actions) == 1


@pytest.mark.parametrize("lang", ["en", "te", "hi", "ta"])
def test_action_labels_match_catalog_language(lang):
    """Accept/Decline labels come from the same language's reviewed template."""
    message = render_offer(_context(), preferred_lang=lang)
    template = TEMPLATE_CATALOG[lang]

    labels = {a.kind: a.label for a in message.actions}
    assert labels["accept"] == template.accept_label
    assert labels["decline"] == template.decline_label


def test_actions_carry_keywords_and_deep_links():
    """Each action carries its channel-agnostic keyword and deep link."""
    context = _context()
    message = render_offer(context, preferred_lang="en")

    accept = next(a for a in message.actions if a.kind == "accept")
    decline = next(a for a in message.actions if a.kind == "decline")

    assert accept.keyword == context.accept_keyword
    assert accept.deep_link == context.accept_deep_link
    assert decline.keyword == context.decline_keyword
    assert decline.deep_link == context.decline_deep_link


@pytest.mark.parametrize("preferred", [None, "", "   "])
def test_missing_preferred_lang_falls_back_to_default(preferred):
    """No recorded preferred language falls back to the configured default (Req 9.2)."""
    message = render_offer(
        _context(), preferred_lang=preferred, settings=_settings("hi")
    )
    assert message.lang == "hi"


def test_unsupported_preferred_lang_falls_back_to_default():
    """An unsupported language falls back to the configured default (Req 9.2)."""
    message = render_offer(
        _context(), preferred_lang="fr", settings=_settings("ta")
    )
    assert message.lang == "ta"


def test_unsupported_default_falls_back_to_english():
    """When neither preferred nor default is supported, English is the final fallback."""
    message = render_offer(
        _context(), preferred_lang="fr", settings=_settings("xx")
    )
    assert message.lang == ULTIMATE_FALLBACK_LANG == "en"


def test_preferred_lang_is_normalized_before_lookup():
    """Whitespace/case in the preferred language tag still resolves to the catalog."""
    message = render_offer(_context(), preferred_lang="  TE ")
    assert message.lang == "te"


def test_resolve_lang_prefers_preferred_over_default():
    """resolve_lang picks a supported preferred language over the default (Req 9.1)."""
    assert resolve_lang("ta", _settings("en")) == "ta"


def test_rendering_is_deterministic():
    """The same context + language always yields the same rendered message."""
    context = _context(units=4)
    first = render_offer(context, preferred_lang="te")
    second = render_offer(context, preferred_lang="te")
    assert first.model_dump() == second.model_dump()


def test_exactly_two_actions_one_accept_one_decline_in_every_language():
    """Across all catalog languages, actions are exactly one accept + one decline (Req 9.3)."""
    for lang in TEMPLATE_CATALOG:
        message = render_offer(_context(), preferred_lang=lang)
        assert len(message.actions) == 2
        assert {a.kind for a in message.actions} == {"accept", "decline"}
