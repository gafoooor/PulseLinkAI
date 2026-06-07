// Unit tests for the coordinator-dashboard risk scoring module (Task 14.5).
//
// These tests validate the pure `risk.js` module against the design's section
// 4.6 weighted formula (`patient_risk`):
//
//   days_to_window_start = (window.start - today).days
//   proximity            = max(0, 1 - days_to_window_start / 14)
//   coverage             = confirmed_units(slot) / max(1, units_needed)
//   forecast_uncertainty = 1 - window.confidence
//   risk = round(100 * (0.5*proximity + 0.4*(1 - coverage) + 0.1*uncertainty), 1)
//
// Coverage:
//   - Requirement 8.1: risk rises as the window start approaches, as confirmed
//     coverage decreases, and as forecast confidence decreases.
//   - Requirement 8.2: patients sort by Risk_Score descending.
//   - Requirement 8.3: ranking operates over a single-city patient set (the
//     city filter itself lives in the dashboard; the module ranks the set it
//     is given). We assert descending order within that set.
//
// Run with Vitest in a Node environment: `npm install && npm test`.

import { describe, it, expect } from "vitest";
import {
  riskScore,
  proximity,
  coverage,
  forecastUncertainty,
  confirmedUnits,
  daysBetween,
  rankByRisk,
  RISK_WEIGHTS,
  PROXIMITY_HORIZON_DAYS,
} from "./risk.js";

// A fixed reference "today" so date math is deterministic.
const TODAY = "2025-01-01";

/**
 * Build a slot fixture. `confirmed` is the number of confirmed assignments
 * (each worth 1 unit unless `units` overrides per assignment).
 */
function makeSlot({ start, end = "2025-12-31", confidence, unitsNeeded, confirmed = 0 } = {}) {
  const assignments = [];
  for (let i = 0; i < confirmed; i += 1) {
    assignments.push({ status: "confirmed", units: 1 });
  }
  const window = {};
  if (start !== undefined) window.start = start;
  if (end !== undefined) window.end = end;
  if (confidence !== undefined) window.confidence = confidence;
  return { window, unitsNeeded, assignments };
}

describe("constants match design 4.6", () => {
  it("uses weights 0.5 / 0.4 / 0.1 that sum to 1", () => {
    expect(RISK_WEIGHTS.proximity).toBe(0.5);
    expect(RISK_WEIGHTS.coverage).toBe(0.4);
    expect(RISK_WEIGHTS.uncertainty).toBe(0.1);
    expect(
      RISK_WEIGHTS.proximity + RISK_WEIGHTS.coverage + RISK_WEIGHTS.uncertainty
    ).toBeCloseTo(1, 10);
  });

  it("divides proximity by a 14-day horizon", () => {
    expect(PROXIMITY_HORIZON_DAYS).toBe(14);
  });
});

describe("daysBetween", () => {
  it("returns the whole-day difference (to - from)", () => {
    expect(daysBetween("2025-01-01", "2025-01-08")).toBe(7);
    expect(daysBetween("2025-01-08", "2025-01-01")).toBe(-7);
    expect(daysBetween("2025-01-01", "2025-01-01")).toBe(0);
  });

  it("accepts Date objects as well as ISO strings", () => {
    expect(daysBetween(new Date("2025-01-01T00:00:00Z"), "2025-01-03")).toBe(2);
  });

  it("returns null when either date is missing or invalid", () => {
    expect(daysBetween(undefined, "2025-01-08")).toBeNull();
    expect(daysBetween("2025-01-01", undefined)).toBeNull();
    expect(daysBetween("not-a-date", "2025-01-08")).toBeNull();
    expect(daysBetween("", "2025-01-08")).toBeNull();
  });
});

describe("confirmedUnits", () => {
  it("sums confirmed assignments, defaulting to 1 unit each", () => {
    const slot = {
      assignments: [
        { status: "confirmed" }, // missing units => 1
        { status: "confirmed", units: 2 },
        { status: "active", units: 5 }, // not confirmed => ignored
        { status: "declined" }, // ignored
      ],
    };
    expect(confirmedUnits(slot)).toBe(3);
  });

  it("treats non-positive or non-finite units as a single unit", () => {
    const slot = {
      assignments: [
        { status: "confirmed", units: 0 },
        { status: "confirmed", units: -4 },
        { status: "confirmed", units: "x" },
      ],
    };
    expect(confirmedUnits(slot)).toBe(3);
  });

  it("returns 0 when there are no assignments or no slot", () => {
    expect(confirmedUnits({ assignments: [] })).toBe(0);
    expect(confirmedUnits({})).toBe(0);
    expect(confirmedUnits(null)).toBe(0);
  });
});

describe("coverage", () => {
  it("computes confirmed_units / max(1, unitsNeeded)", () => {
    expect(coverage(makeSlot({ unitsNeeded: 4, confirmed: 1 }))).toBeCloseTo(0.25, 10);
    expect(coverage(makeSlot({ unitsNeeded: 4, confirmed: 2 }))).toBeCloseTo(0.5, 10);
  });

  it("clamps to 1 when confirmed exceeds the requirement", () => {
    expect(coverage(makeSlot({ unitsNeeded: 2, confirmed: 5 }))).toBe(1);
  });

  it("uses a denominator floor of 1 when unitsNeeded is zero or missing", () => {
    // zero needed => denom 1; one confirmed => coverage 1
    expect(coverage(makeSlot({ unitsNeeded: 0, confirmed: 1 }))).toBe(1);
    // missing needed => denom 1; zero confirmed => coverage 0
    expect(coverage(makeSlot({ confirmed: 0 }))).toBe(0);
  });
});

describe("forecastUncertainty", () => {
  it("returns 1 - confidence", () => {
    expect(forecastUncertainty(makeSlot({ confidence: 0.8 }))).toBeCloseTo(0.2, 10);
    expect(forecastUncertainty(makeSlot({ confidence: 0.3 }))).toBeCloseTo(0.7, 10);
  });

  it("treats a missing confidence as fully uncertain (1)", () => {
    expect(forecastUncertainty(makeSlot({}))).toBe(1);
  });

  it("clamps confidence into [0, 1] before inverting", () => {
    expect(forecastUncertainty(makeSlot({ confidence: 1.5 }))).toBe(0);
    expect(forecastUncertainty(makeSlot({ confidence: -0.2 }))).toBe(1);
  });
});

describe("proximity", () => {
  it("rises toward 1 as the window start approaches", () => {
    const far = proximity(makeSlot({ start: "2025-01-13" }), TODAY); // 12 days out
    const near = proximity(makeSlot({ start: "2025-01-04" }), TODAY); // 3 days out
    expect(near).toBeGreaterThan(far);
    // 1 - 7/14 = 0.5 exactly at the horizon midpoint
    expect(proximity(makeSlot({ start: "2025-01-08" }), TODAY)).toBeCloseTo(0.5, 10);
  });

  it("clamps to 1 for a past-due window (start before today)", () => {
    expect(proximity(makeSlot({ start: "2024-12-20" }), TODAY)).toBe(1);
  });

  it("floors at 0 for far-future windows beyond the horizon", () => {
    // 100 days out: 1 - 100/14 < 0 => clamped to 0
    expect(proximity(makeSlot({ start: "2025-04-11" }), TODAY)).toBe(0);
  });

  it("treats a missing start date as maximally urgent (1)", () => {
    expect(proximity(makeSlot({}), TODAY)).toBe(1);
  });
});

describe("riskScore matches the design 4.6 weighted formula", () => {
  it("computes a known exact value", () => {
    // start 7 days out => proximity 0.5
    // confirmed 1 of 4 => coverage 0.25 => (1 - coverage) 0.75
    // confidence 0.8 => uncertainty 0.2
    // 100 * (0.5*0.5 + 0.4*0.75 + 0.1*0.2) = 100 * (0.25 + 0.30 + 0.02) = 57.0
    const slot = makeSlot({ start: "2025-01-08", confidence: 0.8, unitsNeeded: 4, confirmed: 1 });
    expect(riskScore({}, slot, TODAY)).toBe(57.0);
  });

  it("rounds to one decimal place", () => {
    const slot = makeSlot({ start: "2025-01-09", confidence: 0.55, unitsNeeded: 3, confirmed: 1 });
    const score = riskScore({}, slot, TODAY);
    // one decimal place => value * 10 is an integer
    expect(Number.isInteger(Math.round(score * 10))).toBe(true);
    expect(score).toBe(Math.round(score * 10) / 10);
  });

  it("stays within [0, 100]", () => {
    // most urgent: past-due, uncovered, no confidence
    const worst = makeSlot({ start: "2024-12-01", confidence: 0, unitsNeeded: 5, confirmed: 0 });
    expect(riskScore({}, worst, TODAY)).toBe(100.0);
    // least urgent: far future, fully covered, full confidence
    const best = makeSlot({ start: "2025-06-01", confidence: 1, unitsNeeded: 2, confirmed: 2 });
    expect(riskScore({}, best, TODAY)).toBe(0.0);
  });
});

describe("Requirement 8.1: risk rises with proximity, low coverage, low confidence", () => {
  it("increases as the window start approaches (coverage and confidence fixed)", () => {
    const base = { confidence: 0.5, unitsNeeded: 4, confirmed: 1 };
    const farther = riskScore({}, makeSlot({ ...base, start: "2025-01-13" }), TODAY);
    const middle = riskScore({}, makeSlot({ ...base, start: "2025-01-08" }), TODAY);
    const nearer = riskScore({}, makeSlot({ ...base, start: "2025-01-04" }), TODAY);
    expect(nearer).toBeGreaterThan(middle);
    expect(middle).toBeGreaterThan(farther);
  });

  it("increases as confirmed coverage decreases (proximity and confidence fixed)", () => {
    const base = { start: "2025-01-08", confidence: 0.5, unitsNeeded: 4 };
    const fullyCovered = riskScore({}, makeSlot({ ...base, confirmed: 4 }), TODAY); // 30.0
    const partlyCovered = riskScore({}, makeSlot({ ...base, confirmed: 1 }), TODAY); // 60.0
    const uncovered = riskScore({}, makeSlot({ ...base, confirmed: 0 }), TODAY); // 70.0
    expect(fullyCovered).toBe(30.0);
    expect(partlyCovered).toBe(60.0);
    expect(uncovered).toBe(70.0);
    expect(uncovered).toBeGreaterThan(partlyCovered);
    expect(partlyCovered).toBeGreaterThan(fullyCovered);
  });

  it("increases as forecast confidence decreases (proximity and coverage fixed)", () => {
    const base = { start: "2025-01-08", unitsNeeded: 4, confirmed: 1 };
    const highConfidence = riskScore({}, makeSlot({ ...base, confidence: 1 }), TODAY); // 55.0
    const midConfidence = riskScore({}, makeSlot({ ...base, confidence: 0.5 }), TODAY); // 60.0
    const lowConfidence = riskScore({}, makeSlot({ ...base, confidence: 0 }), TODAY); // 65.0
    expect(highConfidence).toBe(55.0);
    expect(midConfidence).toBe(60.0);
    expect(lowConfidence).toBe(65.0);
    expect(lowConfidence).toBeGreaterThan(midConfidence);
    expect(midConfidence).toBeGreaterThan(highConfidence);
  });
});

describe("Requirement 8.2 / 8.3: rankByRisk sorts patients descending within a city", () => {
  // A single coordinator city's patient set; each entry is { patient, slot }.
  const entries = [
    {
      patient: { patientId: "p-low", cityId: "hyd" },
      // far future, covered, confident => low risk
      slot: makeSlot({ start: "2025-06-01", confidence: 1, unitsNeeded: 2, confirmed: 2 }),
    },
    {
      patient: { patientId: "p-high", cityId: "hyd" },
      // past-due, uncovered, no confidence => high risk
      slot: makeSlot({ start: "2024-12-01", confidence: 0, unitsNeeded: 5, confirmed: 0 }),
    },
    {
      patient: { patientId: "p-mid", cityId: "hyd" },
      // mid window, partial coverage, mid confidence
      slot: makeSlot({ start: "2025-01-08", confidence: 0.5, unitsNeeded: 4, confirmed: 1 }),
    },
  ];

  it("orders entries by Risk_Score descending", () => {
    const ranked = rankByRisk(entries, TODAY);
    expect(ranked.map((e) => e.patient.patientId)).toEqual(["p-high", "p-mid", "p-low"]);
    // scores must be non-increasing
    for (let i = 1; i < ranked.length; i += 1) {
      expect(ranked[i - 1].riskScore).toBeGreaterThanOrEqual(ranked[i].riskScore);
    }
  });

  it("annotates each entry with its computed riskScore", () => {
    const ranked = rankByRisk(entries, TODAY);
    for (const entry of ranked) {
      expect(typeof entry.riskScore).toBe("number");
      expect(entry.riskScore).toBe(riskScore(entry.patient, entry.slot, TODAY));
    }
  });

  it("does not mutate the input array or its order", () => {
    const snapshot = entries.map((e) => e.patient.patientId);
    const ranked = rankByRisk(entries, TODAY);
    expect(entries.map((e) => e.patient.patientId)).toEqual(snapshot);
    expect(ranked).not.toBe(entries);
  });

  it("returns an empty array for empty or missing input", () => {
    expect(rankByRisk([], TODAY)).toEqual([]);
    expect(rankByRisk(undefined, TODAY)).toEqual([]);
  });
});
