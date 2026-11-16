# Resumen del paciente (chart summary)

Built 2026-08-01 from the design in [`docs/roadmap/resumen-historia-clinica.md`](../roadmap/resumen-historia-clinica.md) plus the 2026-08-01 capture (pre-generation + connectors + the product owner's own dashboard as the format reference). Owner: `chart_summary.py` (turnos module route) + `admin-patient-summary.mjs` (UI) + `app/connectors/alepo|marqui` (clinical_history_lookup).

## What it is

Before the visit, the doctor sees a pre-generated, specialty-filtered summary of everything the platform knows about the patient, in the format of the product owner's personal medical dashboard: a **Pendientes** tab first (compact current-indicator cards with sparklines and reference ranges, followed by numbered priority actions and the questions worth asking in the room), then a **Historia** tab that leads with larger directly labelled trends before medication and a compact dated clinical timeline. The visual layer derives only from the code-extracted series already present in the summary: latest values, distance from range, dates, and chart points are never model-generated. Mounted inside the doctor's consult view (`#consulta`, `admin-consulta.js`) under the interview brief through the module's named `mount(container, {dni, medicoId})` export.

## Source tiers (the roadmap doc's three tiers, all wired)

1. **Our own data** — `db_booking.list_by_dni` (solicitudes: service, dates, attendance, the AI-read `ai_json` fields of uploaded orders).
2. **Pre-consult interview** (`app/modules/intake`) — triage, summary, brief, red flags via `db_slots.interviews_by_dni`. Best-effort: module off → tier silently absent.
3. **Digitized legacy history** — the `clinical_history_lookup` capability (`app/connectors/__init__.py`), consumed by trying `alepo` (≈2020-2024) and `marqui` (≈1996-2016) directly; a connector that raises `ProviderUnavailable` (real mode today — no access path until the reference-deployment counterpart answers) is skipped silently and the summary degrades to the tiers above. `TURNOS_FAKE_ALEPO=1`/`TURNOS_FAKE_MARQUI=1` serve fully synthetic, era-correct, OCR-style demo histories (`app/connectors/<id>/sample_historias.json`, keyed to `data/test-identities.json` DNIs, deterministic template fallback for unknown DNIs — so on the demo site every patient has something on file). HSI external history is an optional display-only read, kept separate from every AI source tier (see the external-history section below).
4. **Deployment-private archive** — `app/connectors/local_history` reads normalized rows from Postgres (`clinical_history_records`). `scripts/import_clinical_history.py` imports the same JSON shape as the Alepo/Marqui fixtures, replacing one DNI/source atomically; a configured local JSON remains a development/migration fallback. Source files are excluded from git. Pinamar uses this for the product owner's sourced medical-study timeline; the public demo has no access to it.

## Invariants

- **Chart numbers never come from the AI.** `_series()` extracts numeric series in code from the structured `valores` of digitized lab entries (closed catalog `_SERIES_DEF`, ≥3 points or no chart). The model only *chooses* series ids from `series_disponibles` and writes the one-line reading; a hallucinated id is dropped (`_coerce`). `_indicadores()` starts with those model-selected series and fills the 4-card quick-scan grid from the newest code-extracted out-of-range series. This keeps the overview useful when a paired blood-pressure trend consumes 2 of the model's 3 selected series while still rendering as 1 card.
- **One AI reading, editable prompt.** Same `ai_client.call` + Gemini Flash pattern as `ai_orden`/intake analysis; the prompt is `resumen_paciente` in `prompts.py` (canonical English, staff-editable). `TURNOS_FAKE_AI=1` or no key → `_deterministic()` builds a mechanical summary from the same real data (this is also the model-failure fallback, so the panel never shows an error).
- **Pre-generate, don't compute-on-open.** `start()` (launched from `app/ops/web_runtime.py`, same pattern as `hsi_fallback`) ticks every `TURNOS_SUMMARY_TICK_MIN` (60) and generates for every approved appointment with an assigned doctor in the next `TURNOS_SUMMARY_HORIZON_H` (36) hours; `fingerprint` (hash of source ids/dates + especialidad + prompt-edited flag) skips unchanged patients. On-demand GET generation stays as the same-day/walk-in fallback. `TURNOS_SUMMARY_PREGEN=0` turns the daemon off. Since Tier 4 daemon batch 3 (2026-08-08), with `TURNOS_QUEUE_WORKER` set the sweep runs in the queue worker instead (`app/ai/chart_summary_queue.py`, registry entry `chart_summary` in `worker._JOBS`, `outbound=True`): same tick body, same flags, same cadence, plus one immediate startup sweep per worker boot (durable + deduplicated — `worker._defer_startup_jobs`) so the thread's boot-tick contract survives the topology change. The web process then starts no pre-generation thread (`chart_summary.start()` delegates; on-demand generation unchanged).
- **Keep real and fictional histories apart.** The Pinamar private archive is DNI-keyed and installation-owned. The demo seed creates fictional Sofía Herrera (`99000001`) with prior visits, a completed interview and a near-term appointment; fake Alepo/Marqui provide her older studies and clinical notes. Passwordless demo routes create scoped sessions for that patient or the seeded doctor only.
- **One row per (dni, doctor)** in `patient_summaries` — the summary is filtered by that doctor's specialty, so it is not shareable across doctors. Replaced whole on regeneration, never merged.
- **Identity from the session**: rol `medico` always gets their own `medico_id` (query param ignored); admin/operador must pass `?medico=`.
- **Every claim traceable**: the model must attach `fuente` per item, and the footer line (`_fuentes_linea`) states record counts and eras per system — same discipline as the reference dashboard's source footers.

External HSI history is a separate, display-only, on-demand disclosure, default
off. It is not a source tier and never reaches this summary's AI input or
persisted summary. See [External clinical history](hsi.md#external-clinical-history-read-only)
for the role boundary, memory cache, audit metadata and unverified schemas.

## API

- `GET /api/admin/patient-summary?dni=<dni>[&medico=<id>][&refresh=1]` — stored-if-fresh, generates otherwise.
- `POST /api/admin/patient-summary {dni, medico?}` — forced regeneration (the panel's "Actualizar").

Tests: `tests/test_chart_summary.py` (connectors both modes, series extraction, fingerprint reuse, gates, tick) + `tests/admin-patient-summary.test.mjs` (real mount with fake fetch, indicator cards, sparklines, SVG range bands and distinct blood-pressure limits, friendly dates, XSS escaping, wiring).
