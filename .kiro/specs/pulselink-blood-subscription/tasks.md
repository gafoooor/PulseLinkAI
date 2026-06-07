# Implementation Plan: PulseLink — The Blood Subscription for Thalassemia Care

## Overview

This plan builds PulseLink incrementally on seeded data from `Dataset.csv`, running entirely offline. It starts with shared data models and the PostgreSQL schema, then the CSV importer, then each core service in dependency order (LLM parsing behind `LlmClient`, the forecasting engine with EWMA re-learning, donor reliability scoring + tiering, donor matching with the `next_eligible_date` gate, the subscription generator, and the messaging service behind `MessageChannel`). It then adds **PulseLink Voice** as a new `VoiceChannel` behind that same messaging seam, wires the headline decline → auto-promote-backup → re-score flow, layers in consent / role-based access / audit logging, and finishes with the three React client surfaces and an end-to-end seeded demo.

Per the design's adopted AWS-native stack — tuned to a **~$35 budget production profile** — the demo build targets local equivalents while keeping a clean seam to production:

- **Backend:** all five services are **Python / FastAPI** (parsing, forecasting, reliability, subscription generator, messaging), with **PulseLink Voice** added as a `VoiceChannel` implementation of the same `MessageChannel` seam. There is no TypeScript/NestJS split — a single Python runtime serves REST + async processing. In prod these run on EC2/ECS and as Lambda behind API Gateway.
- **Front-end:** **React.js** for all three surfaces (calm patient/parent app, install-free donor accept/decline screen, coordinator risk dashboard).
- **Primary store:** **local PostgreSQL (+PostGIS)** for the demo, mapping to a single free-tier **RDS `db.t3.micro` (PostgreSQL + PostGIS)** in the budget prod profile. PostGIS drives proximity ranking.
- **Seed source:** the importer reads `Dataset.csv` from the **local filesystem**, mirroring the prod **S3** import mapping (bridge → Patient, user → Donor). The import is a simple **local/Lambda importer — no AWS Glue** (avoids Glue job minimums). S3 also holds model/audio artifacts in prod.
- **Event bus:** an **in-memory bus** for the demo, mapping to **Amazon EventBridge** in the budget prod profile (pay-per-event, no idle shard cost) — **EventBridge replaces Kinesis**. The re-learn, decline, and voice-outcome events flow through this seam.
- **LLM:** **Amazon Bedrock (Anthropic Claude Haiku)** behind the pluggable `LlmClient` in prod, with a **mock `LlmClient`** for the offline demo; the same `LlmClient` generates PulseLink Voice call scripts. JSON-schema enforcement applies to every parsing response.
- **ML serving:** the **EWMA forecast** and **reliability-ranking** formulas are the interpretable baseline models. They run **in-process** in **both** the demo and the budget prod profile — **SageMaker is dropped entirely** to stay within budget (no always-on endpoint cost; future upgrade path only).
- **Voice:** PulseLink Voice runs offline via a **`MockVoiceChannel`** (generated audio played locally / simulated DTMF or voice intent). In the budget prod profile it maps to **Amazon Bedrock (Claude Haiku)** for script-gen, **Amazon Polly** for speech, **Amazon Connect + Lex/Transcribe** to place the call and capture Accept/Decline, and **Amazon SNS** for the SMS fallback — all pay-per-use.
- **Orchestration:** the decline → auto-promote-backup → re-score flow and the subscription-generation pipeline are **in-process service calls** for the demo, mapping to **AWS Step Functions + Lambda** in prod; the gateway/RBAC layer maps to **API Gateway + Lambda authorizers + IAM**.
- **Security:** ContactPoint encryption uses a **local symmetric encryption stand-in** mapping to **AWS KMS**; RBAC uses a **role stub** mapping to API Gateway Lambda authorizers + IAM; observability maps to **CloudWatch**, with the separate append-only audit log kept as the system of record.

Messaging (including voice) and LLM access sit behind pluggable interfaces so the demo runs offline. Property-based tests use **`hypothesis` (Python) only** across all services.

## Tasks

- [x] 1. Set up project structure, shared types, and database schema
  - [x] 1.1 Initialize Python project structure and local infra
    - Create a Python package layout for the five FastAPI services plus the three React client apps
    - Add Docker Compose for local PostgreSQL (+PostGIS), plus a `.env` config seam for provider selection (mock vs real `LlmClient`/`MessageChannel`, including the `VoiceChannel`) and an in-memory event-bus toggle (maps to Amazon EventBridge in prod)
    - Set up the `hypothesis` (Python) test runner as the single property-test toolchain
    - _Requirements: 12.2, 12.3_

  - [x] 1.2 Define shared domain types and enums
    - Implement Python dataclasses / Pydantic models for `Patient`, `TransfusionRecord`, `Donor`, `ReliabilityScore`, `Subscription`, `Slot`, `SlotAssignment`, `Notification`, `Consent`, `ContactPoint`
    - Define enums: `BloodGroup`, `DonorTier`, `SlotStatus`, `AssignmentStatus`, `SlotResponse`, `ConsentScope`
    - Implement validation rules (`cadenceDays > 0`, `quantityRequired >= 1`, `donationsTillDate >= 0`)
    - _Requirements: 1.3, 2.1, 3.1, 4.4, 5.5, 10.1_

  - [x] 1.3 Create local PostgreSQL schema and migrations
    - Write migrations for patient, donor, transfusion_record, subscription, slot, slot_assignment, notification, reliability_score tables, each carrying `city_id` (local Postgres now; maps to RDS `db.t3.micro` in the budget prod profile)
    - Create the separate `contact_point` table (encrypted value column) and the append-only `audit_log` table, kept apart from operational/matching tables
    - Add `consent` table with versioned scopes and `revoked_at`; enable PostGIS for proximity ranking
    - _Requirements: 7.3, 10.1, 11.2_

- [x] 2. Implement the seeded-data importer for Dataset.csv
  - [x] 2.1 Build the Dataset.csv importer
    - Read `Dataset.csv` from the local filesystem, mirroring the prod S3 import column-to-field mapping (a simple local/Lambda importer — no AWS Glue)
    - Map each `bridge_id` to a `Patient` (blood group, `quantity_required`, seed `cadenceDays` from `frequency_in_days`, `last_transfusion_date`, `expected_next_transfusion_date`, `city_id`)
    - Map each donor row to a `Donor` using dataset field names (`user_id`, blood group, role, `preferred_lang`, lat/lng, `last_donation_date`, `next_eligible_date`, `eligibility_status`, `donations_till_date`, `total_calls`, `calls_to_donations_ratio`)
    - Persist imported entities to local PostgreSQL scoped by `city_id`
    - _Requirements: 12.1, 10.1_

  - [x]* 2.2 Write unit tests for the importer
    - Test column-to-field mapping, blood-group normalization, and handling of outlier values (e.g. the 1958-day `frequency_in_days`)
    - _Requirements: 12.1_

- [x] 3. Implement the LLM Parsing Service (behind a pluggable LlmClient)
  - [x] 3.1 Define the `LlmClient` Protocol and adapters
    - Implement the `LlmClient` Python `Protocol` with `complete_structured(...)`, plus a `BedrockClaudeClient` (prod, Amazon Bedrock / Anthropic Claude Haiku) and a deterministic `MockLlmClient` (offline demo), provider selected by config
    - Enforce JSON-schema validation on every response regardless of provider
    - _Requirements: 12.3, 5.1_

  - [x] 3.2 Implement the parser core
    - Call `LlmClient.complete_structured` with the fixed JSON schema and few-shot system prompt at `temperature=0`
    - Validate output against the schema, normalize dates to ISO 8601 and blood groups to the canonical set, attach per-leaf-field confidence, and build review flags below the 0.75 threshold
    - Set unsupported fields to null + flag them; on schema-validation failure after one retry, return an empty record with a `parse_failed` flag; never persist before coordinator confirmation
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x]* 3.3 Write property test for deterministic parsing
    - **Property 10: Deterministic parsing** — output is schema-valid and every leaf field carries a confidence in [0,1]
    - **Validates: Requirements 5.1**
    - Use `hypothesis` to generate varied raw inputs against the `MockLlmClient`

  - [x]* 3.4 Write unit tests for parser normalization and failure paths
    - Test date/blood-group normalization, low-confidence flagging, null-on-unsupported, retry, and `parse_failed`, and the no-persist-before-confirmation guarantee
    - _Requirements: 5.3, 5.4, 5.5, 5.6, 5.7_

- [x] 4. Implement the Forecasting Engine with EWMA re-learning
  - [x] 4.1 Implement cadence estimation and window prediction
    - Implement `estimate_cadence` (EWMA over inter-transfusion gaps, seeded by `frequency_in_days`) and `predict_window` producing `start <= expected <= end`, half-width clamped to [1, 7] days, and confidence in [0,1]
    - For patients with fewer than two records, use the seed cadence and the maximum half-width
    - Keep this as the interpretable in-process baseline in both the demo and the budget prod profile (no SageMaker; no model retraining here)
    - _Requirements: 2.1, 2.2, 2.3, 2.7_

  - [x] 4.2 Implement the re-learn event handler
    - Implement `record_transfusion_and_relearn`: append the record, recompute cadence from complete history, persist updated cadence, and produce a non-decreasing sample count
    - Consume the "transfusion recorded" event from the in-memory bus (maps to Amazon EventBridge in prod)
    - _Requirements: 2.4, 2.5, 2.6_

  - [x]* 4.3 Write property test for window validity
    - **Property 1: Window validity** — `start <= expected <= end` and half-width within [MIN_HALF_WIDTH, MAX_HALF_WIDTH]
    - **Validates: Requirements 2.1**

  - [x]* 4.4 Write property test for re-learn monotonicity
    - **Property 2: Re-learning monotonicity of information** — recording a transfusion never decreases `based_on_samples` and the stored cadence equals `estimate_cadence(history)`
    - **Validates: Requirements 2.2**

  - [x]* 4.5 Write unit tests for forecasting edge cases
    - Test empty history, single record, outlier gaps, and seed-cadence fallback with maximum half-width
    - _Requirements: 2.3, 2.7_

- [x] 5. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement Donor Reliability Scoring and Tiering
  - [x] 6.1 Implement the reliability scoring formula
    - Compute the 0–100 score from acceptance ratio, call efficiency (from `calls_to_donations_ratio`), volume (from `donations_till_date`), and recency (decay on `last_donation_date`) with the documented weights
    - Use a neutral acceptance prior of 0.5 when a donor has no offer history
    - Keep this as the interpretable in-process baseline in both the demo and the budget prod profile (no SageMaker; no model retraining here)
    - _Requirements: 3.1, 3.2, 3.4_

  - [x] 6.2 Implement tier assignment
    - Map score to exactly one of `anchor`, `steady`, `growing` using the non-overlapping, exhaustive bands
    - _Requirements: 3.3_

  - [x] 6.3 Implement `on_donor_response` recompute
    - Recompute the score and tier on a donor response so a `DECLINED` response never raises the score
    - _Requirements: 3.5_

  - [x]* 6.4 Write property test for score bounds
    - **Property 3: Score bounds** — `0 <= score <= 100` for every donor and input
    - **Validates: Requirements 3.1**

  - [x]* 6.5 Write property test for tier consistency
    - **Property 5: Tier consistency** — `tier_for` returns exactly one tier and the bands are non-overlapping and exhaustive
    - **Validates: Requirements 3.1**

  - [x]* 6.6 Write property test for decline penalty
    - **Property 4: Decline penalizes** — processing a `DECLINED` response yields a score `<=` the prior score
    - **Validates: Requirements 3.2**

- [x] 7. Implement Donor Matching for a slot
  - [x] 7.1 Implement `rank_donors_for_slot`
    - Filter to blood-compatible donors with `eligibility_status` of `eligible` and an active `contact_for_slots` consent scope
    - Exclude any donor whose `next_eligible_date` is set and falls after the slot Window `end`; rank by reliability descending, breaking ties by shorter distance (PostGIS); scope to a single `city_id`
    - _Requirements: 4.1, 4.2, 4.3, 10.2_

  - [x]* 7.2 Write property test for the eligibility gate
    - **Property 9: Eligibility gate** — every offered donor is blood-compatible and never offered before `next_eligible_date` (when set) relative to the window
    - **Validates: Requirements 4.1**

  - [x]* 7.3 Write unit tests for matching filters and tie-break
    - Test consent/eligibility filtering, missing `next_eligible_date`, and proximity tie-break ordering
    - _Requirements: 4.2, 4.3_

- [x] 8. Implement the Subscription Generator
  - [x] 8.1 Implement subscription generation
    - Generate slots covering the requested horizon, assign one primary donor at rank 0 plus ranked backups at ascending ranks per slot, using the forecasting window and matching results
    - In-process pipeline for the demo (maps to a Step Functions + Lambda pipeline in prod)
    - _Requirements: 4.4, 4.5_

  - [x] 8.2 Enforce the single-active-primary invariant
    - Ensure that, while a slot is unfulfilled, at most one Slot_Assignment holds active primary status at any time
    - _Requirements: 4.5_

  - [x] 8.3 Implement `regenerate_from_window` wiring
    - Wire the generator to the Forecasting Engine and Matching Service so subscriptions refresh from updated windows
    - _Requirements: 4.4_

  - [x]* 8.4 Write property test for the single-active-primary invariant
    - **Property 6: Promotion safety (invariant portion)** — at most one assignment per slot is active/primary at any time
    - **Validates: Requirements 6.1**

  - [x]* 8.5 Write unit tests for slot generation
    - Test horizon coverage, rank assignment, and primary/backup ordering
    - _Requirements: 4.4_

- [x] 9. Implement the Messaging Service (behind a pluggable MessageChannel)
  - [x] 9.1 Define the `MessageChannel` interface and MockChannel
    - Implement the `MessageChannel` seam with `send(...)` and a `MockChannel` that serves the demo without an app install; make the provider config-selectable (real WhatsApp/SMS provider in prod)
    - _Requirements: 12.2, 6.1_

  - [x] 9.2 Implement multilingual template rendering
    - Render offers from the reviewed translation template catalog in the donor's `preferred_lang`, falling back to the configured default language when none is recorded, including slot details and Accept/Decline actions
    - _Requirements: 9.1, 9.2, 9.3_

  - [x] 9.3 Implement `notify_slot` and `handle_inbound` with the consent gate
    - Send a single-slot offer with Accept/Decline through the Donor_Interface and process inbound ACCEPT/DECLINE; confirm an active `contact_for_slots` consent scope at send time before sending to any Contact_Point
    - _Requirements: 6.1, 7.1_

  - [x]* 9.4 Write property test for the consent gate
    - **Property 8: Consent gate** — for every notification sent, the recipient has an active `contact_for_slots` scope at send time
    - **Validates: Requirements 7.1**

  - [x]* 9.5 Write unit tests for rendering and language fallback
    - Test preferred-language rendering, default-language fallback, and inclusion of slot details + actions
    - _Requirements: 9.1, 9.2, 9.3_

- [ ] 10. Implement PulseLink Voice (VoiceChannel behind the pluggable MessageChannel)
  - [x] 10.1 Define the `VoiceChannel` interface and `MockVoiceChannel`
    - Define `VoiceChannel` as an extension of the existing `MessageChannel` (adding `generate_script(...)`, `synthesize(...)`, `place_call(...)`) so the Messaging Service treats it like any other channel
    - Implement a `MockVoiceChannel` for the offline demo that plays the generated audio locally and simulates DTMF / voice-intent capture, with the provider config-selectable (Amazon Connect + Lex/Transcribe + Polly + SNS in prod)
    - _Requirements: 13.1, 13.10, 12.2_

  - [x] 10.2 Implement voice call-script generation via `LlmClient`
    - Generate a short, empathetic call script through the existing `LlmClient` (Amazon Bedrock / Claude Haiku in prod, `MockLlmClient` offline), grounded in donor + bridge/patient context and containing no medical claims
    - _Requirements: 13.1_

  - [x] 10.3 Implement speech synthesis in the donor's preferred language
    - Synthesize the script to speech in the donor's `preferred_lang`, falling back to the configured default language when none is recorded; persist the audio artifact (Amazon Polly neural voices + S3 in prod; local/canned audio file for the demo)
    - _Requirements: 13.2, 13.3_

  - [x] 10.4 Implement place-call + capture Accept/Decline with the voice consent gate
    - Place the call and capture Accept/Decline via voice intent or DTMF keypad input (Amazon Connect + Lex/Transcribe in prod; simulated in `MockVoiceChannel`), enforcing the active `contact_for_slots` consent gate before placing the call or its SMS fallback
    - _Requirements: 13.4, 13.5_

  - [x] 10.5 Implement the SMS fallback on unanswered/failed calls
    - On an unanswered or failed call, send an SMS offer to the same donor, subject to that donor holding an active `contact_for_slots` consent scope (Amazon SNS in prod; mock for the demo)
    - _Requirements: 13.6_

  - [-] 10.6 Map the captured response to one outcome and publish once
    - Map the captured voice/DTMF response to exactly one `SlotResponse` for exactly one slot; on `DECLINED` recompute the donor's reliability (never raising the score) and publish the outcome exactly once to the event bus (in-memory for the demo; Amazon EventBridge in prod) so the Reliability Service recomputes
    - _Requirements: 13.7, 13.8, 13.9_

  - [x]* 10.7 Write property test for the voice consent gate
    - **Property 11: Voice consent gate** — `VoiceChannel` (and its SMS fallback) never places a call or SMS to a donor without an active `contact_for_slots` consent scope at call time
    - **Validates: Requirements 13.4, 13.5**
    - Use `hypothesis` to generate random donor consent states

  - [x]* 10.8 Write property test for voice outcome integrity
    - **Property 12: Voice outcome integrity** — every captured `VoiceOutcome` maps to exactly one `SlotResponse` for exactly one slot, is published exactly once to the event bus, and a `DECLINED` outcome never raises the donor's score
    - **Validates: Requirements 13.7, 13.8, 13.9**
    - Use `hypothesis` to generate random captured voice responses

  - [ ]* 10.9 Write integration test for the offline voice flow
    - Offline via `MockVoiceChannel`: script-gen → audio → simulated capture → outcome event updates reliability; assert an unanswered call triggers the SMS-fallback path and that a revoked-consent donor is never called or texted
    - _Requirements: 13.1, 13.6, 13.7, 13.10_

- [ ] 11. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Implement the Decline → Auto-Promote-Backup → Re-Score flow
  - [x] 12.1 Implement decline handling
    - Implement `handle_decline` to set the declining Slot_Assignment to `declined` status (in-process for the demo; maps to a Step Functions state machine in prod)
    - _Requirements: 6.2_

  - [x] 12.2 Wire decline-driven re-scoring
    - Publish/consume the donor-response event on the in-memory bus (Amazon EventBridge in prod) so the Reliability_Service recomputes the decliner's score and tier on decline
    - _Requirements: 6.6, 3.5_

  - [x] 12.3 Implement backup auto-promotion
    - Promote the highest-ranked eligible backup to primary, leave at most one active primary, never re-offer the slot to the donor who declined, and instruct the Messaging_Service to send the backup a localized offer
    - _Requirements: 6.3, 6.4, 6.5_

  - [ ] 12.4 Implement coordinator escalation
    - When no eligible backup exists, keep the slot unconfirmed and escalate it to the coordinator
    - _Requirements: 6.7_

  - [ ]* 12.5 Write property test for promotion safety
    - **Property 6: Promotion safety** — after `handle_decline` at most one assignment is active/primary and a declined donor is never re-offered the same slot
    - **Validates: Requirements 6.1**

  - [ ]* 12.6 Write integration test for the decline→promote→confirm flow
    - On seeded data: decline a primary, assert a backup is promoted and confirmed and the decliner's score dropped
    - _Requirements: 6.2, 6.3, 6.5, 6.6_

- [x] 13. Implement Consent, Privacy, Role-Based Access, and Audit Logging
  - [x] 13.1 Implement consent and revocation cascade
    - Implement versioned `Consent` with scopes; on revocation, suppress pending and future notifications and exclude the subject from future matching
    - _Requirements: 7.2_

  - [x] 13.2 Implement Contact_Point protection
    - Store Contact_Point values encrypted at rest using a local symmetric encryption stand-in (maps to AWS KMS in prod), separate from operational matching data; ensure values are never written to logs, traces, or audit entries in plaintext
    - _Requirements: 7.3, 7.4_

  - [x] 13.3 Implement role-based access at the API gateway
    - Authorize each request against the caller's role via a role stub (maps to API Gateway Lambda authorizers + IAM in prod) so patients see only their own data, donors see only their own assigned slots, and coordinators see only their assigned city's data
    - _Requirements: 7.5, 10.2_

  - [x] 13.4 Implement append-only audit logging
    - Append an audit entry (actor, action, timestamp) on every parse confirmation, data share, and notification, stored in an append-only log separate from operational data (CloudWatch provides operational observability in prod; the append-only log remains the system of record)
    - _Requirements: 11.1, 11.2_

  - [x]* 13.5 Write tests for consent, RBAC, and audit
    - Test the send-time consent gate, revocation suppression, role-scoped access enforcement, PII-redaction in logs, and audit entry creation
    - _Requirements: 7.1, 7.2, 7.4, 7.5, 11.1_

- [x] 14. Implement the React client surfaces
  - [x] 14.1 Implement the calm Patient/Parent home screen (React)
    - Show "arranged" when a slot has a `confirmed` assignment and "arranging" otherwise; display the next transfusion Window as an inclusive date range; restrict each view to the patient's own subscription
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x]* 14.2 Write property test for no false reassurance
    - **Property 7: No false reassurance** — patient-facing status is "arranged" iff a `confirmed` assignment exists for that slot
    - **Validates: Requirements 1.1**

  - [x] 14.3 Implement the donor Accept/Decline screen (React)
    - Build the single localized, install-free React web screen (opened from an SMS/WhatsApp link) showing one slot with Accept and Decline actions in the donor's language
    - _Requirements: 6.1, 9.1, 9.3_

  - [x] 14.4 Implement the coordinator risk dashboard (React)
    - Compute `Risk_Score` (rising as the window start approaches, as confirmed coverage decreases, and as forecast confidence decreases), order patients by Risk_Score descending, scope to the coordinator's `city_id`, and provide a paste-to-parse input that submits free text to the Parser and highlights low-confidence fields
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x]* 14.5 Write unit tests for risk scoring and sort
    - Test that risk rises with proximity/low coverage/low confidence and that patients sort descending within a city
    - _Requirements: 8.1, 8.2, 8.3_

- [ ] 15. Final integration and wiring
  - [ ] 15.1 Wire the end-to-end seeded demo flow
    - Connect parse → forecast → generate subscription → offer (including the **PulseLink Voice** path via `MockVoiceChannel`: script-gen → audio → simulated voice/DTMF capture, with SMS fallback on no-answer) → decline → promote → confirm, with the patient screen flipping to "arranged" only on confirmation and the dashboard sorting by risk
    - Run entirely offline on seeded `Dataset.csv` data with in-process service calls (maps to Step Functions + Lambda in prod) and the in-memory event bus (maps to Amazon EventBridge)
    - _Requirements: 1.1, 4.4, 6.5, 8.2, 12.1, 13.7_

  - [ ]* 15.2 Write integration test for consent revocation
    - Revoke consent mid-cycle and assert pending/future notifications (across SMS, WhatsApp, and voice channels) are suppressed and the subject is excluded from matching
    - _Requirements: 7.2_

- [ ] 16. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirement sub-clauses for traceability, and every property test cites its Property number from the design's Correctness Properties section (Properties 1–12, including the two new voice properties).
- The entire backend is Python/FastAPI and all three client surfaces are React.js; PulseLink Voice is a `VoiceChannel` behind the same `MessageChannel` seam. Property tests use `hypothesis` (Python) only across every service.
- Demo ↔ budget-production seams are preserved per the design: local PostgreSQL ↔ RDS `db.t3.micro`, local filesystem ↔ S3 (local/Lambda importer, **no Glue**), in-memory bus ↔ **Amazon EventBridge** (replaces Kinesis), `MockLlmClient` ↔ Amazon Bedrock (Claude Haiku), `MockVoiceChannel` ↔ Amazon Connect + Lex/Transcribe + Polly + SNS, in-process calls ↔ Step Functions + Lambda, role stub + local encryption ↔ API Gateway authorizers/IAM + KMS.
- The EWMA forecast and reliability-ranking formulas are interpretable in-process baselines in **both** the demo and the budget prod profile — **SageMaker is dropped** (no always-on endpoint cost; future upgrade path only) and no new ML model training is included.
- Checkpoints provide incremental validation points; all flows — including the entire PulseLink Voice flow — run on seeded `Dataset.csv` data with messaging, voice, and LLM access behind pluggable interfaces, fully offline.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["2.1", "3.1", "4.1", "6.1", "9.1"] },
    { "id": 3, "tasks": ["2.2", "3.2", "4.2", "4.3", "6.2", "6.4", "9.2", "13.1", "13.2", "13.3", "13.4", "10.1"] },
    { "id": 4, "tasks": ["3.3", "3.4", "4.4", "4.5", "6.3", "6.5", "9.3", "13.5", "10.2"] },
    { "id": 5, "tasks": ["6.6", "7.1", "9.4", "9.5", "14.3", "10.3"] },
    { "id": 6, "tasks": ["7.2", "7.3", "8.1", "10.4"] },
    { "id": 7, "tasks": ["8.2", "12.1", "14.1", "14.4", "10.5"] },
    { "id": 8, "tasks": ["8.3", "8.4", "12.2", "14.2", "14.5", "10.7"] },
    { "id": 9, "tasks": ["8.5", "12.3", "10.6"] },
    { "id": 10, "tasks": ["12.4", "10.8"] },
    { "id": 11, "tasks": ["12.5", "12.6"] },
    { "id": 12, "tasks": ["15.1", "10.9"] },
    { "id": 13, "tasks": ["15.2"] }
  ]
}
```
