// Parse the single-slot offer details from the deep-link URL query (Task 14.3).
//
// The donor screen is opened from an SMS/WhatsApp link such as:
//   https://.../?lang=te&slot=SLOT123&token=abc&start=2025-01-10&end=2025-01-14&units=2
//
// `lang`, `slot`, and `token` come straight from the link. The window dates and
// units may also ride along on the link (the offer is rendered statically until
// the backend is wired in Task 15.1).

const DEFAULTS = {
  windowStart: "",
  windowEnd: "",
  unitsNeeded: 1,
};

/**
 * @param {string} [search] - location.search (defaults to the live URL)
 * @returns {{ lang: string|null, slot: {slotId: string|null, token: string|null,
 *   windowStart: string, windowEnd: string, unitsNeeded: number} }}
 */
export function parseSlotParams(search) {
  const query =
    typeof search === "string"
      ? search
      : typeof window !== "undefined"
      ? window.location.search
      : "";

  const params = new URLSearchParams(query);

  const unitsRaw = params.get("units");
  const unitsParsed = unitsRaw != null ? parseInt(unitsRaw, 10) : NaN;
  const unitsNeeded =
    Number.isFinite(unitsParsed) && unitsParsed >= 1
      ? unitsParsed
      : DEFAULTS.unitsNeeded;

  return {
    lang: params.get("lang"),
    slot: {
      slotId: params.get("slot"),
      token: params.get("token"),
      windowStart: params.get("start") || DEFAULTS.windowStart,
      windowEnd: params.get("end") || DEFAULTS.windowEnd,
      unitsNeeded,
    },
  };
}
