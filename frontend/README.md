# PulseLink — Frontend Apps

Three focused single-page apps built with React 18 + Vite 5. Each is independently deployed to its own S3 + CloudFront distribution. There is no shared component library — each app is self-contained in its own directory.

---

## Apps

### 1. Coordinator Dashboard (`coordinator-dashboard/`)

**Live URL**: https://dpijq2esptlq8.cloudfront.net  
**Local dev**: http://localhost:5175

The operational hub for the blood bank coordinator. Four tabs:

#### Patients tab
- Risk-ranked list of all active Thalassemia patients (risk = urgency × low backup coverage × low forecast confidence)
- Columns: patient ID, city, blood group, cadence, next window, coverage, risk score, actions
- **Start Call** — triggers the IVR donor-calling flow for that patient's next slot
- **View / Close** — expands an inline donor panel beneath the row showing all 8 assigned donors:
  - Role (Primary / Backup), Blood Group, Blood Match %, Reliability score (0–100 with visual bar), Calls:Donations ratio, Total donations, Eligibility, Assignment status (Active / Confirmed / Declined)

#### Call Flow tab
- Shows the live IVR session for the currently active call
- Polls `/calls/status/<session_id>` every 2 seconds
- Displays: donor queue, which donor is currently being called, language they selected, outcome (accepted / declined / no-answer / busy)
- **Simulate Accept / Simulate Decline** panel — tests the full flow without a real phone (useful when Twilio is not configured)

#### WhatsApp Log tab
- All alerts sent by the system: upcoming reminders, acceptance confirmations, patient notifications, decline acknowledgements
- Columns: type, recipient, phone, message text, timestamp
- **Send Reminders** button — triggers `/admin/send-reminders` to proactively contact all donors with windows within N days
- Alert count badge on the tab header

#### Inbox tab
- Incoming WhatsApp messages from potential new patients
- LLM auto-extracts: blood group, cadence (days), last transfusion date, units needed, phone number
- Each field shows a confidence indicator (green = high, yellow = medium, red = low)
- Coordinator reviews, adjusts low-confidence fields, then clicks **Add Patient to System**
- On confirm: patient added to system and immediately visible in the Patients tab

---

### 2. Patient App (`patient-app/`)

**Live URL**: https://d1c51x56ezsfgz.cloudfront.net  
**Local dev**: http://localhost:5173

A personal status page for the patient (or their family carer). Opened via a unique link sent to the patient.

URL format: `/?patient=<patient_id>&token=demo&lang=en`

Shows:
- **"Blood Arranged"** badge when primary donor has confirmed — displayed in the patient's language
- **"Arranging…"** badge when the system is still working through backup donors
- Next transfusion window (start date → end date, expected date)
- Blood group required and units needed
- Refresh button (re-fetches live status)

---

### 3. Donor Screen (`donor-screen/`)

**Live URL**: https://d1ism9anjs6w7j.cloudfront.net  
**Local dev**: http://localhost:5174

A lightweight, install-free response screen for donors. Opened from a WhatsApp or SMS link.

URL format: `/?lang=te&slot=<slot_id>&token=demo&start=<date>&end=<date>&units=<n>`

Shows:
- Slot request in the donor's language (English, Hindi, Telugu, Tamil)
- Transfusion window dates and units needed
- **Accept** and **Decline** buttons — each calls `POST /donor/respond` and shows a confirmation screen
- If the donor has already responded, shows the existing response instead of the buttons

---

## Local Development

Each app is independent. Run any or all in parallel:

```bash
# Install dependencies (first time only)
cd frontend/coordinator-dashboard && npm install
cd frontend/patient-app && npm install
cd frontend/donor-screen && npm install

# Start all three (separate terminals)
cd frontend/coordinator-dashboard && npm run dev   # http://localhost:5175
cd frontend/patient-app && npm run dev              # http://localhost:5173
cd frontend/donor-screen && npm run dev             # http://localhost:5174
```

The backend must be running at http://localhost:8000. Start it with:
```bash
python run_demo.py
```

---

## Environment Variables

Each app reads `VITE_API_BASE_URL` at build time (Vite embeds it into the bundle).

For local dev, the default in `vite.config.js` proxies `/api` to `http://localhost:8000` — no `.env` needed.

For production builds, set in `.env.production` (gitignored):
```
VITE_API_BASE_URL=https://d1u7u2ctmq6ryx.cloudfront.net
```

Do not commit `.env.production` — it contains the live CloudFront backend URL. A template is at `.env.example` (if present) or just set the variable in your deployment script.

---

## Build + Deploy

Each app builds to its own `dist/` directory:

```bash
cd frontend/coordinator-dashboard && npm run build
# Output: frontend/coordinator-dashboard/dist/
```

The `deploy/3_deploy_frontends.ps1` script builds all three and syncs each `dist/` to its S3 bucket, then invalidates the CloudFront cache (`/*`).

---

## Tech Notes

- **No router** — each app is a single view or uses URL params for state (`?patient=...`). No React Router.
- **No state management library** — all state is React hooks (`useState`, `useEffect`, `useMemo`).
- **Polling, not WebSocket** — the Call Flow tab polls every 2s using `setInterval` in a `useEffect`. Adequate for the demo; swap for SSE in production.
- **Inline table expansion** — donor panel uses `<tr colSpan={N}>` inside the same `<tbody>` as the patient row. No separate modal.
- **Vanilla CSS** — all styles in `src/styles.css`. No Tailwind, no CSS-in-JS.
