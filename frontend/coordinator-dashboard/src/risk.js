// Patient risk scoring for the coordinator dashboard (Task 14.4).
//
// This is a PURE module (no React, no network) so task 14.5 can unit-test the
// math directly. It mirrors the backend formula documented in design section
// 4.6 (`patient_risk`):
//
//   days_to_window_start = (window.start - today).days
//   proximity            = max(0, 1 - days_to_window_start / 14)   # rises near window
//   coverage             = confirmed_units(slot) / max(1, units_needed)
//   forecast_uncertainty = 1 - window.confidence
//   risk = round(100 * (0.5*proximity + 0.4*(1 - coverage) + 0.1*forecast_uncertainty), 1)
//
// Risk therefore rises as (a) the window start approaches, (b) confirmed
// coverage decreases, and (c) forecast confidence decreases.
//
// Requirements: 8.1 (risk rises with proximity / low coverage / low confidence),
// 8.2 (patients sortable by risk descending).

// Weights from design 4.6. Kept as named constants so they are easy to audit.
export const RISK_WEIGHTS = Object.freeze({
  proximity: 0.5,
  coverage: 0.4,
  uncertainty: 0.1,
});

// The proximity horizon in days (design 4.6 divides by 14).
export const PROXIMITY_HORIZON_DAYS = 14;

// Normalize a date-ish value (ISO "YYYY-MM-DD" string or Date) to a UTC
// midnight Date. Returns null when it cannot be parsed.
function toDate(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === "string" && value.length > 0) {
    // Anchor bare ISO dates at UTC midnight so day math is stable across zones.
    const iso = value.length === 10 ? `${value}T00:00:00Z` : value;
    const parsed = new Date(iso);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  return null;
}

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/**
 * Whole-day difference (to - from), matching Python's `(a - b).days`.
 * @returns {number|null} integer days, or null if either date is invalid.
 */
export function daysBetween(fromValue, toValue) {
  const from = toDate(fromValue);
  const to = toDate(toValue);
  if (!from || !to) return null;
  return Math.round((to.getTime() - from.getTime()) / MS_PER_DAY);
}

// Read the window object off a slot, tolerating a missing slot.
function windowOf(slot) {
  return (slot && slot.window) || {};
}

/**
 * Sum the confirmed donor units for a slot. Each assignment with status
 * "confirmed" contributes its `units` (default 1 unit per confirmed donor).
 * @returns {number} confirmed units (>= 0)
 */
export function confirmedUnits(slot) {
  const assignments = (slot && slot.assignments) || [];
  let total = 0;
  for (const a of assignments) {
    if (a && a.status === "confirmed") {
      const u = Number(a.units);
      total += Number.isFinite(u) && u > 0 ? u : 1;
    }
  }
  return total;
}

/**
 * Proximity term: 0..1, rising to 1 as the window start approaches (and clamped
 * to 1 once the window has started/passed). Far-future windows trend toward 0.
 */
export function proximity(slot, today) {
  const win = windowOf(slot);
  const days = daysBetween(today, win.start);
  // Unknown start date => treat as maximally urgent so it is not hidden.
  if (days == null) return 1;
  const raw = 1 - days / PROXIMITY_HORIZON_DAYS;
  // Clamp to [0, 1]: past-due windows cap at 1, far-future floors at 0.
  return Math.max(0, Math.min(1, raw));
}

/**
 * Coverage term: confirmed_units / max(1, units_needed), clamped to [0, 1].
 * Higher coverage => lower risk.
 */
export function coverage(slot) {
  const needed = Number(slot && slot.unitsNeeded);
  const denom = Math.max(1, Number.isFinite(needed) ? needed : 1);
  const ratio = confirmedUnits(slot) / denom;
  return Math.max(0, Math.min(1, ratio));
}

/**
 * Forecast uncertainty term: 1 - confidence, clamped to [0, 1]. A missing
 * confidence is treated as fully uncertain (1).
 */
export function forecastUncertainty(slot) {
  const win = windowOf(slot);
  const confidence = Number(win.confidence);
  const c = Number.isFinite(confidence) ? Math.max(0, Math.min(1, confidence)) : 0;
  return 1 - c;
}

/**
 * Compute a patient's Risk_Score for a slot on a given day, mirroring design
 * section 4.6. Returns a number in [0, 100] rounded to one decimal place.
 *
 * @param {Object} patient - the patient (carried through for parity with the
 *   backend signature; the score is driven by the slot + today).
 * @param {Object} slot - { window: { start, end, confidence }, unitsNeeded, assignments }
 * @param {Date|string} today - the reference "today" (Date or ISO string)
 * @returns {number} Risk_Score in [0, 100]
 */
export function riskScore(patient, slot, today) {
  const p = proximity(slot, today);
  const cov = coverage(slot);
  const u = forecastUncertainty(slot);
  const score =
    100 *
    (RISK_WEIGHTS.proximity * p +
      RISK_WEIGHTS.coverage * (1 - cov) +
      RISK_WEIGHTS.uncertainty * u);
  // Round to one decimal place to match the backend's round(..., 1).
  return Math.round(score * 10) / 10;
}

/**
 * Rank patients by Risk_Score descending (Req 8.2). Pure: returns a new array
 * and does not mutate the input. Each input item is expected to be
 * `{ patient, slot }`; the returned items are augmented with `riskScore`.
 *
 * @param {Array<{patient: Object, slot: Object}>} entries
 * @param {Date|string} today
 * @returns {Array<{patient: Object, slot: Object, riskScore: number}>}
 */
export function rankByRisk(entries, today) {
  return (entries || [])
    .map((entry) => ({
      ...entry,
      riskScore: riskScore(entry.patient, entry.slot, today),
    }))
    .sort((a, b) => b.riskScore - a.riskScore);
}
