# Requirements Document

## Introduction

PulseLink turns lifelong, recurring blood transfusions for Thalassemia Major patients into a managed **subscription** instead of a series of disconnected emergencies. The system gives blood logistics a memory and a forecast: it predicts each patient's next transfusion window, maintains a recurring plan of matched donors, and automatically promotes a backup donor when a primary declines — all while keeping the patient/parent experience calm and honest.

These requirements are derived from the approved design document (`design.md`), the written project brief, and the real Blood Warriors–style schema observed in `Dataset.csv`. They cover three user surfaces (calm patient/parent home screen, multilingual donor accept/decline screen, coordinator risk dashboard) and five core services (LLM parsing, forecasting, donor reliability scoring, subscription generation, and notification/messaging). They also cover **PulseLink Voice**, an AI voice-outreach feature that automatically calls donors in their own language to offer a slot — implemented as a `VoiceChannel` behind the existing pluggable messaging interface — which replaces manual coordinator phone calls and directly attacks the dataset's high `calls_to_donations_ratio`. All hackathon flows run on seeded data, with messaging (including voice) and LLM access behind provider-pluggable interfaces.

Field names and behaviors align to `Dataset.csv` where relevant: a `bridge_id` anchors a Patient/Bridge entity, donors carry `frequency_in_days`, `last_transfusion_date`, `expected_next_transfusion_date`, `donations_till_date`, `calls_to_donations_ratio`, `eligibility_status`, `next_eligible_date`, and `latitude`/`longitude`.

**Non-Goals:** No medical dosing, diagnosis, or clinical decision-making. No real-money payments or blood-bank inventory management in v1. No guarantee of donor turnout.

## Glossary

- **PulseLink**: The overall system comprising all services and client surfaces described in this document.
- **Bridge**: The recurring patient–donor support group around one Thalassemia patient, identified by the dataset `bridge_id`.
- **Patient**: A first-class entity derived from a bridge, representing the Thalassemia Major patient who requires recurring transfusions.
- **Donor**: A registered blood donor, tagged in the dataset as `Bridge Donor`, `Emergency Donor`, or `Volunteer`.
- **Coordinator**: An authenticated staff user who manages patients and donors for a single assigned city.
- **Parser**: The LLM Parsing Service that converts messy multilingual free-text records into validated structured JSON.
- **Forecasting_Engine**: The service that predicts each patient's next transfusion window and re-learns cadence after each recorded transfusion.
- **Reliability_Service**: The service that computes donor reliability scores and assigns donor tiers.
- **Subscription_Generator**: The service that builds each patient's recurring plan of slots with matched primary and backup donors.
- **Matching_Service**: The component that filters and ranks eligible, consented, blood-compatible donors for a slot.
- **Messaging_Service**: The service that delivers multilingual slot offers and processes Accept/Decline responses.
- **Patient_App**: The calm patient/parent home screen surface.
- **Donor_Interface**: The single localized accept/decline screen opened from an SMS/WhatsApp link, requiring no app install.
- **Coordinator_Dashboard**: The web surface that lists patients ranked by risk for a single city.
- **Window**: The predicted inclusive date range `[start, end]` with a most-likely `expected` date in which a patient's next transfusion is expected.
- **Cadence**: The estimated number of days between a patient's consecutive transfusions, seeded by `frequency_in_days`.
- **Slot**: A single upcoming transfusion occurrence within a subscription that needs one or more donor commitments.
- **Slot_Assignment**: The link between a slot and a donor, carrying a rank (0 = primary, 1..n = backups) and a status.
- **Reliability_Score**: A donor reliability value in the range 0 to 100.
- **Donor_Tier**: One of exactly three reliability bands: `anchor`, `steady`, or `growing`.
- **Auto_Promote**: Replacing a declined donor with the best available backup automatically.
- **Consent**: A versioned record granting specific data-use scopes for a patient or donor.
- **Contact_Point**: An encrypted, consent-gated record holding a subject's phone or WhatsApp contact details.
- **Confidence**: A value in the range 0 to 1 expressing certainty in a forecast window or a parsed field.
- **City_Id**: The partition key carried by every patient, donor, and bridge for city-scoped scaling.
- **Risk_Score**: The coordinator dashboard sort key expressing a patient's risk of missing their next transfusion.
- **PulseLink_Voice**: The AI voice-outreach feature that automatically calls a donor in their own language to offer a slot, replacing manual coordinator phone calls and reducing the dataset's `calls_to_donations_ratio`.
- **VoiceChannel**: A pluggable message channel implementation (extending the same interface as the SMS/WhatsApp channels) that generates a call script, synthesizes speech, places a voice call, captures the donor's response, and falls back to SMS when the call cannot be completed.
- **MockVoiceChannel**: The offline `VoiceChannel` implementation used in the demo, which runs the entire voice flow without live telephony.
- **LLM_Client**: The pluggable client interface through which services call the configured LLM provider (Amazon Bedrock / Anthropic Claude) for parsing and for voice call-script generation.
- **Slot_Response**: A donor's outcome for a single slot, exactly one of `ACCEPTED`, `DECLINED`, or `NO_RESPONSE`.
- **Event_Bus**: The publish/subscribe channel on which the PulseLink services publish and consume domain events such as donor responses.
- **DTMF**: Dual-tone multi-frequency keypad input, used as an alternative to spoken intent for capturing a donor's response during a voice call.
- **Preferred_Lang**: A donor's recorded preferred language (`preferred_lang`) used to localize messages and voice calls.

## Requirements

### Requirement 1: Calm Patient/Parent Home Screen

**User Story:** As a Thalassemia patient or parent, I want a calm home screen that tells me whether my next transfusion blood is arranged, so that I am not forced to re-live an emergency every cycle.

#### Acceptance Criteria

1. WHERE a slot has at least one Slot_Assignment with status `confirmed`, THE Patient_App SHALL display that slot's patient-facing status as "arranged".
2. IF a slot has no Slot_Assignment with status `confirmed`, THEN THE Patient_App SHALL display that slot's patient-facing status as "arranging".
3. THE Patient_App SHALL display the next transfusion Window as an inclusive date range for the patient.
4. THE Patient_App SHALL restrict each patient view to only that patient's own subscription, Window, and arranged status.

### Requirement 2: Transfusion Window Forecasting and Re-Learning

**User Story:** As a coordinator, I want each patient's next transfusion predicted as a date range that improves after every transfusion, so that I can plan donor outreach with honest lead time instead of false precision.

#### Acceptance Criteria

1. WHEN the Forecasting_Engine produces a Window for a patient, THE Forecasting_Engine SHALL return a `start` date, an `end` date, and an `expected` date such that `start` is on or before `expected` and `expected` is on or before `end`.
2. WHEN the Forecasting_Engine produces a Window, THE Forecasting_Engine SHALL set the window half-width to a whole number of days between the configured minimum half-width of 1 day and the configured maximum half-width of 7 days inclusive.
3. WHEN the Forecasting_Engine produces a Window, THE Forecasting_Engine SHALL set the Confidence to a value between 0 and 1 inclusive.
4. WHEN a TransfusionRecord is recorded for a patient, THE Forecasting_Engine SHALL recompute that patient's Cadence as an exponentially weighted moving average over observed inter-transfusion gaps.
5. WHEN a TransfusionRecord is recorded for a patient, THE Forecasting_Engine SHALL update the stored Cadence so that it equals the cadence computed from the patient's complete transfusion history.
6. WHEN a TransfusionRecord is recorded, THE Forecasting_Engine SHALL produce a sample count that is greater than or equal to the sample count before the record was added.
7. IF a patient has fewer than two transfusion records, THEN THE Forecasting_Engine SHALL use the seed Cadence from `frequency_in_days` and set the window half-width to the maximum half-width.

### Requirement 3: Donor Reliability Scoring and Tiering

**User Story:** As a coordinator, I want donors scored and tiered by real reliability, so that the most dependable donors are matched first and behavior over time is reflected.

#### Acceptance Criteria

1. WHEN the Reliability_Service scores a donor, THE Reliability_Service SHALL produce a Reliability_Score between 0 and 100 inclusive.
2. WHEN the Reliability_Service scores a donor, THE Reliability_Service SHALL compute the Reliability_Score from acceptance ratio, call efficiency derived from `calls_to_donations_ratio`, donation volume derived from `donations_till_date`, and recency derived from `last_donation_date`.
3. WHEN the Reliability_Service assigns a Donor_Tier, THE Reliability_Service SHALL assign exactly one tier from the set `anchor`, `steady`, `growing` using non-overlapping and exhaustive score bands.
4. IF a donor has no prior offer history, THEN THE Reliability_Service SHALL use a neutral acceptance prior of 0.5 when computing the Reliability_Score.
5. WHEN the Reliability_Service processes a `DECLINED` response for a donor, THE Reliability_Service SHALL produce a Reliability_Score that is less than or equal to that donor's Reliability_Score before the response.

### Requirement 4: Subscription Generation and Donor Matching

**User Story:** As a coordinator, I want each patient to have an auto-generated recurring plan with a matched primary donor and ranked backups per slot, so that coverage is arranged ahead of time instead of re-requested each cycle.

#### Acceptance Criteria

1. WHEN the Matching_Service ranks donors for a slot, THE Matching_Service SHALL exclude any donor whose `next_eligible_date` is set and falls after the slot Window `end` date.
2. WHEN the Matching_Service ranks donors for a slot, THE Matching_Service SHALL include only donors who are blood-compatible with the patient, have `eligibility_status` of `eligible`, and hold an active `contact_for_slots` consent scope.
3. WHEN the Matching_Service ranks eligible donors, THE Matching_Service SHALL order them by Reliability_Score in descending order and break ties by shorter distance to the patient first.
4. WHEN the Subscription_Generator generates a subscription, THE Subscription_Generator SHALL create slots covering the requested horizon and assign one primary donor at rank 0 plus ranked backups at ascending ranks per slot.
5. WHILE a slot is not fulfilled, THE Subscription_Generator SHALL maintain at most one Slot_Assignment with primary status for that slot at any time.

### Requirement 5: LLM Parsing of Messy Multilingual Records

**User Story:** As a coordinator, I want messy multilingual WhatsApp-style records converted into clean validated structured data with confidence and review flags, so that I can confirm accurate patient and donor records without manual transcription.

#### Acceptance Criteria

1. WHEN the Parser parses a record, THE Parser SHALL return output that is valid against the fixed JSON schema.
2. WHEN the Parser parses a record, THE Parser SHALL attach a Confidence between 0 and 1 inclusive to every leaf field.
3. WHEN a parsed leaf field has a Confidence below the review threshold of 0.75, THE Parser SHALL add a review flag identifying that field.
4. WHEN the Parser cannot support a field value from the input text, THE Parser SHALL set that field to null and flag it for review rather than inventing a value.
5. WHEN the Parser normalizes a parsed record, THE Parser SHALL convert dates to ISO 8601 format and blood groups to the canonical blood group set.
6. THE Parser SHALL NOT persist any parsed record to the operational store before a coordinator confirms the record.
7. IF the LLM output fails schema validation after one retry, THEN THE Parser SHALL return an empty parsed record with a `parse_failed` flag.

### Requirement 6: Donor Accept/Decline and Auto-Promote Backup

**User Story:** As a donor, I want to accept or decline a single transfusion slot from a link in my own language, and as a coordinator I want a decline to automatically promote the best backup, so that coverage is maintained without manual intervention.

#### Acceptance Criteria

1. WHEN the Messaging_Service sends a slot offer to a donor, THE Messaging_Service SHALL present a single slot with Accept and Decline actions through the Donor_Interface without requiring an app install.
2. WHEN a donor declines a slot, THE Subscription_Generator SHALL set the declining Slot_Assignment to `declined` status.
3. WHEN a donor declines a slot, THE Subscription_Generator SHALL leave at most one Slot_Assignment with active primary status for that slot.
4. WHEN a donor declines a slot, THE Subscription_Generator SHALL NOT re-offer that same slot to the donor who declined it.
5. WHEN a donor declines a slot and an eligible backup exists, THE Subscription_Generator SHALL promote the highest-ranked eligible backup to primary and instruct the Messaging_Service to send that backup a localized offer.
6. WHEN a donor declines a slot, THE Reliability_Service SHALL recompute the declining donor's Reliability_Score and Donor_Tier.
7. IF a donor declines a slot and no eligible backup exists, THEN THE Subscription_Generator SHALL keep the slot unconfirmed and escalate the slot to the coordinator.

### Requirement 7: Consent, Privacy, and Role-Based Access

**User Story:** As a patient or donor, I want my contact details protected and used only with my consent, so that sensitive health-related data is not exposed or misused.

#### Acceptance Criteria

1. WHEN the Messaging_Service sends a notification to a Contact_Point, THE Messaging_Service SHALL confirm that the recipient holds an active `contact_for_slots` consent scope at send time.
2. WHEN a subject revokes consent, THE PulseLink SHALL suppress pending and future notifications to that subject and exclude that subject from future matching.
3. THE PulseLink SHALL store Contact_Point values encrypted at rest and separate from operational matching data.
4. THE PulseLink SHALL NOT write Contact_Point values to logs, traces, or audit entries in plaintext.
5. WHEN the API gateway receives a request, THE PulseLink SHALL authorize the request against the caller's role so that patients see only their own data, donors see only their own assigned slots, and coordinators see only data for their assigned city.

### Requirement 8: Coordinator Risk-Ranked Dashboard

**User Story:** As a coordinator, I want patients ranked by risk of missing their next transfusion, so that I can triage outreach toward the most urgent, uncovered cases first.

#### Acceptance Criteria

1. WHEN the Coordinator_Dashboard computes a Risk_Score for a patient, THE Coordinator_Dashboard SHALL increase the Risk_Score as the slot Window start approaches, as confirmed coverage decreases, and as forecast Confidence decreases.
2. WHEN the Coordinator_Dashboard displays patients, THE Coordinator_Dashboard SHALL order patients by Risk_Score in descending order.
3. THE Coordinator_Dashboard SHALL display only patients belonging to the coordinator's assigned City_Id.
4. THE Coordinator_Dashboard SHALL provide a paste-to-parse input that submits free-text records to the Parser and presents the parsed result for review with low-confidence fields highlighted.

### Requirement 9: Multilingual Donor Messaging

**User Story:** As a donor who reads an Indian language, I want slot offers in my preferred language, so that I can understand and respond to requests without a language barrier.

#### Acceptance Criteria

1. WHEN the Messaging_Service sends a slot offer, THE Messaging_Service SHALL render the message in the donor's `preferred_lang` from the reviewed translation template catalog.
2. IF a donor has no recorded preferred language, THEN THE Messaging_Service SHALL render the message in a configured default language.
3. WHEN the Messaging_Service renders a localized offer, THE Messaging_Service SHALL include the slot details and the Accept and Decline actions in the rendered message.

### Requirement 10: City-Partitioned Data and Scalability

**User Story:** As a system operator, I want every core entity scoped by city, so that PulseLink can scale by adding cities without schema changes.

#### Acceptance Criteria

1. THE PulseLink SHALL assign a City_Id to every Patient, Donor, and Bridge entity.
2. WHEN the Matching_Service, Forecasting_Engine, or Coordinator_Dashboard processes patients or donors, THE PulseLink SHALL scope the operation to a single City_Id.

### Requirement 11: Audit and Consent Logging

**User Story:** As a compliance reviewer, I want every parse, data share, and notification recorded in an append-only log, so that data handling is traceable and accountable.

#### Acceptance Criteria

1. WHEN the Parser confirms a record, the PulseLink shares data, or the Messaging_Service sends a notification, THE PulseLink SHALL append an audit entry recording the actor, the action, and the timestamp.
2. THE PulseLink SHALL store audit entries in an append-only log kept separate from operational data.

### Requirement 12: Seeded Data Import and Provider-Pluggable Interfaces

**User Story:** As a developer, I want the system to run entirely on seeded data with messaging and LLM access behind interfaces, so that the demo works offline and production providers can be swapped in without changing business logic.

#### Acceptance Criteria

1. WHEN the importer loads `Dataset.csv`, THE PulseLink SHALL map each bridge to a Patient entity and each donor row to a Donor entity using the dataset field names.
2. THE Messaging_Service SHALL send messages through a pluggable message channel interface so that a mock channel serves the demo and a real provider can be configured for production.
3. THE Parser SHALL call the LLM through a pluggable client interface so that the LLM provider can be changed by configuration.

### Requirement 13: AI Voice Outreach (PulseLink Voice)

**User Story:** As a coordinator, I want an AI voice agent to automatically call donors in their own language to offer a slot, so that I can stop making manual coordination calls and reduce the wasted calls counted by `calls_to_donations_ratio`.

#### Acceptance Criteria

1. WHEN a slot offer is routed to the VoiceChannel, THE PulseLink SHALL generate the call script through the LLM_Client grounded in the donor and bridge context and containing no medical claims.
2. WHEN the VoiceChannel synthesizes a call, THE PulseLink SHALL render the speech in the donor's `preferred_lang`.
3. IF a donor has no recorded preferred language, THEN THE PulseLink SHALL render the speech in the configured default language.
4. WHEN the VoiceChannel places a call, THE PulseLink SHALL confirm that the donor holds an active `contact_for_slots` consent scope at call time.
5. IF a donor does not hold an active `contact_for_slots` consent scope, THEN THE PulseLink SHALL withhold the voice call and its SMS fallback from that donor.
6. WHEN a voice call is unanswered or fails, THE PulseLink SHALL send an SMS offer to the same donor, subject to that donor holding an active `contact_for_slots` consent scope.
7. WHEN the VoiceChannel captures a donor response through voice intent or DTMF keypad input, THE PulseLink SHALL map that response to exactly one Slot_Response for exactly one slot.
8. WHEN a captured voice response is `DECLINED`, THE PulseLink SHALL recompute the donor's Reliability_Score to a value less than or equal to that donor's Reliability_Score before the response.
9. WHEN a captured voice response is `DECLINED`, THE PulseLink SHALL publish the outcome exactly once to the Event_Bus.
10. THE PulseLink SHALL run the entire voice flow offline in the demo through a MockVoiceChannel without live telephony.
