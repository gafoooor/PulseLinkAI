// CallFlowPage — live view of an active IVR donor-calling session.
//
// Polls /calls/status/{sessionId} every 2 seconds and renders the current
// state of the call: which donor is being called, language selected, and
// whether they accepted or declined. On decline the next donor is auto-called
// and the UI updates instantly. Includes a "Simulate" panel for testing
// without a real Twilio account.

import React, { useCallback, useEffect, useRef, useState } from "react";
import { getCallStatus, simulateCallResponse } from "./api.js";

const STATUS_LABELS = {
  idle: "Preparing call…",
  calling: "Calling donor…",
  awaiting_language: "Waiting for language selection…",
  awaiting_response: "Waiting for donor response…",
  accepted: "Donor accepted!",
  declined_all: "All donors declined",
  failed: "Call failed",
  no_answer: "No answer",
};

const STATUS_CLASS = {
  idle: "status-idle",
  calling: "status-calling",
  awaiting_language: "status-calling",
  awaiting_response: "status-calling",
  accepted: "status-accepted",
  declined_all: "status-declined",
  failed: "status-failed",
  no_answer: "status-declined",
};

function isTerminal(status) {
  return ["accepted", "declined_all", "failed"].includes(status);
}

export default function CallFlowPage({ sessionId, patientId, callMode, twilioError, navigate }) {
  const [session, setSession] = useState(null);
  const [error, setError] = useState("");
  const [simulating, setSimulating] = useState(false);
  const [simLang, setSimLang] = useState("en");
  const intervalRef = useRef(null);

  const poll = useCallback(async () => {
    if (!sessionId) return;
    try {
      const data = await getCallStatus(sessionId);
      setSession(data);
      if (isTerminal(data.status)) {
        clearInterval(intervalRef.current);
      }
    } catch (e) {
      setError("Lost connection to server.");
    }
  }, [sessionId]);

  useEffect(() => {
    poll();
    intervalRef.current = setInterval(poll, 2000);
    return () => clearInterval(intervalRef.current);
  }, [poll]);

  async function handleSimulate(response) {
    if (!session || isTerminal(session.status)) return;
    setSimulating(true);
    try {
      const updated = await simulateCallResponse(sessionId, response, simLang);
      setSession(updated);
      if (!isTerminal(updated.status)) {
        // re-start polling for next call
        clearInterval(intervalRef.current);
        intervalRef.current = setInterval(poll, 2000);
      }
    } catch (e) {
      setError("Simulation failed: " + e.message);
    } finally {
      setSimulating(false);
    }
  }

  if (!sessionId) {
    return (
      <div className="page-content">
        <p className="muted">No active call session. Start a call from the Patients page.</p>
        <button className="btn btn-secondary" onClick={() => navigate("patients")}>
          Back to Patients
        </button>
      </div>
    );
  }

  const status = session?.status || "idle";
  const statusCls = STATUS_CLASS[status] || "status-idle";
  const terminal = isTerminal(status);

  return (
    <div className="page-content">
      <div className="call-header">
        <button className="btn-back" onClick={() => navigate("patients")}>
          ← Back
        </button>
        <h2 className="call-title">Live Call Session</h2>
        <span className="session-id-badge">{sessionId?.slice(0, 16)}</span>
      </div>

      {/* Call mode indicator */}
      {callMode && (
        <div className={`mode-banner ${callMode === "twilio" ? "mode-live" : "mode-sim"}`}>
          {callMode === "twilio" ? (
            <span>Live Twilio call placed — check phone <strong>+918121467201</strong></span>
          ) : callMode === "twilio_failed" ? (
            <span>Twilio call failed — running in simulated mode. Error: {twilioError}</span>
          ) : (
            <span>Simulated mode — no real phone call. {twilioError || "Configure Twilio + ngrok to call real phones."}</span>
          )}
        </div>
      )}

      {error && <p className="error-banner">{error}</p>}

      {!session ? (
        <div className="call-card">
          <div className="call-spinner" />
          <p className="muted">Connecting…</p>
        </div>
      ) : (
        <>
          {/* Status panel */}
          <div className={`call-card call-status-card ${statusCls}`}>
            <div className="call-status-row">
              <span className={`status-dot-large ${statusCls}`} />
              <div>
                <div className="call-status-label">{STATUS_LABELS[status] || status}</div>
                {session.language && status !== "idle" && (
                  <div className="call-lang-badge">
                    Lang: {session.language.toUpperCase()}
                  </div>
                )}
              </div>
            </div>

            <dl className="call-info-grid">
              <div className="call-info-item">
                <dt>Patient</dt>
                <dd title={session.patientId}>{session.patientId?.slice(0, 14)}…</dd>
              </div>
              <div className="call-info-item">
                <dt>Slot</dt>
                <dd>{session.slotId || "—"}</dd>
              </div>
              <div className="call-info-item">
                <dt>Donor queue</dt>
                <dd>
                  {session.currentDonorIdx + 1} / {session.donorQueueLength}
                </dd>
              </div>
              <div className="call-info-item">
                <dt>Current donor</dt>
                <dd title={session.currentDonorId}>
                  {session.currentDonorId?.slice(0, 12)}…
                </dd>
              </div>
              {session.acceptedDonorId && (
                <div className="call-info-item">
                  <dt>Accepted by</dt>
                  <dd className="accepted-text" title={session.acceptedDonorId}>
                    {session.acceptedDonorId?.slice(0, 12)}…
                  </dd>
                </div>
              )}
              <div className="call-info-item">
                <dt>Patient notified</dt>
                <dd>{session.patientNotified ? "Yes (WhatsApp sent)" : "Not yet"}</dd>
              </div>
            </dl>
          </div>

          {/* Declined donors history */}
          {session.declinedDonors?.length > 0 && (
            <div className="call-card">
              <h3 className="panel-subtitle">Declined / No Answer</h3>
              <ol className="declined-list">
                {session.declinedDonors.map((d, i) => (
                  <li key={i} className="declined-item">
                    <span className="declined-dot" />
                    <span title={d}>{d.slice(0, 16)}…</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Simulate panel — shown when Twilio not configured */}
          {!terminal && (
            <div className="call-card simulate-panel">
              <h3 className="panel-subtitle">
                Simulate Donor Response
                <span className="sim-note">
                  (use this to test without a real phone call)
                </span>
              </h3>
              <div className="sim-controls">
                <select
                  className="sim-lang-select"
                  value={simLang}
                  onChange={(e) => setSimLang(e.target.value)}
                  disabled={simulating}
                >
                  <option value="en">English</option>
                  <option value="hi">Hindi</option>
                  <option value="te">Telugu</option>
                </select>
                <button
                  className="btn btn-accept"
                  onClick={() => handleSimulate("accept")}
                  disabled={simulating}
                >
                  {simulating ? "…" : "Simulate ACCEPT (1)"}
                </button>
                <button
                  className="btn btn-decline"
                  onClick={() => handleSimulate("decline")}
                  disabled={simulating}
                >
                  {simulating ? "…" : "Simulate DECLINE (2)"}
                </button>
              </div>
              <p className="sim-hint">
                With Twilio configured: the donor's actual phone ({" "}
                <code>TWILIO_TEST_DONOR_PHONE</code>) receives a real call with
                IVR. Press 1/2/3 for language, then 1 to accept or 2 to decline.
              </p>
            </div>
          )}

          {/* Result banner */}
          {status === "accepted" && (
            <div className="result-banner result-accepted">
              Blood ARRANGED! Donor accepted.
              {session.patientNotified
                ? " WhatsApp sent to patient/coordinator."
                : ""}
            </div>
          )}
          {status === "declined_all" && (
            <div className="result-banner result-declined">
              All {session.donorQueueLength} donors declined or did not answer.
              Coordinator intervention required.
            </div>
          )}

          {terminal && (
            <div className="call-actions">
              <button className="btn btn-primary" onClick={() => navigate("patients")}>
                Back to Patients
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => navigate("notifications")}
              >
                View WhatsApp Log
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
