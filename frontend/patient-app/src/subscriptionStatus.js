// Pure helpers that derive the calm patient-facing status from a subscription
// (Task 14.1). Kept free of React so the "arranged iff confirmed" rule can be
// reasoned about and tested in isolation (Property 7, Task 14.2).
//
// Requirements:
// * 1.1 — a slot is "arranged" WHERE it has at least one assignment with status
//         `confirmed`.
// * 1.2 — otherwise the slot is "arranging".
// * 1.3 — the next transfusion Window is an inclusive date range [start, end].

export const PATIENT_STATUS = {
  ARRANGED: "arranged",
  ARRANGING: "arranging",
};

// The slot statuses that mean a transfusion is already in the past / done.
// These are skipped when picking the patient's NEXT upcoming slot.
const COMPLETED_SLOT_STATUSES = new Set(["fulfilled", "missed"]);

/**
 * True iff the slot has at least one Slot_Assignment with status `confirmed`.
 * This is the single source of truth for "arranged" (Requirement 1.1) — derived
 * from assignment data, never from a slot's own coarse status, so the patient is
 * never falsely reassured.
 *
 * @param {Object} slot - { assignments?: Array<{status: string}> }
 * @returns {boolean}
 */
export function isSlotArranged(slot) {
  if (!slot || !Array.isArray(slot.assignments)) {
    return false;
  }
  return slot.assignments.some(
    (assignment) => assignment && assignment.status === "confirmed"
  );
}

/**
 * Map a slot to its patient-facing status string.
 * "arranged" when a confirmed assignment exists, otherwise "arranging"
 * (Requirements 1.1, 1.2).
 *
 * @param {Object} slot
 * @returns {"arranged"|"arranging"}
 */
export function patientStatusForSlot(slot) {
  return isSlotArranged(slot)
    ? PATIENT_STATUS.ARRANGED
    : PATIENT_STATUS.ARRANGING;
}

/**
 * Pick the patient's NEXT upcoming slot from their own subscription: the
 * not-yet-completed slot with the earliest window start. Returns null when the
 * subscription has no upcoming slots.
 *
 * @param {Object} subscription - { slots?: Array<Object> }
 * @returns {Object|null}
 */
export function selectNextSlot(subscription) {
  if (!subscription || !Array.isArray(subscription.slots)) {
    return null;
  }

  const upcoming = subscription.slots.filter(
    (slot) =>
      slot &&
      slot.window &&
      !COMPLETED_SLOT_STATUSES.has(slot.status)
  );

  if (upcoming.length === 0) {
    return null;
  }

  return upcoming.reduce((earliest, slot) =>
    slot.window.start < earliest.window.start ? slot : earliest
  );
}

/**
 * Build the calm view-model for the home screen from a patient's own
 * subscription: the next slot, its patient-facing status, and its window range.
 *
 * @param {Object|null} subscription
 * @returns {{ status: ("arranged"|"arranging"|null), window: ({start: string, end: string}|null) }}
 */
export function deriveHomeView(subscription) {
  const nextSlot = selectNextSlot(subscription);
  if (!nextSlot) {
    return { status: null, window: null };
  }
  return {
    status: patientStatusForSlot(nextSlot),
    window: { start: nextSlot.window.start, end: nextSlot.window.end },
  };
}
