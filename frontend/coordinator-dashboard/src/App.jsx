// PulseLink Coordinator — unified multi-screen app.
//
// Screens (no react-router, state-based routing):
//   patients     — risk-ranked list, Start Call button, Paste-to-Parse
//   callflow     — live IVR call status with simulate panel
//   notifications — WhatsApp / SMS alert log
//   parse-inbox  — incoming WhatsApp from new patients
//
// The coordinator starts a call flow from the Patients screen → lands on
// CallFlow with live polling → result shown → back to Patients or Notifications.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchPatients, parseRecord, startCallFlow, getCallLog, getParseInbox, getPatientDonors, REVIEW_THRESHOLD } from "./api.js";
import { rankByRisk } from "./risk.js";
import CallFlowPage from "./CallFlowPage.jsx";
import NotificationsPage from "./NotificationsPage.jsx";
import ParseInboxPage from "./ParseInboxPage.jsx";
import "./styles.css";

// ---------------------------------------------------------------------------
// Top-level app with screen routing
// ---------------------------------------------------------------------------
export default function App() {
  const [view, setView] = useState("patients");
  const [callSession, setCallSession] = useState(null); // { sessionId, patientId }
  const [inboxCount, setInboxCount] = useState(0);
  const [callLogCount, setCallLogCount] = useState(0);

  // Poll badge counts
  useEffect(() => {
    async function pollBadges() {
      try {
        const inbox = await getParseInbox();
        setInboxCount((inbox.messages || []).filter((m) => m.status === "pending").length);
      } catch { /* ignore */ }
      try {
        const log = await getCallLog();
        setCallLogCount(log.total || 0);
      } catch { /* ignore */ }
    }
    pollBadges();
    const id = setInterval(pollBadges, 8000);
    return () => clearInterval(id);
  }, []);

  function navigate(newView, data) {
    if (data?.sessionId) setCallSession(data);
    setView(newView);
  }

  return (
    <div className="app-shell">
      <header className="app-nav">
        <span className="nav-brand">PulseLink</span>
        <nav className="nav-links">
          <button
            className={`nav-btn ${view === "patients" ? "nav-active" : ""}`}
            onClick={() => setView("patients")}
          >
            Patients
          </button>
          <button
            className={`nav-btn ${view === "callflow" ? "nav-active" : ""}`}
            onClick={() => setView("callflow")}
          >
            Call Flow
            {callLogCount > 0 && (
              <span className="nav-badge">{callLogCount}</span>
            )}
          </button>
          <button
            className={`nav-btn ${view === "notifications" ? "nav-active" : ""}`}
            onClick={() => setView("notifications")}
          >
            WhatsApp Log
          </button>
          <button
            className={`nav-btn ${view === "parse-inbox" ? "nav-active" : ""}`}
            onClick={() => setView("parse-inbox")}
          >
            Inbox
            {inboxCount > 0 && (
              <span className="nav-badge nav-badge-alert">{inboxCount}</span>
            )}
          </button>
        </nav>
      </header>

      <main className="app-main">
        {view === "patients" && <PatientsPage navigate={navigate} />}
        {view === "callflow" && (
          <CallFlowPage
            sessionId={callSession?.sessionId}
            patientId={callSession?.patientId}
            callMode={callSession?.callMode}
            twilioError={callSession?.twilioError}
            navigate={navigate}
          />
        )}
        {view === "notifications" && <NotificationsPage />}
        {view === "parse-inbox" && <ParseInboxPage />}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PatientsPage — risk list + paste-to-parse
// ---------------------------------------------------------------------------
function resolveCityId() {
  if (typeof window !== "undefined") {
    const p = new URLSearchParams(window.location.search).get("city");
    if (p) return p;
  }
  return "hyderabad";
}

function shortId(id) {
  if (!id) return "—";
  return id.length > 14 ? `${id.slice(0, 10)}…${id.slice(-4)}` : id;
}

function riskBand(score) {
  if (score >= 60) return { label: "High", cls: "risk-high" };
  if (score >= 30) return { label: "Medium", cls: "risk-medium" };
  return { label: "Low", cls: "risk-low" };
}

function formatWindow(slot) {
  const w = (slot && slot.window) || {};
  return w.start && w.end ? `${w.start} → ${w.end}` : "—";
}

function confirmedCount(slot) {
  return ((slot && slot.assignments) || []).filter((x) => x?.status === "confirmed").length;
}

const PATIENT_APP_BASE =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_PATIENT_APP_URL) ||
  "http://localhost:5173";

function tierBadgeClass(tier) {
  if (!tier) return "tier-none";
  switch (tier.toLowerCase()) {
    case "gold":     return "tier-gold";
    case "silver":   return "tier-silver";
    case "bronze":   return "tier-bronze";
    default:         return "tier-none";
  }
}

function assignStatusClass(status) {
  if (!status) return "";
  switch (status) {
    case "confirmed": return "assign-confirmed";
    case "declined":  return "assign-declined";
    default:          return "assign-active";
  }
}

function DonorPanel({ patientId, data, loading }) {
  if (loading) return <p className="muted donor-loading">Loading donors…</p>;
  if (!data) return <p className="muted donor-loading">No donor data.</p>;
  const donors = data.donors || [];
  if (donors.length === 0) return <p className="muted donor-loading">No donors assigned.</p>;

  return (
    <div className="donor-panel">
      <div className="donor-panel-header">
        <span className="donor-panel-title">
          Donors for patient <code title={patientId}>{shortId(patientId)}</code>
        </span>
        <span className="donor-panel-meta">
          Blood: <strong>{data.bloodGroup || "—"}</strong>
          &nbsp;·&nbsp;{donors.length} donor{donors.length !== 1 ? "s" : ""}
        </span>
      </div>
      <div className="donor-table-wrap">
        <table className="donor-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Role</th>
              <th>Blood Group</th>
              <th>Blood Match</th>
              <th>Reliability</th>
              <th>Calls:Don</th>
              <th>Donations</th>
              <th>Eligibility</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {donors.map((d) => (
              <tr key={d.donorId} className={d.rank === 0 ? "donor-primary-row" : ""}>
                <td className="donor-rank">{d.rank + 1}</td>
                <td>
                  <span className={`role-badge ${d.rank === 0 ? "role-primary" : "role-backup"}`}>
                    {d.rank === 0 ? "Primary" : `Backup ${d.rank}`}
                  </span>
                </td>
                <td>{d.bloodGroup || "—"}</td>
                <td>
                  <span className="score-bar-wrap" title={`${Math.round((d.bloodMatchScore || 0) * 100)}%`}>
                    <span className="score-bar" style={{ width: `${Math.round((d.bloodMatchScore || 0) * 100)}%` }} />
                    <span className="score-label">{Math.round((d.bloodMatchScore || 0) * 100)}%</span>
                  </span>
                </td>
                <td className="rel-score">{d.reliabilityScore != null ? d.reliabilityScore.toFixed(1) : "—"}</td>
                <td>{d.callsToDonationsRatio != null ? d.callsToDonationsRatio.toFixed(1) : "—"}</td>
                <td>{d.donationsTillDate ?? "—"}</td>
                <td className={d.eligibilityStatus === "eligible" ? "elig-ok" : "elig-no"}>
                  {d.eligibilityStatus || "—"}
                </td>
                <td>
                  <span className={`assign-badge ${assignStatusClass(d.assignmentStatus)}`}>
                    {d.assignmentStatus || "—"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PatientsPage({ navigate }) {
  const cityId = useMemo(resolveCityId, []);
  const today = useMemo(() => new Date(), []);
  const [patients, setPatients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState({});
  const [expandedId, setExpandedId] = useState(null);
  const [donorData, setDonorData] = useState({});     // patientId → donor response
  const [donorLoading, setDonorLoading] = useState({}); // patientId → bool

  useEffect(() => {
    setLoading(true);
    fetchPatients(cityId).then((res) => {
      setPatients(res.patients || []);
      setLoading(false);
    });
  }, [cityId]);

  const ranked = useMemo(() => {
    const entries = patients.map((p) => ({ patient: p, slot: p.slot }));
    return rankByRisk(entries, today);
  }, [patients, today]);

  async function handleStartCall(patient, slot) {
    const pid = patient.patientId;
    setStarting((s) => ({ ...s, [pid]: true }));
    try {
      const result = await startCallFlow(pid, slot?.slotId || "");
      navigate("callflow", {
        sessionId: result.sessionId,
        patientId: pid,
        callMode: result.callMode,
        twilioError: result.twilioError,
      });
    } catch (e) {
      alert("Failed to start call: " + e.message);
    } finally {
      setStarting((s) => ({ ...s, [pid]: false }));
    }
  }

  async function handleToggleView(pid) {
    if (expandedId === pid) {
      setExpandedId(null);
      return;
    }
    setExpandedId(pid);
    if (!donorData[pid]) {
      setDonorLoading((l) => ({ ...l, [pid]: true }));
      try {
        const data = await getPatientDonors(pid);
        setDonorData((d) => ({ ...d, [pid]: data }));
      } catch {
        setDonorData((d) => ({ ...d, [pid]: null }));
      } finally {
        setDonorLoading((l) => ({ ...l, [pid]: false }));
      }
    }
  }

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h2 className="page-title">Patients — {cityId}</h2>
          <p className="muted">Risk-ranked. Click Start Call for IVR flow · View to see assigned donors.</p>
        </div>
      </div>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : ranked.length === 0 ? (
        <p className="muted">No patients found for this city.</p>
      ) : (
        <div className="panel">
          <table className="risk-table">
            <thead>
              <tr>
                <th>Risk</th>
                <th>Patient</th>
                <th>Blood</th>
                <th>Next window</th>
                <th>Coverage</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map(({ patient, slot, riskScore }) => {
                const band = riskBand(riskScore);
                const confirmed = confirmedCount(slot);
                const needed = (slot && slot.unitsNeeded) || 1;
                const pid = patient.patientId;
                const isOpen = expandedId === pid;
                return (
                  <>
                    <tr key={pid} className={isOpen ? "patient-row-open" : ""}>
                      <td>
                        <span className={`risk-pill ${band.cls}`}>
                          <span className="risk-score">{riskScore.toFixed(1)}</span>
                          <span className="risk-label">{band.label}</span>
                        </span>
                      </td>
                      <th scope="row" className="patient-id" title={pid}>
                        {shortId(pid)}
                      </th>
                      <td>{patient.bloodGroup || "—"}</td>
                      <td className="nowrap">{formatWindow(slot)}</td>
                      <td>
                        <span className={confirmed >= needed ? "coverage-ok" : "coverage-short"}>
                          {confirmed}/{needed}
                        </span>
                      </td>
                      <td className="action-cell">
                        <button
                          className="btn btn-call"
                          onClick={() => handleStartCall(patient, slot)}
                          disabled={starting[pid]}
                          title="Start automated IVR donor call flow"
                        >
                          {starting[pid] ? "Starting…" : "Start Call"}
                        </button>
                        <button
                          className={`btn btn-view ${isOpen ? "btn-view-open" : ""}`}
                          onClick={() => handleToggleView(pid)}
                          title="View assigned donors"
                        >
                          {isOpen ? "Close" : "View"}
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr key={`${pid}-donors`} className="donor-expand-row">
                        <td colSpan={6} className="donor-expand-cell">
                          <DonorPanel
                            patientId={pid}
                            data={donorData[pid]}
                            loading={donorLoading[pid] || false}
                          />
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <PasteToParse cityId={cityId} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// PasteToParse
// ---------------------------------------------------------------------------
const PARSE_PHASE = { IDLE: "idle", SUBMITTING: "submitting", DONE: "done", ERROR: "error" };

function isLowConfidence(field) {
  if (field.flagged) return true;
  const c = Number(field.confidence);
  return !Number.isFinite(c) || c < REVIEW_THRESHOLD;
}

function PasteToParse({ cityId }) {
  const [text, setText] = useState("");
  const [phase, setPhase] = useState(PARSE_PHASE.IDLE);
  const [result, setResult] = useState(null);

  async function onSubmit(e) {
    e.preventDefault();
    if (!text.trim()) return;
    setPhase(PARSE_PHASE.SUBMITTING);
    try {
      const parsed = await parseRecord(text, cityId);
      setResult(parsed);
      setPhase(PARSE_PHASE.DONE);
    } catch {
      setResult(null);
      setPhase(PARSE_PHASE.ERROR);
    }
  }

  return (
    <section className="panel" aria-labelledby="parse-heading">
      <h2 id="parse-heading" className="panel-title">Paste a WhatsApp record to parse</h2>
      <p className="muted">
        Paste any messy text from a patient or family. The LLM Parser extracts
        structured fields — low-confidence ones are highlighted for review.
      </p>
      <form onSubmit={onSubmit} className="parse-form">
        <textarea
          className="parse-input"
          rows={4}
          placeholder="e.g. Patient Ravi B+ve every 21 days Hyderabad, donor 9XXXX speaks Telugu…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button
          type="submit"
          className="btn btn-primary"
          disabled={phase === PARSE_PHASE.SUBMITTING || !text.trim()}
        >
          {phase === PARSE_PHASE.SUBMITTING ? "Parsing…" : "Parse for review"}
        </button>
      </form>
      {phase === PARSE_PHASE.ERROR && (
        <p className="error">Could not parse. Please try again.</p>
      )}
      {phase === PARSE_PHASE.DONE && result && <ParsedReview result={result} />}
    </section>
  );
}

function ParsedReview({ result }) {
  const fields = result.fields || [];
  const flaggedCount = fields.filter(isLowConfidence).length;
  return (
    <div className="parsed">
      <p className="parsed-meta">
        Parsed by <code>{result.modelVersion || "parser"}</code>
        {flaggedCount > 0 ? (
          <span className="flag-summary">{flaggedCount} field(s) need review</span>
        ) : (
          <span className="flag-summary ok">All fields confident</span>
        )}
      </p>
      <ul className="field-list">
        {fields.map((field) => {
          const low = isLowConfidence(field);
          const pct = Math.round(Number(field.confidence) * 100) || 0;
          const val = field.value === null || field.value === undefined ? "—" : String(field.value);
          return (
            <li key={field.path} className={low ? "field field-flagged" : "field"}>
              <div className="field-main">
                <span className="field-label">{field.label || field.path}</span>
                <span className="field-value">{val}</span>
              </div>
              <div className="field-meta">
                <span className="field-conf">{pct}%</span>
                {low && <span className="review-tag">review{field.reason ? ` · ${field.reason}` : ""}</span>}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
