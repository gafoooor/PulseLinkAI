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
import { fetchPatients, startCallFlow, getCallLog, getPatientDonors } from "./api.js";
import { rankByRisk } from "./risk.js";
import CallFlowPage from "./CallFlowPage.jsx";
import NotificationsPage from "./NotificationsPage.jsx";
import "./styles.css";

// ---------------------------------------------------------------------------
// Top-level app with screen routing
// ---------------------------------------------------------------------------
export default function App() {
  const [view, setView] = useState("patients");
  const [callSession, setCallSession] = useState(null); // { sessionId, patientId }
  const [callLogCount, setCallLogCount] = useState(0);

  // Poll badge counts
  useEffect(() => {
    async function pollBadges() {
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

function formatMonthLabel(ym) {
  const [year, month] = ym.split("-");
  const names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${names[parseInt(month, 10) - 1]} ${year}`;
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
  const [donorData, setDonorData] = useState({});
  const [donorLoading, setDonorLoading] = useState({});

  // Search + filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [filterRisk, setFilterRisk] = useState("all");
  const [filterBlood, setFilterBlood] = useState("all");
  const [filterMonth, setFilterMonth] = useState("all");
  const [filterCoverage, setFilterCoverage] = useState("all");

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

  // Unique blood groups from loaded patients
  const bloodGroups = useMemo(() => {
    const groups = new Set(patients.map((p) => p.bloodGroup).filter(Boolean));
    return [...groups].sort();
  }, [patients]);

  // Unique months (YYYY-MM) from next window start dates
  const windowMonths = useMemo(() => {
    const months = new Set();
    patients.forEach((p) => {
      const start = p.slot?.window?.start;
      if (start) months.add(start.slice(0, 7));
    });
    return [...months].sort();
  }, [patients]);

  // Apply search + all filters to the risk-ranked list
  const displayed = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return ranked.filter(({ patient, slot, riskScore }) => {
      if (q) {
        const pid = (patient.patientId || "").toLowerCase();
        const blood = (patient.bloodGroup || "").toLowerCase();
        if (!pid.includes(q) && !blood.includes(q)) return false;
      }
      if (filterRisk !== "all" && riskBand(riskScore).label.toLowerCase() !== filterRisk) return false;
      if (filterBlood !== "all" && patient.bloodGroup !== filterBlood) return false;
      if (filterMonth !== "all") {
        const start = slot?.window?.start;
        if (!start || start.slice(0, 7) !== filterMonth) return false;
      }
      if (filterCoverage !== "all") {
        const confirmed = confirmedCount(slot);
        const needed = (slot && slot.unitsNeeded) || 1;
        if (filterCoverage === "full" && confirmed < needed) return false;
        if (filterCoverage === "partial" && (confirmed === 0 || confirmed >= needed)) return false;
        if (filterCoverage === "none" && confirmed > 0) return false;
      }
      return true;
    });
  }, [ranked, searchQuery, filterRisk, filterBlood, filterMonth, filterCoverage]);

  const hasActiveFilter =
    searchQuery.trim() !== "" ||
    filterRisk !== "all" ||
    filterBlood !== "all" ||
    filterMonth !== "all" ||
    filterCoverage !== "all";

  function clearFilters() {
    setSearchQuery("");
    setFilterRisk("all");
    setFilterBlood("all");
    setFilterMonth("all");
    setFilterCoverage("all");
  }

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
          <h2 className="page-title">PulseLink Coordinator Dashboard</h2>
          <p className="muted">Risk-ranked. Click Start Call for IVR flow · View to see assigned donors.</p>
        </div>
      </div>

      {/* Search + filter bar */}
      <div className="search-filter-bar">
        <div className="search-wrap">
          <svg className="search-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" strokeWidth="1.6" />
            <path d="M13 13l3.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <input
            className="search-input"
            type="search"
            placeholder="Search by patient ID or blood group…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        <div className="filter-controls">
          <label className="filter-label">
            <span className="filter-label-text">Risk</span>
            <select className="filter-select" value={filterRisk} onChange={(e) => setFilterRisk(e.target.value)}>
              <option value="all">All</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>
          <label className="filter-label">
            <span className="filter-label-text">Blood group</span>
            <select className="filter-select" value={filterBlood} onChange={(e) => setFilterBlood(e.target.value)}>
              <option value="all">All</option>
              {bloodGroups.map((g) => (
                <option key={g} value={g}>{g}</option>
              ))}
            </select>
          </label>
          <label className="filter-label">
            <span className="filter-label-text">Next window</span>
            <select className="filter-select" value={filterMonth} onChange={(e) => setFilterMonth(e.target.value)}>
              <option value="all">All months</option>
              {windowMonths.map((m) => (
                <option key={m} value={m}>{formatMonthLabel(m)}</option>
              ))}
            </select>
          </label>
          <label className="filter-label">
            <span className="filter-label-text">Coverage</span>
            <select className="filter-select" value={filterCoverage} onChange={(e) => setFilterCoverage(e.target.value)}>
              <option value="all">All</option>
              <option value="full">Full</option>
              <option value="partial">Partial</option>
              <option value="none">None</option>
            </select>
          </label>
        </div>
      </div>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : ranked.length === 0 ? (
        <p className="muted">No patients found for this city.</p>
      ) : (
        <div className="panel">
          <div className="filter-count-row">
            <span className="filter-count">
              Showing <strong>{displayed.length}</strong> of <strong>{ranked.length}</strong> patients
            </span>
            {hasActiveFilter && (
              <button className="clear-filters-btn" onClick={clearFilters}>
                Clear filters
              </button>
            )}
          </div>
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
              {displayed.length === 0 ? (
                <tr>
                  <td colSpan={6} className="no-results">
                    No patients match the current filters.
                  </td>
                </tr>
              ) : (
                displayed.map(({ patient, slot, riskScore }) => {
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
                })
              )}
            </tbody>
          </table>
        </div>
      )}

    </div>
  );
}
