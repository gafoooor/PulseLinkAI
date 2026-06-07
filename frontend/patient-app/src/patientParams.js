// Parse the patient identity from the URL query (Task 14.1).
//
// The calm patient/parent home screen is opened from a personal link such as:
//   https://.../?patient=PAT123&token=abc&lang=te
//
// `patient` (the patient/bridge id) and `token` (the opaque link credential)
// together identify exactly ONE patient. The screen renders only that patient's
// own subscription, Window, and arranged status — it never shows another
// patient's data (Requirement 1.4). `lang` selects the localization.
//
// The identity can also be supplied as props (e.g. when this screen is embedded
// after authentication); the URL is just the default source for the standalone
// demo page wired in Task 15.1.

/**
 * @param {string} [search] - location.search (defaults to the live URL)
 * @returns {{ patientId: string|null, token: string|null, lang: string|null }}
 */
export function parsePatientParams(search) {
  const query =
    typeof search === "string"
      ? search
      : typeof window !== "undefined"
      ? window.location.search
      : "";

  const params = new URLSearchParams(query);

  return {
    patientId: params.get("patient"),
    token: params.get("token"),
    lang: params.get("lang"),
  };
}
