// ParseInboxPage — incoming WhatsApp messages from new patients.
//
// When a new patient (or family) texts the coordinator's WhatsApp number,
// Twilio hits /webhooks/whatsapp → the LLM parses it → the message appears
// here. The coordinator reviews the parsed fields and clicks "Add to System"
// to create a real patient subscription.

import React, { useCallback, useEffect, useState } from "react";
import { getParseInbox, confirmPatient } from "./api.js";

const REVIEW_THRESHOLD = 0.75;
const BLOOD_GROUPS = [
  "O Negative", "O Positive", "A Negative", "A Positive",
  "B Negative", "B Positive", "AB Negative", "AB Positive",
];

function isLow(field) {
  return field.flagged || Number(field.confidence) < REVIEW_THRESHOLD;
}

export default function ParseInboxPage() {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [confirmForm, setConfirmForm] = useState({});
  const [confirming, setConfirming] = useState(false);
  const [confirmResult, setConfirmResult] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getParseInbox();
      setMessages(data.messages || []);
    } catch {
      setMessages([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function toggleExpand(msgId) {
    setExpandedId((prev) => (prev === msgId ? null : msgId));
    if (!confirmForm[msgId]) {
      const msg = messages.find((m) => m.messageId === msgId);
      const p = {};
      (msg?.parsedFields || []).forEach((f) => {
        if (f.value != null && !f.flagged) p[f.path] = f.value;
      });
      setConfirmForm((f) => ({
        ...f,
        [msgId]: {
          blood_group: p["patient.blood_group"] || "B Positive",
          cadence_days: p["patient.cadence_days"] || 28,
          city_id: p["patient.city_id"] || "hyderabad",
          quantity_required: p["patient.quantity_required"] || 2,
        },
      }));
    }
  }

  function updateForm(msgId, field, value) {
    setConfirmForm((f) => ({
      ...f,
      [msgId]: { ...f[msgId], [field]: value },
    }));
  }

  async function handleConfirm(msg) {
    setConfirming(true);
    try {
      const form = confirmForm[msg.messageId] || {};
      const result = await confirmPatient({
        message_id: msg.messageId,
        blood_group: form.blood_group || "B Positive",
        cadence_days: Number(form.cadence_days) || 28,
        city_id: form.city_id || "hyderabad",
        quantity_required: Number(form.quantity_required) || 2,
      });
      setConfirmResult((r) => ({
        ...r,
        [msg.messageId]: `Patient added! ID: ${result.patientId}`,
      }));
      load();
    } catch (e) {
      setConfirmResult((r) => ({
        ...r,
        [msg.messageId]: `Failed: ${e.message}`,
      }));
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div className="page-content">
      <h2 className="page-title">Incoming WhatsApp Messages</h2>
      <p className="muted">
        Messages sent to the coordinator's WhatsApp number by new patients or
        family members. The LLM auto-parses them — review and confirm to add to
        the system.
      </p>

      {!loading && messages.length === 0 && (
        <div className="empty-state">
          <p>No incoming messages yet.</p>
          <p className="muted">
            Set <code>TWILIO_WHATSAPP_FROM</code> and configure your Twilio
            WhatsApp webhook to <code>/webhooks/whatsapp</code>. Then text the
            coordinator number from a patient's phone.
          </p>
          <div className="demo-inbox-hint">
            <strong>To test now:</strong> POST to{" "}
            <code>http://localhost:8000/webhooks/whatsapp</code> with form fields{" "}
            <code>From=whatsapp:+919741546360</code>,{" "}
            <code>Body=Patient Ravi B+ every 28 days Hyderabad</code>
          </div>
        </div>
      )}

      <div className="inbox-list">
        {messages.map((msg) => {
          const expanded = expandedId === msg.messageId;
          const form = confirmForm[msg.messageId] || {};
          const confirmed = msg.status === "confirmed";
          const result = confirmResult[msg.messageId];

          return (
            <div
              key={msg.messageId}
              className={`inbox-item ${confirmed ? "inbox-confirmed" : ""}`}
            >
              <div className="inbox-header" onClick={() => !confirmed && toggleExpand(msg.messageId)}>
                <div className="inbox-from">
                  <span className="inbox-phone">{msg.fromNumber}</span>
                  <span className="inbox-time">
                    {new Date(msg.receivedAt).toLocaleString()}
                  </span>
                </div>
                <span className={`inbox-status-badge ${confirmed ? "badge-confirmed" : "badge-pending"}`}>
                  {confirmed ? "Added" : "Pending"}
                </span>
              </div>

              <p className="inbox-body">{msg.body}</p>

              {expanded && !confirmed && (
                <div className="inbox-expanded">
                  {/* Parsed fields preview */}
                  {msg.parsedFields?.length > 0 && (
                    <div className="parsed-preview">
                      <h4 className="parsed-preview-title">LLM-parsed fields</h4>
                      <ul className="field-list">
                        {msg.parsedFields.map((f) => (
                          <li
                            key={f.path}
                            className={isLow(f) ? "field field-flagged" : "field"}
                          >
                            <div className="field-main">
                              <span className="field-label">{f.label || f.path}</span>
                              <span className="field-value">{String(f.value ?? "—")}</span>
                            </div>
                            <div className="field-meta">
                              <span className="field-conf">
                                {Math.round(Number(f.confidence) * 100)}%
                              </span>
                              {isLow(f) && (
                                <span className="review-tag">review</span>
                              )}
                            </div>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Confirm form */}
                  <div className="confirm-form">
                    <h4 className="confirm-title">Add patient to system</h4>
                    <div className="confirm-fields">
                      <label className="confirm-label">
                        Blood Group
                        <select
                          className="confirm-select"
                          value={form.blood_group || "B Positive"}
                          onChange={(e) =>
                            updateForm(msg.messageId, "blood_group", e.target.value)
                          }
                        >
                          {BLOOD_GROUPS.map((g) => (
                            <option key={g} value={g}>{g}</option>
                          ))}
                        </select>
                      </label>
                      <label className="confirm-label">
                        Cadence (days)
                        <input
                          type="number"
                          className="confirm-input"
                          value={form.cadence_days || 28}
                          min={7}
                          max={60}
                          onChange={(e) =>
                            updateForm(msg.messageId, "cadence_days", e.target.value)
                          }
                        />
                      </label>
                      <label className="confirm-label">
                        City
                        <input
                          type="text"
                          className="confirm-input"
                          value={form.city_id || "hyderabad"}
                          onChange={(e) =>
                            updateForm(msg.messageId, "city_id", e.target.value)
                          }
                        />
                      </label>
                      <label className="confirm-label">
                        Units needed
                        <input
                          type="number"
                          className="confirm-input"
                          value={form.quantity_required || 2}
                          min={1}
                          max={10}
                          onChange={(e) =>
                            updateForm(
                              msg.messageId,
                              "quantity_required",
                              e.target.value
                            )
                          }
                        />
                      </label>
                    </div>
                    <div className="confirm-actions">
                      <button
                        className="btn btn-primary"
                        onClick={() => handleConfirm(msg)}
                        disabled={confirming}
                      >
                        {confirming ? "Adding…" : "Add Patient to System"}
                      </button>
                      <button
                        className="btn btn-secondary"
                        onClick={() => setExpandedId(null)}
                      >
                        Cancel
                      </button>
                    </div>
                    {result && (
                      <p
                        className={
                          result.startsWith("Failed") ? "error" : "success-text"
                        }
                      >
                        {result}
                      </p>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <button className="btn btn-secondary refresh-btn" onClick={load} disabled={loading}>
        {loading ? "Loading…" : "Refresh"}
      </button>
    </div>
  );
}
