"""In-memory call session state for the sequential donor IVR flow.

When a coordinator triggers a call flow for a patient, the orchestrator:
1. Builds a donor_queue from the patient's slot assignments (rank 0 first).
2. Creates a CallSession tracking the live state: who is being called,
   which language they selected, and what outcome they gave.
3. On decline / no-answer, advances current_idx to the next donor and
   initiates a new outbound call automatically.
4. Publishes final outcome (accepted / declined_all) for the coordinator
   dashboard to poll via GET /calls/status/{session_id}.

All state is in-process (no database needed for the demo). The orchestrator
is a singleton on DemoState, built once at startup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class CallSession:
    """Live state for one donor-call sequence for a patient slot."""

    session_id: str
    patient_id: str
    slot_id: str
    donor_queue: list
    current_idx: int = 0
    call_sid: str = ""
    language: str = "en"
    status: str = "idle"
    current_donor_id: str = ""
    accepted_donor_id: str = ""
    declined_donors: list = field(default_factory=list)
    patient_notified: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def current_donor(self) -> str:
        if 0 <= self.current_idx < len(self.donor_queue):
            return self.donor_queue[self.current_idx]
        return ""

    def to_dict(self) -> dict:
        return {
            "sessionId": self.session_id,
            "patientId": self.patient_id,
            "slotId": self.slot_id,
            "status": self.status,
            "currentDonorIdx": self.current_idx,
            "currentDonorId": self.current_donor_id,
            "donorQueueLength": len(self.donor_queue),
            "acceptedDonorId": self.accepted_donor_id,
            "declinedDonors": list(self.declined_donors),
            "language": self.language,
            "callSid": self.call_sid,
            "patientNotified": self.patient_notified,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }


class InMemoryCallOrchestrator:
    """Tracks all call sessions and indexes them by call SID for webhook routing."""

    def __init__(self) -> None:
        self._sessions: dict[str, CallSession] = {}
        self._call_sid_index: dict[str, str] = {}  # call_sid -> session_id

    def create_session(
        self, patient_id: str, slot_id: str, donor_queue: list
    ) -> CallSession:
        session_id = f"cs-{uuid.uuid4().hex[:12]}"
        session = CallSession(
            session_id=session_id,
            patient_id=patient_id,
            slot_id=slot_id,
            donor_queue=list(donor_queue),
            current_donor_id=donor_queue[0] if donor_queue else "",
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[CallSession]:
        return self._sessions.get(session_id)

    def get_session_by_call_sid(self, call_sid: str) -> Optional[CallSession]:
        sid = self._call_sid_index.get(call_sid)
        return self._sessions.get(sid) if sid else None

    def register_call_sid(self, session_id: str, call_sid: str) -> None:
        self._call_sid_index[call_sid] = session_id
        if session_id in self._sessions:
            self._sessions[session_id].call_sid = call_sid
            self._sessions[session_id].updated_at = datetime.now(timezone.utc)

    def update(self, session_id: str, **kwargs) -> Optional[CallSession]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        for k, v in kwargs.items():
            setattr(session, k, v)
        session.updated_at = datetime.now(timezone.utc)
        return session

    def all_sessions(self) -> list:
        return sorted(
            self._sessions.values(), key=lambda s: s.created_at, reverse=True
        )
