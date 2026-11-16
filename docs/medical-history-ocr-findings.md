# Medical-history OCR → patient summaries — findings & plan (handoff)

> Research/decision note from a Claude session on **2026-07-24**. **Nothing built yet** —
> The product owner asked to record the thinking before prototyping. This is the plan of record for
> digitizing handwritten scanned medical histories into per-patient clinical summaries.

## Goal

Turn Pinamar's **handwritten, scanned** medical histories into structured clinical
summaries attached to each patient — so a doctor sees a usable summary at the point of care.

## Scope decision — demand-driven, NOT a bulk backfill

We are **not** OCR-ing the whole town at once. Summaries are prepared **just-in-time for
patients who have a confirmed appointment**. Rationale for skipping a full backfill:

- Deceased patients and people who moved away.
- **Imaging-only** users — many people used the local service only for tomography/estudios
  (the study that can be done privately here), never for doctor visits, so there is no
  clinical history worth summarizing.
- Occasional users with no meaningful record.

**Volume shape:** likely **fewer pages per history on average, but more total histories** —
and only a small active slice (the booked ones) is ever processed.

## Model — Gemini 2.5 Flash

- **Gemini 2.5 Flash** is the current top pick for **bad Spanish medical handwriting**
  ("médico scribbles"). This was always the intended engine — **no Opus / premium vision
  model** for this. Premium models (Gemini Pro, GPT-4o vision) cost 5–10× for no accuracy
  win on this task.
- Dedicated OCR engines (Mistral OCR ~$2/1k pages, Google Document AI ~$1.50/1k pages) are
  in the same price ballpark as Flash but weaker on hard handwriting — Flash reads *and*
  structures in one pass, so it wins here.

## BYOK (OpenRouter Bring-Your-Own-Key) — **decided: don't bother**

The original idea was to route Google/OpenAI through OpenRouter BYOK to dodge the "5% fee."
Investigated and dropped:

- OpenRouter does **not** take a 5% cut on inference for major providers — it passes token
  prices through at cost. Its revenue is the **~5.5% credit top-up fee** (Stripe).
- **BYOK's own fee is 5% per request**, but **waived under the free monthly request
  threshold** (docs say ~1M req/mo, raised Oct 2025; the product owner saw a 3.1M figure — promo/plan
  tier, doesn't change the conclusion).
- **At demand-driven volume the fee is a rounding error.** Even 100 histories/day × ~5
  pages ≈ 15k pages/month ≈ **~$30/month** at Flash rates; 5% of that is **~$1.50**.
- Not worth a provider-key integration, and BYOK spend lands on the provider bill (not
  OpenRouter credits), which **breaks the per-app daily-cap governance** we use elsewhere.

**Verdict:** use **OpenRouter credits or the Gemini API direct**. BYOK only ever paid off at
the bulk-backfill scale we've now ruled out.

## Privacy pipeline — anonymize locally, then OCR in the cloud

The histories are third-party PHI (Ley 25.326 sensitive health data; the clinic is the data
controller). Plan is to **de-identify locally before anything leaves the building**.

**Key insight that makes this easy:** we **already know who the patient is, from the
booking**. So the cloud model never needs to read the name/DNI — we carry identity from our
own appointment record and re-attach the summary afterward. This turns "de-identify a
medical record" (hard, open-ended) into **"mask a name + DNI we already hold"** (targeted,
reliable).

Split the work by difficulty:

1. **Local = the easy privacy step** (on the local machines, before upload):
   - crop the **demographics header band** (usually the printed/typed part of the form),
   - **search-and-black-out the known patient name + DNI** (we have both from the booking),
   - regex-mask any `XX.XXX.XXX` DNI-pattern numbers.
   - Locate step via local OCR+regex, or a small local VLM to find the header box.
2. **Cloud = the hard reading step:** redacted image → **Gemini 2.5 Flash** → structured
   clinical summary. It only ever sees de-identified clinical content.
3. **Defense in depth:** run Flash via **Vertex AI** (no-training + regional data
   residency) rather than the consumer AI Studio key, so even redacted content is under a
   no-retention contract.

### Honest caveat — residual PII

Free-text handwritten notes can still name third parties (family history, referring doctor,
"derivado por Dr. X"). **Perfect de-identification of handwriting is not achievable** — we
get the demographics reliably and most of the rest. The residual is a **consent / legal-basis
decision with the clinic**, not a technical one: the clinic needs a lawful basis under Ley
25.326 to process the records regardless of masking.

## Rough cost

- Demand-driven, ~15k pages/month worst case → **~$30/month** at Gemini Flash rates.
- BYOK saving on that ≈ **$1.50/month** → not worth the setup.

## Open questions / next steps (not started)

- **Locate-and-mask engine:** benchmark a small local VLM vs OCR+regex on ~10 real scanned
  pages for the redaction step. Header is usually printed → should be tractable.
- **Gemini access path:** **Vertex AI** (no-training, regional) preferred over AI Studio key
  for PHI.
- **Inputs needed to prototype:** sample scanned histories (or a blank form template) + the
  list of identity fields the booking record already stores.
- **Trigger wiring:** kick the pipeline off a *confirmed appointment*, not on ingest.

## Sources

- OpenRouter BYOK docs & pricing (5% BYOK fee, ~1M req/mo free threshold; 5.5% Stripe credit
  fee) — openrouter.ai/docs, /pricing, /blog (2025-10-01 "1M free BYOK requests").
- OCR per-page pricing: Mistral OCR ~$2/1k pages (mistral.ai/pricing), Google Document AI
  ~$1.50/1k pages (cloud.google.com/document-ai/pricing).
