// PulseLink Coordinator API — all backend calls live here.

const API_BASE =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE_URL) ||
  "http://localhost:8000";

export const REVIEW_THRESHOLD = 0.75;

async function apiFetch(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${text}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Patients + risk list
// ---------------------------------------------------------------------------
export async function fetchPatients(cityId) {
  try {
    const data = await apiFetch(`/coordinator/patients?city=${encodeURIComponent(cityId || "")}`);
    return { patients: data.patients || [] };
  } catch {
    return { patients: [], stubbed: true };
  }
}

// ---------------------------------------------------------------------------
// Parser (paste-to-parse)
// ---------------------------------------------------------------------------
export async function parseRecord(rawText, cityId) {
  return apiFetch("/parse", {
    method: "POST",
    body: JSON.stringify({ rawText, cityId }),
  });
}

// ---------------------------------------------------------------------------
// Call flow
// ---------------------------------------------------------------------------
export async function startCallFlow(patientId, slotId = "") {
  return apiFetch("/calls/start", {
    method: "POST",
    body: JSON.stringify({ patient_id: patientId, slot_id: slotId }),
  });
}

export async function getCallStatus(sessionId) {
  return apiFetch(`/calls/status/${sessionId}`);
}

export async function getCallLog() {
  return apiFetch("/calls/log?limit=30");
}

export async function simulateCallResponse(sessionId, response, language = "en") {
  return apiFetch("/calls/simulate-response", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, response, language }),
  });
}

// ---------------------------------------------------------------------------
// WhatsApp notifications log
// ---------------------------------------------------------------------------
export async function getNotificationsLog(recipientId = "", slotId = "") {
  const params = new URLSearchParams();
  if (recipientId) params.set("recipient_id", recipientId);
  if (slotId) params.set("slot_id", slotId);
  return apiFetch(`/notifications/log?${params}`);
}

export async function sendReminders(daysAhead = 7) {
  return apiFetch(`/admin/send-reminders?days_ahead=${daysAhead}`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// Incoming WhatsApp parse inbox
// ---------------------------------------------------------------------------
export async function getParseInbox() {
  return apiFetch("/parse-inbox");
}

export async function confirmPatient(payload) {
  return apiFetch("/patients/confirm", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// Demo links helper
// ---------------------------------------------------------------------------
export async function getDemoLinks(limit = 5) {
  return apiFetch(`/demo/links?limit=${limit}`);
}

// ---------------------------------------------------------------------------
// Donor map for a patient
// ---------------------------------------------------------------------------
export async function getPatientDonors(patientId) {
  return apiFetch(`/coordinator/donors/${encodeURIComponent(patientId)}`);
}
