// Install-free donor accept/decline screen (Task 14.3).
//
// App reads the donor's language and the single slot's details from the deep
// link query (?lang=te&slot=...&token=...&start=...&end=...&units=...), since
// the page is opened from an SMS/WhatsApp link, then renders the localized
// single-slot Accept/Decline screen.
//
// Requirements: 6.1, 9.1, 9.3
import React, { useMemo } from "react";
import DonorSlotScreen from "./DonorSlotScreen.jsx";
import { parseSlotParams } from "./slotParams.js";
import "./styles.css";

export default function App() {
  const { lang, slot } = useMemo(() => {
    const params = parseSlotParams();
    // Fall back to demo defaults in dev when no link params are present.
    if (import.meta.env.DEV && !params.slot.slotId) {
      return {
        lang: params.lang || "en",
        slot: {
          slotId: "SLOT-1001-A",
          token: "demo",
          windowStart: "2025-02-03",
          windowEnd: "2025-02-07",
          unitsNeeded: 2,
        },
      };
    }
    return params;
  }, []);
  return <DonorSlotScreen slot={slot} lang={lang} />;
}
