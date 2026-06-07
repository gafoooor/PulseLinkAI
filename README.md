# PulseLink — The Blood Subscription for Thalassemia Care

PulseLink turns lifelong, recurring blood transfusions for Thalassemia Major patients into a managed **subscription** — not a series of disconnected emergencies. The system gives blood logistics a memory and a forecast: it predicts each patient's next transfusion window, maintains a ranked plan of matched donors, automatically promotes a backup when a primary declines, and alerts everyone over WhatsApp/IVR — all without the coordinator having to chase individuals manually every cycle.

Built for the **AI for Good Hackathon**.

---

## Live AWS Deployment

| Surface | URL |
|---|---|
| Coordinator Dashboard | https://dpijq2esptlq8.cloudfront.net |
| Patient App | https://d1c51x56ezsfgz.cloudfront.net |
| Donor Screen | https://d1ism9anjs6w7j.cloudfront.net |
| Backend API / Swagger | https://d1u7u2ctmq6ryx.cloudfront.net/docs |

---

## Tech Stack

### Backend
| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| API framework | FastAPI + Uvicorn |
| Data validation | Pydantic v2 |
| LLM parsing | Rule-based MockLlmClient |
| Voice IVR | Twilio Programmable Voice (TwiML, DTMF) |
| WhatsApp | Twilio WhatsApp Business API |
| Forecasting | EWMA cadence estimation (custom, no external ML lib) |
| Reliability scoring | Weighted multi-factor score (acceptance ratio, call efficiency, volume, recency) |

### Frontend
| Layer | Technology |
|---|---|
| Framework | React 18 |
| Build tool | Vite 5 |
| Styling | Vanilla CSS (no UI framework) |
| State | React hooks (useState, useEffect, useMemo) |
| API calls | Fetch API |

### Infrastructure (AWS)
| Component | AWS Service |
|---|---|
| Container registry | Amazon ECR |
| Backend compute | ECS Fargate (single task, 512 CPU / 1 GB RAM) |
| Load balancer | Application Load Balancer |
| Backend HTTPS | CloudFront → ALB |
| Frontend hosting | S3 static website + CloudFront (3 distributions) |
| DNS / CDN | CloudFront (PriceClass_All) |
| IVR + WhatsApp | Twilio (external) |

---

## Architecture

### Local Development

```
┌─────────────────────────────────────────────────────────────┐
│                      Client Surfaces                         │
├────────────────┬──────────────────┬──────────────────────────┤
│  Patient App   │  Donor Screen    │  Coordinator Dashboard    │
│  React/Vite    │  React/Vite      │  React/Vite               │
│  :5173         │  :5174           │  :5175                    │
└───────┬────────┴────────┬─────────┴──────────────┬───────────┘
        │                 │                        │
        └─────────────────┴────────────────────────┘
                          │ HTTP
               ┌──────────▼──────────┐
               │  FastAPI Demo API   │
               │  port 8000          │
               │  (in-memory state)  │
               └──────────┬──────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
  ┌─────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
  │ Forecasting│  │  Matching   │  │ Reliability │
  │ Engine     │  │  Service    │  │ Scoring     │
  └────────────┘  └─────────────┘  └─────────────┘
        │                 │                 │
  ┌─────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
  │Subscription│  │  Messaging  │  │ LLM Parsing │
  │ Generator  │  │  + Voice    │  │ Service     │
  └────────────┘  └─────────────┘  └─────────────┘
```

### AWS Production

```
 Twilio ────────────────────────────────────────────────────────────┐
 (IVR + WhatsApp)                                                   │
                                                                    │
 Browser → CloudFront (coord) → S3 ─────────────────────────────┐  │
 Browser → CloudFront (patient) → S3 ───────────────────────┐   │  │
 Browser → CloudFront (donor) → S3 ─────────────────────┐   │   │  │
                                                         │   │   │  │
                           CloudFront (API HTTPS) ───────┴───┴───┴──┘
                                    │
                            ALB (HTTP:80)
                                    │
                          ECS Fargate Task
                          (FastAPI + in-memory state)
                                    │
                            Dataset.csv (baked into Docker image)
```

---

## Five Core Services

### 1. LLM Parsing Service (`backend/pulselink/parsing/`)
Converts messy WhatsApp-style text ("Ravi B- every 18 days last transfusion 24th Jan 2025 3 units") into structured JSON with per-field confidence scores. Uses a pluggable `LlmClient` interface:
- **Offline / demo**: `MockLlmClient` — regex-based, deterministic, handles ISO dates, natural language dates (`24th January 2025`), relative dates (`12 days ago`), and blood groups including negative types (`B-`, `O-`)
- **Production**: `BedrockClaudeClient` — Amazon Bedrock (Claude Haiku) with tool-use structured output

Fields extracted: blood group, cadence (days), last transfusion date, units required, donor phone.

### 2. Forecasting Engine (`backend/pulselink/forecasting/`)
Predicts each patient's next transfusion window `[start, expected, end]` using an EWMA (Exponentially Weighted Moving Average) cadence estimator. Re-learns cadence after each recorded transfusion. Returns confidence score based on number of historical samples.

### 3. Donor Reliability Scoring (`backend/pulselink/reliability/`)
Produces a 0–100 score per donor from four weighted components:
- **Acceptance ratio** — fraction of slot offers accepted historically
- **Call efficiency** — inverse of calls-to-donations ratio (high calls per donation = low efficiency)
- **Volume factor** — total donations (log-normalized)
- **Recency factor** — exponential decay since last donation

Donors are ranked: primary (rank 0) + up to 7 backups per slot, sorted by reliability descending.

### 4. Subscription Generator (`backend/pulselink/subscription/`)
Builds a rolling recurring plan of blood slots for each patient:
- Uses forecasted windows as slot anchors
- Matches donors by blood compatibility + reliability score
- On decline: demotes declined donor, promotes next backup, re-scores, sends offer to new primary
- Generates slots starting from today (not historical anchors) so `Send Reminders` always produces future-dated windows

### 5. Messaging Service (`backend/pulselink/messaging/`)
Four alert types, multilingual (English, Hindi, Telugu, Tamil):
- **`UPCOMING_REMINDER`** — sent to primary donor when their window is within N days
- **`DONOR_ACCEPTED`** — confirmation to donor after accepting via IVR
- **`PATIENT_ARRANGED`** — "blood is arranged" reassurance to patient/coordinator
- **`DONOR_DECLINED`** — polite acknowledgement to declining donor

All alerts are logged to `InMemoryAlertLog` and visible in the coordinator's WhatsApp Log tab.

---

## Three Frontend Apps

### Patient App (`frontend/patient-app/`)
Personal link opened by the patient or their family. Shows:
- Status badge: **"Arranged"** (donor confirmed) or **"Arranging"** (in progress)
- Next transfusion window with dates
- Number of units needed and blood group

URL format: `/?patient=<id>&token=demo&lang=en`

### Donor Screen (`frontend/donor-screen/`)
Install-free single-page app opened from a WhatsApp/SMS link. Shows the slot request in the donor's language. The donor taps **Accept** or **Decline** — the response updates the subscription and triggers the promote-and-re-score flow.

URL format: `/?lang=te&slot=<slot_id>&token=demo&start=<date>&end=<date>&units=<n>`

### Coordinator Dashboard (`frontend/coordinator-dashboard/`)
Full-featured coordinator interface with four tabs:

| Tab | What it does |
|---|---|
| **Patients** | Risk-ranked table (risk score = urgency × low coverage × low confidence). Each row has **Start Call** (triggers IVR flow) and **View** (expands inline donor panel) |
| **Call Flow** | Live IVR session status — polling every 2s. Shows donor queue, current donor being called, language selected, accept/decline outcome. Has a **Simulate** panel for testing without a real phone |
| **WhatsApp Log** | All sent alerts (reminders, acceptance confirmations, patient notifications, decline acknowledgements) with recipient, phone, message text, timestamp. **Send Reminders** button triggers proactive outreach |
| **Inbox** | Incoming WhatsApp messages from new patients. LLM auto-parses them — coordinator reviews parsed fields (blood group, cadence, date, units) with confidence indicators and clicks **Add Patient to System** |

**Donor panel** (expanded on View): shows all 8 assigned donors with blood group, blood match %, reliability score, calls:donations ratio, total donations, eligibility, and assignment status badge (Active / Confirmed / Declined).

---

## Key Flows

### IVR Donor Call Flow
1. Coordinator clicks **Start Call** for a patient
2. Twilio places outbound call to `TWILIO_TEST_DONOR_PHONE`
3. Donor hears language menu (1=English, 2=Hindi, 3=Telugu) — presses choice
4. Donor hears slot offer in their language — presses **1** to accept, **2** to decline
5. On accept: slot marked confirmed, WhatsApp sent to donor + patient, alert logged
6. On decline: next backup promoted, call placed to next donor automatically
7. If all donors decline: coordinator receives a WhatsApp alert to intervene

### WhatsApp Inbound → Add Patient
1. Patient/family texts the Twilio WhatsApp number
2. Webhook (`POST /webhooks/whatsapp`) receives message, parses with LLM
3. Parsed fields appear in coordinator **Inbox** tab with confidence scores
4. Coordinator reviews, adjusts any low-confidence fields, clicks **Add Patient to System**
5. Patient + subscription created, patient immediately appears in the risk list

### Decline → Promote → Re-Score
1. Donor declines → assignment marked `declined`
2. Reliability score recomputed (call efficiency decreases)
3. Highest-ranked eligible backup auto-promoted to primary
4. Promoted donor receives a WhatsApp reminder when their window approaches

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Service status + patient/donor/subscription counts |
| GET | `/patient/subscription?patient=<id>` | Patient's subscription and slot status |
| POST | `/donor/respond` | Accept/Decline a slot `{slot_id, response}` |
| GET | `/coordinator/patients?city=<city>` | Risk-ranked patient list |
| GET | `/coordinator/donors/<patient_id>` | 8 donors mapped to a patient with scores |
| POST | `/parse` | Parse messy text `{rawText, cityId}` |
| GET | `/parse-inbox` | Incoming WhatsApp messages queue |
| POST | `/patients/confirm` | Add confirmed patient from inbox parse |
| POST | `/calls/start` | Start IVR call flow `{patient_id, slot_id}` |
| GET | `/calls/status/<session_id>` | Live call session state (poll every 2s) |
| GET | `/calls/log` | All recent call sessions |
| POST | `/calls/simulate-response` | Simulate donor response without real phone |
| GET | `/notifications/log` | WhatsApp / SMS alert log |
| POST | `/admin/send-reminders?days_ahead=<n>` | Trigger proactive donor reminders |
| GET | `/demo/links` | Ready-to-use demo URLs for all patients |
| POST | `/webhooks/whatsapp` | Twilio inbound WhatsApp webhook |
| POST | `/webhooks/voice/outbound` | Twilio call initiation TwiML |
| POST | `/webhooks/voice/language` | Twilio language selection DTMF |
| POST | `/webhooks/voice/response` | Twilio accept/decline DTMF |
| POST | `/webhooks/voice/status` | Twilio call status callback (no-answer/busy) |

---

## Local Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+ and npm
- `Dataset.csv` in the project root (included)

### 1. Backend

```bash
cd backend
pip install -r requirements-demo.txt
cd ..
python run_demo.py
# API running at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

### 2. Frontend (three terminals)

```bash
# Terminal 1 — Patient App (port 5173)
cd frontend/patient-app && npm install && npm run dev

# Terminal 2 — Donor Screen (port 5174)
cd frontend/donor-screen && npm install && npm run dev

# Terminal 3 — Coordinator Dashboard (port 5175)
cd frontend/coordinator-dashboard && npm install && npm run dev
```

### 3. Get demo links

```
http://localhost:8000/demo/links
```

Returns prefilled patient app URLs and donor screen URLs. The Coordinator Dashboard at http://localhost:5175 works with no URL params.

### 4. Enable real Twilio (optional)

Copy `.env.example` to `.env` and fill in your credentials:

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
TWILIO_TEST_DONOR_PHONE=+91xxxxxxxxxx   # receives test IVR calls
TWILIO_COORDINATOR_PHONE=+91xxxxxxxxxx  # receives WhatsApp alerts
WEBHOOK_BASE_URL=https://<ngrok-id>.ngrok-free.app
```

Without these, the app runs in **simulated mode** — the coordinator can click "Simulate Accept/Decline" in the UI instead of answering a phone.

---

## AWS Deployment

Scripts in `deploy/` provision all infrastructure from scratch:

```
deploy/
├── config.ps1               # Credentials and IDs (gitignored — see config.example.ps1)
├── 1_push_image.ps1         # Build Docker image → push to ECR
├── 2_create_infra.ps1       # VPC, ECS cluster, ALB, ECS service, ECR repo
├── 3_deploy_frontends.ps1   # S3 buckets, CloudFront distributions, build + upload all 3 apps
└── 4_add_backend_https.ps1  # CloudFront in front of ALB (free HTTPS), rebuild frontends with HTTPS URL
```

Run in order from the project root with PowerShell:

```powershell
. .\deploy\config.ps1
.\deploy\1_push_image.ps1
.\deploy\2_create_infra.ps1
.\deploy\3_deploy_frontends.ps1
.\deploy\4_add_backend_https.ps1
```

Estimated cost: **~$35/month** (ECS Fargate + 4 CloudFront distributions + ALB).

---

## Environment Variables

Copy `.env.example` to `.env`. The demo runs fully offline with default values.

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` (offline regex) or `bedrock` (Claude Haiku) |
| `MESSAGE_CHANNEL` | `mock` | `mock`, `sms`, `whatsapp`, or `voice` |
| `VOICE_PROVIDER` | `mock` | `mock` or `connect` (Amazon Connect) |
| `EVENT_BUS` | `memory` | `memory` or `eventbridge` |
| `DATABASE_URL` | — | PostgreSQL URL (only needed for DB-backed mode) |
| `DATASET_CSV_PATH` | `./Dataset.csv` | Path to the input dataset |
| `DEFAULT_LANG` | `en` | Fallback language for donor messages |
| `PARSE_REVIEW_THRESHOLD` | `0.75` | Confidence below this is flagged for review |
| `CONTACT_ENCRYPTION_KEY` | — | 32-byte key for contact point encryption |
| `BEDROCK_MODEL_ID` | `anthropic.claude-3-haiku-20240307-v1:0` | Bedrock model (when `LLM_PROVIDER=bedrock`) |
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock and ECR |
| `TWILIO_ACCOUNT_SID` | — | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | — | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | — | Outbound call number |
| `TWILIO_WHATSAPP_FROM` | — | WhatsApp sender number |
| `TWILIO_TEST_DONOR_PHONE` | — | Phone to receive test IVR calls |
| `TWILIO_COORDINATOR_PHONE` | — | Phone to receive WhatsApp coordinator alerts |
| `WEBHOOK_BASE_URL` | — | Public URL Twilio uses for callbacks |
| `CORS_ORIGINS` | (localhost ports) | Comma-separated allowed origins |

---

## Project Structure

```
PulseLinkAiforgood/
├── backend/
│   └── pulselink/
│       ├── common/          # Domain models, enums, consent, config
│       ├── demo/            # Unified FastAPI app, seed data, call state, webhooks
│       ├── forecasting/     # EWMA cadence estimator + window predictor
│       ├── ingest/          # CSV importer → Patient/Donor domain objects
│       ├── matching/        # Blood match score, haversine distance, donor ranking
│       ├── messaging/       # Alert templates, IVR TwiML, Twilio channel, voice
│       ├── parsing/         # LlmClient seam, MockLlmClient, BedrockClaudeClient
│       ├── reliability/     # Donor reliability score (0–100)
│       └── subscription/    # Subscription generator, slot promotion engine
├── frontend/
│   ├── patient-app/         # Patient status view
│   ├── donor-screen/        # Donor accept/decline
│   └── coordinator-dashboard/ # Full coordinator interface (4 tabs)
├── deploy/
│   ├── config.example.ps1   # Deployment config template (copy → config.ps1)
│   └── *.ps1                # Step-by-step AWS deployment scripts
├── Dataset.csv              # ~80 patients, ~4900 donors (Hyderabad Thalassemia data)
├── Dockerfile               # Python 3.11-slim, copies backend + Dataset.csv
├── .env.example             # Environment variable template
└── run_demo.py              # Convenience script: starts uvicorn on port 8000
```

---

## Testing

```bash
# Backend
cd backend
pip install -e ".[dev]"
python -m pytest tests/ -q

# Frontend (each app)
cd frontend/coordinator-dashboard && npm test
cd frontend/patient-app && npm test
```

---

## What We Use

| Component | Technology |
|---|---|
| Compute | ECS Fargate + ALB |
| Frontend hosting | S3 + CloudFront (3 distributions) |
| Container registry | Amazon ECR |
| Voice IVR | Twilio Programmable Voice (real outbound calls, DTMF) |
| WhatsApp messaging | Twilio WhatsApp API |
| LLM parsing | MockLlmClient (regex-based, offline) |
| App state | In-memory (reset on ECS task restart) |
| Event bus | InMemoryEventBus |
| Dataset | Dataset.csv baked into Docker image |
| Contact encryption | Local symmetric key |
