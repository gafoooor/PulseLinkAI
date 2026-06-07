// Network seam for the calm patient/parent home screen (Task 14.1).
//
// All backend access lives here so it is swappable when the real endpoint is
// wired in Task 15.1. For now `fetchMySubscription` is a STUB: it issues a GET
// to /patient/subscription when a backend is reachable, but falls back to canned
// sample data offline so the screen renders without a backend.
//
// IMPORTANT (Requirement 1.4 — own subscription only): the request is always
// scoped by the patient's own id + opaque token. The stub only ever returns the
// subscription whose id matches the requested patient; it never returns another
// patient's data. In production the backend enforces this server-side via
// role-based access (design Requirement 7.5).

// Base URL for the patient backend. Can be overridden at build time via Vite's
// `import.meta.env.VITE_API_BASE_URL`; defaults to a same-origin path.
const API_BASE_URL =
  (typeof import.meta !== "undefined" &&
    import.meta.env &&
    import.meta.env.VITE_API_BASE_URL) ||
  "";

const SUBSCRIPTION_PATH = "/patient/subscription";

// Canned, offline sample subscriptions keyed by patient/bridge id. These mirror
// the backend Subscription/Slot/SlotAssignment shape (design Data Models):
// a slot carries a window { start, end, expected } and an ordered list of
// assignments, each with a status. A slot is patient-facing "arranged" when any
// assignment is `confirmed`, otherwise "arranging" (Requirements 1.1, 1.2).
//
// PAT-1001 has a confirmed backup → should render "Arranged".
// PAT-1002 has only an active (not yet confirmed) primary → "Arranging".
const SAMPLE_SUBSCRIPTIONS = {
  "PAT-1001": {
    subscriptionId: "SUB-1001",
    patientId: "PAT-1001",
    cadenceDays: 21,
    slots: [
      {
        slotId: "SLOT-1001-A",
        window: { start: "2025-02-03", end: "2025-02-07", expected: "2025-02-05" },
        unitsNeeded: 2,
        status: "confirmed",
        assignments: [
          { assignmentId: "ASG-1", donorId: "D-1", rank: 0, status: "declined" },
          { assignmentId: "ASG-2", donorId: "D-2", rank: 1, status: "confirmed" },
        ],
      },
      {
        slotId: "SLOT-1001-B",
        window: { start: "2025-02-24", end: "2025-02-28", expected: "2025-02-26" },
        unitsNeeded: 2,
        status: "planned",
        assignments: [
          { assignmentId: "ASG-3", donorId: "D-3", rank: 0, status: "active" },
        ],
      },
    ],
  },
  "PAT-1002": {
    subscriptionId: "SUB-1002",
    patientId: "PAT-1002",
    cadenceDays: 28,
    slots: [
      {
        slotId: "SLOT-1002-A",
        window: { start: "2025-02-10", end: "2025-02-16", expected: "2025-02-13" },
        unitsNeeded: 1,
        status: "offered",
        assignments: [
          { assignmentId: "ASG-9", donorId: "D-9", rank: 0, status: "active" },
        ],
      },
    ],
  },
};

/**
 * Fetch the requesting patient's OWN subscription.
 *
 * @param {Object} params
 * @param {string} params.patientId - the patient's own id/bridge id
 * @param {string} params.token     - the opaque link credential authorizing access
 * @returns {Promise<{ok: boolean, stubbed?: boolean, subscription: Object|null}>}
 *
 * NOTE: This is a STUB pending Task 15.1. When no backend is reachable (the
 * offline demo), it resolves the canned subscription for `patientId` only.
 */
export async function fetchMySubscription({ patientId, token }) {
  // Always carry the patient id + token so the backend scopes to this patient
  // alone (Requirement 1.4 / RBAC). The query is intentionally explicit.
  const query = new URLSearchParams({
    patient: patientId || "",
    token: token || "",
  }).toString();
  const url = `${API_BASE_URL}${SUBSCRIPTION_PATH}?${query}`;

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
    });

    if (!res.ok) {
      throw new Error(`Unexpected response status: ${res.status}`);
    }
    const subscription = await res.json();
    return { ok: true, subscription };
  } catch (err) {
    // Offline demo / endpoint not yet wired (Task 15.1). Fall back to canned
    // data, returning ONLY the requested patient's subscription so the screen
    // can never show another patient's plan.
    if (import.meta && import.meta.env && import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        "[patient-app] /patient/subscription not reachable; using offline stub.",
        err
      );
    }
    const subscription = patientId
      ? SAMPLE_SUBSCRIPTIONS[patientId] || null
      : null;
    return { ok: true, stubbed: true, subscription };
  }
}
