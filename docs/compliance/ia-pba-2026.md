# AI governance evidence for Buenos Aires province

Status: product controls implemented locally; **no claim of legal compliance,
municipal adhesion, homologation, registration or notification**. Reviewed
2026-10-14. Owner of institutional actions: **municipal authority**. The product
exports facts, never files or signs a provincial submission.

## Normative map and provenance

**VERIFIED official text**: [Decree 742/2026 register page](https://normas.gba.gob.ar/ar-b/decreto/2026/742/604136)
(publication 2026-07-14; promulgation 2026-07-06), [operative text](https://normas.gba.gob.ar/documentos/B11QWLI4.html),
and [official annex](https://normas.gba.gob.ar/anexos/descargar/20471dYV.pdf).
The two resolutions are mentioned by the decree; their operative duties below
are **SUMMARY-ONLY**, from the hospital slide/task brief. No full Res. 4/25 or
Res. 9/25 annex/risk criteria was obtained. Those summaries are not substituted
for an authoritative text or a legal determination.

| Norm / provenance | Obligation or principle | Product control | Remaining gap | Owner |
| --- | --- | --- | --- | --- |
| Decree art. 2, VERIFIED | Ministry of Government / Digital Government is the authority | Export names the authority; human notification attestation | Determine the competent office and submission channel | Municipal authority |
| Decree art. 3, VERIFIED | Record characteristics, current state and modifications; no deadline stated | `/admin/ai-register`, JSON dossier and printable HTML | Obtain registration requirements and submit/sign; product does not attest registration | Municipal authority |
| Decree art. 5 + annex art. 7, VERIFIED | Municipal adhesion is voluntary; provincial scope is distinct | No UI claim that the municipality has adhered | Determine applicable local instruments and whether to adhere | Municipal authority |
| Decree art. 6, VERIFIED | Resolutions 4/25 and 9/25 apply supplementarily to unregulated cases | Missing-text register below | Obtain the operative texts and reconcile institutional duties | Municipal authority |
| Annex art. 2, VERIFIED | Human centrality; traceability; data protection; oversight; fairness; accountability; sovereignty; innovation; interoperability; system/data quality; technological reasonableness | Inventory, PII descriptions, prior notices, kill switch, audit trail, explicit human decisions | Institutional accountability, accessibility/bias assessment, procurement and data/provenance validation | Product for controls; municipal authority for institutional acceptance |
| Annex art. 3, VERIFIED | Prior intervention for development/use/contracting/renewal; intervention on substantial modifications including function, scope, training data/provider | Conservative pending status for model/provider/prompt/function/data-scope changes; actor/time and safe fingerprints | Request intervention before the next renewal/contracting and substantial changes; local audit is not authorization | Municipal authority |
| Annex art. 6, VERIFIED | Multidisciplinary rights/service/data/bias/labour/oversight/environment assessment | Provisional risk file for every function | Hospital validates actual impact, data representativeness, staff procedures and energy/provider evidence | Municipal authority |
| Resolution 4/25, SUMMARY-ONLY | Generative-AI guidelines for provincial administration | Product controls support governance | Full operative text unavailable; no article-by-article conformity finding | Municipal authority |
| Resolution 9/25, SUMMARY-ONLY | Unacceptable/high/limited/minimal risk; high-risk assessment/mitigation; notice before AI interaction; registration; data governance, bias control, auditability and human centrality | Conservative high/limited self-assessment, notices and oversight tests | Annex/risk thresholds unavailable; clinical functions remain high until hospital assessment; official category acceptance unresolved | Municipal authority |

## Product inventory and scope

`app/core/ai_register.py` owns functions, Spanish purpose, data categories, PII
handling, oversight, provisional risk/rationale, notice key/surface, kill
switches, prompt links, and deployment-date evidence. `app/core/ai_policy.py`
resolves the models used both by callers and by exports. The voice model resolves separately from its existing voice configuration;
Fal queue/upload destinations share the transport constants. Portrait processing
is deterministic local Pillow work, not an AI function. Transport profile and
host resolve from runtime configuration; OpenRouter's downstream processor,
jurisdiction and retention are not verified. MCP's connected assistant has its
own provider/model, controlled outside this product and **not known** here; the
inventory's configured transport describes the product-side readers it can
invoke, not that external assistant.

The AST inventory test covers direct inference and shared wrapper call sites,
including locale generation. Diagnostic benchmarks that delegate to these
registered functions are tooling, not additional deployed functions. A new
inference call site without a register mapping fails the test. A new network
client must remain subject to the existing single-client architecture gate.

Deployment dates are **unknown**, not inferred from git, document dates or
installation age. The first dossier read stores a baseline. Later environment,
code-function and data-category changes are recorded at **observation time**,
with `system:observed`; this is not a claim about the deployer or exact deployment
time. Settings and prompt edits log the authenticated operator in the same
transaction as the write. Audit failure rolls that write back. Provider/model
and prompt content are represented in the log by SHA-256 and byte length; no
patient data, secrets or full prompt text is copied. A later observation may
also record an effective transition previously evidenced by a settings write;
both are evidence records, not two government submissions.

The global kill switch is `TURNOS_AI_DISABLED=1`. It stops the shared inference
client even when a caller tries an explicit key. It does not revoke data already
sent or cancel an in-flight call. Fake-mode and module/feature switches are
listed with their actual narrower scope. INTAKE_ENABLED=0 changes the inherited
default, not explicit doctor overrides; it is not a universal interview kill —
and since 2026-10-21 the inherited default itself is OFF: the interview runs
only where a doctor explicitly opted in (or a deployment turns the default on).
`TURNOS_SUMMARY_PREGEN=0` stops only the
daemon; it is not a kill switch for on-demand summaries. Fake mode is not a universal inference kill: the guarded fictional seed can
explicitly request a real reading. TURNOS_AI_DISABLED still stops that call.
The profile extractor is a draft-only helper; automatic close-time persistence has been removed.

The dossier has `características`, `estado_actual`, and `modificaciones`.
Its schema is a product preparation artifact, **not a verified provincial upload
format**. The printable HTML is suitable for printing to PDF; no PDF service or
submission integration was added. The notification action requires an
administrative reference and a date, records the authenticated admin, and is
only a **human attestation**; no external notification is sent or verified.
Sistema has one row with the pending count, or an explicit unavailable state.
No email or other alert channel was added.

## Transparency and privacy

Before interaction/content: web chat; WhatsApp's first reply (including auth and
media failures); interview introduction; order upload and every booking order
step; patient labs before fetch/upload; staff summary and consultation; account
MCP connection before showing credentials; DNI photos; raw voice dictation before microphone capture; prompts/wiki/translation
editors and inference probe. ES/EN keys live in `locales/{es,en}/ai.json` with
Spanish fallbacks. Other locales were not generated because this slice forbids
external AI calls. An old catalog may use the Spanish fallback until a later
authorized translation run.

WhatsApp delivers the notice before first inference, and the whole-document
notice before each attachment read. A failed notice send stops AI processing of
that message and stores no delivery receipt. Account/authentication and
deterministic reminder replies do not require an AI call. Delivery evidence
is the existing transport's success result, not proof that a person read it.

Text uses `app/core/pii.py` reversible pseudonymisation where patient data exists;
generic institutional text is filtered for recognizable identifiers too.
**Whole-document exceptions**: referral, DNI photo, uploaded laboratory file,
interview image; full voice audio goes to Fal for transcription before text
filtering is possible. Fal is an existing separate transport, now covered by the
global kill switch, explicit voice switch and fake-mode egress guard. The filter cannot erase identity inside those images, detect
every misspelled/third-party name, or prove downstream provider confidentiality.
Both legal documents publish the exact register-owned function annex, including
when editable body overlays exist. The standard AI section is also refreshed at render time without changing the
stored institutional overlay; other prose remains editable. The municipality
must review any conflicting claims placed outside that section.

## Human oversight findings and remedies

1. **Model-selected cancellation executed immediately.** Fixed: the shared tool
   returns `/request/<id>` for a person to confirm. Applies to web, WhatsApp and
   MCP. No model-supplied confirmation boolean can bypass this boundary.
2. **Assistant booking could reach automatic approval for `auto_confirmar` or
   same-day opening services.** Fixed: those requests return the service booking
   page for an explicit human action. Ordinary assistant-created requests remain
   pending; staff approve/reject/reassign through existing authenticated actions.
   Automatic confirmation on the ordinary web page still follows the patient's
   explicit submit, independent of AI opinion.
3. **Interview-close extraction wrote durable health-profile data with no review.**
   This was conversational memory, not an HSI clinical evolution; it was
   nonetheless persistent health content and has been treated conservatively.
   Fixed: extraction returns a draft; close no longer invokes it. A clinician can
   edit profile fields and opt in to saving them with the existing confirmation
   action. Existing stored profiles are retained, not reclassified as reviewed.

Clinical summaries/triage/lab analysis are marked derived outputs and do not
write clinical records or decide appointment status. Prescriptions/orders are
composed only after clinician/admin confirmation and must be reviewed before
use/signature. No HSI enablement or write policy was changed. Runtime and
negative-control tests cover the three findings; broader real-DB and browser
acceptance remains separate from source/fixture proofs.

## Municipality checklist

- [ ] **Municipal authority:** obtain the full Res. 4/25 and Res. 9/25 annex/risk criteria.
- [ ] **Municipio:** determine applicable provincial/local instruments and decide voluntary adhesion; record its instrument if adopted.
- [ ] **Municipal authority:** request Digital Government intervention before the next development/use/contracting/renewal and substantial modification.
- [ ] **Municipal authority:** obtain the live registration channel, format and requirements; submit initial characteristics/current state/modifications.
- [ ] **Municipio:** designate the competent human signer and accountable officer. The institutional coordinator does not gain signing authority from this code.
- [ ] **Hospital / municipio:** validate each provisional risk file; high clinical/identity risks remain high until formal assessment and mitigation.
- [ ] **Municipio:** review processor contracts, jurisdiction, retention, lawful processing, whole-image exceptions and editable legal overlays.
- [ ] **Hospital:** validate bias, Spanish accessibility, provenance/quality, escalation, staff training, labour effects and environmental evidence.
- [ ] **Authorized admin:** record each actual government notification's administrative reference/date; a local checkbox/action is not receipt by the authority.
- [ ] **Municipal authority:** establish an ongoing change-review owner, refresh deployment evidence and retain official receipts. Do not invent a decree deadline.

## Missing verification

No external AI/provider calls, commits, deployment, government filing, adhesion,
homologation or real patient traffic occurred. Model/provider status is resolved
locally without a provider probe. Per-installation first deployment, downstream
model routing, provider retention/jurisdiction, institutional ownership/signing
and government acceptance are unverified. The continuation's sandbox refuses
PostgreSQL socket/TCP connections and Chromium's required macOS bootstrap
service. The browser-free ASGI/ledger acceptance test remains unexecuted on a
real database; the full Node command reports 143 Chromium launch failures.
Focused source/fixture tests pass. See `slice8-final-report.md` and `evidence/`.
Automated/fixture checks do not establish legal compliance.
