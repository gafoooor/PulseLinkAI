"""Twilio webhook handlers for PulseLink IVR + WhatsApp.

Routes (all under /webhooks):

  POST /webhooks/voice/outbound     -- initial TwiML when Twilio dials a donor
  POST /webhooks/voice/language     -- Gather callback: donor pressed 1/2/3
  POST /webhooks/voice/response     -- Gather callback: donor pressed 1 (accept) or 2 (decline)
  POST /webhooks/voice/status       -- status callback: no-answer / busy / failed -> next donor
  POST /webhooks/whatsapp           -- incoming WhatsApp message from new patient / coordinator

All webhooks return TwiML (application/xml) or empty 204. They read from and
write to the global DemoState via the shared _state singleton in api.py.

Ngrok / public URL
------------------
Twilio must reach these URLs from the internet. For local dev, install ngrok
and run:
    ngrok http 8000
Then set WEBHOOK_BASE_URL=https://<your-id>.ngrok.io in .env.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import traceback

from fastapi import APIRouter, Form, Query
from fastapi.responses import PlainTextResponse, Response

from pulselink.common.enums import AssignmentStatus, SlotStatus
from pulselink.messaging.twilio_channel import (
    TwilioConfig,
    get_twilio_config,
    make_voice_call,
    send_whatsapp,
    twiml_accepted,
    twiml_declined,
    twiml_error,
    twiml_language_menu,
    twiml_offer_menu,
    whatsapp_twiml_ack,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _get_state():
    from pulselink.demo.api import get_state  # late import to avoid circular
    state = get_state()
    # Guard: ensure call_orchestrator always exists (may be None if startup failed)
    if state.call_orchestrator is None:
        from pulselink.demo.call_state import InMemoryCallOrchestrator
        state.call_orchestrator = InMemoryCallOrchestrator()
    return state


def _find_slot(state, slot_id: str):
    for sub in state.subscriptions.values():
        for slot in sub.slots:
            if slot.slot_id == slot_id:
                return slot
    return None


def _call_next_donor(
    session_id: str,
    state,
    cfg: TwilioConfig,
) -> bool:
    """Place an outbound call to the current donor in the session.

    Returns True if the call was placed, False if we ran out of donors.
    """
    sess = state.call_orchestrator.get_session(session_id)
    if sess is None or sess.current_idx >= len(sess.donor_queue):
        return False

    donor_id = sess.donor_queue[sess.current_idx]
    state.call_orchestrator.update(
        session_id, status="calling", current_donor_id=donor_id
    )

    to_phone = cfg.test_donor_phone or cfg.phone_number
    webhook_base = cfg.webhook_base_url
    call_sid = make_voice_call(
        to=to_phone,
        twiml_url=f"{webhook_base}/webhooks/voice/outbound?session={session_id}",
        status_callback=f"{webhook_base}/webhooks/voice/status?session={session_id}",
        cfg=cfg,
    )
    state.call_orchestrator.register_call_sid(session_id, call_sid)
    return True


def _notify_coordinator(msg: str, cfg: TwilioConfig) -> None:
    """Send a WhatsApp message to the coordinator/patient phone (best effort)."""
    target = cfg.coordinator_phone or cfg.test_donor_phone
    if not target:
        return
    try:
        send_whatsapp(target, msg, cfg)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Voice webhook 1 — initial TwiML (language menu)
# --------------------------------------------------------------------------- #
@router.post(
    "/voice/outbound",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def voice_outbound(
    session: str = Query(...),
    CallSid: str = Form(""),
):
    """Twilio calls this URL when the donor picks up. Returns language-menu TwiML."""
    try:
        state = _get_state()
        sess = state.call_orchestrator.get_session(session)
        if sess is None:
            print(f"[WARN] voice_outbound: session not found: {session}")
            return PlainTextResponse(content=twiml_error("Session expired. Goodbye."), media_type="application/xml")

        if CallSid:
            state.call_orchestrator.register_call_sid(session, CallSid)
        state.call_orchestrator.update(session, status="awaiting_language")

        cfg = get_twilio_config()
        webhook_base = cfg.webhook_base_url if cfg else os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000")
        twiml = twiml_language_menu(session_id=session, webhook_base=webhook_base)
        print(f"[INFO] voice_outbound: session={session} -> language menu sent")
        return PlainTextResponse(content=twiml, media_type="application/xml")
    except Exception:
        traceback.print_exc()
        return PlainTextResponse(content=twiml_error(), media_type="application/xml")


# --------------------------------------------------------------------------- #
# Voice webhook 2 — language selection Gather callback
# --------------------------------------------------------------------------- #
@router.post(
    "/voice/language",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def voice_language_selected(
    session: str = Query(...),
    Digits: str = Form(""),
    CallSid: str = Form(""),
):
    """Donor pressed 1/2/3. Returns accept/decline TwiML in the chosen language."""
    try:
        print(f"[INFO] voice_language: session={session} Digits={Digits!r}")
        state = _get_state()
        sess = state.call_orchestrator.get_session(session)
        if sess is None:
            print(f"[WARN] voice_language: session not found: {session}")
            return PlainTextResponse(content=twiml_error("Session not found. Please try again."), media_type="application/xml")

        lang_map = {"1": "en", "2": "hi", "3": "te"}
        lang = lang_map.get(Digits, "en")
        print(f"[INFO] voice_language: lang={lang}")
        state.call_orchestrator.update(session, language=lang, status="awaiting_response")

        slot = _find_slot(state, sess.slot_id)
        if slot is None:
            print(f"[WARN] voice_language: slot not found: {sess.slot_id}")
            return PlainTextResponse(content=twiml_error("Slot not found. Goodbye."), media_type="application/xml")

        cfg = get_twilio_config()
        webhook_base = cfg.webhook_base_url if cfg else os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000")
        twiml = twiml_offer_menu(
            session_id=session,
            lang=lang,
            webhook_base=webhook_base,
            units=slot.units_needed,
            start=slot.window.start.isoformat(),
            end=slot.window.end.isoformat(),
        )
        print(f"[INFO] voice_language: offer menu sent (lang={lang})")
        return PlainTextResponse(content=twiml, media_type="application/xml")
    except Exception:
        traceback.print_exc()
        return PlainTextResponse(content=twiml_error(), media_type="application/xml")


# --------------------------------------------------------------------------- #
# Voice webhook 3 — accept / decline Gather callback
# --------------------------------------------------------------------------- #
@router.post(
    "/voice/response",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def voice_response_captured(
    session: str = Query(...),
    lang: str = Query("en"),
    Digits: str = Form(""),
    CallSid: str = Form(""),
):
    """Donor pressed 1 (accept) or 2 (decline)."""
    try:
     return await _voice_response_inner(session, lang, Digits, CallSid)
    except Exception:
        traceback.print_exc()
        return PlainTextResponse(content=twiml_error(), media_type="application/xml")


async def _voice_response_inner(session, lang, Digits, CallSid):
    print(f"[INFO] voice_response: session={session} lang={lang} Digits={Digits!r}")
    state = _get_state()
    sess = state.call_orchestrator.get_session(session)
    if sess is None:
        print(f"[WARN] voice_response: session not found: {session}")
        return PlainTextResponse(content=twiml_error("Session not found."), media_type="application/xml")

    cfg = get_twilio_config()
    slot = _find_slot(state, sess.slot_id)

    if Digits == "1":
        # --- ACCEPT ---
        if slot:
            for a in slot.assignments:
                if a.donor_id == sess.current_donor_id:
                    a.status = AssignmentStatus.CONFIRMED
                    slot.status = SlotStatus.CONFIRMED
                    break

        state.call_orchestrator.update(
            session, status="accepted", accepted_donor_id=sess.current_donor_id
        )

        # Send WhatsApp alerts
        if state.alert_log and slot:
            from pulselink.messaging.alerts import send_acceptance_alerts
            try:
                send_acceptance_alerts(
                    slot=slot,
                    donor_id=sess.current_donor_id,
                    patient_id=sess.patient_id,
                    donors_by_id=state.donors,
                    alert_log=state.alert_log,
                    contact_points=state.contact_points,
                )
            except Exception:
                pass

        if cfg and slot:
            patient = state.patients.get(sess.patient_id)
            patient_name = getattr(patient, "patient_id", sess.patient_id)[:10]
            msg = (
                f"PulseLink: Blood ARRANGED for patient {patient_name}!\n"
                f"Donor accepted for {slot.window.start} to {slot.window.end}.\n"
                f"Units: {slot.units_needed}\n"
                f"Session: {session[:8]}"
            )
            _notify_coordinator(msg, cfg)
            state.call_orchestrator.update(session, patient_notified=True)

        return PlainTextResponse(content=twiml_accepted(lang), media_type="application/xml")

    elif Digits == "2":
        # --- DECLINE ---
        if slot:
            for a in slot.assignments:
                if a.donor_id == sess.current_donor_id:
                    a.status = AssignmentStatus.DECLINED
                    break

        declined = list(sess.declined_donors) + [sess.current_donor_id]
        next_idx = sess.current_idx + 1

        if next_idx < len(sess.donor_queue):
            state.call_orchestrator.update(
                session,
                current_idx=next_idx,
                current_donor_id=sess.donor_queue[next_idx],
                declined_donors=declined,
                status="calling",
            )
            if cfg:
                try:
                    _notify_coordinator(
                        f"PulseLink: Donor declined. Calling donor {next_idx + 1} of {len(sess.donor_queue)}...",
                        cfg,
                    )
                    _call_next_donor(session, state, cfg)
                except Exception:
                    traceback.print_exc()
                    state.call_orchestrator.update(session, status="failed")
            else:
                state.call_orchestrator.update(session, status="awaiting_response")
        else:
            state.call_orchestrator.update(
                session, status="declined_all", declined_donors=declined
            )
            if cfg:
                try:
                    _notify_coordinator(
                        f"PulseLink ALERT: All {len(declined)} donors declined for patient {sess.patient_id[:10]}. Coordinator intervention needed.",
                        cfg,
                    )
                except Exception:
                    pass

        return PlainTextResponse(content=twiml_declined(lang), media_type="application/xml")

    else:
        # No input or invalid — retry
        if slot:
            cfg = get_twilio_config()
            webhook_base = cfg.webhook_base_url if cfg else os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000")
            twiml = twiml_offer_menu(
                session_id=session,
                lang=lang,
                webhook_base=webhook_base,
                units=slot.units_needed,
                start=slot.window.start.isoformat(),
                end=slot.window.end.isoformat(),
            )
            return PlainTextResponse(content=twiml, media_type="application/xml")
        return PlainTextResponse(content=twiml_error(), media_type="application/xml")


# --------------------------------------------------------------------------- #
# Voice webhook 4 — Twilio status callback (no-answer / busy / failed)
# --------------------------------------------------------------------------- #
@router.post("/voice/status", include_in_schema=False)
async def voice_status_callback(
    session: str = Query(...),
    CallStatus: str = Form(""),
    CallSid: str = Form(""),
):
    """Twilio posts here when a call ends with no-answer, busy, or failed."""
    try:
        print(f"[INFO] voice_status: session={session} CallStatus={CallStatus!r}")
        terminal_no_response = {"no-answer", "busy", "failed"}
        if CallStatus not in terminal_no_response:
            return Response(status_code=204)

        state = _get_state()
        sess = state.call_orchestrator.get_session(session)
        if sess is None or sess.status in ("accepted", "declined_all"):
            return Response(status_code=204)

        declined = list(sess.declined_donors)
        next_idx = sess.current_idx + 1
        cfg = get_twilio_config()

        if next_idx < len(sess.donor_queue):
            state.call_orchestrator.update(
                session,
                current_idx=next_idx,
                current_donor_id=sess.donor_queue[next_idx],
                declined_donors=declined + [f"no-answer:{sess.current_donor_id}"],
                status="calling",
            )
            if cfg:
                try:
                    _notify_coordinator(
                        f"PulseLink: No answer from donor. Calling next donor ({next_idx + 1}/{len(sess.donor_queue)})...",
                        cfg,
                    )
                    _call_next_donor(session, state, cfg)
                except Exception:
                    traceback.print_exc()
        else:
            state.call_orchestrator.update(session, status="declined_all", declined_donors=declined)
            if cfg:
                try:
                    _notify_coordinator(
                        f"PulseLink ALERT: No donors available for patient {sess.patient_id[:10]}. Please intervene.",
                        cfg,
                    )
                except Exception:
                    pass
    except Exception:
        traceback.print_exc()
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# WhatsApp webhook — incoming messages from new patients / coordinator
# --------------------------------------------------------------------------- #
@router.post(
    "/whatsapp",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def whatsapp_inbound(
    From: str = Form(""),
    Body: str = Form(""),
    SmsMessageSid: str = Form(""),
    To: str = Form(""),
):
    """Receive an incoming WhatsApp message, parse it with the LLM, store in parse_inbox."""
    state = _get_state()

    # Store the raw incoming message
    from_clean = From.replace("whatsapp:", "")
    message_id = SmsMessageSid or f"wa-{abs(hash(Body + From))}"

    # Parse with MockLlmClient
    parsed_fields = []
    try:
        from pulselink.parsing.llm_client import MockLlmClient
        from pulselink.parsing.service import parse_record as parse_fn
        result = parse_fn(Body, None, MockLlmClient())
        from pulselink.demo.api import _transform_parsed_record
        parsed_fields = _transform_parsed_record(result)
    except Exception:
        parsed_fields = []

    # Store in parse_inbox
    inbox_item = {
        "messageId": message_id,
        "fromNumber": from_clean,
        "body": Body,
        "receivedAt": datetime.now(timezone.utc).isoformat(),
        "parsedFields": parsed_fields,
        "status": "pending",
    }
    if hasattr(state, "parse_inbox"):
        state.parse_inbox.append(inbox_item)

    # Acknowledge with a WhatsApp reply
    reply = (
        "PulseLink received your message. A coordinator will review and add you to the system shortly. "
        "Thank you for reaching out."
    )
    return PlainTextResponse(
        content=whatsapp_twiml_ack(reply), media_type="application/xml"
    )
