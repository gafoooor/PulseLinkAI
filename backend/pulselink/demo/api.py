"""Unified demo API server (Task 15.1).

A single FastAPI app serving all three frontend surfaces with real endpoints
backed by the in-memory demo state. This replaces the per-service scaffold
`main.py` files for the demo and wires the full end-to-end flow:

  parse → forecast → generate subscription → offer → decline → promote → confirm

Endpoints:
  GET  /health
  GET  /patient/subscription?patient=...&token=...
  POST /donor/respond  { slot_id, token, response }
  GET  /coordinator/patients?city=...
  POST /parse  { rawText, cityId }

All three frontends (patient-app, donor-screen, coordinator-dashboard) call
these endpoints. CORS is enabled for local Vite dev servers.

Run:
    uvicorn pulselink.demo.api:app --reload --port 8000
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from pulselink.common.enums import AssignmentStatus, SlotResponse, SlotStatus
from pulselink.demo.seed import DemoState, build_demo_state
from pulselink.messaging.alerts import InMemoryAlertLog, send_upcoming_reminders, send_acceptance_alerts, send_decline_alert
from pulselink.messaging.twilio_channel import get_twilio_config, make_voice_call, send_whatsapp
from pulselink.parsing.llm_client import MockLlmClient
from pulselink.parsing.service import parse_record as parse_record_fn
from pulselink.subscription.promote import handle_decline

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="PulseLink Demo API",
    version="0.2.0",
    description="Unified demo server for PulseLink — The Blood Subscription. Real Twilio IVR + WhatsApp.",
)

# CORS for the three Vite dev servers
_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env
    else [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:3000",
    ]
)
_cors_wildcard = "*" in _cors_origins or not _cors_origins_env

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_wildcard else _cors_origins,
    allow_credentials=False,  # must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Twilio webhook router
from pulselink.demo.webhooks import router as webhooks_router
app.include_router(webhooks_router)

# ---------------------------------------------------------------------------
# Demo state (built on startup)
# ---------------------------------------------------------------------------
_state: Optional[DemoState] = None


def get_state() -> DemoState:
    global _state
    if _state is None:
        _state = build_demo_state()
    return _state


@app.on_event("startup")
def startup():
    """Build demo state on server startup."""
    global _state
    try:
        _state = build_demo_state()
    except Exception as e:
        # If CSV not found, create a minimal demo state
        import traceback
        traceback.print_exc()
        _state = DemoState(today=date.today())


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    state = get_state()
    return {
        "service": "pulselink-demo",
        "status": "ok",
        "patients": len(state.patients),
        "donors": len(state.donors),
        "subscriptions": len(state.subscriptions),
    }


@app.get("/demo/links")
def demo_links(limit: int = Query(10, description="How many sample patients")):
    """Return ready-to-use demo links for the patient app and donor screen.

    Handy for navigating the offline demo: each entry carries the patient id,
    whether their next slot is 'arranged', and example URLs to open the patient
    app and a donor accept/decline screen.
    """
    state = get_state()
    links = []
    for patient_id, sub in list(state.subscriptions.items())[:limit]:
        arranged = any(
            (a.status.value if hasattr(a.status, "value") else a.status) == "confirmed"
            for slot in sub.slots
            for a in slot.assignments
        )
        # Find a slot with an active assignment for the donor link
        donor_link = None
        for slot in sub.slots:
            for a in slot.assignments:
                a_status = a.status.value if hasattr(a.status, "value") else a.status
                if a_status == "active":
                    donor_link = (
                        f"http://localhost:5174/?lang=en&slot={slot.slot_id}"
                        f"&token=demo&start={slot.window.start.isoformat()}"
                        f"&end={slot.window.end.isoformat()}&units={slot.units_needed}"
                    )
                    break
            if donor_link:
                break

        links.append({
            "patientId": patient_id,
            "arranged": arranged,
            "patientAppUrl": f"http://localhost:5173/?patient={patient_id}&token=demo&lang=en",
            "donorScreenUrl": donor_link,
        })
    return {"links": links}


# ---------------------------------------------------------------------------
# Patient App: GET /patient/subscription
# ---------------------------------------------------------------------------
@app.get("/patient/subscription")
def get_patient_subscription(
    patient: str = Query(..., description="Patient/bridge id"),
    token: str = Query("", description="Opaque link token"),
):
    """Return the patient's own subscription (Requirement 1.4).

    Scoped to exactly one patient — never returns another patient's data.
    Accepts the patient id in either the raw dataset form (``\\x...``) or the
    normalized form (prefix stripped).
    """
    state = get_state()

    # Resolve the id to a stored key (handles the \x prefix mismatch).
    resolved = state.resolve_patient_id(patient)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    sub = state.subscriptions.get(resolved)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    # Serialize to the frontend's expected shape
    return _serialize_subscription(sub)


def _serialize_subscription(sub):
    """Convert a Subscription to the frontend JSON shape."""
    return {
        "subscriptionId": sub.subscription_id,
        "patientId": sub.patient_id,
        "cadenceDays": sub.cadence_days,
        "slots": [_serialize_slot(s) for s in sub.slots],
    }


def _serialize_slot(slot):
    return {
        "slotId": slot.slot_id,
        "window": {
            "start": slot.window.start.isoformat(),
            "end": slot.window.end.isoformat(),
            "expected": slot.window.expected.isoformat(),
        },
        "unitsNeeded": slot.units_needed,
        "status": slot.status.value if hasattr(slot.status, "value") else slot.status,
        "assignments": [_serialize_assignment(a) for a in slot.assignments],
    }


def _serialize_assignment(a):
    return {
        "assignmentId": a.assignment_id,
        "donorId": a.donor_id,
        "rank": a.rank,
        "status": a.status.value if hasattr(a.status, "value") else a.status,
    }


# ---------------------------------------------------------------------------
# Donor Screen: POST /donor/respond
# ---------------------------------------------------------------------------
class DonorResponseRequest(BaseModel):
    slot_id: str
    token: str = ""
    response: str  # "ACCEPTED" or "DECLINED"


@app.post("/donor/respond")
def donor_respond(req: DonorResponseRequest):
    """Process a donor's Accept/Decline for a single slot.

    On ACCEPTED: mark the assignment as confirmed.
    On DECLINED: trigger the decline -> promote -> re-score flow.
    """
    state = get_state()

    # Find the slot across all subscriptions
    slot = _find_slot(state, req.slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")

    # Determine which donor is responding (for demo: the first active assignment)
    donor_assignment = None
    for a in slot.assignments:
        status_val = a.status.value if hasattr(a.status, "value") else a.status
        if status_val == "active":
            donor_assignment = a
            break

    if donor_assignment is None:
        raise HTTPException(status_code=400, detail="No active assignment on this slot")

    donor_id = donor_assignment.donor_id

    if req.response == "ACCEPTED":
        # Mark as confirmed
        donor_assignment.status = AssignmentStatus.CONFIRMED
        slot.status = SlotStatus.CONFIRMED
        if state.alert_log:
            try:
                send_acceptance_alerts(
                    slot=slot, donor_id=donor_id, patient_id=slot.patient_id,
                    donors_by_id=state.donors, alert_log=state.alert_log,
                )
            except Exception:
                pass
        return {"ok": True, "action": "confirmed", "donorId": donor_id}

    elif req.response == "DECLINED":
        # Use the full decline -> promote -> re-score flow
        if state.promote_deps:
            try:
                result = handle_decline(req.slot_id, donor_id, state.promote_deps)
                return {
                    "ok": True,
                    "action": "declined",
                    "donorId": donor_id,
                    "promoted": result.promoted,
                    "promotedDonorId": result.promoted_donor_id,
                    "rescored": result.rescored,
                }
            except KeyError:
                # Slot not in repo — fall back to direct mutation
                donor_assignment.status = AssignmentStatus.DECLINED
                return {"ok": True, "action": "declined", "donorId": donor_id}
        else:
            donor_assignment.status = AssignmentStatus.DECLINED
            return {"ok": True, "action": "declined", "donorId": donor_id}

    else:
        raise HTTPException(status_code=400, detail="Invalid response; use ACCEPTED or DECLINED")


def _find_slot(state: DemoState, slot_id: str):
    """Find a slot by id across all subscriptions."""
    for sub in state.subscriptions.values():
        for slot in sub.slots:
            if slot.slot_id == slot_id:
                return slot
    return None


# ---------------------------------------------------------------------------
# Coordinator Dashboard: GET /coordinator/patients
# ---------------------------------------------------------------------------
@app.get("/coordinator/patients")
def get_coordinator_patients(city: str = Query("", description="City ID")):
    """Return patients for a city, with their next slot for risk scoring.

    Scoped to a single city_id (Requirement 8.3).
    """
    state = get_state()
    city_id = city.lower() if city else ""

    results = []
    for patient in state.patients.values():
        if city_id and patient.city_id.lower() != city_id:
            continue

        sub = state.subscriptions.get(patient.patient_id)
        slot_data = None
        if sub and sub.slots:
            # Pick the first non-fulfilled slot
            for slot in sub.slots:
                status_val = slot.status.value if hasattr(slot.status, "value") else slot.status
                if status_val not in ("fulfilled", "missed"):
                    slot_data = _serialize_slot_for_dashboard(slot, sub)
                    break

        results.append({
            "patientId": patient.patient_id,
            "cityId": patient.city_id,
            "bloodGroup": patient.blood_group.value,
            "cadenceDays": patient.cadence_days,
            "slot": slot_data,
        })

    return {"patients": results}


def _serialize_slot_for_dashboard(slot, sub):
    """Serialize a slot for the coordinator dashboard's risk scoring."""
    # Compute a simple confidence based on sample count
    confidence = min(1.0, len(sub.slots) / 6.0) if sub.slots else 0.5

    return {
        "slotId": slot.slot_id,
        "unitsNeeded": slot.units_needed,
        "window": {
            "start": slot.window.start.isoformat(),
            "end": slot.window.end.isoformat(),
            "expected": slot.window.expected.isoformat(),
            "confidence": round(confidence, 2),
        },
        "assignments": [
            {
                "donorId": a.donor_id,
                "status": a.status.value if hasattr(a.status, "value") else a.status,
                "units": 1,
            }
            for a in slot.assignments
        ],
    }


# ---------------------------------------------------------------------------
# Parser: POST /parse
# ---------------------------------------------------------------------------
class ParseRequest(BaseModel):
    rawText: str
    cityId: str = ""


@app.post("/parse")
def parse_text(req: ParseRequest):
    """Submit messy free-text to the Parser and return structured result for review.

    Uses the MockLlmClient for offline demo (Requirement 12.3).
    Nothing is persisted until coordinator confirms (Requirement 5.6).
    """
    llm = MockLlmClient()

    try:
        result = parse_record_fn(req.rawText, None, llm)
    except Exception:
        # Return a minimal failed parse result
        fields = [
            {
                "path": "error",
                "label": "Parse status",
                "value": "Failed to parse",
                "confidence": 0.0,
                "flagged": True,
                "reason": "parse_failed",
            }
        ]
        return {
            "modelVersion": "mock-parser-0.1",
            "rawText": req.rawText,
            "stubbed": False,
            "fields": fields,
        }

    # Transform to the frontend's expected shape
    fields = _transform_parsed_record(result)

    return {
        "modelVersion": result.model_version,
        "rawText": req.rawText,
        "stubbed": False,
        "fields": fields,
    }


def _transform_parsed_record(result) -> list[dict]:
    """Transform the parser's ParsedRecord into the frontend's field-list shape."""
    if result.parse_failed:
        return [
            {
                "path": "error",
                "label": "Parse status",
                "value": "Failed to parse",
                "confidence": 0.0,
                "flagged": True,
                "reason": "parse_failed",
            }
        ]

    fields = []
    flagged_paths = {f.field_path for f in result.review_flags}

    # Human-readable labels for common parsed fields
    labels = {
        "blood_group": "Blood group",
        "frequency_in_days": "Cadence (days)",
        "city_id": "City",
        "expected_next_transfusion_date": "Next transfusion",
        "last_transfusion_date": "Last transfusion",
        "donor_type": "Donor type",
        "role": "Role",
        "eligibility_status": "Eligibility",
        "preferred_lang": "Preferred language",
        "donations_till_date": "Donations",
        "name": "Name",
        "phone": "Phone",
        "quantity_required": "Units required",
    }

    # Extract fields from patient dict
    if result.patient:
        for key, value in result.patient.items():
            path = f"patient.{key}"
            conf = result.field_confidence.get(path, 0.5)
            is_flagged = path in flagged_paths
            fields.append({
                "path": path,
                "label": labels.get(key, key.replace("_", " ").title()),
                "value": value,
                "confidence": conf,
                "flagged": is_flagged,
                "reason": "low confidence" if is_flagged else None,
            })

    # Extract fields from donors
    for i, donor in enumerate(result.donors):
        for key, value in donor.items():
            path = f"donors[{i}].{key}"
            conf = result.field_confidence.get(path, 0.5)
            is_flagged = path in flagged_paths
            fields.append({
                "path": path,
                "label": f"Donor {i+1}: {labels.get(key, key.replace('_', ' ').title())}",
                "value": value,
                "confidence": conf,
                "flagged": is_flagged,
                "reason": "low confidence" if is_flagged else None,
            })

    return fields


# ---------------------------------------------------------------------------
# Notifications: GET /notifications/log
# ---------------------------------------------------------------------------
@app.get("/notifications/log")
def get_notifications_log(
    recipient_id: str = Query("", description="Filter by recipient id"),
    slot_id: str = Query("", description="Filter by slot id"),
    limit: int = Query(50, description="Max results"),
):
    """Return the WhatsApp/SMS alert log (for demo visibility)."""
    state = get_state()
    if not state.alert_log:
        return {"alerts": []}
    alerts = state.alert_log.get_alerts(
        recipient_id=recipient_id or None,
        slot_id=slot_id or None,
    )
    return {
        "total": len(alerts),
        "alerts": [
            {
                "alertId": a.alert_id,
                "alertType": a.alert_type.value,
                "recipientType": a.recipient_type,
                "recipientId": a.recipient_id,
                "slotId": a.slot_id,
                "lang": a.lang,
                "message": a.message_text,
                "sentAt": a.sent_at.isoformat(),
                "channel": a.channel,
                "mockPhone": a.mock_phone,
            }
            for a in alerts[-limit:]
        ],
    }


# ---------------------------------------------------------------------------
# Admin: POST /admin/send-reminders
# ---------------------------------------------------------------------------
@app.post("/admin/send-reminders")
def trigger_reminders(
    days_ahead: int = Query(7, description="Send reminders for slots within N days"),
):
    """Trigger WhatsApp reminders to primary donors for upcoming slots.

    Sends a reminder to the primary (rank-0) donor of every slot whose window
    starts within days_ahead days. The alert log records all sent messages.
    """
    state = get_state()
    if not state.alert_log:
        return {"sent": 0, "alerts": []}

    try:
        sent = send_upcoming_reminders(
            subscriptions=list(state.subscriptions.values()),
            donors_by_id=state.donors,
            alert_log=state.alert_log,
            today=state.today,
            days_ahead=days_ahead,
        )
        return {
            "sent": len(sent),
            "alerts": [
                {
                    "alertId": a.alert_id,
                    "recipientId": a.recipient_id,
                    "slotId": a.slot_id,
                    "message": a.message_text,
                }
                for a in sent
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Voice: POST /voice/call/{slot_id}
# ---------------------------------------------------------------------------
@app.post("/voice/call/{slot_id}")
def trigger_voice_call(slot_id: str):
    """Demo: trigger a PulseLink Voice call for the primary donor of a slot.

    Uses the MockVoiceChannel to simulate the full voice-agent flow:
    script generation -> synthesis -> call placement -> outcome capture.
    The outcome is returned and recorded in the voice log.
    """
    state = get_state()
    if not state.voice_channel:
        raise HTTPException(status_code=503, detail="Voice channel not initialized")

    slot = _find_slot(state, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")

    # Find the primary donor
    primary = None
    for a in slot.assignments:
        status_val = a.status.value if hasattr(a.status, "value") else a.status
        if a.rank == 0 and status_val in ("active", "confirmed"):
            primary = a
            break

    if primary is None:
        raise HTTPException(status_code=400, detail="No active primary donor for this slot")

    donor_id = primary.donor_id
    donor = state.donors.get(donor_id)
    if donor is None:
        raise HTTPException(status_code=404, detail="Donor not found")

    # Get contact point
    cp = state.contact_points.get(donor_id)
    if cp is None:
        from pulselink.common.models import ContactPoint
        cp = ContactPoint(
            contact_id=f"cp-demo-{donor_id}",
            subject_id=donor_id,
            type="phone",
            value_encrypted="+911234567890",
            preferred_lang=donor.preferred_lang or "en",
        )

    from pulselink.messaging.voice import VoiceCallContext
    from pulselink.common.enums import SlotResponse

    lang = donor.preferred_lang or "en"
    ctx = VoiceCallContext(
        donor_id=donor_id,
        slot_id=slot_id,
        lang=lang,
        bridge_context=f"A patient in your support network needs {slot.units_needed} unit(s) of blood.",
    )

    # Generate script, synthesize, program a simulated response, place call
    script = state.voice_channel.generate_script(ctx)
    audio = state.voice_channel.synthesize(script, lang)

    from pulselink.common.enums import SlotResponse
    state.voice_channel.program_response(
        donor_id=donor_id,
        slot_id=slot_id,
        response=SlotResponse.ACCEPTED,
        captured_via="voice_intent",
    )
    outcome = state.voice_channel.place_call(cp, audio)

    return {
        "donorId": donor_id,
        "slotId": slot_id,
        "script": script.text,
        "scriptLang": script.lang,
        "audioUri": audio.audio_uri,
        "durationMs": audio.duration_ms,
        "response": outcome.response.value,
        "capturedVia": outcome.captured_via,
        "fallbackUsed": outcome.fallback_used,
    }


# ---------------------------------------------------------------------------
# Voice: GET /voice/log
# ---------------------------------------------------------------------------
@app.get("/voice/log")
def get_voice_log():
    """Return the PulseLink Voice call log (demo visibility)."""
    state = get_state()
    if not state.voice_channel:
        return {"calls": []}

    return {
        "totalCalls": len(state.voice_channel.placed_calls),
        "calls": [
            {
                "toDonorId": c.donor_id,
                "audioUri": c.audio.audio_uri,
                "response": c.outcome.response.value,
                "capturedVia": c.outcome.captured_via,
                "placedAt": c.placed_at.isoformat(),
            }
            for c in state.voice_channel.placed_calls
        ],
        "smsFallbacks": len(state.voice_channel.sms_fallbacks),
    }


# ---------------------------------------------------------------------------
# Coordinator: GET /coordinator/donors/{patient_id}
# ---------------------------------------------------------------------------
@app.get("/coordinator/donors/{patient_id}")
def get_patient_donor_map(patient_id: str):
    """Return the 8 donors mapped to a patient with reliability scores and ML ranking.

    Shows each donor's blood_match_score, distance_score, reliability tier,
    and ML-predicted rank. This is the core 'subscription' mapping view.
    """
    state = get_state()
    resolved = state.resolve_patient_id(patient_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient = state.patients[resolved]
    sub = state.subscriptions.get(resolved)
    if sub is None or not sub.slots:
        return {"patientId": resolved, "donors": []}

    # Get assignments from the first slot (primary + backups = up to 8)
    slot = sub.slots[0]
    from pulselink.matching.match import blood_match_score, distance_km, distance_score
    from pulselink.reliability.scoring import DonorStats, reliability_score
    import datetime as dt

    donor_details = []
    for assignment in slot.assignments:
        donor = state.donors.get(assignment.donor_id)
        if donor is None:
            continue

        stats = state.stats_repo.get_stats(assignment.donor_id) if state.stats_repo else DonorStats()
        score_result = reliability_score(stats, state.today)

        dist = distance_km(patient.lat, patient.lng, donor.lat, donor.lng)
        bms = blood_match_score(donor.blood_group, patient.blood_group)
        ds = distance_score(dist)

        donor_details.append({
            "rank": assignment.rank,
            "donorId": assignment.donor_id,
            "role": "primary" if assignment.rank == 0 else f"backup-{assignment.rank}",
            "assignmentStatus": assignment.status.value if hasattr(assignment.status, "value") else assignment.status,
            "bloodGroup": donor.blood_group.value,
            "bloodMatchScore": round(bms, 2),
            "distanceKm": round(dist, 1) if dist != float("inf") else None,
            "distanceScore": round(ds, 3),
            "reliabilityScore": score_result["score"],
            "tier": score_result["tier"],
            "callEfficiency": round(score_result["components"]["callEfficiency"], 3),
            "volumeFactor": round(score_result["components"]["volumeFactor"], 3),
            "recencyFactor": round(score_result["components"]["recencyFactor"], 3),
            "callsToDonationsRatio": donor.calls_to_donations_ratio,
            "donationsTillDate": donor.donations_till_date,
            "eligibilityStatus": donor.eligibility_status,
        })

    donor_details.sort(key=lambda d: d["rank"])

    return {
        "patientId": resolved,
        "bloodGroup": patient.blood_group.value,
        "totalDonors": len(donor_details),
        "donors": donor_details,
    }


# ---------------------------------------------------------------------------
# Call Flow: POST /calls/start — kick off the sequential donor IVR flow
# ---------------------------------------------------------------------------
class StartCallRequest(BaseModel):
    patient_id: str
    slot_id: str = ""


@app.post("/calls/start")
def start_call_flow(req: StartCallRequest):
    """Start the sequential IVR donor-calling flow for a patient slot.

    Builds the donor queue from the slot's ranked assignments, creates a
    CallSession, and places the first outbound Twilio call (or simulates it
    if Twilio is not configured). Returns the session_id for polling.
    """
    state = get_state()
    resolved = state.resolve_patient_id(req.patient_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    sub = state.subscriptions.get(resolved)
    if sub is None or not sub.slots:
        raise HTTPException(status_code=404, detail="No subscription found")

    # Find target slot
    slot = None
    if req.slot_id:
        slot = _find_slot(state, req.slot_id)
    if slot is None:
        # Pick first non-fulfilled slot
        for s in sub.slots:
            status_val = s.status.value if hasattr(s.status, "value") else s.status
            if status_val not in ("fulfilled", "missed", "confirmed"):
                slot = s
                break
    if slot is None:
        slot = sub.slots[0]

    # Build donor queue: ranked assignments (rank 0 = primary first)
    donor_queue = sorted(slot.assignments, key=lambda a: a.rank)
    donor_ids = [a.donor_id for a in donor_queue]

    if not donor_ids:
        raise HTTPException(status_code=400, detail="No donors assigned to this slot")

    # Create the call session
    sess = state.call_orchestrator.create_session(
        patient_id=resolved,
        slot_id=slot.slot_id,
        donor_queue=donor_ids,
    )

    cfg = get_twilio_config()
    call_mode = "simulated"
    twilio_error = None

    if cfg and cfg.test_donor_phone:
        from pulselink.demo.webhooks import _call_next_donor
        try:
            placed = _call_next_donor(sess.session_id, state, cfg)
            call_mode = "twilio" if placed else "twilio_failed"
        except Exception as exc:
            twilio_error = str(exc)
            call_mode = "twilio_failed"
    else:
        # No Twilio config — simulate
        missing = []
        if not cfg:
            missing.append("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER")
        elif not cfg.test_donor_phone:
            missing.append("TWILIO_TEST_DONOR_PHONE")
        twilio_error = f"Twilio not configured — missing: {', '.join(missing)}. Running in simulated mode."

    if call_mode == "simulated" or call_mode == "twilio_failed":
        state.call_orchestrator.update(
            sess.session_id,
            status="calling",
            current_donor_id=donor_ids[0],
        )

    return {
        "sessionId": sess.session_id,
        "patientId": resolved,
        "slotId": slot.slot_id,
        "donorQueueLength": len(donor_ids),
        "callMode": call_mode,
        "twilioError": twilio_error,
        "status": state.call_orchestrator.get_session(sess.session_id).status,
    }


# ---------------------------------------------------------------------------
# Call Flow: GET /calls/status/{session_id} — poll live status
# ---------------------------------------------------------------------------
@app.get("/calls/status/{session_id}")
def get_call_status(session_id: str):
    """Return the live status of a call session. Poll every 2s from the UI."""
    state = get_state()
    sess = state.call_orchestrator.get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess.to_dict()


# ---------------------------------------------------------------------------
# Call Flow: GET /calls/log — all call sessions
# ---------------------------------------------------------------------------
@app.get("/calls/log")
def get_calls_log(limit: int = Query(20)):
    """Return recent call sessions for the coordinator dashboard."""
    state = get_state()
    sessions = state.call_orchestrator.all_sessions()
    return {
        "total": len(sessions),
        "sessions": [s.to_dict() for s in sessions[:limit]],
    }


# ---------------------------------------------------------------------------
# Simulate: POST /calls/simulate-response — for demo without real Twilio
# ---------------------------------------------------------------------------
class SimulateResponseRequest(BaseModel):
    session_id: str
    response: str  # "accept" or "decline"
    language: str = "en"


@app.post("/calls/simulate-response")
def simulate_call_response(req: SimulateResponseRequest):
    """Simulate a donor's IVR response without a real Twilio call.

    Use this when Twilio is not configured to test the full accept/decline flow.
    Mirrors exactly what the real voice webhook does on accept or decline.
    """
    state = get_state()
    sess = state.call_orchestrator.get_session(req.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if sess.status in ("accepted", "declined_all", "failed"):
        raise HTTPException(status_code=400, detail=f"Session already terminal: {sess.status}")

    state.call_orchestrator.update(req.session_id, language=req.language)

    slot = _find_slot(state, sess.slot_id)
    cfg = get_twilio_config()

    if req.response == "accept":
        if slot:
            for a in slot.assignments:
                if a.donor_id == sess.current_donor_id:
                    a.status = AssignmentStatus.CONFIRMED
                    slot.status = SlotStatus.CONFIRMED
                    break
        state.call_orchestrator.update(
            req.session_id, status="accepted", accepted_donor_id=sess.current_donor_id
        )
        if state.alert_log and slot:
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
            pid_short = (getattr(patient, "patient_id", sess.patient_id) or "")[:10]
            msg = (
                f"PulseLink: Blood ARRANGED for patient {pid_short}!\n"
                f"Donor accepted for {slot.window.start} to {slot.window.end}.\n"
                f"Units: {slot.units_needed}"
            )
            try:
                target = cfg.coordinator_phone or cfg.test_donor_phone
                if target:
                    send_whatsapp(target, msg, cfg)
            except Exception:
                pass
            state.call_orchestrator.update(req.session_id, patient_notified=True)

    elif req.response == "decline":
        if slot:
            for a in slot.assignments:
                if a.donor_id == sess.current_donor_id:
                    a.status = AssignmentStatus.DECLINED
                    break
        declined = list(sess.declined_donors) + [sess.current_donor_id]
        next_idx = sess.current_idx + 1

        if next_idx < len(sess.donor_queue):
            state.call_orchestrator.update(
                req.session_id,
                current_idx=next_idx,
                current_donor_id=sess.donor_queue[next_idx],
                declined_donors=declined,
                status="calling",
            )
            if cfg:
                try:
                    target = cfg.coordinator_phone or cfg.test_donor_phone
                    if target:
                        send_whatsapp(
                            target,
                            f"PulseLink: Donor declined. Calling next ({next_idx + 1}/{len(sess.donor_queue)})...",
                            cfg,
                        )
                except Exception:
                    pass
        else:
            state.call_orchestrator.update(
                req.session_id, status="declined_all", declined_donors=declined
            )
            if cfg:
                try:
                    target = cfg.coordinator_phone or cfg.test_donor_phone
                    if target:
                        send_whatsapp(
                            target,
                            f"PulseLink ALERT: All donors declined for patient {sess.patient_id[:10]}. Please intervene.",
                            cfg,
                        )
                except Exception:
                    pass
    else:
        raise HTTPException(status_code=400, detail="response must be 'accept' or 'decline'")

    updated = state.call_orchestrator.get_session(req.session_id)
    return updated.to_dict()


# ---------------------------------------------------------------------------
# Parse inbox: GET /parse-inbox — incoming WhatsApp messages from new patients
# ---------------------------------------------------------------------------
@app.get("/parse-inbox")
def get_parse_inbox():
    """Return incoming WhatsApp messages received via /webhooks/whatsapp."""
    state = get_state()
    inbox = getattr(state, "parse_inbox", [])
    return {"total": len(inbox), "messages": list(reversed(inbox))}


# ---------------------------------------------------------------------------
# Patients: POST /patients/confirm — coordinator confirms a parsed patient
# ---------------------------------------------------------------------------
class ConfirmPatientRequest(BaseModel):
    message_id: str
    blood_group: str
    cadence_days: float = 28.0
    city_id: str = "hyderabad"
    quantity_required: int = 2


@app.post("/patients/confirm")
def confirm_patient(req: ConfirmPatientRequest):
    """Add a new patient to the system from a coordinator-reviewed parse result.

    Creates a Patient, grants consent, generates a subscription, and marks
    the incoming message as confirmed. The patient immediately appears in
    the coordinator dashboard.
    """
    state = get_state()

    # Mark message as confirmed in inbox
    inbox = getattr(state, "parse_inbox", [])
    for msg in inbox:
        if msg.get("messageId") == req.message_id:
            msg["status"] = "confirmed"
            break

    # Create patient
    from pulselink.common.enums import BloodGroup
    from pulselink.common.models import Patient
    from pulselink.matching.match import MatchCandidate
    from pulselink.reliability.scoring import DonorStats
    from pulselink.subscription.generate import generate

    try:
        blood_group = BloodGroup(req.blood_group)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown blood group: {req.blood_group}")

    patient_id = f"PAT-WA-{uuid.uuid4().hex[:8].upper()}"
    patient = Patient(
        patient_id=patient_id,
        city_id=req.city_id,
        blood_group=blood_group,
        quantity_required=req.quantity_required,
        cadence_days=req.cadence_days,
        consent_id=f"consent-{patient_id}",
    )
    state.patients[patient_id] = patient

    # Grant consent
    if state.consent:
        from pulselink.common.enums import ConsentScope
        state.consent.grant(
            patient_id, "patient",
            [ConsentScope.STORE_CONTACT, ConsentScope.USE_IN_FORECASTING],
            "v1-coordinator-confirm",
        )

    # Generate subscription
    candidates = [
        MatchCandidate(donor=d, stats=state.stats_repo.get_stats(d.donor_id) if state.stats_repo else DonorStats())
        for d in state.donors.values()
    ]
    try:
        sub = generate(
            patient,
            horizon_days=90,
            candidates=candidates,
            consent_store=state.consent,
            today=state.today,
            transfusion_dates=[],
            max_backups=7,
        )
        state.subscriptions[patient_id] = sub
    except Exception as exc:
        return {"ok": True, "patientId": patient_id, "warning": f"Subscription generation failed: {exc}"}

    return {"ok": True, "patientId": patient_id, "subscriptionId": sub.subscription_id}
