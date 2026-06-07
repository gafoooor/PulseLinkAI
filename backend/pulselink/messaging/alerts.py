"""WhatsApp/SMS alert system for PulseLink (Design Component 5 extension).

Sends three categories of consent-gated, multilingual alert messages:

1. UPCOMING_REMINDER — to the primary donor when their slot window is within
   N days. Directly attacks the high calls_to_donations_ratio dataset signal:
   proactive reminders reduce the need for repeated manual calls.

2. DONOR_ACCEPTED — confirmation to the donor after they accept a slot.

3. PATIENT_ARRANGED — notification to the patient/family when a donor confirms.
   This is the "blood is arranged" reassurance described in Design Requirement 1.

4. DONOR_DECLINED — polite confirmation to a donor after they decline.

The alert log (InMemoryAlertLog) records every sent alert with a mock phone
number so the coordinator dashboard can inspect all outbound messages without
a live WhatsApp gateway. In production this module routes through the real
MessageChannel (Twilio / Gupshup WhatsApp Business API).

Note on phone numbers: the Dataset.csv contains no real phone numbers. The
demo assigns deterministic mock phone numbers (generated in seed.py) stored in
ContactPoint.value_encrypted. InMemoryAlertLog uses these for display only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


# --------------------------------------------------------------------------- #
# Alert type enum
# --------------------------------------------------------------------------- #
class AlertType(str, Enum):
    UPCOMING_REMINDER = "upcoming_reminder"
    DONOR_ACCEPTED = "donor_accepted"
    PATIENT_ARRANGED = "patient_arranged"
    DONOR_DECLINED = "donor_declined"


# --------------------------------------------------------------------------- #
# Multilingual alert templates
# --------------------------------------------------------------------------- #
_TEMPLATES: Dict[str, Dict[str, str]] = {
    AlertType.UPCOMING_REMINDER: {
        "en": (
            "PulseLink Reminder: A patient in your support network needs "
            "{units} unit(s) of blood between {start} and {end}. "
            "Please confirm your availability. "
            "Reply ACCEPT to confirm or DECLINE to pass. Thank you!"
        ),
        "te": (
            "PulseLink రిమైండర్: మీ సహాయ నెట్‌వర్క్‌లో ఒక రోగికి "
            "{start} నుండి {end} వరకు {units} యూనిట్(ల) రక్తం అవసరం. "
            "దయచేసి మీ అందుబాటును నిర్ధారించండి. "
            "నిర్ధారించడానికి ACCEPT అని లేదా వదిలేయడానికి DECLINE అని రిప్లై ఇవ్వండి. ధన్యవాదాలు!"
        ),
        "hi": (
            "PulseLink रिमाइंडर: आपके सहयोग नेटवर्क में एक मरीज़ को "
            "{start} और {end} के बीच {units} यूनिट रक्त की आवश्यकता है। "
            "कृपया अपनी उपलब्धता की पुष्टि करें। "
            "पुष्टि के लिए ACCEPT या पास के लिए DECLINE रिप्लाई करें। धन्यवाद!"
        ),
        "ta": (
            "PulseLink நினைவூட்டல்: உங்கள் ஆதரவு வலைப்பின்னலில் உள்ள "
            "ஒரு நோயாளிக்கு {start} முதல் {end} வரை {units} யூனிட் இரத்தம் "
            "தேவை. தயவுசெய்து உங்கள் கிடைக்கும் தன்மையை உறுதிப்படுத்துங்கள். "
            "ஏற்க ACCEPT அல்லது மறுக்க DECLINE என்று பதில் அனுப்புங்கள். நன்றி!"
        ),
    },
    AlertType.DONOR_ACCEPTED: {
        "en": (
            "Thank you for accepting! Your support means the world. "
            "You are confirmed as the blood donor for {start} to {end}. "
            "The patient's family has been notified. You are saving a life!"
        ),
        "te": (
            "అంగీకరించినందుకు ధన్యవాదాలు! మీ మద్దతు చాలా విలువైనది. "
            "మీరు {start} నుండి {end} వరకు రక్త దాతగా నిర్ధారించబడ్డారు. "
            "రోగి కుటుంబానికి తెలియజేయబడింది. మీరు ఒక జీవితాన్ని కాపాడుతున్నారు!"
        ),
        "hi": (
            "स्वीकार करने के लिए धन्यवाद! आपका सहयोग बहुत मायने रखता है। "
            "आप {start} से {end} तक रक्त दाता के रूप में पुष्टि किए गए हैं। "
            "मरीज़ के परिवार को सूचित कर दिया गया है। आप एक जीवन बचा रहे हैं!"
        ),
        "ta": (
            "ஏற்றுக்கொண்டதற்கு நன்றி! உங்கள் ஆதரவு மிகவும் மதிப்புமிக்கது. "
            "நீங்கள் {start} முதல் {end} வரை இரத்த தானியாளராக உறுதிசெய்யப்பட்டுள்ளீர்கள். "
            "நோயாளியின் குடும்பத்திற்கு தெரிவிக்கப்பட்டது. நீங்கள் ஒரு உயிரைக் காப்பாற்றுகிறீர்கள்!"
        ),
    },
    AlertType.PATIENT_ARRANGED: {
        "en": (
            "Great news from PulseLink! Blood has been arranged for your "
            "transfusion between {start} and {end}. Your donor is confirmed. "
            "Stay strong — we have got you covered."
        ),
        "te": (
            "PulseLink నుండి శుభవార్త! {start} నుండి {end} వరకు మీ "
            "రక్తమార్పిడికి రక్తం సిద్ధం చేయబడింది. మీ రక్తదాత నిర్ధారించబడ్డారు. "
            "ధైర్యంగా ఉండండి — మేము మీతో ఉన్నాం."
        ),
        "hi": (
            "PulseLink से अच्छी खबर! {start} से {end} के बीच आपके "
            "रक्त आधान के लिए रक्त की व्यवस्था हो गई है। आपका दाता पुष्टि "
            "किया गया है। मजबूत रहें — हम आपके साथ हैं।"
        ),
        "ta": (
            "PulseLink இலிருந்து நல்ல செய்தி! {start} முதல் {end} வரை "
            "உங்கள் இரத்தமாற்றத்திற்கு இரத்தம் ஏற்பாடு செய்யப்பட்டுள்ளது. "
            "உங்கள் தானியாளர் உறுதிசெய்யப்பட்டுள்ளார். தைரியமாக இருங்கள் — "
            "நாங்கள் உங்களுடன் இருக்கிறோம்."
        ),
    },
    AlertType.DONOR_DECLINED: {
        "en": (
            "Understood. We will reach out to another donor in your support "
            "network. Thank you for letting us know promptly — your honesty "
            "helps us plan better. We appreciate your continued support!"
        ),
        "te": (
            "అర్థమైంది. మేము మీ సహాయ నెట్‌వర్క్‌లో మరొక దాతను సంప్రదిస్తాం. "
            "తక్షణమే తెలియజేసినందుకు ధన్యవాదాలు — మీ నిజాయితీ మాకు మెరుగ్గా "
            "ప్రణాళిక చేయడంలో సహాయపడుతుంది. మీ నిరంతర మద్దతుకు ధన్యవాదాలు!"
        ),
        "hi": (
            "समझ गया। हम आपके सहयोग नेटवर्क में किसी अन्य दाता से संपर्क "
            "करेंगे। तुरंत बताने के लिए धन्यवाद — आपकी ईमानदारी हमें बेहतर "
            "योजना बनाने में मदद करती है। आपके निरंतर समर्थन के लिए आभार!"
        ),
        "ta": (
            "புரிந்தது. உங்கள் ஆதரவு வலைப்பின்னலில் மற்றொரு தானியாளரை "
            "தொடர்புகொள்வோம். உடனடியாக தெரிவித்தற்கு நன்றி — உங்கள் நேர்மை "
            "நாங்கள் சிறப்பாக திட்டமிட உதவுகிறது. தொடர்ந்த ஆதரவுக்கு நன்றி!"
        ),
    },
}

_FALLBACK_LANG = "en"


# --------------------------------------------------------------------------- #
# Alert record + log
# --------------------------------------------------------------------------- #
@dataclass
class AlertRecord:
    """An inspectable record of one sent alert message."""

    alert_id: str
    alert_type: AlertType
    recipient_type: str   # "donor" or "patient"
    recipient_id: str
    slot_id: str
    lang: str
    message_text: str
    sent_at: datetime
    channel: str          # "whatsapp", "sms", or "mock"
    mock_phone: str       # demo: unencrypted mock phone number


class InMemoryAlertLog:
    """In-process alert log for the offline demo.

    Records every alert sent during the demo session. Production routes
    through the real MessageChannel (WhatsApp Business API / SNS SMS).
    All sends are inspectable via :meth:`get_alerts` so the coordinator
    dashboard can display the full notification history.
    """

    def __init__(self) -> None:
        self._alerts: List[AlertRecord] = []
        self._counter: int = 0

    def send_alert(
        self,
        alert_type: AlertType,
        recipient_type: str,
        recipient_id: str,
        slot_id: str,
        lang: str,
        mock_phone: str = "+910000000000",
        channel: str = "mock",
        **kwargs,
    ) -> AlertRecord:
        """Record and return a sent alert.

        Renders the template for ``alert_type`` in ``lang`` (falling back
        to English) and records it in the in-memory log.
        """
        self._counter += 1
        text = format_alert(alert_type, lang, **kwargs)
        record = AlertRecord(
            alert_id=f"alert-{self._counter}-{str(uuid.uuid4())[:8]}",
            alert_type=alert_type,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            slot_id=slot_id,
            lang=lang,
            message_text=text,
            sent_at=datetime.now(timezone.utc),
            channel=channel,
            mock_phone=mock_phone,
        )
        self._alerts.append(record)
        return record

    def get_alerts(
        self,
        recipient_id: Optional[str] = None,
        slot_id: Optional[str] = None,
        alert_type: Optional[AlertType] = None,
    ) -> List[AlertRecord]:
        """Return a filtered view of the alert log."""
        result = list(self._alerts)
        if recipient_id:
            result = [a for a in result if a.recipient_id == recipient_id]
        if slot_id:
            result = [a for a in result if a.slot_id == slot_id]
        if alert_type:
            result = [a for a in result if a.alert_type == alert_type]
        return result

    @property
    def all_alerts(self) -> List[AlertRecord]:
        """All alerts in chronological order."""
        return list(self._alerts)

    def __bool__(self) -> bool:
        return True  # empty log is still a valid log; don't short-circuit callers

    def __len__(self) -> int:
        return len(self._alerts)


# --------------------------------------------------------------------------- #
# Template rendering
# --------------------------------------------------------------------------- #
def format_alert(alert_type: AlertType, lang: str, **kwargs) -> str:
    """Render an alert template for ``alert_type`` in ``lang``.

    Falls back to English when ``lang`` is not in the catalog for this type.
    """
    lang_norm = (lang or _FALLBACK_LANG).strip().lower()
    catalog = _TEMPLATES.get(alert_type, {})
    template = catalog.get(lang_norm, catalog.get(_FALLBACK_LANG, ""))
    if not template:
        return f"[PulseLink alert: {alert_type.value}]"
    try:
        return template.format(**kwargs)
    except KeyError:
        return template  # return unformatted if kwargs are missing


# --------------------------------------------------------------------------- #
# High-level alert senders
# --------------------------------------------------------------------------- #
def send_upcoming_reminders(
    subscriptions,
    donors_by_id: dict,
    alert_log: InMemoryAlertLog,
    today: date,
    days_ahead: int = 7,
    contact_points: Optional[dict] = None,
) -> List[AlertRecord]:
    """Send upcoming-slot reminders to primary donors whose window is near.

    For each subscription's first non-fulfilled slot whose window starts
    within ``days_ahead`` days of ``today``, sends a reminder to the
    primary (rank-0) donor. Returns the list of alerts sent.

    This directly attacks the dataset's high calls_to_donations_ratio:
    proactive WhatsApp reminders reduce the number of manual calls needed.
    """
    sent: List[AlertRecord] = []

    for sub in subscriptions:
        for slot in sub.slots:
            status_val = (
                slot.status.value if hasattr(slot.status, "value") else slot.status
            )
            if status_val in ("fulfilled", "missed"):
                continue

            days_to_start = (slot.window.start - today).days
            if days_to_start < 0 or days_to_start > days_ahead:
                continue

            # Find primary (rank 0) active assignment
            primary_donor_id = None
            for a in slot.assignments:
                status = a.status.value if hasattr(a.status, "value") else a.status
                if a.rank == 0 and status in ("active", "confirmed"):
                    primary_donor_id = a.donor_id
                    break

            if primary_donor_id is None:
                continue

            donor = donors_by_id.get(primary_donor_id)
            if donor is None:
                continue

            lang = getattr(donor, "preferred_lang", "en") or "en"
            cp = (contact_points or {}).get(primary_donor_id)
            mock_phone = cp.value_encrypted if cp else "+910000000000"

            alert = alert_log.send_alert(
                alert_type=AlertType.UPCOMING_REMINDER,
                recipient_type="donor",
                recipient_id=primary_donor_id,
                slot_id=slot.slot_id,
                lang=lang,
                mock_phone=mock_phone,
                start=slot.window.start.isoformat(),
                end=slot.window.end.isoformat(),
                units=slot.units_needed,
            )
            sent.append(alert)
            break  # one reminder per subscription (first eligible slot)

    return sent


def send_acceptance_alerts(
    slot,
    donor_id: str,
    patient_id: str,
    donors_by_id: dict,
    alert_log: InMemoryAlertLog,
    lang: str = "en",
    contact_points: Optional[dict] = None,
) -> List[AlertRecord]:
    """Send confirmation alerts to both donor and patient when slot is accepted.

    Returns a list of two alerts: [donor_confirmation, patient_notification].
    The patient gets the "blood is arranged" reassurance (Design Req 1.1).
    """
    sent: List[AlertRecord] = []

    donor = donors_by_id.get(donor_id)
    donor_lang = lang
    if donor:
        donor_lang = getattr(donor, "preferred_lang", lang) or lang

    cp_donor = (contact_points or {}).get(donor_id)
    mock_phone_donor = cp_donor.value_encrypted if cp_donor else "+910000000000"

    # Alert to donor
    donor_alert = alert_log.send_alert(
        alert_type=AlertType.DONOR_ACCEPTED,
        recipient_type="donor",
        recipient_id=donor_id,
        slot_id=slot.slot_id,
        lang=donor_lang,
        mock_phone=mock_phone_donor,
        start=slot.window.start.isoformat(),
        end=slot.window.end.isoformat(),
    )
    sent.append(donor_alert)

    # Alert to patient (English fallback; patient preferred lang not tracked in demo)
    cp_patient = (contact_points or {}).get(patient_id)
    mock_phone_patient = cp_patient.value_encrypted if cp_patient else "+910000000001"

    patient_alert = alert_log.send_alert(
        alert_type=AlertType.PATIENT_ARRANGED,
        recipient_type="patient",
        recipient_id=patient_id,
        slot_id=slot.slot_id,
        lang="en",
        mock_phone=mock_phone_patient,
        start=slot.window.start.isoformat(),
        end=slot.window.end.isoformat(),
    )
    sent.append(patient_alert)

    return sent


def send_decline_alert(
    donor_id: str,
    slot_id: str,
    donors_by_id: dict,
    alert_log: InMemoryAlertLog,
    lang: str = "en",
    contact_points: Optional[dict] = None,
) -> AlertRecord:
    """Send a polite decline-confirmation to the donor.

    Acknowledges the donor's decision and thanks them promptly so they feel
    heard — a key factor in maintaining long-term donor engagement.
    """
    donor = donors_by_id.get(donor_id)
    donor_lang = lang
    if donor:
        donor_lang = getattr(donor, "preferred_lang", lang) or lang

    cp = (contact_points or {}).get(donor_id)
    mock_phone = cp.value_encrypted if cp else "+910000000000"

    return alert_log.send_alert(
        alert_type=AlertType.DONOR_DECLINED,
        recipient_type="donor",
        recipient_id=donor_id,
        slot_id=slot_id,
        lang=donor_lang,
        mock_phone=mock_phone,
    )
