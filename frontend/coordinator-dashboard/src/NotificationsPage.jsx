// NotificationsPage — WhatsApp/SMS alert log sent by PulseLink.
//
// Shows every alert that was sent: upcoming reminders, acceptance confirmations,
// patient notifications, and decline confirmations. Includes a "Send Reminders"
// button that triggers /admin/send-reminders for any slot within N days.

import React, { useCallback, useEffect, useState } from "react";
import { getNotificationsLog, sendReminders } from "./api.js";

const TYPE_LABELS = {
  upcoming_reminder: "Reminder",
  donor_accepted: "Accepted",
  patient_arranged: "Arranged",
  donor_declined: "Declined",
};

const TYPE_CLASS = {
  upcoming_reminder: "badge-reminder",
  donor_accepted: "badge-accepted",
  patient_arranged: "badge-arranged",
  donor_declined: "badge-declined",
};

export default function NotificationsPage() {
  const [alerts, setAlerts] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [daysAhead, setDaysAhead] = useState(365);
  const [sendResult, setSendResult] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getNotificationsLog();
      setAlerts(data.alerts || []);
      setTotal(data.total || 0);
    } catch {
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function handleSendReminders() {
    setSending(true);
    setSendResult(null);
    try {
      const result = await sendReminders(daysAhead);
      setSendResult(`Sent ${result.sent} reminder(s).`);
      load();
    } catch (e) {
      setSendResult("Failed: " + e.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="page-content">
      <h2 className="page-title">WhatsApp / SMS Alert Log</h2>

      <div className="notif-controls">
        <span className="notif-total">{total} total alerts</span>
        <div className="reminder-row">
          <label className="reminder-label">
            Send reminders for slots within
            <input
              type="number"
              className="days-input"
              value={daysAhead}
              min={1}
              max={730}
              onChange={(e) => setDaysAhead(Number(e.target.value))}
            />
            days
          </label>
          <button
            className="btn btn-primary"
            onClick={handleSendReminders}
            disabled={sending}
          >
            {sending ? "Sending…" : "Send Reminders"}
          </button>
        </div>
        {sendResult && <p className="send-result">{sendResult}</p>}
        <button className="btn btn-secondary" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : alerts.length === 0 ? (
        <div className="empty-state">
          <p>No alerts sent yet.</p>
          <p className="muted">
            Alerts are sent when donors accept/decline, or when you trigger
            reminders above.
          </p>
        </div>
      ) : (
        <div className="notif-list">
          {alerts.map((a) => (
            <div key={a.alertId} className="notif-item">
              <div className="notif-meta">
                <span className={`notif-badge ${TYPE_CLASS[a.alertType] || "badge-reminder"}`}>
                  {TYPE_LABELS[a.alertType] || a.alertType}
                </span>
                <span className="notif-recipient">
                  {a.recipientType === "donor" ? "Donor" : "Patient"}&nbsp;
                  <span title={a.recipientId}>{a.recipientId?.slice(0, 10)}…</span>
                </span>
                <span className="notif-phone">{a.mockPhone}</span>
                <span className="notif-time">{new Date(a.sentAt).toLocaleTimeString()}</span>
              </div>
              <p className="notif-message">{a.message}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
