# Clinical access log and «a doctor only sees their own patients»

K10 of the launch plan (2026-10-23). Laws: 26.529 art. 18 (inviolability of
the clinical record), 25.326 arts. 9-10 (security and confidentiality), Res.
AAIP 47/2018 (security measures: access logging, least privilege). Code:
`app/core/access_log.py`, table `clinical_access_log` (Alembic revision 0026),
`scripts/db_app_role.py`.

## What gets recorded

One row per staff read of clinical data from the panel — the DENIED ones too
(`allowed = false`):

| column | what it is |
|---|---|
| `ts` | when (timestamptz; the screen shows it in the installation's timezone) |
| `actor`, `actor_role` | the panel user and their role at that moment |
| `action` | which screen or action (closed list `access_log.ACTIONS`) |
| `dni_hash` | the patient: HMAC-SHA256 of the clinical DNI, **never the DNI** |
| `target_id` | which object: request id, interview id, doctor id |
| `allowed` | whether it was allowed |

The DNI is not stored: `dni_hash` is keyed with `TURNOS_ACCESS_LOG_KEY`
(falling back to `TURNOS_ADMIN_SECRET`). The admin looks a patient up by DNI,
and a dump of the table lists no documents. **Changing the key leaves older
rows unreachable by DNI** — set it once per installation. A DNI correction
(`change_dni`) does not rewrite the log: earlier accesses stay under the old
DNI's hash (`DNI_KEPT_COLUMNS`).

Append-only. The `clinical_access_log_guard` trigger (`GUARD_SQL` in `alembic/versions/0026_clinical_access_log.py`)
refuses every UPDATE and every DELETE outside the age purge, and even inside
the purge never deletes a row younger than one year. TRUNCATE bypasses row
triggers: the application role (below) simply does not have it.

What the trigger stops and what it does not: it stops mistakes (an UPDATE or
DELETE from code, a console) and any deletion of rows younger than a year. It
does NOT stop whoever holds the application's credential: the app role keeps
DELETE for the purge and can set `turnos.access_log_purge`, so rows older than
a year can be deleted with that credential even if the retention says ten
years. Closing that completely means taking DELETE away from the role and
purging as the owner (an operations task) — not done.

Retention: `TURNOS_HOUSEKEEPING_ACCESS_LOG_DAYS` (default 3650 = ten years,
never under 365), purged by `ops/housekeeping.tick_purges`.

If the row cannot be written, the read **does not happen**: when the database
does not answer (`db.UNAVAILABLE_ERRORS`) it is `AccessLogUnavailable`, a
`DbUnavailable`, and every route answers 503; any other failure of the insert
is `AccessLogFailed`, the server's own error, without the `db_unavailable`
signal (2026-11-10). The clinical data live in the same database, so this only
bites when the log itself is broken.

## Read map (where it is recorded)

| Screen / API | What it shows | `action` | Rule |
|---|---|---|---|
| `GET/POST /api/admin/patient-summary` | AI summary: requests, interviews, clinical history, uploaded labs | `resumen_ia` | clinical |
| `GET /api/admin/patient-external-history` | HSI history | `historia_externa` | admin or own doctor |
| `GET /api/admin/intake/consult/<id>` | interview: answers, triage, red flags, health profile, transcript | `entrevista` | clinical, per interview |
| `POST /api/admin/intake/consult/<id>/confirm` | consultation: diagnoses, exams, medication | `consulta` | clinical, per interview |
| `GET /api/admin/intake/alerts` | RED alerts | `alertas` (list) | clinical |
| `GET /api/admin/doctor-patients` | «Mis pacientes» with triage and summary | `mis_pacientes` (list) | clinical |
| `GET /api/admin/my-appointments` | «Mis turnos» with triage | `mis_turnos` (list) | own doctor |
| `GET /api/admin/order/<id>`, `/my-appointments/<id>/order` | the referral file | `orden` | desk by role, own doctor |
| `GET /api/admin/requests` | requests with the AI reading of the referral | `solicitudes` (list) | desk by role, own doctor |
| `GET /api/admin/day-queue`, `/admin/cola` | today's patients: triage, summary, referral reading | `cola` (list) | desk by role, own doctor |
| `GET /api/admin/doctors/<id>/appointments` | a doctor's agenda (editor) with the referral reading | `agenda` (list) | permission `medicos` |
| `GET /admin/cola/print` | one doctor's day as HSI's «Turnos programados» sheet: document, names, coverage, phone (2026-11-07) | `agenda_impresa` (list) | permission `cola`, own doctor; another doctor's id is a 404 with a denied row |
| `GET /api/admin/patients/<dni>` | the patient file | `ficha` | desk by role, own doctor |
| `GET /api/admin/patients?q=<exact dni>` (doctor) | finding someone who is NOT theirs (identity only) | `busqueda` | allowed and recorded |
| `GET /admin/chats` | the start of each conversation; for pre-consult interviews, what the patient said about their health | `conversaciones` (list, one row per interview) | permission `asistente` (admin); if it can't be recorded, the list is not shown |
| `POST /api/admin/dni-claims/<id>/resolve` | the desk confirms or rejects a document-number claim (TASK-68, 2026-11-14); target = the claimed number | `reclamo` | permission `crear_turnos`, never a médico; if it can't be recorded, nothing is resolved |
| `POST /api/admin/patients/<dni>/temp-password` | a new temporary password for the patient, printed once at the desk (TASK-50, 2026-10-26) | `contrasena` | permission `crear_turnos`, never a médico (same hands as creating a patient); if it can't be recorded, the password is not changed |
| `PUT /api/admin/patients/<dni>` | the desk edits the patient's file: fills in a missing name, corrects phone, email, address (2026-11-02; the hospital's technical contact asked to edit patients without a name) | `edicion` | same hands as creating a patient; the row is written BEFORE the edit, a row that can't be written refuses it; an unknown DNI is a 404 with no row |
| `POST /api/admin/table-export` of a table whose rows name patients (`/admin/cola`, `/admin/mis-turnos`, `/admin/my-patients`, the doctor's day in `/admin/doctors`) | the visible table downloaded as a CSV file (2026-11-07, `app/admin_panel/table_export.py`) | `descarga` (list, target = the screen path) | the screen's own permission; if it can't be recorded, no file |
| `GET /api/admin/patients/<dni>/data-export`, `scripts/subject_export.py` | ALL the person's data at once (Ley 25.326 art. 14; [derechos-de-los-titulares.md](derechos-de-los-titulares.md)) | `exportacion` (the script records it as `script:<operator>`) | permission `usuarios` (admin); if it can't be recorded, nothing is built |

Someone else's patient is a **404, not a 403** (the product owner's rule,
`admin-cola-y-agenda.md`: if you can't see it, it doesn't exist for you); the
access row still records the attempt as denied.

Lists write one row per patient shown and do not repeat the same (user, role,
action, patient, target) within the same day (installation date) and process:
they refresh themselves all day, and the question the log answers is «who saw
this person's data, on which day». Every refresh over ten years would be
hundreds of millions of rows. **List precision**: the first time of the day,
not every refresh nor the time of the last look; with several processes (or
after a restart) there may be one row too many, never one too few — a key is
marked «seen» only after its INSERT committed. Opening ONE thing (file,
interview, summary, referral) is recorded every time.

Deliberately outside the log: `/r/<token>` and `/o/<token>` (printed
prescription and order: a capability in the patient's hands, no panel
session), the patient's own routes over their own data, the
`/api/chat-transcripts` export (machine token) and summary pre-generation
(nobody reads it). The clinical history is only WRITTEN from the panel
(`POST .../clinical-history`, admin only); it is read through the summary.

## «A doctor only sees their own patients»

`access_log.scoped(identity, clinical)`:

- **Clinical screens** (`clinical=True`: summary, interview, consultation,
  external history, alerts, Mis pacientes): only the **admin** sees anyone.
  Every other role — the `medico` preset and any panel-created role holding
  «atención médica» — is held to the doctor linked to its account; with no
  linked doctor, to nobody. The cut used to check `rol == "medico"` only, so a
  custom role with that permission saw everything.
- **Desk screens** (`clinical=False`: requests, queue, referrals, file): by
  role, as always; only a `medico` is held to their own (even when granted
  «pacientes»).
- **«Ver pacientes de todos los médicos»** (`todos_los_pacientes`, el responsable del producto
  2026-10-23, q781376 option B): the one way past the hold short of admin —
  heads of service and audit. Off by default, in no built-in preset, granted
  per role or per user from the panel; read from the same store as the gate
  (`access_log.sees_all_patients`), so revoking it applies on the next
  request and an unreadable store keeps the hold. It lifts both cuts; every
  read it allows is recorded like any other. With no `?medico=`, Mis pacientes
  and the summary start from the account's own linked doctor (the doctor's
  screens never send one). The external history keeps its own `admin`/`medico`
  role check.

«Own patient» (`db_clinical.doctor_has_patient`): a request of that clinical DNI
assigned to the doctor's agenda (`assigned_doctor_id`) or booked in it
(`slot_doctor_id`), or an interview addressed to them (`interviews.doctor_id`),
with no date or status cut — the same definition as «Mis pacientes» with the
full history: a patient seen years ago, or a cancelled request that stayed
assigned, is still theirs. Whoever books for someone else (`requester_dni`)
does not become a patient. Interview and consultation are decided by the
interview itself (its `doctor_id`), not by the patient.

The doctor's exact-DNI search still finds someone who is not theirs (decision
2026-08-05: give a free slot to whoever is in front of them), identity only,
and it is recorded as `busqueda`.

## «Who saw this patient»

`GET /api/admin/patients/<dni>/access-log` — permission `usuarios` (the
admin): newest first, up to 500 rows, day and time already in the
installation's timezone. In the file, the «Quién abrió sus datos» section
(panel copy through i18n: `admin.queue.access_log.*`, Spanish fallback in code,
English in `locales/en`) appears only when the server sends `ver_accesos: true`,
and fetches the log on click. Looking at the log is not logged: it shows no
clinical data.

## Database role without schema rights

Until K10 the application connected with a single login that owned the
schema. It can now run as a role that only reads and writes data:

1. Boot once the version with revision 0026 (creates the table).
2. As the owner: `TURNOS_DB_MIGRATION_URL=<owner> python3 scripts/db_app_role.py`
   (dry-run: prints the SQL), then with `TURNOS_DB_APP_PASSWORD=<password>` and
   `--apply`. The role (`turnos_app` by default) has SELECT/INSERT/UPDATE/
   DELETE, sequences and functions; no CREATE on the schema, no TRUNCATE, no
   UPDATE on `clinical_access_log`; what the owner creates later reaches it
   through ALTER DEFAULT PRIVILEGES.
3. The installation boots with `DATABASE_URL` = the new role and
   `TURNOS_DB_MIGRATION_URL` = the owner: the boot brings the schema to head
   (Alembic, trigram indexes) as the owner and serves as `turnos_app`. If the
   two URLs point at different databases (name, database oid and server start
   time), the boot refuses.
4. After a revision that creates tables, re-running the script is idempotent
   (it re-applies the UPDATE revoke if the table was recreated).

Outside this: the queue's first use (`procrastinate`, only with
`TURNOS_QUEUE_WORKER`) applies its schema with `DATABASE_URL`: apply that
schema once as the owner before switching roles. Backup/restore tooling is the
operator's and uses its own login.

Tests: `tests/test_clinical_access_log.py`, `tests/test_access_log_routes.py`,
`tests/test_db_app_role.py`, `tests/test_admin_chats_screen.py`, and the K10
cases of `test_chart_summary.py`, `test_intake_consult_admin.py`,
`test_intake_alerts.py`, `test_doctor_patients.py`, `test_external_history.py`,
`test_legal.py` (the privacy clause).
