// Calm patient/parent home screen (Task 14.1).
//
// This screen is intentionally CALM and anxiety-reducing. It has NO request
// buttons and asks nothing of the family — it simply reassures them about the
// next transfusion (design: Patient/Parent App).
//
// It loads ONLY the requesting patient's own subscription (by id + token) and
// shows:
//  * a large, soft status: "Arranged" when the next slot has a `confirmed`
//    assignment, otherwise "Arranging" (Requirements 1.1, 1.2);
//  * the next transfusion Window as an inclusive date range (Requirement 1.3).
//
// The view is restricted to the patient's own subscription (Requirement 1.4):
// identity comes from props or the personal link, and the api stub returns only
// that patient's data.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { resolveLang, getStrings, format } from "./i18n.js";
import { fetchMySubscription } from "./api.js";
import { deriveHomeView, PATIENT_STATUS } from "./subscriptionStatus.js";

// Loading lifecycle phases.
const PHASE = {
  LOADING: "loading",
  READY: "ready",
  ERROR: "error",
};

// Format an ISO date (YYYY-MM-DD) for display in the patient's locale, falling
// back to the raw value if it cannot be parsed.
function formatDate(iso, lang) {
  if (!iso) return "";
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat(lang, {
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(parsed);
  } catch {
    return iso;
  }
}

/**
 * @param {Object} props
 * @param {string} props.patientId - the patient's own id/bridge id
 * @param {string} props.token     - opaque link credential authorizing access
 * @param {string} props.lang      - raw language tag (e.g. "te"), en fallback
 */
export default function PatientHomeScreen({ patientId, token, lang }) {
  const resolvedLang = resolveLang(lang);
  const t = useMemo(() => getStrings(resolvedLang), [resolvedLang]);

  const [phase, setPhase] = useState(PHASE.LOADING);
  const [subscription, setSubscription] = useState(null);

  // A page without an identifying patient id cannot be scoped to one patient.
  const linkValid = Boolean(patientId);

  const load = useCallback(async () => {
    setPhase(PHASE.LOADING);
    try {
      const result = await fetchMySubscription({ patientId, token });
      if (result.ok) {
        setSubscription(result.subscription);
        setPhase(PHASE.READY);
      } else {
        setPhase(PHASE.ERROR);
      }
    } catch {
      setPhase(PHASE.ERROR);
    }
  }, [patientId, token]);

  // Initial load + auto-refresh every 10s so the patient sees "Arranged"
  // as soon as a donor accepts via IVR — no manual refresh needed.
  useEffect(() => {
    if (!linkValid) return;
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [linkValid, load]);

  if (!linkValid) {
    return (
      <main className="screen" lang={resolvedLang}>
        <section className="card" role="alert">
          <p className="brand">{t.appName}</p>
          <h1 className="status-title">{t.missingTitle}</h1>
          <p className="status-body">{t.missingBody}</p>
        </section>
      </main>
    );
  }

  if (phase === PHASE.LOADING) {
    return (
      <main className="screen" lang={resolvedLang}>
        <section className="card" aria-busy="true">
          <p className="brand">{t.appName}</p>
          <p className="status-body" role="status" aria-live="polite">
            {t.loadingTitle}
          </p>
        </section>
      </main>
    );
  }

  if (phase === PHASE.ERROR) {
    return (
      <main className="screen" lang={resolvedLang}>
        <section className="card" role="alert">
          <p className="brand">{t.appName}</p>
          <h1 className="status-title">{t.errorTitle}</h1>
          <p className="status-body">{t.errorBody}</p>
          <div className="actions">
            <button type="button" className="btn" onClick={load}>
              {t.retry}
            </button>
          </div>
        </section>
      </main>
    );
  }

  // READY — derive the calm view-model from the patient's OWN subscription.
  const { status, window } = deriveHomeView(subscription);
  const arranged = status === PATIENT_STATUS.ARRANGED;
  const statusText = arranged ? t.statusArranged : t.statusArranging;
  const reassurance = arranged
    ? t.reassuranceArranged
    : t.reassuranceArranging;
  const windowText = window
    ? format(t.windowRange, {
        start: formatDate(window.start, resolvedLang),
        end: formatDate(window.end, resolvedLang),
      })
    : "";

  return (
    <main className="screen" lang={resolvedLang}>
      <section className="card" aria-labelledby="plan-heading">
        <p className="brand">{t.appName}</p>
        <h1 id="plan-heading" className="greeting">
          {t.greeting}
        </h1>

        <div
          className={`status-panel ${arranged ? "is-arranged" : "is-arranging"}`}
          role="status"
          aria-live="polite"
        >
          <p className="status-eyebrow">{t.statusLabel}</p>
          <p className="status-headline">
            <span className="status-dot" aria-hidden="true" />
            {statusText}
          </p>
          <p className="reassurance">{reassurance}</p>
        </div>

        <dl className="details">
          <div className="detail-row">
            <dt>{t.windowLabel}</dt>
            <dd>{windowText || t.noWindow}</dd>
          </div>
        </dl>

        <p className="footer-note">{t.footer}</p>
      </section>
    </main>
  );
}
