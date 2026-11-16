# Legal texts and PII redaction

Two things that only make sense together: what the site **publishes** about how
patient data is handled (`legal.py`), and what the code **actually does** to that
data before it leaves the building (`pii.py`). If one changes, the other has to.

Origin: The product owner, 2026-08-03 — "create the terms and conditions, make them editable
in the admin, and make sure there are clauses about us doing all our AI stuff on
the external servers … make sure we don't send personally identifiable
information when not needed, such as for chat, such as for the medical history."

---

## `legal.py` — the two documents

`terminos` and `privacidad`. Both are served at `/terms.html` and
`/privacy.html`, which stay ordinary static shells (head, nav, footer, styles);
the whole body is one placeholder, `{{legal.terminos}}` / `{{legal.privacidad}}`,
resolved by `legal.render()` inside `site_identity.render_page()`.

**Overlay, exactly like `catalog.py` and `prompts.py`.** The canonical text
lives in the code; a `legal_docs` row (migration `003_legal_docs`) overrides it;
"restore the original" is a DELETE of that row, never a rewrite.

**The body is one editable text, not 40 i18n keys.** Before this, the legal copy
was split across `terms.s3.p1`, `privacy.s1.p2_bold` and friends — translatable
but not editable. A legal text is written and corrected by the hospital, so it is
now a single mini-markdown body per document:

| Escrito así | Sale así |
|---|---|
| paragraph before the first heading | `<p class="lead">` |
| `## Título` | `<h2>` |
| `- ítem` | `<ul><li>` |
| `> texto` | `<div class="box">` |
| `**negrita**`, `` `código` ``, `[texto](/ruta)` | `<b>`, `<code>`, `<a>` |

Everything is HTML-escaped first, and `_safe_href` allows only inert schemes
(`http(s):`, `mailto:`, `tel:`, `/`, `#`) — a `javascript:` URL in a panel-editable
document would execute in the reader's session.

**The installation name is NEVER written literally.** `{{sitio.brand}}` renders
"Pinamar Turnos" on Pinamar and "Human Rounds" on the demo. There is exactly ONE
document for both installations: `docs/demo-installation.md`'s rule that the demo
is the same application applies here too. The demo difference is two things and
nothing else — the brand placeholder resolves differently, and
`demo_installation.enabled()` prepends the `legal.demo_notice` callout saying the
document does not govern the demo. The body itself also carries a permanent
clause ("no se aplica a las instalaciones de demostración") so the statement
survives even where the banner is not shown.

**The institutional version (2026-10-27, d1147202).** The default texts are the
prototype's: they call the service «un prototipo en desarrollo» and decline
responsibility for loss or leak of data — right for pinamar.humanrounds.org and
the demo, wrong for salud.pinamar.gob.ar (a municipal service with real
patients). An installation that names its **data controller** gets the
institutional texts instead: `SITE_ID_DATA_CONTROLLER` (or `data_controller` in
`sitio.json`) → `legal.institucional()` → `legal.original()` returns
`_TERMINOS_INSTITUCIONAL` / `_PRIVACIDAD_INSTITUCIONAL`. The switch is a
deployment key, not a domain check and not a Settings field: two sites can share
one pack, so each installation's env decides (`site_identity.DEPLOYMENT_KEYS`).
On salud: `SITE_ID_DATA_CONTROLLER="Municipalidad de Pinamar — Secretaría de Salud"`.

What the institutional version changes, and only that:
- no «prototipo», no «tal cual», no «exclusiva responsabilidad», no disclaimer of
  loss or leak; `{{sitio.data_controller}}` «responde por la seguridad de tus datos
  conforme a la Ley 25.326» (security, privacy §7);
- the controller named in the lead of both documents, in the rights channel
  (privacy §8 «Cómo pedirlo») and in a new privacy §9 «Responsable de los datos y
  marco legal» (Ley 25.326 art. 8, Ley 26.529 arts. 2, 14, 18, whether giving data
  is mandatory); terms §1, §3, §6, §8, §9 rewritten for a public service;
- the points counsel still has to decide are NOT in the public text (29.10, after
  the salud pre-window review: the pages used to show bold `[Asesoría Letrada: …]`
  placeholders, 2 in terms and 5 in privacy). The text says what we know —
  the installation's data (`{{sitio.data_controller}}`, the contact of the site) —
  and otherwise a neutral sentence (e.g. privacy §9 sends the access / rectification
  / suppression requests to §8). The seven questions live in
  `legal.PENDIENTES_ASESORIA` (municipal liability for interruptions, jurisdiction,
  incident notification, the controller's address for requests, RNBD registration,
  the art. 12 basis for the international AI/e-mail transfers, what counts as
  historia clínica) for the hand-over to counsel. As a belt, `legal.sin_marcas()`
  strips any `[Asesoría Letrada: …]` marker from a body before render
  (`body_html`), so a DB override or a pasted draft cannot publish one either.
  `test_legal.TestPaginasSinRelleno` pins it: no public legal page, in any
  configuration (with or without a controller, every language), contains
  `[Asesoría` or a `[… …]` placeholder. Terms and privacy say the same thing:
  change one, check the other.

Every factual section — what data, the access log, the AI (§5, refreshed from
the code by number exactly as in the prototype), the technical logs — is the
prototype's section copied by number (`_seccion`), so the two versions cannot
drift; `_reemplazar` fails at import if a replaced paragraph changes.
`test_legal.TestContenidoInstitucional` re-runs every factual pin against the
institutional text.

**Translation.** The canonical body is in the pack's language; other languages go
through `translate.localized_text("legal.<doc>", …)`, the same content cache the
preparations use. If the translation comes back with a different set of
`{{sitio.*}}` tokens than the source, it is **discarded** and the canonical text
is served: a legal page in the wrong language beats a legal page that lost the
name of the institution mid-sentence.

**Admin.** `#legal` ("Términos y privacidad", under Sistema), `admin-legal.js`,
API `/api/admin/legal` — `solo_admin`, GET list / GET one / PUT body / DELETE
restore. Save and restore reuse the controls of «Instrucciones de la IA» (the
accent `Guardar` button, the `Restaurar el original` link) because it is the same
job; `tests/admin-legal.test.mjs` pins that pair.

**Platform-core, not an app module.** A deployment without `turnos` still has
terms and a privacy policy, so `legal` sits in `PLATFORM_ROUTES` /
`_PLATFORM_ADMIN_SECTIONS` next to `site_identity` and `branding`.

**The privacy policy describes the clinical access log (2026-10-23, K10).**
Section 4 says what `app/core/access_log.py` does and nothing more: who on staff
opened which health data, when and whether it was allowed; lists once a day;
the DNI stored as a keyed hash; the log cannot be modified and is purged only
at the installation's retention (ten years by default); a doctor only opens the
health data of their own patients, and only staff granted the explicit
«todos los pacientes» permission (heads of service, audit) open anyone's, every
such read recorded too. Change the log, change the clause —
`test_legal` pins the phrases against `access_log.retention_days()`. An
installation that edited its policy from the panel keeps its own text: the new
clause reaches it only after «Restaurar el original» or a manual edit.

---

## `pii.py` — what leaves the building, and what doesn't

Every AI call uses `ai_client.call()` and the active inference profile.
`cloud_shared` uses OpenRouter; hosted or self-hosted profiles use the configured
endpoint. A hosted model provider may be in another country. The clinical
content has to go out — that is what the model is asked to read. The identity
does not.

**Reversible pseudonymisation, not deletion.** One `Scrubber` per request. Each
identifying value becomes a stable label inside that request (`[nombre 1]`,
`[documento 1]`), the model works on labels, and `restore()` puts the real values
back before anything is persisted, executed or shown. The assistant can still say
"el turno de Ana" without the provider ever learning that an Ana exists. The map
lives in memory for the duration of one request; nothing is stored.

Two detection passes, in order:

1. **Known values** — the session identity plus every dependent
   (`for_account`), or the patient behind a clinical DNI (`for_dni`): name,
   surname, DNI, phone, email, address. This is the reliable half; it replaces
   exact values and guesses nothing.
2. **Generic sweep** — email, then ONE numeric pass classified by digit count
   (11 → CUIL, ≥9 or leading `+` → phone, 7–8 → DNI). One pass, not three
   competing regexes: in a clinical text a DNI fits any phone pattern and a CUIL
   contains a DNI, so ordering decided the label. Dates (`12-05-2026`) and
   thousands-grouped lab values (`1.250.000`) are explicitly excluded — masking
   those would destroy the very data the model has to read.

A single-word surname is only replaced when it appears as a proper noun (initial
capital or all caps, which is how HSI and the front desk write it). "Molina" the
surname is masked; "molina" the common word is not.

### Where it is wired

| Surface | Treatment |
|---|---|
| Booking chat (`chat_agent`, site + WhatsApp) | messages, tool arguments and tool results scrubbed on every call; reply and tool arguments restored |
| Pre-consult interview (`modules/intake/interview_agent`) | same, seeded from the interview's DNI |
| Chart summary (`chart_summary`) | the `DATOS` blob scrubbed, the model's JSON restored |
| Profile draft (`health_profile`) | transcript/previous profile scrubbed; draft restored, never saved automatically |
| Consultation wording (`consult_compose`) | patient/practitioner identifiers and text scrubbed; reply restored before schema coercion |
| Lab reading (`lab_analysis`) | report text scrubbed/restored by DNI |
| **Whole image/PDF: referral, DNI, laboratory upload, interview attachment** | **sent as is, deliberately** |

These whole-document images are the deliberate exceptions to text scrubbing. Reading it whole is
the entire point, and blanking parts of a photo would make it impossible to
verify the order is valid. That is stated in plain words in the privacy policy —
which is the reason both halves live in the same doc.

The `system` message gets only the known-value pass, never the numeric sweep: it
carries our own instructions (where a number can BE the instruction) *and*
`chat_agent._memory_block`, the summary of this patient's earlier conversations,
where the name does have to go.

### The honest limit

The filter is automatic. A misspelled name, or a name inside OCR'd text, can get
through. The privacy policy promises "esfuerzo razonable", not a guarantee,
because that is what this is. Do not upgrade that wording without upgrading the
code.

---

## Rights channel and photos (2026-10-25)

Section 8 of the privacy policy names how a data subject exercises access /
correction / erasure and the Ley 25.326 deadlines (10 calendar days for access,
5 working days for correction/erasure). The channel is NOT a new setting: it
points to the contact data each centre already publishes on `/centers`, and the
text never writes an address, phone or a person in charge — the controller of
the database is an open question to the hospital. Section 1 states the profile
photo (stored under the DNI, no automatic deletion), the Gravatar lookup (MD5 of
the email, `TURNOS_GRAVATAR_OFF`) and the photo attached to the interview
(stored as a data URI in `chat_messages`, not purged because its key is
`interview:<id>`). **Only the AI section and the technical-records section are
force-refreshed on an edited body** (`document()`); an installation that edited
the policy from the panel gets these additions only after «Restaurar el
original». If a clause in sections 1/7/8 changes with the code (a retention
purge for the photos, a Gravatar switch), change the text and
`test_lo_dicho_de_gravatar_es_lo_que_hace_el_codigo` together.

## What an erasure request does not reach (2026-10-25)

The «Lo que no se borra a pedido» block of section 8 names four things: the
clinical access log, the copy held in HSI (the province's record — corrected or
erased only through the HSI administrator, we pass the request on), the
encrypted backups (the erased data stays in older archives until rotation, whose
window is the installation's `TURNOS_BACKUP_RETAIN_*` setting; after a restore
the erasures recorded in the ledger — `app/core/subject_erase.py`,
`app/core/subject_requests.py` — are applied again) and the external providers.
`test_lo_que_no_se_borra_a_pedido_nombra_el_hsi_y_las_copias_cifradas` pins the
HSI and backup clauses; change it with `app/ops/backup_full.py` retention or the
erasure ledger.

## Tests

- `tests/test_legal.py` — the clauses that cannot disappear (external servers,
  reasonable-effort anonymisation, order image sent as is, chat/history without
  personal data, rights channel and deadlines, photos and Gravatar, demo does not apply) for both
  the prototype and the institutional version, what the institutional version must never say, its
  counsel markers, the markdown renderer, the translation
  fallback, and both pages rendering with no leftover placeholders.
- `tests/test_legal_db.py` — the overlay: save, publish, restore, and the update
  date in the installation timezone.
- `tests/test_pii.py` — masking, round-trip restore, the false positives that must
  NOT be masked, and the attached image passing through untouched.
- `tests/admin-legal.test.mjs` — the admin wiring and the shared controls.


## Logs and error reports (2026-10-21)

Privacy section 6, «Registros técnicos y reportes de errores», is the second
factual section next to the AI one: `legal._con_registros_tecnicos` puts the
canonical text into an edited body too (under the body's own heading if it
has one, otherwise unnumbered right after section 5). It states only what the
code does: `app/core/error_tracking.py`'s allowlist (and what never leaves, with
the regex limit said aloud — no «sin datos» promise), staff browser errors
only, and the logs: the access line without its query, masked DNI/phone/email
on `log_pii` lines, and that a failure line carries the error's type, code
and place, never its message (`log_pii.exc`/`traceback_text`, and
`log_pii.install()` for library records and uncaught exceptions; the static
gate in `tests/test_logs_pii.py` fails on a raw `{e!r}` in server code). The
staff «Reportar un problema» paragraph lands with that feature, not before.
Section 7 lists only the deletions the code performs, as adjustable defaults
(referral file 180 days, chat messages 90 days once summarised, idle sessions
30 days); `tests/test_legal.py` pins the wording and the referral default (the
housekeeping defaults are inline in `app/ops/housekeeping.py`). Change the code,
change the sentence. Section 7 cites Resolución AAIP 47/2018 (recommended
security measures) as the security reference; Resolución AAIP 14/2018 is only
the source of the section 8 information text and is not named in the body
(2026-10-23, `test_las_medidas_de_seguridad_citan_la_resolucion_47_2018`).

## Provincial AI facts (2026-10-14)

`app/core/ai_register.py` is the factual inventory. Both legal documents render the
same non-editable function annex even when their prose has a DB override; their
standard AI section is refreshed from the current canonical statements at render
time. The source originals now mention every whole-image exception, lab reading,
interview, draft memory, consultation wording and deployment-selectable inference.
No promise that all derived output is reviewed before display, or that all
inference is external, is made. Local source dates do not establish deployment.
See [provincial governance evidence](../compliance/ia-pba-2026.md).


The existing Fal voice-transcription transport is separate from ai_client.call.
Complete audio goes to Fal storage/queue before text can be scrubbed; the later
chat text retains ordinary pii.py handling. The shared composer discloses this
before recording. Voice now honors global/voice disable and fake mode at its
transport boundary. MCP token-authorized responses to an external user client
are a distinct disclosed data-access boundary, not pseudonymised provider-side
inference. Both exceptions require institutional processing/risk review.

Slice 8 review: triage summaries now use the same reversible patient scrubber.
Public/institutional translations are sent without that filter to preserve public
contacts; the legal text and register say so. Profile proposals are separate from
confirmed health memory. Fake dictation stays available with no provider egress.
Kill-switch propagation and free-tier image retention risk are recorded in
[the runtime controls](../compliance/runtime-controls.md).
