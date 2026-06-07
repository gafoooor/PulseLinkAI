// Coordinator risk dashboard (Task 14.4).
//
// Two surfaces on one calm page:
//  1. A risk-ranked patient list. Each patient's Risk_Score is computed
//     client-side via the pure `risk.js` module (mirroring design 4.6) and the
//     list is sorted by Risk_Score DESCENDING (Req 8.1, 8.2). The list is
//     scoped to the coordinator's city_id (Req 8.3).
//  2. A paste-to-parse box: the coordinator pastes a messy record, it is sent
//     to the Parser (stubbed in api.js), and the parsed result is shown for
//     review with low-confidence fields visually highlighted (Req 8.4).
//
// The city_id comes from a prop or the URL (?city=...). Network access is in
// api.js and is stubbed offline until Task 15.1.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchPatients, parseRecord, REVIEW_THRESHOLD } from "./api.js";
import { rankByRisk } from "./risk.js";
import "./styles.css";

// Resolve the coordinator's city from an explicit prop or the URL (?city=...),
// falling back to a demo city so the page is never empty offline.
function resolveCityId(propCity) {
  if (propCity) return propCity;
  if (typeof window !== "undefined") {
    const fromUrl = new URLSearchParams(window.location.search).get("city");
    if (fromUrl) return fromUrl;
  }
  return "hyderabad";
}

// The patient app dev server (used to build per-patient "Open" links). Override
// at build time via VITE_PATIENT_APP_URL.
const PATIENT_APP_BASE_URL =
  (typeof import.meta !== "undefined" &&
    import.meta.env &&
    import.meta.env.VITE_PATIENT_APP_URL) ||
  "http://localhost:5173";

// Truncate long dataset hex ids for display (full id is kept in the title attr).
function shortId(id) {
  if (!id) return "—";
  return id.length > 14 ? `${id.slice(0, 10)}…${id.slice(-4)}` : id;
}

// A risk band label/class for the at-a-glance indicator. Bands are presentation
// only; the sort key is always the numeric Risk_Score.
function riskBand(score) {
  if (score >= 60) return { label: "High", cls: "risk-high" };
  if (score >= 30) return { label: "Medium", cls: "risk-medium" };
  return { label: "Low", cls: "risk-low" };
}

function formatWindow(slot) {
  const w = (slot && slot.window) || {};
  if (!w.start && !w.end) return "—";
  return `${w.start || "?"} → ${w.end || "?"}`;
}

function confirmedCount(slot) {
  const a = (slot && slot.assignments) || [];
  return a.filter((x) => x && x.status === "confirmed").length;
}

export default function CoordinatorDashboard({ cityId: cityProp, today }) {
  const cityId = useMemo(() => resolveCityId(cityProp), [cityProp]);
  // "today" is injectable for tests/demos; defaults to the real current date.
  const referenceDay = today || new Date();

  const [patients, setPatients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [usingStub, setUsingStub] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPatients(cityId).then((res) => {
      if (cancelled) return;
      setPatients(res.patients || []);
      setUsingStub(Boolean(res.stubbed));
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [cityId]);

  // Compute Risk_Score per patient and sort descending (Req 8.1, 8.2).
  const ranked = useMemo(() => {
    const entries = patients.map((p) => ({ patient: p, slot: p.slot }));
    return rankByRisk(entries, referenceDay);
  }, [patients, referenceDay]);

  return (
    <main className="dash">
      <header className="dash-header">
        <p className="brand">PulseLink</p>
        <h1 className="dash-title">Coordinator risk dashboard</h1>
        <p className="dash-sub">
          City <strong>{cityId}</strong> · patients ranked by risk of missing
          their next transfusion
          {usingStub ? <span className="stub-badge">offline demo data</span> : null}
        </p>
      </header>

      <RiskList loading={loading} ranked={ranked} today={referenceDay} />

      <PasteToParse cityId={cityId} />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Risk-ranked patient list (Req 8.1, 8.2, 8.3)
// ---------------------------------------------------------------------------

function RiskList({ loading, ranked, today }) {
  return (
    <section className="panel" aria-labelledby="risk-heading">
      <h2 id="risk-heading" className="panel-title">
        Patients by risk
      </h2>

      {loading ? (
        <p className="muted">Loading patients…</p>
      ) : ranked.length === 0 ? (
        <p className="muted">No patients found for this city.</p>
      ) : (
        <table className="risk-table">
          <caption className="sr-only">
            Patients ordered by Risk_Score, highest risk first
          </caption>
          <thead>
            <tr>
              <th scope="col">Risk</th>
              <th scope="col">Patient</th>
              <th scope="col">Blood</th>
              <th scope="col">Next window</th>
              <th scope="col">Coverage</th>
              <th scope="col">View</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map(({ patient, slot, riskScore }) => {
              const band = riskBand(riskScore);
              const confirmed = confirmedCount(slot);
              const needed = (slot && slot.unitsNeeded) || 1;
              const patientUrl = `${PATIENT_APP_BASE_URL}/?patient=${encodeURIComponent(
                patient.patientId
              )}&token=demo&lang=en`;
              return (
                <tr key={patient.patientId}>
                  <td>
                    <span className={`risk-pill ${band.cls}`}>
                      <span className="risk-score">{riskScore.toFixed(1)}</span>
                      <span className="risk-label">{band.label}</span>
                    </span>
                  </td>
                  <th scope="row" className="patient-id" title={patient.patientId}>
                    {shortId(patient.patientId)}
                  </th>
                  <td>{patient.bloodGroup || "—"}</td>
                  <td className="nowrap">{formatWindow(slot)}</td>
                  <td>
                    <span
                      className={
                        confirmed >= needed ? "coverage-ok" : "coverage-short"
                      }
                    >
                      {confirmed}/{needed} units
                    </span>
                  </td>
                  <td>
                    <a
                      className="patient-link"
                      href={patientUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open
                    </a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Paste-to-parse box (Req 8.4)
// ---------------------------------------------------------------------------

const PARSE_PHASE = { IDLE: "idle", SUBMITTING: "submitting", DONE: "done", ERROR: "error" };

function PasteToParse({ cityId }) {
  const [text, setText] = useState("");
  const [phase, setPhase] = useState(PARSE_PHASE.IDLE);
  const [result, setResult] = useState(null);

  const onSubmit = useCallback(
    async (e) => {
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
    },
    [text, cityId]
  );

  return (
    <section className="panel" aria-labelledby="parse-heading">
      <h2 id="parse-heading" className="panel-title">
        Paste a record to parse
      </h2>
      <p className="muted">
        Paste a messy WhatsApp-style record. It is sent to the Parser for review
        — low-confidence fields are highlighted. Nothing is saved until you
        confirm.
      </p>

      <form onSubmit={onSubmit} className="parse-form">
        <label htmlFor="paste-box" className="sr-only">
          Record text
        </label>
        <textarea
          id="paste-box"
          className="parse-input"
          rows={5}
          placeholder="e.g. Patient Ravi, B+ve, every 21 days, next around 15 Jan, donor 9XXXX speaks Telugu…"
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

      {phase === PARSE_PHASE.ERROR ? (
        <p className="error" role="alert">
          Could not parse that record. Please try again.
        </p>
      ) : null}

      {phase === PARSE_PHASE.DONE && result ? (
        <ParsedReview result={result} />
      ) : null}
    </section>
  );
}

// A leaf field counts as low-confidence (needs review) when it is explicitly
// flagged or its confidence is below the Parser's review threshold.
function isLowConfidence(field) {
  if (field.flagged) return true;
  const c = Number(field.confidence);
  return !Number.isFinite(c) || c < REVIEW_THRESHOLD;
}

function ParsedReview({ result }) {
  const fields = result.fields || [];
  const flaggedCount = fields.filter(isLowConfidence).length;

  return (
    <div className="parsed" role="region" aria-label="Parsed record for review">
      <p className="parsed-meta">
        Parsed by <code>{result.modelVersion || "parser"}</code>
        {result.stubbed ? <span className="stub-badge">offline stub</span> : null}
        {flaggedCount > 0 ? (
          <span className="flag-summary">
            {flaggedCount} field{flaggedCount === 1 ? "" : "s"} need review
          </span>
        ) : (
          <span className="flag-summary ok">All fields confident</span>
        )}
      </p>

      <ul className="field-list">
        {fields.map((field) => {
          const low = isLowConfidence(field);
          const pct = Number.isFinite(Number(field.confidence))
            ? Math.round(Number(field.confidence) * 100)
            : 0;
          const displayValue =
            field.value === null || field.value === undefined || field.value === ""
              ? "—"
              : String(field.value);
          return (
            <li
              key={field.path}
              className={low ? "field field-flagged" : "field"}
              // Mark low-confidence fields assertively for screen readers.
              aria-invalid={low ? "true" : undefined}
            >
              <div className="field-main">
                <span className="field-label">{field.label || field.path}</span>
                <span className="field-value">{displayValue}</span>
              </div>
              <div className="field-meta">
                <span className="field-conf">{pct}%</span>
                {low ? (
                  <span className="review-tag">
                    review{field.reason ? ` · ${field.reason}` : ""}
                  </span>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
