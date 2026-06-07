// Single-slot, localized, install-free donor Accept/Decline screen (Task 14.3).
//
// Opened from an SMS/WhatsApp deep link, this plain web page shows ONE slot
// (transfusion window date range + units needed + a short empathetic line with
// no medical claims) and two large Accept / Decline buttons in the donor's
// language. On a choice it POSTs to the backend (stubbed via api.js until Task
// 15.1) and shows a confirmation state.
//
// Requirements:
// * 6.1 — present a single slot with Accept and Decline actions, no app install.
// * 9.1 — render labels/strings in the donor's language (from ?lang), en fallback.
// * 9.3 — include the slot details and both Accept and Decline actions.

import React, { useMemo, useState } from "react";
import { resolveLang, getStrings, format } from "./i18n.js";
import { submitDonorResponse } from "./api.js";

// UI phases for the single-slot flow.
const PHASE = {
  OFFER: "offer",
  SUBMITTING: "submitting",
  DONE: "done",
  ERROR: "error",
};

// Format an ISO date (YYYY-MM-DD) for display in the donor's locale, falling
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
 * @param {Object} props.slot - { slotId, token, windowStart, windowEnd, unitsNeeded }
 * @param {string} props.lang - raw language tag from the link (e.g. "te")
 */
export default function DonorSlotScreen({ slot, lang }) {
  const resolvedLang = resolveLang(lang);
  const t = useMemo(() => getStrings(resolvedLang), [resolvedLang]);

  const [phase, setPhase] = useState(PHASE.OFFER);
  // The submitted choice: "ACCEPTED" | "DECLINED" | null.
  const [choice, setChoice] = useState(null);

  // Guard: a malformed link (missing slot id or token) cannot be acted on.
  const linkValid = Boolean(slot && slot.slotId && slot.token);

  async function respond(response) {
    setChoice(response);
    setPhase(PHASE.SUBMITTING);
    try {
      const result = await submitDonorResponse({
        slotId: slot.slotId,
        token: slot.token,
        response,
      });
      setPhase(result.ok ? PHASE.DONE : PHASE.ERROR);
    } catch {
      setPhase(PHASE.ERROR);
    }
  }

  if (!linkValid) {
    return (
      <main className="screen" lang={resolvedLang}>
        <section className="card" role="alert">
          <h1 className="status-title">{t.missingTitle}</h1>
          <p className="status-body">{t.missingBody}</p>
        </section>
      </main>
    );
  }

  const windowText = format(t.windowRange, {
    start: formatDate(slot.windowStart, resolvedLang),
    end: formatDate(slot.windowEnd, resolvedLang),
  });

  return (
    <main className="screen" lang={resolvedLang}>
      <section className="card" aria-labelledby="offer-heading">
        <p className="brand">{t.appName}</p>

        {phase === PHASE.DONE ? (
          <Confirmation t={t} choice={choice} />
        ) : phase === PHASE.ERROR ? (
          <ErrorState t={t} onRetry={() => respond(choice)} />
        ) : (
          <>
            <h1 id="offer-heading" className="heading">
              {t.heading}
            </h1>

            <dl className="details">
              <div className="detail-row">
                <dt>{t.windowLabel}</dt>
                <dd>{windowText}</dd>
              </div>
              <div className="detail-row">
                <dt>{t.unitsLabel}</dt>
                <dd>{slot.unitsNeeded}</dd>
              </div>
            </dl>

            <p className="empathetic">{t.empathetic}</p>

            <div className="actions">
              <button
                type="button"
                className="btn btn-accept"
                onClick={() => respond("ACCEPTED")}
                disabled={phase === PHASE.SUBMITTING}
                aria-label={t.accept}
              >
                {phase === PHASE.SUBMITTING && choice === "ACCEPTED"
                  ? t.sending
                  : t.accept}
              </button>
              <button
                type="button"
                className="btn btn-decline"
                onClick={() => respond("DECLINED")}
                disabled={phase === PHASE.SUBMITTING}
                aria-label={t.decline}
              >
                {phase === PHASE.SUBMITTING && choice === "DECLINED"
                  ? t.sending
                  : t.decline}
              </button>
            </div>
          </>
        )}
      </section>
    </main>
  );
}

function Confirmation({ t, choice }) {
  const accepted = choice === "ACCEPTED";
  return (
    <div className="status" role="status" aria-live="polite">
      <h1 className="status-title">
        {accepted ? t.acceptedTitle : t.declinedTitle}
      </h1>
      <p className="status-body">
        {accepted ? t.acceptedBody : t.declinedBody}
      </p>
    </div>
  );
}

function ErrorState({ t, onRetry }) {
  return (
    <div className="status" role="alert">
      <h1 className="status-title">{t.errorTitle}</h1>
      <p className="status-body">{t.errorBody}</p>
      <div className="actions">
        <button type="button" className="btn btn-accept" onClick={onRetry}>
          {t.retry}
        </button>
      </div>
    </div>
  );
}
