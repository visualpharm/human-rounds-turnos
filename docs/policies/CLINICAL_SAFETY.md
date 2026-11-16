# Clinical safety

Human Rounds schedules appointments and prepares a doctor's visit. It is not
a medical device and treats no one; no model output reaches a patient as a
diagnosis, a severity rating or a promise of care. This document states who
owns clinical safety, what could go wrong, what the product does about it,
and what it cannot do.

## Publication

- The public repository is one commit made with `git archive` from the private main branch; its history is not published.
- Licence AGPL-3.0, copyright Generated Media, Inc.; [LICENSING.md](LICENSING.md) lists the assets that are not under the AGPL.
- Kept out of the release (`export-ignore` in `.gitattributes`): the reference deployment's private data pack, agent harness files (`.claude/`, `CLAUDE.md`), research records made against the real catalog, the login screen's humor images, and installation-only operations notes.
- Everyone who wrote code for the project agreed on 2026-11-11 to its publication under AGPL-3.0.

## Safety ownership

- **Clinical decisions belong to the treating professional.** Every AI
  output is addressed to staff, or is a transcription of the patient's own
  words. The patient-facing interview is told, in a fixed part of its
  instructions, never to diagnose, never to rate severity to the patient and
  never to promise a wait time (`app/modules/intake/interview_agent.py`,
  [entrevista-previa.md](../architecture/entrevista-previa.md)).
- **Engineering:** the maintainers in [MAINTAINERS.md](MAINTAINERS.md) own the
  mitigations below; changing one is a clinical-safety change that needs a
  specialist reviewer ([GOVERNANCE.md](../../.github/GOVERNANCE.md),
  "Approvals and review").
- **Installation:** the institution names the clinician accountable for how
  triage colours and briefs are used, edits the AI instructions from the
  panel (`app/ai/prompts.py`) and decides which services offer the
  pre-consult interview.
- **Safety incidents** (a wrong triage that reached a doctor, a model output
  that reached a patient, a misread referral that blocked care) follow
  [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) and are reported privately
  through [SECURITY.md](../../SECURITY.md), without patient data.
- Decision needed: the named clinical safety owner for the municipal
  installation (a clinician at the hospital) and for the project.

## Clinical safety case

The product touches care in four places.

1. **Referral reading** (`app/ai/ai_referral.py`). Failure: a valid order
   rejected or an invalid one accepted. One model reading; the front desk
   approves or rejects every request; a rejection carries the next concrete
   step for the patient (`app/modules/turnos/remedies.py`), and rejection
   reasons come from a closed list.
2. **Pre-consult interview and triage** (`app/modules/intake/`). Failure: an
   urgent patient marked non-urgent. The colour is for the doctor's queue
   only. A deterministic red-flag scan of the patient's words is the floor:
   the model may raise urgency, never lower a colour the rules raised
   (`redflags.more_urgent`).
3. **Chart summary and lab reading** (`app/ai/chart_summary.py`,
   `app/ai/lab_analysis.py`). Failure: an invented value or trend. Trend
   series are extracted by code; every statement shows its source;
   patient-uploaded reports are marked unverified (`tercero: true`); a
   staff-editable sensitivity filter governs what the patient sees.
4. **Prescriptions and study orders**
   (`app/modules/intake/consult_compose.py`). Failure: a drug or exam the
   doctor did not choose. The printable is built from what the doctor ticked;
   the optional rewording pass is clipped back onto the original schema, so it
   cannot add a drug, exam or diagnosis.

Scheduling rules with clinical weight are closed product decisions:
walk-in services never offer appointments, how many appointments a patient
may hold is a number per doctor or service, and approval persists locally
even if the HSI push fails.

## In-product mitigations

- **Deterministic degradation.** With no key, `TURNOS_FAKE_AI=1`, or any
  call or parse failure, interview analysis falls back to the rule scan, so
  "no AI" behaves like "the AI said nothing useful".
- **Catalog-constrained output.** Suggested diagnoses and exams are limited to
  the specialty's catalog ids (`app/modules/intake/intake_catalog.py`); an
  unknown id is dropped.
- **One model, one reading.** Gemini 3 Flash via OpenRouter; the backup key
  (`OPENROUTER_API_KEY_BACKUP`) is for availability, not second opinions.
  Costs and failures are journaled (`ai_costs`, `app/ai/ai_health.py`).
- **Abuse guards.** Per-IP limits and cooldowns on the AI endpoints
  (`app/ai/abuse_guard.py`, `app/ai/ai_cooldown.py`).
- **Privacy.** Identifiers are masked before text leaves for the model
  (`app/core/pii.py`); the referral file, the DNI photo and interview images
  are the stated exceptions ([DPA.md](DPA.md)).
- **Clinical access log.** Every staff read of clinical data is logged, and
  a doctor's screens show only that doctor's own patients
  ([registro-de-accesos.md](../architecture/registro-de-accesos.md)).
- **Elderly-first floor.** 16 px minimum, one primary action per screen, a
  solution instead of an error, so a confused patient is steered to the desk.
- **Pinned by tests:** `tests/test_legal.py`, `tests/test_pii.py`, the intake
  suites (red-flag floor, compose clipping), `tests/test_remedies.py`.

## Limitations

- **The red-flag rules are a floor, not a triage system.** They catch
  explicit phrases; they miss negation, context and understatement. No
  sensitivity figure exists because none has been measured.
- **No clinical validation has been done**: no study, no reference standard,
  no measured outcome. The public site states inventory facts only.
- **Masking is best effort**: a misspelled name, or one inside OCR'd text,
  can reach the model provider.
- **Referral reading is photo interpretation.** Blurry, cropped or
  handwritten orders are rejected more often; the mitigation is the desk and
  the remedy text.
- **No SLA** ([SUPPORT.md](../../.github/SUPPORT.md)); an installation keeps
  its phone and in-person path open, as the privacy policy tells patients.
- Decision needed: whether the municipal installation wants a periodic
  clinician review of a sample of triage colours and briefs, and who signs
  off each edit of an AI prompt.

## Approval record

record: approval:hr-0e0122dee6e8596e

date: 2026-11-16
