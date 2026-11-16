# Resumen de historia clínica — design thinking for a future feature

> Status: **BUILT 2026-08-01** as «Resumen del paciente» — see
> [`docs/architecture/resumen-paciente.md`](../architecture/resumen-paciente.md) for what
> shipped (pre-generation batch instead of compute-on-open, the three source tiers wired
> including the alepo/marqui `clinical_history_lookup` connectors with demo fake modes,
> specialty filtering, and the Pendientes-first format modeled on the product owner's own dashboard).
> This doc stays as the original design thinking; the HSI-clinical-record tier below
> remains unbuilt, exactly as it argues.

## The idea

Before the consult, the doctor sees **one automatic paragraph** summarizing the patient's
relevant prior history — instead of opening a blank screen and asking the patient to
recount everything from scratch. This is a point-of-care aid, not a clinical record.

## Sources of data — three tiers, ordered by how far away they are from being built

1. **Our own `solicitudes`/attendance record (closest — this repo already has it).**
   Every request this system has processed lives in `solicitudes` with `estado`,
   `turno_dt`, `asistencia` (`db.mark_attendance`), the assigned service/doctor, and — for
   `solicitud_con_orden` services — the AI-extracted `ai_json` fields from the uploaded
   order (`ai_orden.py`'s `campos`). A summary built **purely from this** ("vino 3 veces por
   Cardiología en los últimos 12 meses, la última el 14/06 por control; subió una orden de
   ergometría el 02/07") is buildable **today**, from data we already store, with no new
   integration and no new privacy review beyond what already governs this data.
2. **Uploaded orders (`analizar_orden`/`ai_orden.py`, already built).** The AI reading of
   each order (práctica, diagnóstico when present, médico derivante) is already structured
   JSON sitting in `solicitudes.ai_json` — a summary generator can fold these in as
   "motivos de consulta" across visits without touching anything outside this repo's own
   database.
3. **HSI's own clinical history (`hsi_client.py`, farthest away — do not build first).**
   The actual clinical record — diagnoses, prescriptions, lab results, physician notes —
   lives in HSI, not in this repo's Postgres. Pulling and summarizing THAT is a materially
   different, harder problem: it's third-party PHI under a different system's access
   control, and (per the linked OCR doc below) a meaningful chunk of Pinamar's real history
   is still **handwritten and scanned**, not structured data HSI can hand back cleanly.

## Why not built yet

- **The HSI-clinical-data version is the hard version**, and it's not needed to deliver
  value. A summary from our own booking/order data is a real, useful v1 with none of HSI's
  access-control or handwriting-OCR complexity.
- **The handwritten-history digitization problem already has its own research track**,
  done separately and on 2026-07-24: see
  [`docs/medical-history-ocr-findings.md`](../medical-history-ocr-findings.md) (commit
  `14299a4`). Key findings from that doc, relevant here:
  - Scope is **demand-driven, not a bulk backfill** — histories get OCR'd only for patients
    with a confirmed appointment, because a large share of past patients are deceased,
    moved away, or were imaging-only users with no real clinical narrative worth
    summarizing.
  - The reading model is **Gemini 2.5 Flash** (best price/accuracy for bad Spanish medical
    handwriting; premium vision models cost 5–10× for no accuracy win on this task).
  - **Privacy pipeline**: crop+black out the known patient name/DNI **locally**, before
    anything leaves the building — the booking record already tells us who the patient is,
    so this is "mask a name we already hold," not open-ended de-identification. The redacted
    image is then read in the cloud (Vertex AI preferred over a consumer API key, for
    no-training + regional PHI handling).
  - Residual PII in free-text handwriting (referring doctors, family history) is an honest,
    accepted limitation — a consent/legal-basis question for the clinic, not something OCR
    can fully solve.
  - Rough cost: ~$30/month at demand-driven volume; BYOK provider-key routing was evaluated
    and explicitly dropped as not worth the integration effort at this volume.
  - **This means the "safe v1" below is deliberately the OUR-DATA-ONLY tier — the
    HSI/OCR tier is real future work with its own doc, not something to fold in casually.**

## Sketch of a safe v1 (our data only, no HSI clinical data)

1. **Trigger**: same as the day-queue's existing "today's patients" view
   (`medico_turnos.py`'s doctor appointment listing) — when a doctor opens a patient's row
   for an upcoming/today appointment, compute the summary on the fly (or cache it briefly)
   rather than pre-generating for the whole patient base.
2. **Input**: that patient's own `solicitudes` rows (by `dni`/`solicitante_dni`, the same
   ownership key used everywhere else in this repo — `db.list_by_dni`) — service,
   center, date, `estado`, `asistencia`, and any `ai_json.campos` from uploaded orders.
   **Nothing from HSI, nothing handwritten, nothing outside this repo's own Postgres.**
3. **Generation**: a single small-model prompt (same family as `ai_orden.py`'s existing
   OpenRouter call) turning that structured history into one plain-Spanish paragraph — no
   new AI infra, reuses the `_call_openrouter` pattern already in the codebase.
4. **Where it's shown**: the doctor's existing per-patient view in the day-queue/calendar
   (`medico_turnos.py` + `#mis-turnos`/`#cola`), next to the order preview that's already
   there — not a new screen, an addition to a screen the doctor already opens.
5. **Explicitly out of scope for v1**: anything from HSI's clinical record, anything from
   scanned paper histories. Those stay gated behind the OCR doc's own open questions
   (locate-and-mask engine benchmark, Vertex access path) before they're safe to fold in.

## Open questions for the product owner / the reference-deployment counterpart

1. **Is the our-data-only v1 actually useful enough to ship**, or does it feel thin without
   the real clinical history (labs, diagnoses, prescriptions) that only HSI holds? Worth a
   gut-check with an actual doctor before building.
2. **When (if ever) does the HSI-clinical-data tier get picked up**, and does that decision
   depend on the OCR pipeline's open questions (locate-and-mask benchmark, Vertex access)
   being resolved first?
3. **Who signs off on showing AI-generated clinical summaries to a doctor** — even a
   deliberately narrow, our-data-only paragraph is a clinical decision-support surface, and
   whoever owns clinical governance at the hospital should see the exact wording/format
   before it ships, not after.
