// Tests for the calm patient-facing status helpers (Task 14.2).
//
// Property 7: No false reassurance —
//   the patient-facing status is "arranged" IFF a `confirmed` assignment
//   exists for that slot. **Validates: Requirements 1.1**
//
// These tests combine a fast-check property test (the iff over arbitrary
// assignment-status arrays) with table-driven unit tests for the critical
// "coarse status says confirmed but no confirmed assignment" trap and for
// deriveHomeView's earliest-upcoming-slot selection.

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import {
  isSlotArranged,
  patientStatusForSlot,
  selectNextSlot,
  deriveHomeView,
  PATIENT_STATUS,
} from "./subscriptionStatus.js";

// The full AssignmentStatus union from the design's data model. Only
// "confirmed" must ever produce "arranged".
const ASSIGNMENT_STATUSES = [
  "active",
  "declined",
  "confirmed",
  "promoted",
  "expired",
];

// The full SlotStatus union. Crucially this includes "confirmed", which the
// patient-facing rule must IGNORE — arranged is assignment-driven only.
const SLOT_STATUSES = ["planned", "offered", "confirmed", "fulfilled", "missed"];

// Arbitrary for a single slot: a coarse status plus a random array of
// assignments each carrying one of the assignment statuses.
const slotArb = fc.record({
  status: fc.constantFrom(...SLOT_STATUSES),
  window: fc.record({
    start: fc.constant("2025-01-10"),
    end: fc.constant("2025-01-17"),
    expected: fc.constant("2025-01-14"),
  }),
  assignments: fc.array(
    fc.record({ status: fc.constantFrom(...ASSIGNMENT_STATUSES) }),
    { maxLength: 6 }
  ),
});

describe("Property 7: No false reassurance", () => {
  it('status is "arranged" iff at least one assignment is "confirmed"', () => {
    fc.assert(
      fc.property(slotArb, (slot) => {
        const hasConfirmed = slot.assignments.some(
          (a) => a.status === "confirmed"
        );
        const status = patientStatusForSlot(slot);

        // The iff: arranged exactly when a confirmed assignment exists.
        expect(status === PATIENT_STATUS.ARRANGED).toBe(hasConfirmed);
        // And it is always one of the two valid values, never undefined.
        expect([PATIENT_STATUS.ARRANGED, PATIENT_STATUS.ARRANGING]).toContain(
          status
        );
        // isSlotArranged agrees with the derived status.
        expect(isSlotArranged(slot)).toBe(hasConfirmed);
      })
    );
  });

  it('a slot whose coarse status is "confirmed" but with NO confirmed assignment is still "arranging"', () => {
    fc.assert(
      fc.property(
        // assignments drawn from everything EXCEPT "confirmed"
        fc.array(
          fc.record({
            status: fc.constantFrom(
              ...ASSIGNMENT_STATUSES.filter((s) => s !== "confirmed")
            ),
          }),
          { maxLength: 6 }
        ),
        (assignments) => {
          const slot = {
            status: "confirmed", // coarse status tries to falsely reassure
            window: {
              start: "2025-01-10",
              end: "2025-01-17",
              expected: "2025-01-14",
            },
            assignments,
          };
          // Never falsely reassured: assignment-driven rule wins.
          expect(patientStatusForSlot(slot)).toBe(PATIENT_STATUS.ARRANGING);
          expect(isSlotArranged(slot)).toBe(false);
        }
      )
    );
  });
});

describe("isSlotArranged / patientStatusForSlot — table-driven cases", () => {
  const cases = [
    {
      name: "no assignments -> arranging",
      slot: { status: "offered", assignments: [] },
      arranged: false,
    },
    {
      name: "only active/declined -> arranging",
      slot: {
        status: "offered",
        assignments: [{ status: "active" }, { status: "declined" }],
      },
      arranged: false,
    },
    {
      name: "single confirmed -> arranged",
      slot: { status: "offered", assignments: [{ status: "confirmed" }] },
      arranged: true,
    },
    {
      name: "confirmed among others -> arranged",
      slot: {
        status: "planned",
        assignments: [
          { status: "declined" },
          { status: "confirmed" },
          { status: "expired" },
        ],
      },
      arranged: true,
    },
    {
      name: "coarse status confirmed but assignments not confirmed -> arranging",
      slot: {
        status: "confirmed",
        assignments: [{ status: "active" }, { status: "promoted" }],
      },
      arranged: false,
    },
    {
      name: "missing assignments array -> arranging (defensive)",
      slot: { status: "planned" },
      arranged: false,
    },
    {
      name: "null/garbage assignment entries are ignored",
      slot: { status: "offered", assignments: [null, { status: "active" }] },
      arranged: false,
    },
  ];

  for (const { name, slot, arranged } of cases) {
    it(name, () => {
      expect(isSlotArranged(slot)).toBe(arranged);
      expect(patientStatusForSlot(slot)).toBe(
        arranged ? PATIENT_STATUS.ARRANGED : PATIENT_STATUS.ARRANGING
      );
    });
  }

  it("null/undefined slot is treated as arranging, never throws", () => {
    expect(isSlotArranged(null)).toBe(false);
    expect(isSlotArranged(undefined)).toBe(false);
    expect(patientStatusForSlot(null)).toBe(PATIENT_STATUS.ARRANGING);
  });
});

describe("selectNextSlot / deriveHomeView — earliest upcoming slot", () => {
  const mkSlot = (start, status, assignments = []) => ({
    status,
    window: { start, end: start, expected: start },
    assignments,
  });

  it("selectNextSlot picks the earliest-start non-completed slot", () => {
    const subscription = {
      slots: [
        mkSlot("2025-03-01", "offered"),
        mkSlot("2025-01-15", "planned"), // earliest upcoming
        mkSlot("2025-02-01", "offered"),
      ],
    };
    expect(selectNextSlot(subscription).window.start).toBe("2025-01-15");
  });

  it("selectNextSlot skips fulfilled/missed slots even if they are earlier", () => {
    const subscription = {
      slots: [
        mkSlot("2025-01-01", "fulfilled"),
        mkSlot("2025-01-05", "missed"),
        mkSlot("2025-02-10", "offered"), // earliest UPCOMING
      ],
    };
    expect(selectNextSlot(subscription).window.start).toBe("2025-02-10");
  });

  it("selectNextSlot returns null when there are no upcoming slots", () => {
    expect(selectNextSlot({ slots: [] })).toBeNull();
    expect(
      selectNextSlot({ slots: [mkSlot("2025-01-01", "fulfilled")] })
    ).toBeNull();
    expect(selectNextSlot(null)).toBeNull();
  });

  it("deriveHomeView reports arranged + window for the earliest slot with a confirmed assignment", () => {
    const subscription = {
      slots: [
        mkSlot("2025-02-01", "offered", [{ status: "active" }]),
        mkSlot("2025-01-10", "offered", [{ status: "confirmed" }]), // earliest
      ],
    };
    const view = deriveHomeView(subscription);
    expect(view.status).toBe(PATIENT_STATUS.ARRANGED);
    expect(view.window).toEqual({ start: "2025-01-10", end: "2025-01-10" });
  });

  it("deriveHomeView reports arranging when the earliest slot has no confirmed assignment", () => {
    const subscription = {
      slots: [
        // earliest slot has NO confirmed assignment -> arranging, even though
        // a later slot is confirmed. The patient sees the next slot honestly.
        mkSlot("2025-01-10", "confirmed", [{ status: "active" }]),
        mkSlot("2025-02-01", "offered", [{ status: "confirmed" }]),
      ],
    };
    const view = deriveHomeView(subscription);
    expect(view.status).toBe(PATIENT_STATUS.ARRANGING);
    expect(view.window).toEqual({ start: "2025-01-10", end: "2025-01-10" });
  });

  it("deriveHomeView returns null status/window for an empty subscription", () => {
    expect(deriveHomeView({ slots: [] })).toEqual({
      status: null,
      window: null,
    });
    expect(deriveHomeView(null)).toEqual({ status: null, window: null });
  });
});
