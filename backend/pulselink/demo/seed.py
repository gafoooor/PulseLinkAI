"""Seed the demo with in-memory data from Dataset.csv (Task 15.1).

This module builds the entire in-memory demo state without any database:
patients, donors, consent grants, subscriptions, forecasted windows, and
reliability scores — all derived from the CSV importer's pure mapping output.

The resulting :class:`DemoState` is the single source of truth the demo API
endpoints read from. It mirrors what a production system would persist to RDS
but lives entirely in process so the demo runs offline.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from pulselink.common.config import get_settings
from pulselink.common.consent import InMemoryConsentService
from pulselink.common.enums import ConsentScope, DonorTier, SlotStatus
from pulselink.common.event_bus import InMemoryEventBus
from pulselink.common.models import (
    ContactPoint,
    Donor,
    Patient,
    Slot,
    SlotAssignment,
    Subscription,
)
from pulselink.forecasting.engine import estimate_cadence, predict_window
from pulselink.ingest.importer import map_dataset, normalize_id, read_csv_rows
from pulselink.matching.match import MatchCandidate, rank_donors_for_slot
from pulselink.demo.call_state import InMemoryCallOrchestrator
from pulselink.messaging.alerts import InMemoryAlertLog
from pulselink.reliability.scoring import DonorStats, reliability_score
from pulselink.subscription.generate import generate
from pulselink.subscription.promote import (
    InMemoryDonorStatsRepo,
    InMemorySlotRepo,
    PromoteDeps,
    RecordingEscalation,
    RecordingMessaging,
    handle_decline,
    wire_reliability,
)


def _generate_mock_phone(seed_str: str) -> str:
    """Generate a deterministic mock Indian mobile number from a donor_id hash.

    Uses MD5 of the seed string to produce a reproducible 10-digit mobile
    number in the Indian format +91XXXXXXXXXX. Numbers are for demo display
    only — they are not real phone numbers and are never dialled.
    """
    digest = hashlib.md5(seed_str.encode()).hexdigest()
    # Take 10 hex digits and mod to get a 10-digit number starting with 6-9
    # (Indian mobile number rules: starts with 6, 7, 8, or 9)
    raw = int(digest[:10], 16) % 9_000_000_000
    number = 6_000_000_000 + raw  # guarantee 10 digits starting with 6
    return f"+91{number}"


@dataclass
class DemoState:
    """The entire in-memory state for the offline demo."""

    patients: dict[str, Patient] = field(default_factory=dict)
    donors: dict[str, Donor] = field(default_factory=dict)
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    consent: Optional[InMemoryConsentService] = None
    event_bus: Optional[InMemoryEventBus] = None
    slot_repo: Optional[InMemorySlotRepo] = None
    stats_repo: Optional[InMemoryDonorStatsRepo] = None
    messaging: Optional[RecordingMessaging] = None
    promote_deps: Optional[PromoteDeps] = None
    alert_log: Optional[InMemoryAlertLog] = None
    voice_channel: Optional[object] = None
    contact_points: dict = field(default_factory=dict)
    call_orchestrator: Optional[InMemoryCallOrchestrator] = None
    parse_inbox: list = field(default_factory=list)
    today: date = field(default_factory=date.today)

    def resolve_patient_id(self, raw_id: str) -> Optional[str]:
        """Resolve an incoming patient id to a stored key.

        Dataset ids are stored with the leading ``\\x`` prefix stripped (see
        :func:`pulselink.ingest.importer.normalize_id`). A link may carry the id
        in either form (raw ``\\x...`` or already-normalized), so this tries the
        value as-is first, then the normalized form.
        """
        if raw_id in self.patients:
            return raw_id
        normalized = normalize_id(raw_id)
        if normalized and normalized in self.patients:
            return normalized
        return None


def build_demo_state(csv_path: Optional[str] = None, today: Optional[date] = None) -> DemoState:
    """Build the full demo state from Dataset.csv.

    Pipeline:
    1. Import CSV -> Patient + Donor domain objects
    2. Grant consent for all donors (so matching works)
    3. Score all donors (reliability)
    4. For each patient, forecast window + generate subscription
    5. Wire the decline -> promote -> re-score flow
    """
    settings = get_settings()
    path = csv_path or settings.dataset_csv_path

    # Resolve CSV path relative to the backend directory if not absolute
    if not os.path.isabs(path):
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidate = os.path.join(backend_dir, path)
        if os.path.exists(candidate):
            path = candidate
        else:
            # Try project root
            project_root = os.path.dirname(backend_dir)
            candidate2 = os.path.join(project_root, path)
            if os.path.exists(candidate2):
                path = candidate2

    ref_today = today or date.today()

    # 1. Import
    rows = read_csv_rows(path)
    result = map_dataset(rows, default_lang=settings.default_lang)

    state = DemoState(today=ref_today)
    state.patients = {p.patient_id: p for p in result.patients}
    state.donors = {d.donor_id: d for d in result.donors}

    # 2. Consent — grant all donors contact_for_slots so matching works
    event_bus = InMemoryEventBus()
    state.event_bus = event_bus
    consent = InMemoryConsentService(event_bus=event_bus)
    state.consent = consent

    for donor_id in state.donors:
        consent.grant(
            donor_id,
            "donor",
            [ConsentScope.CONTACT_FOR_SLOTS, ConsentScope.STORE_CONTACT],
            "v1-seed",
        )
    for patient_id in state.patients:
        consent.grant(
            patient_id,
            "patient",
            [ConsentScope.STORE_CONTACT, ConsentScope.USE_IN_FORECASTING],
            "v1-seed",
        )

    # 3. Score all donors
    donor_stats: dict[str, DonorStats] = {}
    for d in state.donors.values():
        stats = DonorStats(
            accepted=0,
            offered=0,
            calls_to_donations_ratio=d.calls_to_donations_ratio,
            donations_till_date=d.donations_till_date,
            last_donation_date=d.last_donation_date,
        )
        donor_stats[d.donor_id] = stats

    state.stats_repo = InMemoryDonorStatsRepo(donor_stats)

    # 4. Generate subscriptions for every patient.
    candidates = [
        MatchCandidate(donor=d, stats=donor_stats.get(d.donor_id, DonorStats()))
        for d in state.donors.values()
    ]
    state.candidates = candidates

    for patient in state.patients.values():
        try:
            transfusion_dates = (
                [patient.last_transfusion_date]
                if patient.last_transfusion_date
                else []
            )
            sub = generate(
                patient,
                horizon_days=90,
                candidates=candidates,
                consent_store=consent,
                today=ref_today,
                transfusion_dates=transfusion_dates,
                max_backups=7,
                patient_lat=patient.lat,
                patient_lng=patient.lng,
            )
            state.subscriptions[patient.patient_id] = sub
        except Exception:
            # Skip patients that can't generate (e.g., no eligible donors)
            pass

    # Simulate some confirmed assignments for demo realism
    _simulate_confirmations(state)

    # 5. Wire decline -> promote -> re-score
    slot_repo = InMemorySlotRepo()
    for sub in state.subscriptions.values():
        for slot in sub.slots:
            slot_repo.add_slot(slot)
    state.slot_repo = slot_repo

    rescorer = wire_reliability(event_bus, state.stats_repo)
    messaging = RecordingMessaging()
    state.messaging = messaging
    escalation = RecordingEscalation()

    state.promote_deps = PromoteDeps(
        repo=slot_repo,
        event_bus=event_bus,
        rescorer=rescorer,
        messaging=messaging,
        escalation=escalation,
    )

    # 6. Alerts log (WhatsApp/SMS demo)
    state.alert_log = InMemoryAlertLog()

    # 7. Contact points: generate mock phone numbers for every donor
    contact_points: dict[str, ContactPoint] = {}
    for donor_id, donor in state.donors.items():
        mock_phone = _generate_mock_phone(donor_id)
        cp = ContactPoint(
            contact_id=f"cp-{donor_id}",
            subject_id=donor_id,
            type="phone",
            value_encrypted=mock_phone,
            preferred_lang=getattr(donor, "preferred_lang", "en") or "en",
        )
        contact_points[donor_id] = cp
    state.contact_points = contact_points

    # 8. Voice channel (MockVoiceChannel for offline demo)
    try:
        from pulselink.messaging.voice import MockVoiceChannel
        state.voice_channel = MockVoiceChannel(event_bus=event_bus)
    except Exception:
        state.voice_channel = None

    # 9. Call orchestrator (manages sequential IVR donor-calling sessions)
    state.call_orchestrator = InMemoryCallOrchestrator()

    # 10. Parse inbox (incoming WhatsApp messages from new patients)
    state.parse_inbox = []

    return state


def _simulate_confirmations(state: DemoState) -> None:
    """Mark some assignments as confirmed so the patient app shows 'arranged'.

    For demo purposes, confirm the primary donor on the first slot of every
    other subscription.
    """
    for i, sub in enumerate(state.subscriptions.values()):
        if i % 2 == 0 and sub.slots:
            slot = sub.slots[0]
            if slot.assignments:
                slot.assignments[0].status = "confirmed"
                slot.status = SlotStatus.CONFIRMED
