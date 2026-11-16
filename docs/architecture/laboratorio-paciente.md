# Lab results in the patient account — redesign (2026-08-06)

Redesign of the «Resultados de laboratorio» tab (`lab_results.py` + `lab-results.js`)
from the product owner's 2026-08-06 directive. The old tab was a raw read-through: it listed only
the analytes that happened to match `chart_summary._SERIES_DEF` and silently dropped
every report without a catalog match. The redesign turns it into a report-first view
with an AI reading per report, a staff-editable sensitivity filter, and a second
"Evolución" view with trend charts — all cached so the steady state costs zero AI
calls.

## What the patient sees

**List view (default).** One group per report, newest first:

- **Date** in words (canonical `static/platform/dates.mjs` `nacimiento` formatter — a 20-year-old report
  still shows its year).
- **Study type** — a short human label ("Análisis de sangre completo", "Perfil
  tiroideo") classified by the AI pass, deterministic fallback from the `servicio`
  field and analyte composition.
- **A cautious one-line summary** ("Todos los valores dentro del rango de
  referencia." / "Hay valores para revisar con tu médico en la próxima consulta.")
  with a tone class `ok | revisar`. NEVER alarming language, never a diagnosis,
  never "anormal"/"peligroso" — the existing footer note (out-of-range ≠ diagnosis,
  book a clinician) stays.
- The determinations table under each group as today (Determinación · Resultado ·
  Valores de referencia · Estado) — but now including **every** extracted
  determination, not only catalog ones. Non-catalog analytes show the reference
  range printed on the report itself when the AI extracted one.

**Sensitive reports** render as date + "Tu médico tiene tus resultados." + the
existing book-a-clinician link. No type, no summary, no values — the redaction
happens **server-side** (the values never cross the wire), not with CSS.

**Evolución view.** A second view inside the same tab (segmented toggle, copying
the `admin-patient-summary.mjs` Pendientes/Historia tab control — "a new control
copies the existing control"). Line charts per catalog series with the shaded
reference band, out-of-range-first ordering, latest value + unit as the title —
same SVG approach as `admin-patient-summary.mjs`, restyled patient-side. Charts are
built **on demand when the patient opens the view**, purely in code from cached
extracted values — the AI never draws a point (repo invariant).

## Data structure

New table (created by the frozen baseline, `alembic/baseline/0001_schema.sql`, and by revision `0007_existing_install_shape` on older installs):

```sql
CREATE TABLE IF NOT EXISTS lab_report_analysis (
  id           BIGSERIAL PRIMARY KEY,
  dni          TEXT NOT NULL,
  report_hash  TEXT NOT NULL,   -- sha256 of the canonical report content
  prompt_hash  TEXT NOT NULL,   -- sha256 of both resolved prompt texts
  fecha        TEXT,
  sistema      TEXT,
  tipo_estudio TEXT,
  resumen      TEXT,
  tono         TEXT,            -- 'ok' | 'revisar'
  sensible     BOOLEAN NOT NULL DEFAULT FALSE,
  valores      JSONB,           -- extracted determinations (see shape below)
  modelo       TEXT,            -- model id, or 'fake'/'sin_clave_api'/'fallback'
  created      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (dni, report_hash)
);
```

`valores` shape — a list of determinations, catalog and non-catalog alike:

```json
[{"clave": "glucemia_mg_dl",        // canonical _SERIES_DEF key, or null
  "nombre": "Glucemia",             // display label (from catalog or the report)
  "valor": 118, "unidad": "mg/dL",
  "rango": [70, 110],               // catalog range, or the report's own printed range
  "fuera": true}]
```

- `report_hash` = sha256 over the canonical JSON of the connector entry
  (`fecha`, `sistema`, `servicio`, `texto`, `valores`). Same report re-served by a
  connector → same hash → cache hit. A corrected report (any content change) is a
  *different* report and gets its own row.
- `prompt_hash` covers the resolved text of BOTH prompts (`laboratorio_paciente` +
  `laboratorio_sensible`). Staff edits a prompt → stored rows' `prompt_hash` no
  longer matches → rows are re-analyzed lazily, batch-capped (below). Rows are
  UPSERTed on `(dni, report_hash)` so a re-analysis replaces, never duplicates.

## The AI pass (one, batched, cached)

`lab_analysis.py` owns the pipeline. On `GET /api/account/lab-results`:

1. Collect `tipo == "laboratorio"` entries from `chart_summary._historia_clinica`
   (same three connectors — local_history, alepo, marqui; same degradation
   contract).
2. Hash each report; load cached rows for the DNI in one query.
3. Reports with a fresh row (matching `report_hash` + `prompt_hash`, `modelo` not a
   fallback marker) are done — **zero AI cost**, the typical steady state.
4. Uncached/stale reports go to ONE batched call (Gemini Flash via
   `ai_client.call`, same pattern as `chart_summary.generate`): up to
   `_BATCH = 10` reports per call, at most `_MAX_BATCHES = 2` calls per request.
   More pending than that → analyzed on subsequent opens; the response carries
   `analisis_pendiente: N` so the frontend can say the reading continues.
5. The single call does ALL four jobs per report — classification, cautious
   summary, sensitivity verdict, and extraction of determinations from the OCR
   `texto` (canonical keys where they match `_SERIES_DEF`, report-printed ranges
   otherwise). One call instead of four.
6. PII: `pii.for_dni(dni)` scrub before the call, restore on the way back — same
   discipline as the chart summary. The raw report text leaves; the identity does
   not.
7. Cost accounting: `ai_costs.record("laboratorio_paciente", ...)`.

**Deterministic fallback** (`TURNOS_FAKE_AI=1`, no key, model/parse failure): type
from the `servicio` field + analyte-composition heuristic, mechanical summary from
code-computed out-of-range counts, `sensible=False`, and **only structured catalog
values** — nothing derived from raw OCR text is ever shown unvetted (see
robustness §3). Fallback rows persist with `modelo` = the reason and are retried
(within the same batch caps) on the next open once a key is available.

## Sensitivity filter

- **`laboratorio_sensible`** is its own staff-editable prompt in `prompts.py`
  (canonical English, edited in Spanish — same as every other prompt). Default
  criteria: oncology markers / biopsy & pathology results, HIV and STI serology,
  pregnancy tests, genetic tests, toxicology — anything life-changing, private or
  embarrassing to learn from a website instead of a doctor.
- **`laboratorio_paciente`** (classification + summary instructions) is the second
  new prompt; both appear automatically in the panel's prompt editor.
- **Code-side floor** (like intake's red-flag floor, not staff-editable): keyword
  match on the report text and extracted labels — `vih`/`hiv`, `biopsia`,
  `anatomía patológica`, `marcador tumoral`, `toxicolog` — forces
  `sensible = TRUE` even if the model says otherwise.
- A sensitive report's values are excluded from the wire payload AND from the
  Evolución series.

## Robustness evaluation (the product owner asked explicitly)

1. **Reports the catalog doesn't know.** Old behavior dropped them wholesale; now
   every lab report renders, catalog or not. Non-catalog analytes are listed with
   the report's own printed range; only catalog keys chart (closed `_SERIES_DEF`
   stays the chart gate — no AI-invented axes).
2. **Duplicate reports across connectors.** The same study digitized by two
   systems produces two rows today (alepo vs marqui eras barely overlap; real risk
   is low). `report_hash` dedups exact duplicates; near-duplicates (different OCR
   of the same paper) are out of scope and documented as such.
3. **Raw OCR text can contain third parties or devastating findings.** Invariant
   kept from the old module and now load-bearing: the patient NEVER sees raw
   `texto` — only structured connector values (curated, inherently non-sensitive
   catalog) or AI-vetted output whose same single response carried the sensitivity
   verdict. Extraction and sensitivity are atomic — there is no state where
   AI-extracted values exist without a sensitivity verdict. AI down → degrade to
   exactly the old behavior.
4. **Prompt edits.** Lazy re-analysis, batch-capped: an edit never fires an
   unbounded backfill. Cost spike is bounded at 2 calls per patient-open.
5. **Unit variance (mg/dL vs mmol/L).** The prompt instructs canonical keys ONLY
   when the report's unit matches the catalog unit; otherwise keep the analyte
   non-catalog (listed, not charted, report's own range). No silent conversions.
6. **PDF sources.** A future PDF connector OCRs/attaches `texto` exactly like
   alepo/marqui fixtures do — the pipeline is already text-first, so a new source
   needs zero changes here. Structured sources (HSI lab feed someday) land in
   `valores` and skip extraction cost entirely.
7. **AI hallucination containment.** Chart points come only from code-extracted
   structured values plus AI-extracted values whose `clave` the code validates
   against `_SERIES_DEF` (wrong key → kept as non-catalog text row, not charted…
   and a numeric sanity clamp: value must parse as float). Summary tone is a
   closed enum; unknown → `revisar`.
8. **Language.** The reading is stored ONCE, in Spanish (the deployment's
   patient language), exactly as the model wrote it; the visitor's language is
   resolved when the report is shown (`app/ai/lab_language.py`, `i18n.active()`),
   never when it is stored — no translation is written to `lab_report_analysis`.
   - *Model readings* (`tipo_estudio`, `resumen`): the GET reads the translation
     cache (`translate.cached_text`, key `labs.reading.<report_hash>.<field>`,
     no DNI in it, the source hash decides validity) and makes NO model call.
     The cache is filled by the pass that made the reading
     (`lab_analysis._calentar_traducciones`) and by `ops/translate_warmup.sweep`
     (`lab_language.entradas_barrido()`). A reading not translated yet shows in
     Spanish. The text is the one cautious sentence the prompt asks for (no
     names), so the pii scrub is not applied: its placeholders would poison
     the shared cache.
   - *Fallback readings* (`modelo` fake / sin_clave_api / fallback): nothing to
     translate, the text is rebuilt on display with `i18n.t_or` from the number
     of values out of range (`lab_analysis.fallback_textos`).
   - The closed catalog's determination names come from
     `account.mine.lab.analyte.<clave>`; names the model extracted from the
     paper and the pack's `servicio` stay as printed.
   - Sensitive reports are never shown nor translated. Deleting an uploaded
     result deletes its `labs.reading.<hash>.*` rows with the reading.
9. **Identity.** Session-only, unchanged. The endpoint never accepts a DNI.

## Patient-uploaded results

The patient can upload a lab result the hospital has not digitized (photo or
PDF) from the same Laboratorio screen. The file goes to `patient_lab_uploads`
(bytes included) and is transcribed once by `app/ai/lab_upload.py` — the same model
path the referral reader uses, with a transcribe-only prompt: every
determination, value, unit, reference range and the report date, no
interpretation. The clinical reading on top of that text is the ordinary
`lab_analysis.py` pass, unchanged. With no AI available the row is stored with
`sin_lectura` and empty text: it appears in the account right away and gets its
reading later. Like the referral image, the file travels to the provider as is
— the deliberate PII exception documented in `legal.md`.

Those rows come back into every reader through the `patient_uploads`
clinical-history connector, flagged `tercero: true`: patient-supplied, not
hospital-sourced. The patient sees "Subido por vos" next to the report; the
doctor's chart summary presents it as "aportado por el paciente, sin
verificar" and its provenance line reads "Aportado por el paciente: N
registros".

`POST /api/account/lab-results/upload` (multipart: `archivo`, optional
`patient_dni` — only a dependent already linked to the account) and
`DELETE /api/account/lab-results/upload/{id}` (only whoever uploaded it) are
gated exactly like the GET: identity from the session, never from the body.

**«Delete» deletes, and the file expires (schema review №11, 2026-10-25).** The
patient's delete sets `archivo`, `texto` and `filename` to NULL in the same
statement as `deleted=now()` (`db_clinical.patient_lab_upload_delete`): the emptied row
stays as the record that it happened and holds nothing clinical. The cached AI
reading of that report goes too (`lab_results.borrar_resultado` →
`db_clinical.lab_analysis_delete`, keyed by the same `report_hash` the view uses via
`patient_uploads.entry_for`), so no summary or value of a deleted report
survives. Without a delete the file bytes (only the bytes: the transcription is
what the doctor reads) expire after `ops.housekeeping.lab_upload_retention_days()`:
`TURNOS_LAB_UPLOAD_RETENTION_DAYS` when set, else the referral files' own
`TURNOS_REFERRAL_RETENTION_DAYS` (default 180) — so `TURNOS_REFERRAL_RETENTION_DAYS=0`
(the municipal installation: «do not clean») keeps them as well, and any value
<=0 never purges. The sweep is `db_clinical.purge_lab_upload_files`, one of the DB-only
purges of the housekeeping job (`tick_purges()["lab_upload_files"]`), idempotent
and LIMIT-bounded. A restored old dump brings back what was in it, but the
age-based sweep removes every file past the window again on its next pass (no
journal needed); a patient's delete made after that dump is not recoverable
from the database alone — that is the law-25326 erasure ledger's job. Other
derived copies (the doctor's pre-generated `patient_summaries`) are not touched
by this delete; they are outside this slice. Proofs: `tests/test_lab_upload_erasure.py`.

## Provider link

The provider's own page (`https://laboratorio.pinamar.gob.ar/ingresos/hc-paciente/false/<DNI>`)
is reachable from the section by a button «Ver resultados del laboratorio»
(`account-labs.mjs` `montarProveedor`, shown with or without reports, above the
upload control). The button is a plain link to `GET /account/lab-results/open`
(`lab_results_router.py`), never to the provider: the route reads the DNI of the
signed-in account (a DNI in the request is ignored), builds
`<base>/<DNI>?md5=<sig>&expires=<unix>` (`app/ai/lab_link.py`) and answers 302 with
`Cache-Control: no-store`; no session → the sign-in entry. The provider's nginx
verifies `secure_link_md5 "$secure_link_expires$uri KEY"` on `/ingresos/hc-paciente/`
only: `sig = base64url(MD5("<expires><uri> KEY"))` — unpadded, `+`→`-`, `/`→`_`;
`<uri>` is the path without the query; `expires` = now + 5 minutes. Check by hand:
`printf '%s%s %s' "$expires" "$uri" "$KEY" | openssl md5 -binary | openssl base64 | tr '+/' '-_' | tr -d '='`.

Installation environment (process env or the env file; never a DB setting — it is a secret):

| Variable | Meaning |
|---|---|
| `TURNOS_LAB_LINK_SECRET` | the shared key (32+ random bytes, base64url, the same text as nginx's `KEY`). Unset = no button and the route answers 404 |
| `TURNOS_LAB_LINK_BASE` | the address without the DNI; default `https://laboratorio.pinamar.gob.ar/ingresos/hc-paciente/false` |

Only the salud installation carries the key (password-vault item «Human Rounds —
laboratorio secure_link (salud)»); pinamar.humanrounds.org and the demo have none.
Proofs: `tests/test_lab_link.py` (signature against the nginx formula, 5-minute
expiry, another DNI impossible, no key = no button), `tests/lab-results.test.mjs`.

## API

`GET /api/account/lab-results` →
```json
{"informes": [ {"fecha","tipo_estudio","resumen","tono","servicio","items":[...]}
             | {"fecha","sensible":true} ],
 "series": [ {"id","label","unidad","rango_ok","puntos":[["2024-05-01",118],…]} ],
 "analisis_pendiente": 0,
 "fuentes_estado": {...}, "clinico_href": "/d/...",
 "laboratorio_href": "/account/lab-results/open" | null}
```

Tests: `tests/test_lab_results.py` (caching = second request fires zero AI calls,
prompt-edit invalidation, server-side sensitive redaction, keyword floor,
deterministic fallback shows only catalog values, batch caps, AI-extracted points
join series, sensitive reports excluded from series) +
`tests/lab-results.test.mjs` (view toggle, summary line + tone, sensitive row,
chart render with band, XSS escaping).
