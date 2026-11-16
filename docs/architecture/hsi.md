# HSI integration and conflict resolution

HSI is the clinical system of record. Human Rounds records outbound
appointment intent in Postgres before network I/O and records inbound HSI
observations before reconciling them locally. The durable journal is the
source of truth for retry state: `pending`, `sending`, `confirmed`, `error`,
and `conflict` are persisted rather than hidden in a background thread.

Read [the durable synchronization solution](../solutions/hsi/durable-bidirectional-sync.md)
before changing outbound operations, checkpoints, replay, or conflict
handling. This document records the currently supported topology and its
invariants.

## Core to Enterprise boundary

The durable journal calls the generic `EmrSyncConnector`, never a vendor
client. `app/connectors/hsi/` is the temporary legacy adapter that contains HSI
wire details; `app/connectors/emr_bridge/` is the explicit HTTP adapter for an
institution-owned Enterprise bridge. Its public v1 contract is
[emr-bridge-protocol.md](emr-bridge-protocol.md). The bridge is selected
explicitly with `CONNECTOR_EMR_SYNC=emr_bridge`; the temporary in-process
adapter is selected explicitly with `CONNECTOR_EMR_SYNC=hsi`. Neither is a
default. Missing selection or bridge configuration is a safe degradation,
not a vendor import or localhost traffic.

The conflict resolver is vendor behavior and stays inside `app/connectors/hsi/`
(`hsi_resolver.py`). Core reaches it ONLY through three generic, documented
`EmrSyncConnector` entry points resolved via `connectors.adapter_for(EMR_SYNC)`:
`run_conflict_resolution(now)` (the sync sweep's and «Recheck»'s one pass),
`conflict_panel(limit)` (the admin monitor payload) and
`apply_conflict_resolution(action, ids, resolved_by, now)` (the resolution
buttons). The ABC defaults are the safe degradation (`{}`, `None`, a
`resolucion_no_disponible` refusal); an unrouted capability degrades the same
way. `tests/test_emr_sync_boundary.py` proves the Core side against a fake
generic connector and proves a clean ASGI lifespan makes no vendor import
attempt; `tests/test_hsi_connector.py` proves the explicitly selected legacy
adapter's delegation.

Moving `app/connectors/hsi/` out of this repository into the proprietary
Enterprise service is a required later slice because no such repository exists
yet. The in-repo HSI package is only a temporary compatibility implementation:
it is not a licensing boundary and does not complete the Core/Enterprise split.
It must leave the AGPL Core repository before that split is called complete.

The bridge v1 has no generic equivalent for HSI's vendor-specific automatic
"find another diary" resolver action. Core retains normalized conflicts and
local-only resolver work, but sends no such legacy mutation while the bridge
is selected.

## Durable outbound transport and legacy fallback

`app/modules/turnos/hsi_journal.py` (the `hsi_sync` job; its queue side is
`app/modules/turnos/hsi_journal_queue.py`) owns durable outbound operations: create,
cancel, reassign, and attendance. Each operation has a stable outbox
idempotency key, a claim lease, remote confirmation/probe behavior, a local
acknowledgement, and checkpoint persistence. A restart after remote acceptance
therefore replays the same intent rather than creating a second appointment.

`hsi_journal._replay_one` reads the local row before touching the EMR
(TLC, `AppointmentHsiFixed.tla`, 2026-09-24): a create for an unlinked
cancelled request is skipped unless the connector's `probe_intent` shows an
earlier lease already created it (then it is adopted and its cancel queued); a
create for an ALREADY-linked request is skipped (an earlier replay or the
inbound reconciliation linked it); a reassign for a cancelled request is
skipped.

**A request has at most one create in flight (2026-10-25, closes the former
«Known residual»).** The trace: the approval's create reaches the EMR, its
response is lost (crash or transport failure after acceptance), the row stays
unlinked, then the patient reschedules or accepts a waitlist offer on it — a
direct create there opened a second clinical appointment. Now
`enqueue_appointment` returns the request's oldest unsettled create
(`db_hsi.hsi_outbox_outstanding`: any state but `confirmed`) instead of minting a
key for the new slot — the key hashes the slot, so a new key meant a new
appointment — and this covers backfill, «Reintentar» and the resolver too. An
unlinked patient move (`_reschedule_core`, `appointment_offer.accept_offer`)
moves only the local time (`hsi_estado` pending) and calls
`hsi_journal.replay_unlinked_move`, which retries that one create now (a
`conflict` stays with the operator). When a create lands on a row whose local
time no longer matches its payload, `_replay_one` links the EMR id without
dragging the time back and queues a reassign onto the patient's slot.
Proof: `tests/test_hsi_unlinked_move.py` (real Postgres, keyed fake EMR) — red
on the old code with two live EMR appointments in all three paths. Not
closed: a patient move committed between `_replay_one`'s local read and its
write can still be overwritten by the payload time — a millisecond window
that needs a row lock across both, never a second appointment.

**Movement generations, lease fencing and the uncertain cancellation
(2026-10-28, ported from the former `codex/hsi-cancel-uncertainty-20260925`
branch; its tests are the specification).** Three rules keep a slow or
retried worker from undoing what the desk did meanwhile:
- *Generation.* Every intent for an appointment carries `move_generation`, the
  count of its `reasignada`/`aprobada` events (`db_booking._appointment_move_generation`).
  A reassign or cancel built for an older generation is stale and is settled
  without touching the EMR; `reassign_appointment` refuses a request that is no
  longer approved (`db_booking.SlotConflict`).
- *Lease fence.* A worker settles an outbox row only while it still holds the
  lease it claimed (`state='sending'`, same `attempts`); otherwise
  `db_hsi.StaleHsiLease` aborts the write. `hsi_outbox_reset` re-arms only
  `pending`/`error`/`conflict` rows or a `sending` row whose lease expired — a
  live lease stays with its worker. `hsi_outbox_enqueue` takes the advisory lock
  `hsi-create:<id>` and returns the oldest unconfirmed create for the request
  (the dedupe above), and a corrective cancel names its
  `predecessor_intent_id`.
- *Uncertain cancellation.* Cancelling a request whose create may have reached
  the EMR keeps `hsi_estado` pending when a connector is selected
  (`pending_hsi_with_connector`) so the journal probes and cancels it; with no
  connector it is marked skipped (`skip_hsi_without_connector`). The lock order
  is `booking_requests` row, then `hsi_outbox` row.
Proof: `test_hsi_cancel_ordering.py`, `test_hsi_worker_fence.py`,
`test_hsi_outbox_identity.py`, `test_hsi_legacy_reclaim_http.py` (real Postgres,
keyed fake EMR) and the ported `test_hsi_sync.py`/`test_hsi_queue_proof.py`.

`app/modules/turnos/hsi_fallback.py` is deliberately a separate, thread-only/manual
fallback for future requests whose HSI state remains `error` or `pending`,
including linked cancellations. It is **not** queue-registered and
`TURNOS_QUEUE_WORKER` never
starts or schedules it. It owns only its own cadence
(`TURNOS_HSI_RESYNC_INTERVAL_MIN`), flags (`TURNOS_HSI_RESYNC_OFF` and
`TURNOS_SKIP_HSI`), and stop-first behavior.

For every eligible row, the fallback calls `hsi_journal.intent_for_row()` and
`hsi_journal.replay_item()` through `hsi_journal.enqueue_and_replay_row()`. The shared row to
intent mapping preserves linked cancellations and reassignments rather than
falling back to an accidental create. The fallback must never call the legacy
direct push or local HSI-status write path itself.

This exact-item route is intentionally narrower than `hsi_journal.tick()`: it
does not perform inbound reconciliation or call the resolver. In particular,
running the fallback while `TURNOS_HSI_SYNC_OFF=1` must still avoid a full
inbound pass.

## After «Crear turno»

The desk create response carries `hsi: {conectado, estado, extension}`. A turno
with a patient record ends on «Turno creado» with its HSI line: connected, the
push already ran by itself (in HSI, or on its way and retried by the journal);
not connected, «Cargar en HSI» uses the desk extension
(`extensions/hsi-humanizador`, `data-hr-hsi` + `data-hr-hsi-dia`), offered only
on sites it serves: its manifest's `content_scripts`, plus `TURNOS_OPCIONALES` in
its `lib/shared.js` (sites the operator allows at runtime, salud.pinamar.gob.ar).
See docs/decisions/2026-10-w4a.md (2026-10-19) and docs/decisions/2026-10-w4d.md (2026-10-24).

## One «¿está en HSI?» control (2026-10-27)

Every place a turno reaches HSI from draws `static/platform/hsi-sync.mjs` and
nothing else: four states, one look (button → turning icon → check, or the
reason in red). Server push on (`emr_sync_active`): `serverHtml(hsi_estado)` —
«En HSI», «Envío automático a HSI» while the journal still owes it, «Reintentar
envío a HSI» after an error (POST `retry-hsi`, painted in place by
`pintarServidor`; the route answers for ANY selected EMR connector, 404 only
with none). Server push off: `extensionHtml()` draws the extension's
hook and the extension paints it; Solicitudes gets the extension's store link
as `hsi_extension` in `GET /api/admin/requests` (`app/core/hsi_extension.py`, the
same rule «Turno creado» uses) and shows the control only when it is set.

| Entry | Control | Automatic |
|---|---|---|
| «Pacientes de hoy» row | the extension's own row button | — |
| «Turno creado» (Crear turno) | `#ct-hsi` | `data-hr-hsi-auto` |
| Solicitudes «Recién aprobadas» (this page's approvals, memory only; the card `#hsi-recientes` above the queue, one line «name · service · turno» each, no counter) | one per approval | `data-hr-hsi-auto` |
| Solicitudes Aprobadas / Todas (HSI column: with the push, or where the extension serves this site, `hsi_extension` — never waiting for the page mark; it stays with a request open above 1200px, its label wrapping) and an approved request's detail, today or later | one per turno | — |

`data-hr-hsi-auto` sends by itself only with the extension's «Cargar en HSI
apenas apruebo o creo un turno» on (off by default, hidden in «Solo copiar el
DNI»); HSI takes one load at a time, so the rest wait «en cola».

The extension also passes on what changes **after** the load (0.7.0), with no
schema change: it remembers only the place of each turno it loaded (day, hour,
doctor — no PII, 60 days, local storage `turnosEnHsi`) and asks
`GET /api/admin/day-queue/changes?ids=…` (`doctor_appointments.day_changes_data`,
at most 100 ids, the same scopes and the same `cola` access-log row as the day
queue) for `aprobada`/`cancelada` and the attendance. A cancellation (desk,
patient, WhatsApp «No»), a reschedule (cancel the old place + load the new
one; a reassigned waitlist slot is this same request) and a patient who showed
up (HSI «Confirmado»; «Ausente»/«Atendido» are disabled in HSI's screen, so
they cannot be marked) each get a button the operator presses; the final press
of a cancellation is always the operator's. The server push stays the driver
when it is active; the two are mutually exclusive.

What has passed never reaches that box (0.7.3, 2026-11-05: three cancelled
turnos of past days stayed on a desk for good, «HSI no deja cancelar este
turno», with nothing to close them). The answer carries `ahora`, the
installation's hour (`core.clock.now()`), and the extension leaves out every
place behind it (`lib/shared.js` `cambiosPorPasar`/`yaPaso`); a refusal for a
turno that passed meanwhile is dropped quietly (`cambioPasado`). Any other
refusal offers «Ocultar»: `POST /api/admin/day-queue/changes/<id>/hide`
(`hide_change_data`, same scopes as the read, 404 outside them) writes an
`appointment_events` row of kind `hsi_oculto` with the turno's state, time and
attendance as they were (`db_hsi.hide_hsi_change`). The read answers
`oculto` for every change whose latest such row still matches the turno
(`hsi_hidden_marks`/`hsi_change_hidden`), so the row stays hidden on every
computer of the desk until the turno changes again; the computer that hid it
also keeps its own mark in `turnosEnHsi` (state, place, doctor, attendance),
so an installation without the endpoint still hides it there. A change of
doctor alone at the same hour leaves no `appointment_events` row, so the
server mark does not reopen it.

A turno the extension loaded never learns its HSI id, so the request keeps
`hsi_appointment_id` empty. Where the inbound sync runs (an EMR connector
selected), an observed HSI appointment that no request links is matched to
exactly one approved, unlinked request of the same patient (the observation's
DNI, or the patient mirror's when the observation carries none — a DNI-less
observation never overwrites what the patient poll learned), the same minute and the
same catalog doctor (the diary's `professional_name` through
`doctor_appointments.resolve_doctor_id`, as `hsi_busy` does)
(`hsi_journal._link_unlinked_request`, `db_hsi.hsi_unlinked_approved_at`). It is
linked once (`db_hsi.hsi_link_request`: row lock re-asserting DNI, doctor and
minute, `hsi_status` «ok», an `appointment_events` row of kind `hsi_vinculada`)
and then reconciled like any linked request: attendance HSI reports
(`attended`, `no_show`), a cancellation (a conflict for a person) and a move.
Several candidates, or a request whose own appointment intent is still in flight
in `hsi_outbox` (pending, sending, error), are a visible conflict (`vinculo`
cluster of the resolver, opened once) with no link; none is the old `imported`.
An appointment observed again unchanged is retried while unlinked and from today
on (`hsi_journal._relink_unchanged`), so a request that turns linkable later
(DNI learned, request approved, extra candidate rejected) links on the next tick. A cancelled or blocked HSI appointment and
a time without an offset never link. Nothing is written to HSI. HSI has no
attendance field of its own: attendance IS `appointment_state_id` (3 Ausente,
5 Atendido, 9 Reserva ausente — its turnos screen disables «Ausente»/«Atendido»
for everyone, «Confirmado» is state 2 = scheduled, not attendance), so through
this link cancellation and moves come back in practice, and attendance only if
HSI's clinical side records the state itself. Once the journal links a request,
`GET /api/admin/day-queue/changes` answers it without `turno` while an EMR
connector is routed: the extension's panel never offers to replay in HSI what
the server already did.

## Durable `hsi_sync` scheduling

Only `hsi_sync` is queue-migrated. Its queue task self-reschedules after a
completed tick, preserving the original completion-to-next-run cadence rather
than approximating it with a cron grid. A shared Postgres advisory lock spans
the `chain_alive()` check and defer decision; a watchdog restores a missing
chain. The task is exclusive, so concurrent workers cannot run two HSI ticks
at once.

The queue owns timing and recovery only. It does not change `hsi_sync`'s
business semantics: backfill adopts existing pending local rows, exact-item
replay delivers durable outbox work, and the inbound pass reconciles facts then
offers conflicts to the resolver.

## Inbound reconciliation and conflicts

`hsi_journal.tick()` imports remote HSI facts into `hsi_inbox`, then reconciles
them against local links/checkpoints. The resolver closes deterministic,
technical conflicts and exposes only cases requiring an operator. It never
silently overwrites a changed remote appointment.

The admin HSI surface is available only to the `admin` role when
HSI is active. It presents one conflict per row, with the recommended action
and bounded alternatives. See [the conflict-resolution solution](../solutions/hsi/resolutor-automatico-conflictos.md)
for the decision rules and user-facing behavior.

When the resolver books the appointment with another professional (or as an
overbook), it records who through `hsi_resolver._asignar_profesional`: the HSI
name is resolved against the catalog with `doctor_appointments.resolve_pair` —
exactly ONE catalog doctor → its id and catalog name (the row stays in that
doctor's «Mis pacientes» and under the doctor+slot unique index); otherwise
the HSI name as given with `assigned_doctor_id` NULL (2026-10-23, see
[admin-cola-y-agenda.md](admin-cola-y-agenda.md), «A request's doctor is ONE pair»).

The native lifecycle candidate has one shell and one controller: `/admin/hsi`
(`app/admin_panel/admin_hsi_screen.py` + `templates/admin/hsi.html`, admin-only,
turnos-gated — 404 when the module is off, exactly like the APIs). The
controller is `static/platform/admin-hsi.mjs`, imported through a fingerprinted
URL by the template. The classic `admin-hsi.js` is physically absent in this
candidate; the hash shell was separately retired on2026-09-01. Import is inert,
and each mount owns its requests, mutations and listeners until disposal.
The three APIs —
`GET /api/admin/hsi-sync`, `POST /api/admin/hsi-sync/resolver`,
`POST /api/admin/hsi-sync/conflicts` — are unchanged and remain the only
authority; the screen render never calls them, the resolver, the connector,
the database or a socket. Connector availability is also checked through
`screen_nav`; no HSI probe is made by the screen renderer. The controller
passes request outcomes to the shared session classifier: confirmed expiry
re-enters login, required2FA routes to repair, and ordinary refusal stays local.
Every failure restores owned controls, and a failed or
null panel shows one load-error line with Retry instead of an eternal
"Cargando…". UI literals live in `locales/{es,en}/admin.hsi.json`. Detail →
[native ownership and browser checks](../solutions/dev/hsi-native-acknowledgement-and-refresh-ownership.md).
The table stacks at container widths <=900px. Local synthetic-browser evidence
does not prove actual BFCache or deployment of this candidate.

## Hours taken by appointments created in HSI (2026-11-11)

An appointment the hospital's desk creates straight in HSI has no local request, so
the free-hours code could not see it and the site could offer the same hour to a
second patient. `app/modules/turnos/hsi_busy.py` turns the inbound mirror
(`hsi_appointments` + `hsi_doctor_schedules`, read by
`db_hsi.hsi_future_unlinked_appointments`) into busy minutes, in ONE place:

- **Which rows count:** not `cancelled`, in the future, and linked to no request
  (`booking_requests.hsi_appointment_id`: a linked turn is already counted as that
  request, never twice). The diary's `professional_name` must resolve to exactly one
  catalog doctor (`doctor_appointments.resolve_doctor_id`, the rule approval uses), and
  the minute must lie inside a window of that doctor's agenda for a service that books
  (`doctor_windows.for_service`). Anything else takes nothing and never raises.
- **Off switch:** with `TURNOS_HSI_SYNC_OFF=1` or no EMR routed the answer is empty,
  the behaviour before this module. Cached 3 s with the taken-slots cache
  (`slot_holds.invalidate()` drops both).
- **Where it is read:** `slot_holds._taken_raw` and `try_take` (the public site, the
  wizard, the desk's grids by service), `booking_capacity._doctor_appointments_between`
  (approval, «Crear turno» through `doctor_busy_minutes`, `_overbookings`: an HSI turn is
  an ordinary busy one, so an overbook counts it as a used place) and
  `doctor_appointments._free_of_day` («Pacientes de hoy»).
- **Proofs:** `tests/test_hsi_busy.py`, `tests/test_db_hsi_store.py`
  (`TestFutureUnlinkedAppointments`).

## External clinical history (read-only)

`TURNOS_HSI_EXTERNAL_HISTORY=1` explicitly enables on-demand reads; the default
is `0`. Only the explicitly selected `emr_sync` connector is used. The optional
`EmrSyncConnector.external_clinical_history(identifier)` defaults to
`available: false, reason: not_supported`; the Enterprise `emr_bridge` adapter
keeps that default. Core does not import the HSI vendor package.

### Verified endpoints and unknown responses

The supplied investigation of the HSI **2.71.2** sandbox verified authentication
with `POST /api/auth` (JWT cookie `token` / Bearer), patient minimal search at
`GET /api/patient/minimalsearch`, and basic identity reads at
`GET /api/patient/<id>/basicdata`. This slice makes no calls to a real HSI.

Frontend bundle chunk 6417 declares
`GET /api/institutions/<institutionId>/patient/<patientId>/outpatient/consultations/getExternalClinicalHistoryList`.
HSI shows it only with `HABILITAR_HISTORIA_CLINICA_EXTERNA`. The sandbox admin
received **403**, so the response schema remains **unknown**, and a clinical
role or the HSI feature flag may be required.

Frontend chunk 5632 declares both federation reads:
`GET /api/masterfile-federacion-service/Patient/patient-location?identifier=<dni>`
and `GET /api/masterfile-federacion-service/Patient?subject=<id>&custodian=<id>`.
Both returned **404** in the sandbox: federation was not deployed there. Their
schemas also remain **unknown**. These client methods are available for later
validation; the patient-summary endpoint does not chain them or infer subject
and custodian identifiers.

Every read returns `{available, reason, items}`. A list of raw dictionaries is
preserved; a dictionary is preserved as one raw item, without guessing wrapper
fields. Scalars and mixed lists are errors. Empty lists/dictionaries are valid
empty reads. `403` maps to `forbidden`, `404` to `not_deployed`, and timeouts,
malformed bodies or other failures to `error`. Success uses an empty reason.
Exceptions, request identifiers, URLs and clinical response bodies are never
logged by this path.

### Staff boundary and storage

`GET /api/admin/patient-external-history?dni=<dni>` belongs to the turnos
manifest. It admits an admin or a doctor linked to an appointment whose own
clinical DNI matches the request; dependent-account ownership is insufficient.
The doctor identity comes from the session and is rechecked before every read,
including cache hits. Front desk and other roles are refused even if a custom
permission override admits them through the generic admin gate. The flag-off
response makes no connector/HSI call. The response is always `Cache-Control:
no-store`; no mutation method is registered.

The HSI adapter uses the existing minimal-search path via the read-only
`find_patient` helper and accepts exactly one patient id. It never calls
`ensure_patient` or creates a patient. Missing/ambiguous matches degrade to
`error`. Successful results live only in a bounded process-memory cache for
five minutes per `(user, patient)`; failures remain retryable and no external
history is stored in Postgres, browser storage, or the chart-summary cache.

This checkout has no existing patient-granular chart-summary access audit. The
new read reuses the existing timestamped `login_panel_events` trail through
`db_analytics.log_panel_login`: `sid` is the verified staff username, `variant` is
`patient-chart`, `event` is `external_history_access`, and `item` is the patient
DNI. Each authorized enabled read, including cache hits, records this metadata
only. The existing trail is best-effort and follows its existing retention.
No table or migration is added.

The doctor's summary adds a collapsed **Historia en otras ciudades** disclosure.
Only **Cargar historia** loads it. The flag-off block is absent. Forbidden,
federation-unavailable, unsupported, empty and retryable failure states are
localized in Spanish/English. Until the schema is verified, each raw item is
displayed as escaped scalar text, with ISO dates passed through the shared date
formatter and technical/identity identifiers excluded. This is defensive
presentation, not a claim about HSI's date/institution/title field names.

External history is a separate display-only endpoint, never a
`clinical_history_lookup` source, hook, summary generator input, or AI prompt.
The AI payload builder allowlists the local appointment/interview fields and
excludes an `hsi_external` history entry; synthetic tests pin the exclusion.
The AI data-flow register therefore gains no external-history flow.

### Required production validation before enabling

- Validate access and authorization with the hospital's clinical read account,
  `HABILITAR_HISTORIA_CLINICA_EXTERNA`, and its actual institution id.
- Verify DNI-only minimal search, uniqueness/identity resolution and clinical
  access restrictions. Never create a patient to make a read work.
- Inspect approved, privacy-safe response examples to confirm wrappers and the
  real date, institution/place and title/summary fields; replace generic text
  presentation only after those fields are verified.
- Confirm federation deployment, identifier/subject/custodian semantics and
  jurisdictional permissions; do not assume the sandbox's missing service is
  available in production.
- Validate local role scoping, audit durability/retention expectations, cache
  expiry, and the forbidden, unavailable, empty and failure states. In-memory
  caches are per process, not shared across workers.

## Verification

- `tests/test_external_history.py` pins client failures, read-only identity search,
  safe ABC/bridge defaults, role/patient scoping, cache, audit metadata, GET-only
  module ownership, flag-off no-call behavior and AI payload exclusion.
- `tests/admin-patient-summary.test.mjs` pins on-demand loading, escaped clinical
  text, hidden flag-off state and retryable failures.

- `tests/test_hsi_sync.py` covers row-to-intent mapping and exact-item replay.
- `tests/test_hsi_resync.py` proves the fallback delegates one row without
  invoking a full sync, preserves stop-first behavior, and remains no-op under
  `TURNOS_SKIP_HSI`.
- `tests/test_hsi_queue_proof.py` proves both HSI paths make zero network egress
  with the outbound kill switch armed.
- `tests/test_queue_worker.py::TestHsiResyncRerun` is the composed
  disposable-Postgres plus loopback-HSI proof of remote-accepted,
  local-uncommitted recovery: two fallback runs leave one durable intent and
  one remote appointment.
