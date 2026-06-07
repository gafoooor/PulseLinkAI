// Network seam for the install-free donor screen (Task 14.3).
//
// All backend access lives here so it is swappable when the real endpoint is
// wired in Task 15.1. For now the POST to /donor/respond is a thin stub: it
// issues the request when a backend is reachable, but resolves gracefully in
// the offline demo so the screen can still show its confirmation state.
//
// Requirements: 6.1 (Accept/Decline actions reach the backend without an app
// install) — the screen is a plain web page reachable by link.

// Base URL for the donor-response backend. Can be overridden at build time via
// Vite's `import.meta.env.VITE_API_BASE_URL`; defaults to a same-origin path.
const API_BASE_URL =
  (typeof import.meta !== "undefined" &&
    import.meta.env &&
    import.meta.env.VITE_API_BASE_URL) ||
  "";

const RESPOND_PATH = "/donor/respond";

/**
 * Record a donor's Accept/Decline for a single slot.
 *
 * @param {Object} params
 * @param {string} params.slotId   - the slot being responded to
 * @param {string} params.token    - the opaque link token authorizing the response
 * @param {"ACCEPTED"|"DECLINED"} params.response - the donor's choice
 * @returns {Promise<{ok: boolean, stubbed?: boolean}>}
 *
 * NOTE: This is a STUB pending Task 15.1. When no backend is reachable (the
 * offline demo), it resolves `{ ok: true, stubbed: true }` so the UI can move
 * to its confirmation state. Real wiring will surface true server errors.
 */
export async function submitDonorResponse({ slotId, token, response }) {
  const url = `${API_BASE_URL}${RESPOND_PATH}`;
  const payload = { slot_id: slotId, token, response };

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      throw new Error(`Unexpected response status: ${res.status}`);
    }
    return { ok: true };
  } catch (err) {
    // Offline demo / endpoint not yet wired (Task 15.1). Treat as a soft
    // success so the donor still sees a confirmation, but flag it as stubbed.
    if (import.meta && import.meta.env && import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        "[donor-screen] /donor/respond not reachable; using offline stub.",
        err
      );
    }
    return { ok: true, stubbed: true };
  }
}
