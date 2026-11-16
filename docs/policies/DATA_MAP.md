# Human Rounds data map

This technical inventory maps the data surfaces implemented by Human
Rounds. It is for operators evaluating a deployment, contributors changing a
data path, and reviewers tracing an external flow. It describes software
behaviour, not an institution's legal basis, processor agreement, retention
schedule, or production configuration.

It carries no institutional approval yet: see "Approval record" at the end.

## Publication

- The public repository is one commit made with `git archive` from the private main branch; its history is not published.
- Licence AGPL-3.0, copyright Generated Media, Inc.; [LICENSING.md](LICENSING.md) lists the assets that are not under the AGPL.
- Kept out of the release (`export-ignore` in `.gitattributes`): the reference deployment's private data pack, agent harness files (`.claude/`, `CLAUDE.md`), research records made against the real catalog, the login screen's humor images, and installation-only operations notes.
- Everyone who wrote code for the project agreed on 2026-11-11 to its publication under AGPL-3.0.

## Data categories

Human Rounds processes appointment and care-adjacent information. The main
data subjects are patients, their dependents and account holders; clinical and
administrative staff; medical professionals and centres; and visitors to
public pages or chat.

Patient and account data can include name, national identity number, date of
birth, contact details, credentials and session state, health-coverage details,
appointment requests, attendance and communications. A configured identity
provider can return identity claims, which are retained with the account.
Referral uploads, interview answers, consultation notes, laboratory-result
analysis, chart summaries, clinical-history records, and patient health
profiles are clinical or health-adjacent data. They need the deployment's
clinical governance; this document does not classify them as a diagnosis or
replace professional review.

Staff and professional data includes operator roles, security settings,
passkeys, preferences, prompts, schedules, directory and catalog entries,
professional or centre photographs, and administrative audit events. Visitors
may contribute search text, chat messages, referral files, contact details,
bot-challenge tokens, and network metadata required to serve a request.

## Storage

### PostgreSQL

The application tables at Alembic head (`alembic/schema-head.txt`), by area:

- Appointments and availability: `booking_requests`, `appointment_events`, `attendance_events`,
  `day_queue_events`, `waitlist`, `appointment_offers`, `slot_holds`, and
  `alert_claims`. The queue-event replay log contains scheduling coordinates
  but no patient identifier, attendance value, or operator.
- Accounts and access: `users`, `dependents`, `sessions`, `reset_tokens`,
  `dni_claims` (the accounts whose document number the front desk has not checked yet: the number
  claimed, the key of the account asking, the status and who closed it),
  `operators`, `roles`, `operator_access`, `admin_preferences`, `admin_reset_tokens`,
  `operator_username_history` (old→new login names of renamed staff accounts; keeps a
  retired login reserved so a stale signed cookie can never land on a reused name),
  `admin_account_security`, `passkeys`, `passkey_challenges`, `otp_codes`, and
  `otp_verified`. Passkey challenges contain only a SHA-256 digest, purpose,
  optional registration owner, expiry, and consumption timestamps; the raw
  challenge remains browser-only. `admin_session_revocations` lists the staff
  sessions that «Cerrar sesión» ended (a random session id, the staff login name,
  the time, and when the row may go); it holds no cookie and no patient data.
- Clinical and care-adjacent records: `interviews`, `patient_health_profile`,
  `consultations`, `patient_summaries`, `clinical_history_records`, and
  `lab_report_analysis`. `patient_lab_uploads` holds lab results the patient
  uploads from their own account, including the uploaded file itself; they are
  patient-supplied, never authoritative clinical history. When the patient
  deletes one, the file, its transcription and its filename are set to NULL
  (the emptied row stays as the record) and the cached reading of that report
  in `lab_report_analysis` is deleted; the file bytes also expire on their own
  after the same window as referral files (`TURNOS_LAB_UPLOAD_RETENTION_DAYS`,
  defaulting to `TURNOS_REFERRAL_RETENTION_DAYS`; a non-positive value keeps
  them), keeping the transcription.
- Clinical access log: `clinical_access_log` — one row per staff read of a
  patient's clinical data (who, when, which screen, allowed or denied). The
  patient is an HMAC of the DNI, never the DNI; the table is append-only by
  trigger. Forward revision: `0026_clinical_access_log`;
  detail in `docs/architecture/registro-de-accesos.md`.
- Data-subject requests: `data_subject_requests` and `data_subject_request_events`
  — the ledger of access, correction and erasure requests (Ley 25.326
  arts. 14-16): kind, day received, due day, channel, how the identity was
  verified, outcome. The person is an HMAC of the DNI, never the DNI; the
  events table is append-only by trigger. Forward revision:
  `0028_data_subject_requests`; the registry of where each person's data lives
  is `app/core/personal_data.py`, detail in
  `docs/architecture/derechos-de-los-titulares.md`.
- Messaging and AI: `chat_messages`, `chat_summaries`, `chat_handoffs`,
  `chat_compactions`, `ai_prompts`, `ai_prompts_history`, `ai_costs`,
  `mcp_ai_budget`, `ai_register_changes`, and `translations`.
  `ai_register_changes` holds AI configuration/prompt hashes and lengths,
  operator identity, observation time and notification references; no
  patient data or prompt text. Forward revision: `0019_ai_register_changes`.
- Integration and operational state: `hsi_outbox`, `hsi_inbox`,
  `hsi_sync_conflicts`, `hsi_patients`, `hsi_doctor_schedules`,
  `hsi_appointments`, `hsi_checkpoints`, `refeps_cache`, `searches`,
  `login_panel_events`, `demo_refresh_completion`, and `legal_docs`.
- Anonymous visit analytics (off unless `TURNOS_ANALYTICS=1`): `analytics_events`
  (page, search and booking-step events: a 23-character event id, a visit id that
  lives in the browser tab only and rotates after 30 minutes, route, language,
  device class, search text reduced to catalog vocabulary; no DNI, name, e-mail,
  IP or persistent id), `analytics_schedule` (daily snapshot of the public
  schedule: hours, slots and free slots per doctor, center, service and day) and
  `analytics_keys` (SHA-256 hashes of the read-only export keys, never the key).
  Events are pruned after `TURNOS_ANALYTICS_RETENTION_DAYS` (default 400).
- Backup accounting: `backup_journal`, `backup_versions`, and
  `backup_version_tombstones`, and encrypted full-installation `backup_runs`;
  `purged_files` is the journal of file paths (relative to `data/`, no content)
  the referral retention deleted on purpose.
  `backup_purge_state` is one row with counts and times of the last remote purge pass
  (no path, no content).
- Operational counters: `launch_signal_counters` holds, per hour, how many times a fixed list of
  events happened (a staff sign-in, a mail sent or failed, a swallowed background failure); the
  worker sends the totals of the last day to the error tracker. Only the hour, the code from the
  list and a number: no name, DNI, contact or address.
- Migration residue: `date_time_conversion_leftovers` holds, whole and with the
  reason, the slot holds and report dates that the day/time column conversion
  (Alembic 0030) could not keep in a typed column. Normally empty; no name,
  DNI or contact.
- Deployment catalog: `catalog`.
- Anonymous usability research: `study_sessions`, `study_task_results`,
  `study_events`, and `study_findings`. Opt-in on the demo only. Session tokens
  are SHA-256 hashes in storage. No participant identity or IP is collected.
  Event descriptors contain only a tag, sibling index and interactive flag;
  paths use a closed vocabulary and never include query strings, fragments,
  patient identifiers or input values. Participants may write optional
  comments; conservative identifier filtering does not guarantee detection
  of every name or personal detail. No screen, audio or video is captured.
  Owner-triggered analysis is deterministic and makes no external AI call.
  Results are reviewed at `/admin/usability`; the demo cookie can read but
  cannot edit. The existing housekeeping worker purges sessions (transactionally removing
  their events and results) and findings after `TURNOS_STUDY_RETENTION_DAYS`
  (default 90 days), in bounded batches, even after the feature is disabled.

Alembic owns the `alembic_version` metadata table for current installations.
Installations from before Alembic keep the old numbered runner's
`schema_migrations` metadata table as a fossil. Neither metadata table is an application
data category or a patient record.

### Filesystem and deployment data

The Docker layout mounts the installation's `data/` directory at `/app/data`.
There is no single universal path resolver: data-pack and status code honour
their respective configuration, while several long-lived stores are relative
to the repository root. Deployments must therefore mount and back up the
paths actually used by their configuration.

The persistent `data/` volume can contain referral uploads in `ordenes/` and
short-lived upload staging in `ordenes/pending/`; account identity photographs
in `cuenta-fotos/`; professional and centre photographs; branding uploads;
an avatar-sprite cache; data packs; a private clinical-history import/fallback
archive under `private/`; local availability and medical-slot snapshots; and
operational housekeeping state, including `private/meta-wa-state.json` (the
pause after a Meta WhatsApp rate limit and Meta's last error code: numbers and
timestamps only, no phone, no name, no message text). In the default container geometry it can also
contain `/app/data/turnos.env`, a raw configuration and credentials file.
`data/calendar.json`, where an older installation still has it, is a
dormant compatibility store (it held an owner iCalendar capability URL) that no current
code reads. `data/test-identities.json`
is an optional legacy/test-only identity store, not normal production identity
storage.

Some stores are intentionally outside that volume by default. An installation
older than 2026-08-27 may still hold the legacy HTTP handler's access log under
`logs/` (timestamp, client IP, full request line, status, user agent; it rotated
at 10 MB with five retained files); that handler was deleted with the legacy
topology, and nothing writes there now. The referral-analysis cache is
module-relative and contains structured AI results only. The WebTurno
connector can use an operator-supplied local password file. Their paths, as
well as configured overrides, need separate consideration in a deployment
backup plan.

The legacy plaintext B2 component (superseded by the encrypted full backup,
kept for installations that still run it) walks the hard-coded `<repo>/data` tree, ignores files
larger than 200 MiB, and records accepted backup receipts in PostgreSQL. It
does not automatically include the legacy access log, the module-relative
cache, or arbitrary `TURNOS_DATA_DIR` overrides merely because they are in a
source checkout. In the default container geometry the recursive walk does
include `/app/data/turnos.env`; backup access controls, encryption, and the
processor agreement must therefore cover its configuration and credentials.

### Browser storage and automatic browser egress

The browser keeps convenience state in local or session storage. Local-storage
keys are `st_nav`, `st_photo_v`, `st_identity`, `st_gp_tab`, and `st_sso_dni`.
Session-storage keys are `st_order_pending`, `st_hold_token`, and
`stz_chat_sidebar_v2`. They cover cached navigation shape, account form
values, photo and tab preferences, an in-progress referral handoff, a
temporary slot hold, and a chat sidebar. Administrative column preferences
are stored through the `admin-preferences.mjs` module in PostgreSQL, not browser storage.

`st_identity` persists across logout and can hold national identity number,
name, birth date, sex, contact data, and email. `st_order_pending` can hold a
structured referral result, its capability token, and filename, including a
diagnosis. `stz_chat_sidebar_v2` stores rendered chat HTML and a WhatsApp URL.
Those keys are therefore personal-data stores, not merely interface settings.

Cookies are `st_ses` for a patient account session, `st_admin` for an
administrative session, `st_oidc` for the identity-provider callback state,
`st_setup` for a first-run setup session, and `lang` for language preference.
`st_ses` has a client lifetime of 10 years but its server session is purged
after 30 idle days. `st_admin` has a sliding 30-day lifetime, `st_oidc` lasts
10 minutes, `lang` lasts one year, and `st_setup` lasts one hour; setup state
can include setup email, password-hash, and TOTP state. The tracked
application has no browser persistence through IndexedDB, service workers, or
CacheStorage. Browser state is not a replacement for server-side
authorisation; shared-browser storage can expose personal data.

Rendering can make browser-side requests without an application API call. The
Pinamar theme self-hosts its fonts; the Human Rounds/generic theme loads Google
Fonts. Administrative surfaces load Material Symbols. Only the legacy
`admin.html` shell loads the Cloudflare Turnstile script unconditionally, and
that shell is no longer in the tree; the
server-routed `templates/admin/base.html` shell does not. Patient surfaces can
load Turnstile when their gate is enabled. The test identity surface loads
Google Fonts and an Argentina government logo. The legacy directory's logo
resolution can fetch `img.logo.dev`, and a custom data pack can conditionally
name remote images. These browser egresses need evaluation in the deployment's
processor and privacy review.

## Processors and flows

The following processors are optional or configuration-dependent. An absent
connector is designed to degrade its corresponding capability rather than
silently substituting a different provider.

- AI inference is used for referral reading, booking chat, intake,
  chart-summary, laboratory-analysis, and translation flows, through
  OpenRouter to Gemini 3 Flash (one reading). Shared booking
  chat, chart-summary, laboratory, and translation paths apply the central
  PII scrubber. Intake analysis, health-profile analysis, and consultation
  composition call AI directly and mask the patient's identifiers themselves
  with `pii.for_dni` before the call. The original referral file, front-and-back national-identity OCR
  photographs, and intake image attachments are deliberate exceptions:
  health text can leave after identifier scrubbing, while the corresponding
  image content can leave the service unredacted. Voice audio is another
  deliberate non-text exception. Each path needs deployment-specific clinical
  and privacy review.
- AI profiles can declare `hosted_jurisdiction` for a hosted provider or
  `self_hosted_vllm` for an operator-controlled endpoint. An MCP medical-file
  flow can fetch a user-supplied file from ChatGPT's constrained
  `*.oaiusercontent.com` host family before analysis.
- Email notifications send appointment confirmations, reminders, rejection
  notices, closer-slot offers, account recovery, administrator invitations and
  resets, and disk alerts through the configured email transport: the
  `mailgun` connector by default, or `mailjet` when an installation selects it
  (the municipal installation does, through its own account); each message
  carries the recipient address, subject, and body. The
  production WhatsApp transport is Meta; WAHA is loopback development-only.
  A configured inbound webhook accepts message data and downloads and
  processes image, document, audio, and video media through production Meta or
  development WAHA. This tree has no production one-time-code template and
  patient entry flow, and there is no wired SMS transport.
- The `renaper`, `puco`, `refeps`, `hsi`, and `msd` connectors respectively
  cover identity verification, coverage lookup, licence lookup, electronic
  medical-record synchronisation, and a local slot-source snapshot. The
  `emr_bridge` connector sends normalized synchronization messages to the
  separately deployed Enterprise integration boundary; vendor credentials
  and wire formats remain outside Core.
- The `alepo` and `marqui` connectors have synthetic sample histories only;
  they have no real outbound access path. The `local_history` connector reads
  same-deployment PostgreSQL rows first and an explicitly configured local
  archive as a fallback. It is not an outbound provider call.
- Bot protection validates challenge tokens with Cloudflare Turnstile for
  configured public and administrative entry points. The initial setup flow
  can request a country code from an IP-geolocation service when its trusted
  proxy header is unavailable. Account-photo fallback can query Gravatar
  using an email hash.
- The backup component transmits raw files no larger than 200 MiB from its
  hard-coded source tree to a configured Backblaze B2 bucket. Its only
  automatic remote deletion is the removal of every version of a file the
  referral retention journaled in `purged_files` (and of expired pending
  referral uploads), and on a `*.gob.ar` installation not even that: there the
  application deletes or overwrites no remote backup by any path
  (`app/core/backup_lock.py`). Remote-retention pruning requires a reviewed,
  explicit operator action. Its operator-only restore path authorises and
  downloads one exact raw object into a fresh local destination, with remote
  metadata and downloaded-byte verification before acceptance. The chat voice
  feature uploads audio for transcription through its configured fal.ai flow.
  Its upload asks for a one-hour object lifecycle, but provider deletion is
  not guaranteed. Both are external transmissions only when configured and
  invoked.
- Appointment calendar feeds are capability URLs: a subscriber's calendar
  provider can fetch patient or clinician appointment details from such a URL.
- No current code makes an inbound HTTP fetch of
  a configured owner iCalendar capability URL: the owner busy-time exclusion
  was deleted; doctors' schedules and HSI define availability.
- WebTurno is not the default slot source (MSD is). An operator may select it
  for services that carry its clinic token; its CLI can send configured owner
  credentials to the remote booking system and write a local availability
  snapshot.
- Sentry-compatible error tracking is optional and disabled without a DSN
  (`TURNOS_SENTRY_DSN`). The two test sites send nothing since 2026-11-10; the
  municipal installation reports only to its own Sentry project.
  Since the 2026-10-19 error-data review (`app/core/error_tracking.py`) every
  event is rebuilt from an allowlist before it leaves: exception type, a
  swept message (emails, DNI/CUIL/phones and 6+-digit numbers masked, cut to
  300 characters), code frames without local variables, the request method
  and a path without query with id/token segments masked, the server's
  runtime/os, and the release commit. IP, cookies, headers, user, request
  bodies, `extra` and breadcrumb data never leave. Browser errors are reported
  from staff screens only. The sweep is regex-based: a name inside an
  exception message is not caught. The destination project, its region and
  its retention belong to whoever operates the deployment.
- The web process's uvicorn access line (stdout) holds timestamp, client IP,
  method, path and status. `core/log_pii.AccessLogFilter` drops the query and
  fragment and masks id/token path segments before it is written. Lines that
  use `app/core/log_pii.py` mask DNI, phone and email and log an exception by type
  and code only, never its message; a traceback keeps frames and exception
  types, never values — for our own lines and, through `log_pii.install()` in
  `asgi_main.py`/`worker.py`, for every library log record and uncaught
  exception too. `tests/test_logs_pii.py` pins it (static gate over the server
  code plus synthetic-data runs). Their retention is the host's log rotation.

The connector inventory is `alepo`, `emr_bridge`, `hsi`, `local_history`,
`mailgun`, `mailjet`, `marqui`, `meta`, `msd`, `patient_uploads`, `puco`,
`refeps`, `renaper`, `waha`, and `webturno`. Connector manifests
identify their capability and configuration names without embedding credential
values in source control.

## Retention

Referral uploads have an application default of 180 days. Only terminal
rejected or cancelled requests, or approved appointments whose appointment
time is already past, become file-deletion candidates after that interval.
Pending requests and future approved appointments are never eligible. The
database row and its history remain after file deletion. The retention value
is configurable per deployment; a non-positive value disables the automatic
referral sweep, so an operator must not interpret it as deletion. Pending
referral staging is cleaned on a best-effort 48-hour basis.

The referral-analysis cache defaults to a 24-hour lifetime and is bounded to
512 entries; it keeps structured AI output rather than the original file. Other
database, account-photo, catalog, messaging, clinical, backup, and
browser-storage retention is not given a universal application TTL. The
responsible institution must set and document its legal and operational
retention schedule, including backups and deletion handling, before it
operates a live service.

The housekeeping sweep has current defaults of 30 days for idle `sessions`,
180 days for `searches`, 180 days for `login_panel_events`, 90 days for
captured `chat_messages`, 180 days for `alert_claims`, and ten years for
`clinical_access_log` (`TURNOS_HOUSEKEEPING_ACCESS_LOG_DAYS`, never under one
year — its trigger refuses younger deletes). Chat messages are
eligible only after their conversation has a captured summary or compaction.
The full request line in a leftover legacy access log can itself contain a
capability or token: keep such files under the same access controls as other
operational data, or delete them.

## Jurisdictions

Human Rounds is deployment-neutral software. The repository includes adapters
for Argentine public-health and identity systems, but a deployment determines
where its database, volume, backups, email, messaging, AI, bot protection,
voice transcription, calendar, and other configured processors operate.

Known defaults do not establish a deployment's data residency: Mailgun uses a
US default endpoint but is configurable; Mailjet's Send API endpoint is
`api.mailjet.com`, and its account region is the operator's choice; RENAPER, SISA/PUCO, and REFEPS are
national Argentine systems; MSD is a Buenos Aires Province source; and HSI and
appointment-calendar subscriptions are operator-selected. OpenRouter and its
downstream model provider, Sentry, B2, Meta, Cloudflare, fal.ai, Gravatar,
Google, OpenAI/ChatGPT `*.oaiusercontent.com` file hosting, Logo.dev
`img.logo.dev`, and the setup IP-geolocation service have no pinned
jurisdiction in this repository. An AI profile's `hosted_jurisdiction`
declaration or a `self_hosted_vllm` deployment can make that AI placement
explicit, but the operator must verify the declared value against the actual
endpoint.

An operator must record the hosting region, each enabled processor, transfer
mechanism, data-controller identity, and applicable health-data requirements
for its own jurisdiction. This map intentionally does not assert a data
residency, subprocessors agreement, or cross-border-transfer basis on behalf
of an institution. The municipal installation `salud.pinamar.gob.ar` is hosted
by the municipality, which holds its database, volume and backups and sends
its mail through its own Mailjet account.

## Booking schedule policy (slice 4, 2026-10-13)

`catalog.doc` stores `agenda`, doctor `centros[].franjas/excepciones`, and optional
`dias_anticipacion` / `apertura_turnos` beside `turnos_activos_max`. These are
installation-owned operational configuration, not patient or clinical data.
`BOOKING_HORIZON_DAYS` and `BOOKING_OPENS_AT` use the existing site config store.
The queue calendar returns only permission-scoped occupied dates and adjacent
dates, projected from approved `booking_requests`; it does not serialize patients.
No table or column was added, so no Alembic revision or schema-head change applies.

## Doctor setup and private portrait originals (slice 5)

`operators.onboarding_completed_at` is a nullable per-operator completion
timestamp in the platform-owned staff table. Skipping stores no completion and
does not gate unrelated screens. The Turnos manifest owns the onboarding,
schedule screens and completion API, and classifies `TURNOS_PHOTO_AUTO_ENHANCE`
under its existing `TURNOS_` configuration prefix (default 1).

Doctor uploads retain byte-identical originals in the persistent volume under
`data/medico-fotos/.originals/<doctor>/portrait-*/original`, with private metadata
and a 512px WebP. A single public portrait pointer commits the bundle atomically;
small avatars still come from `avatar_sprite.py`. Original bytes and metadata
have no public route and never leave for an AI provider. Prior successful
bundles remain private until the doctor photo is removed, which removes all
portrait bundles for that doctor, or until the doctor is deleted for good
(nothing names it), which removes the public portrait and the whole
`.originals/<doctor>/` before the row goes (2026-11-06). A disabled doctor
keeps them. Doctors are not data subjects of `core/personal_data` (staff and
doctors are Q8): erasing a doctor's photo on request is the photo's «Quitar»
(public file, originals and bundles) or the deletion for good. Include these originals in the installation's
volume backup and photo-retention policy.

## Encrypted full-backup inventory (2026-10-14)

backup_full uploads one client-side AES-256-GCM encrypted archive to the configured
R2/path-style HTTPS S3 destination. It never sends plaintext to object storage.
A 200 MiB file cap, symlink/unsafe/unreadable paths and changing files are
recorded omissions and make the run partial; no such omission is silent.
The old B2 producer can still send raw bytes when explicitly left active.
The platform backup_runs journal records upload, integrity and restore-test
evidence separately. See docs/operations/backup-and-restore.md.

The following machine-readable inventory is checked by test_backup_inventory.py.
The recursive data rule includes every current and future top-level directory:
ordenes, cuenta-fotos, medico-fotos (including .originals), centro-fotos, marca,
avatar-sprite, private, packs, snapshots and any other persistent volume entries.
It is not a filename allowlist that silently omits new subdirectories.

<!-- backup-inventory:start -->
[
  {
    "location": "PostgreSQL application database",
    "policy": "include",
    "archive": "database.dump",
    "reason": "All application tables, schedules, accounts, clinical data, catalog and backup journals; exported consistent snapshot"
  },
  {
    "location": "data/**",
    "policy": "include",
    "archive": "data/**",
    "reason": "Every regular data-volume file, including referral staging, private originals, clinical history and operational state"
  },
  {
    "location": "TURNOS_DATA_DIR/**",
    "policy": "include",
    "archive": "data/**",
    "reason": "Configured data root; default repo data also included as legacy-data if different"
  },
  {
    "location": "TURNOS_PRIVATE_PACKS_DIR/**",
    "policy": "include",
    "archive": "private-packs/**",
    "reason": "Installation-owned town packs, manifests and inventories"
  },
  {
    "location": "TURNOS_WEBDATA_DIR/**",
    "policy": "include",
    "archive": "legacy-webdata/**",
    "reason": "Configured legacy installation catalog/pack data may contain private local overrides"
  },
  {
    "location": "TURNOS_ENV_FILE",
    "policy": "include-redacted",
    "archive": "config/installation.env",
    "reason": "Canonical installation env; active/retained backup KEKs removed from every env copy"
  },
  {
    "location": "logs/**",
    "policy": "exclude",
    "reason": "Legacy access logs are observability, not application recovery state; retain under separate operator incident/log policy"
  },
  {
    "location": "referral-analysis cache",
    "policy": "exclude",
    "reason": "Regenerable, module-relative AI result cache with 24h TTL; source files/database are backed up"
  },
  {
    "location": "WebTurno local password file",
    "policy": "exclude",
    "reason": "Operator-supplied external credential source; re-provision or rotate on restore, not a portable application store"
  },
  {
    "location": "browser local/session storage",
    "policy": "exclude",
    "reason": "Per-device navigation/session/cache state; users sign in again after recovery"
  },
  {
    "location": "application source tree and bundled public packs",
    "policy": "release-pin",
    "reason": "Commit/image plus requirements hash in manifest; matching repository/registry release retained separately"
  },
  {
    "location": "TURNOS_BACKUP_RECOVERY_KEY and TURNOS_BACKUP_OLD_KEYS_SECRET",
    "policy": "exclude",
    "reason": "Intentional cryptographic boundary: recovery keys must be saved outside the server/archive"
  }
]
<!-- backup-inventory:end -->

Slice 8 review: `chat_messages` also stores non-context `ai_disclosure`,
`booking_proposal`/`booking_consumed`, and `health_profile_proposal` rows.
These are conversation metadata, excluded from all LLM history/compaction
reads. Profile proposals follow the interview transcript retention policy;
only explicitly reviewed doctor values enter `patient_health_profile`.

## Approval record

record: approval:hr-b36d2a86a180ccc6

date: 2026-11-16
