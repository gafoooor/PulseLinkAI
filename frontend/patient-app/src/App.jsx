// Calm patient/parent home screen (Task 14.1).
//
// App reads the patient's identity and language from their personal link
// (?patient=...&token=...&lang=...) — the screen renders ONLY that patient's
// own subscription (Requirement 1.4) — then renders the calm, no-buttons home
// screen that reassures whether the next transfusion is arranged or arranging
// (Requirements 1.1, 1.2) and shows the next Window as a date range
// (Requirement 1.3).
import React, { useMemo } from "react";
import PatientHomeScreen from "./PatientHomeScreen.jsx";
import { parsePatientParams } from "./patientParams.js";
import "./styles.css";

export default function App() {
  const { patientId, token, lang } = useMemo(() => {
    const params = parsePatientParams();
    // Fall back to demo defaults in dev when no link params are present.
    if (import.meta.env.DEV && !params.patientId) {
      return { patientId: "PAT-1001", token: "demo", lang: params.lang || "en" };
    }
    return params;
  }, []);
  return <PatientHomeScreen patientId={patientId} token={token} lang={lang} />;
}
