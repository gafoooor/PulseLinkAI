"""Real Twilio integration for PulseLink voice IVR and WhatsApp messaging.

Two capabilities:
1. Outbound voice calls — TwiML-driven IVR that asks the donor to select their
   language then accept or decline the slot. Requires TWILIO_ACCOUNT_SID,
   TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER.

2. WhatsApp messages — sends WhatsApp messages via Twilio Messaging API.
   Uses the Twilio WhatsApp Sandbox (TWILIO_WHATSAPP_FROM) for dev/demo.
   Production uses an approved WhatsApp Business sender.

Demo override:
  Because Dataset.csv has no real phone numbers, the demo routes ALL outbound
  calls to TWILIO_TEST_DONOR_PHONE and ALL WhatsApp notifications to
  TWILIO_COORDINATOR_PHONE. Set these in .env to the two test numbers.

TwiML helper functions live here too (pure string generation, no Twilio SDK
needed for TwiML — it is just XML).
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class TwilioConfig:
    account_sid: str
    auth_token: str
    phone_number: str      # E.164, e.g. +12025551234 (makes voice calls)
    whatsapp_from: str     # e.g. whatsapp:+14155238886 (sandbox)
    webhook_base_url: str  # publicly reachable base, e.g. https://abc.ngrok.io
    test_donor_phone: str = ""        # all demo calls go here
    coordinator_phone: str = ""       # WhatsApp notifications go here


def get_twilio_config() -> Optional[TwilioConfig]:
    """Return config from env, or None if Twilio credentials are not set."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    phone = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
    if not (sid and token and phone):
        return None
    return TwilioConfig(
        account_sid=sid,
        auth_token=token,
        phone_number=phone,
        whatsapp_from=os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"),
        webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000").strip().rstrip("/"),
        test_donor_phone=os.getenv("TWILIO_TEST_DONOR_PHONE", ""),
        coordinator_phone=os.getenv("TWILIO_COORDINATOR_PHONE", ""),
    )


# --------------------------------------------------------------------------- #
# Voice — outbound call
# --------------------------------------------------------------------------- #
def make_voice_call(
    to: str,
    twiml_url: str,
    status_callback: str,
    cfg: TwilioConfig,
) -> str:
    """Place an outbound call and return the Twilio Call SID."""
    from twilio.rest import Client  # noqa: PLC0415

    client = Client(cfg.account_sid, cfg.auth_token)
    call = client.calls.create(
        to=to,
        from_=cfg.phone_number,
        url=twiml_url,
        status_callback=status_callback,
        status_callback_method="POST",
        status_callback_event=["completed", "no-answer", "busy", "failed"],
    )
    return call.sid


# --------------------------------------------------------------------------- #
# WhatsApp — send message
# --------------------------------------------------------------------------- #
def send_whatsapp(to: str, message: str, cfg: TwilioConfig) -> str:
    """Send a WhatsApp message and return the Twilio Message SID."""
    from twilio.rest import Client  # noqa: PLC0415

    client = Client(cfg.account_sid, cfg.auth_token)
    to_wa = f"whatsapp:{to}" if not to.startswith("whatsapp:") else to
    msg = client.messages.create(
        body=message,
        from_=cfg.whatsapp_from,
        to=to_wa,
    )
    return msg.sid


# --------------------------------------------------------------------------- #
# TwiML generators (pure XML strings — no Twilio SDK needed)
# --------------------------------------------------------------------------- #

# Polly voice mapping by language code
_VOICE_MAP = {
    "en": ("Polly.Raveena", "en-IN"),
    "hi": ("Polly.Aditi", "hi-IN"),
    "te": ("Polly.Raveena", "en-IN"),  # Telugu — use Indian English voice
}

# Language selection menu text
_LANG_MENU_TEXT = (
    "Hello! This is PulseLink, the blood subscription service for thalassemia patients. "
    "Press 1 for English. "
    "2 ke liye Hindi dabaayein. "
    "Press 3 for Telugu."
)

# Offer text per language
_OFFER_TEXT = {
    "en": (
        "A thalassemia patient in your support network urgently needs {units} unit of blood "
        "between {start} and {end}. "
        "Press 1 to accept and help this patient. "
        "Press 2 to decline."
    ),
    "hi": (
        "Aapke support network mein ek thalassemia patient ko {start} se {end} ke beech "
        "{units} unit khoon ki zaroorat hai. "
        "Sweekar karne ke liye 1 dabaayein. "
        "Asweekaar karne ke liye 2 dabaayein."
    ),
    "te": (
        "A thalassemia patient needs {units} unit of blood between {start} and {end}. "
        "Press 1 to accept and help this patient. "
        "Press 2 to decline."
    ),
}

_ACCEPT_TEXT = {
    "en": "Thank you so much for accepting! You are saving a life. You will receive a WhatsApp confirmation shortly. Goodbye.",
    "hi": "Bahut shukriya! Aap ek zindagi bachaa rahe hain. Aapko jald hi WhatsApp par confirmation milegi. Dhanyavaad.",
    "te": "Thank you so much for accepting! You are saving a life. You will receive a WhatsApp confirmation shortly. Goodbye.",
}

_DECLINE_TEXT = {
    "en": "We understand. We will try another donor. Thank you for letting us know. Goodbye.",
    "hi": "Hum samajhte hain. Hum doosre donor se sampark karenge. Shukriya. Alvida.",
    "te": "We understand. We will try another donor. Thank you for letting us know. Goodbye.",
}

_NO_INPUT_TEXT = {
    "en": "We did not receive your response. Please try again later. Goodbye.",
    "hi": "Hum aapka jawab nahi sun sake. Baad mein dobara try karein. Alvida.",
    "te": "We did not receive your response. Please try again later. Goodbye.",
}


def _say(text: str, lang: str) -> str:
    """Build a <Say> TwiML element with the appropriate Polly voice."""
    voice, lang_code = _VOICE_MAP.get(lang, _VOICE_MAP["en"])
    safe_text = html.escape(text)
    return f'<Say voice="{voice}" language="{lang_code}">{safe_text}</Say>'


def twiml_language_menu(session_id: str, webhook_base: str) -> str:
    """TwiML: greet donor, ask them to select their language (1/2/3)."""
    action = f"{webhook_base}/webhooks/voice/language?session={html.escape(session_id)}"
    say = _say(_LANG_MENU_TEXT, "en")
    no_input = _say(_NO_INPUT_TEXT["en"], "en")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather numDigits="1" action="{action}" method="POST" timeout="10">'
        f"{say}"
        "</Gather>"
        f"{no_input}"
        "<Hangup/>"
        "</Response>"
    )


def twiml_offer_menu(
    session_id: str,
    lang: str,
    webhook_base: str,
    units: int,
    start: str,
    end: str,
) -> str:
    """TwiML: describe the slot in the donor's chosen language, ask 1=accept 2=decline."""
    action = (
        f"{webhook_base}/webhooks/voice/response"
        f"?session={html.escape(session_id)}&amp;lang={html.escape(lang)}"
    )
    tmpl = _OFFER_TEXT.get(lang, _OFFER_TEXT["en"])
    text = tmpl.format(units=units, start=start, end=end)
    say = _say(text, lang)
    no_input = _say(_NO_INPUT_TEXT.get(lang, _NO_INPUT_TEXT["en"]), lang)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather numDigits="1" action="{action}" method="POST" timeout="15">'
        f"{say}{say}"  # repeat once for clarity
        "</Gather>"
        f"{no_input}"
        "<Hangup/>"
        "</Response>"
    )


def twiml_accepted(lang: str) -> str:
    """TwiML: thank the donor for accepting."""
    say = _say(_ACCEPT_TEXT.get(lang, _ACCEPT_TEXT["en"]), lang)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{say}"
        "<Hangup/>"
        "</Response>"
    )


def twiml_declined(lang: str) -> str:
    """TwiML: acknowledge the donor's decline politely."""
    say = _say(_DECLINE_TEXT.get(lang, _DECLINE_TEXT["en"]), lang)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{say}"
        "<Hangup/>"
        "</Response>"
    )


def twiml_error(msg: str = "An error occurred. Goodbye.") -> str:
    """TwiML: generic error message."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say>{html.escape(msg)}</Say>"
        "<Hangup/>"
        "</Response>"
    )


def whatsapp_twiml_ack(reply: str) -> str:
    """TwiML: acknowledge an incoming WhatsApp message with a reply."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Message>{html.escape(reply)}</Message>"
        "</Response>"
    )
