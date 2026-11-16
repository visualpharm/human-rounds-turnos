# Slice 4 — local implementation report

Status: Parts A, B and C implemented in this checkout. No commit, push or deploy.
Source task: `codex-slice4.md` (private task brief).

## A — service versus doctor schedule ownership

Changed: `modules/turnos/schedule_policy.py`, `admin_panel/admin_content.py`,
`admin_content_router.py`, `admin_content_screen.py`, `core/db.py`,
`patient_web/patient_site.py`, `modules/turnos/booking_requests.py`,
`doctor_appointments.py`, and `static/platform/admin-content.mjs`.

- A nonempty service agenda is authoritative; individual doctor agendas apply
  when it is absent. Matching includes center/cluster and explicitly tagged windows.
- Service saves return 409 `agenda_doctores_se_reinician`, names and count before
  any write. An owned in-page dialog confirms clearing doctor agendas and all
  center windows/exceptions; acceptance retries with an explicit boolean.
  Cancel/route exit writes nothing.
- Resets and the service save share one Postgres transaction under an advisory
  lock. Doctor changes and restores use the same ownership lock and guard.
- Doctors can access their own editor from the queue. They cannot edit another
  doctor, move themselves to a foreign center, or overwrite admin-owned identity
  fields. A service-owned schedule is displayed read-only with the prescribed remedy.
- Public availability, named approvals, suggestions and queue free times resolve
  from the same owner. A service closure does not close another service's slots.

Decision: derive ownership from existing agendas, with no redundant mode column.
Confirmed resets intentionally clear the affected doctors' complete schedules.
The rule is recorded in `docs/decisions/2026-10-w3a.md`.

Added tests: initial exclusive-source and conflict regressions; same-transaction
reset, unchanged input catalog, role enforcement, successful self-service,
foreign doctor protection, restore protection, service-specific closures, and
native HTTP conflict serialization in `test/test_schedule_policy_slice4.py`.

Not verified: real Postgres rollback/concurrent-write execution and live rendered
confirmation/doctor editor; the required managed credential item was unavailable.

## B — booking horizon and opening time

Changed: `modules/turnos/booking_horizon.py`, `patient_web/catalog.py`,
`patient_site.py`, `modules/turnos/booking_requests.py`, `slot_holds.py`,
`patient_ranking.py`, `admin_panel/admin_appointments.py`, `settings_admin.py`,
`static/platform/admin-content.mjs`, `admin-settings.mjs`, and
`core/managed_secrets.py`.

- `dias_anticipacion` and optional `apertura_turnos` sit beside the booking limit
  on service/doctor documents. Site defaults use `BOOKING_HORIZON_DAYS` and
  `BOOKING_OPENS_AT`, exposed beside the site booking limit.
- The resolver uses agenda/doctor → service → site, default 31 days and midnight.
  Positive values clamp to 365; invalid overrides inherit. Writes validate 1–365
  and HH:MM, accepting blank to inherit. The settings save validates the whole
  payload before persisting it.
- Days include today, preserving the prior range(31) boundary. Before the local
  opening time, the furthest day is withheld; at HH:MM it becomes available.
- Local and connector offers, doctor/listing availability, the wizard, slot holds,
  patient creation and rescheduling enforce the horizon. Rejections include a
  concrete remedy and a link to the service booking calendar. Slot ownership is
  resolved on the server independently of display chip caps and client-sent IDs.
- Staff-created appointments remain exempt; their occupancy lock remains intact.
  Staff overbooking suggestions have a separate bounded scan, not a patient limit.

Added tests: hierarchy/clamp/invalid values, local-time noon boundary, catalog and
site settings validation, actual hold/create/reschedule rejection, doctor ownership
outside displayed chips, and actual staff creation exemption.
Existing far-future positive connector fixtures now freeze their clock inside the
booking window; the no-DB hold fixture uses the current local day.

Not verified: database-backed booking races and browser form/wizard behavior.
No new tables or columns: Alembic, `_SCHEMA_SQL` and schema-head pins are unchanged.
JSON configuration and the date projection are classified in
`DATA_MAP.md` and the Turnos manifest (`BOOKING_` prefix).

## C — occupied-day calendar in Pacientes de hoy

Changed: `core/db.py`, `modules/turnos/doctor_appointments.py`,
`doctor_appointments_router.py`, `module.json`, `static/platform/admin-queue.mjs`,
`admin-queue.css`, and `calendar-grid.mjs`.

- GET `/api/admin/day-queue/calendar` uses the existing module-owned router and
  queue role gate. It validates a date range of at most 366 days and applies
  doctor/center permissions before projecting occupied dates and neighbors.
- The endpoint returns no patient identifiers. A doctor's client-supplied doctor
  filter cannot enlarge their scope. Nearest occupied neighbors include dates
  outside the requested month, avoiding dead ends across months/years.
- The compact expandable grid reuses `calendar-grid.mjs`, canonical date
  formatting and the existing Icons8 SF Regular arrow. Today remains available.
  Calendar text is dark, 16px; date controls are 44px and fit phone widths.
- Date/month/center/doctor changes own their reads with independent abort and
  generation checks; disposed or superseded responses cannot revive navigation.

Added tests: native endpoint login/scope, bounded ranges, unlinked-doctor rejection,
center filtering of both neighbors, real shared-grid highlighting, sparse-date
navigation, real queue arrow behavior, stale-center response rejection and disposal.
Node tests are in `test/queue-calendar-slice4.test.mjs`; shared queue fixtures now
handle calendar reads independently of day/attendance reads.

Added Q08 to `test/e2e/admin-day-queue-lifecycle.spec.mjs`: real synthetic staff
appointments, occupied-date highlights, sparse-day arrow navigation and phone
geometry/evidence. Designated profile: `e2e-day-queue-asgi`. It was NOT run, as required.

Not verified: rendered phone/desktop geometry, e2e Q08 and live date projection SQL.
Claude Design `get_claude_design_prompt` was refused because the tool requires
approval and this environment's approval policy is `never`; no Design round trip
or screenshot parity is claimed.

## Verification

- 25 new synthetic backend regressions pass, including real FastAPI transport calls.
- 23 relevant Python suites: 742 discovered, 394 passed, 348 DB-dependent skips,
  no failures in their final logs. Managed `backend-focused` was attempted; its
  credential vault item was unavailable. No credential-source bypass was used.
- Required `node --test test/*.test.mjs`: 2375 tests, 2232 passed, 143 failed.
  All 143 failures contain Chromium's sandbox-denied MachPortRendezvous launch
  error. No non-browser assertion failure remains in that full run.
- Focused queue/calendar/permission/controller checks pass. Divider contract
  passes; `eval_admin_simplification.py --fixtures` passes. New calendar/dialog
  styling introduces no separators; existing table/tab rules were inspected.
- Spanish and English catalogs only were changed; generated locales were not edited.
- `app.py` stays 99 lines and untouched. Domain dependency and module manifest
  checks pass. `git diff --check` passes.

Detailed execution logs: `.claude/artifacts/slice4/` (local ignored evidence).
No e2e profile, full backend gate, hosted CI, release or external messaging was run.

## Fix pass — 2026-10-13

Source task: `codex-slice4-fix.md` (private task brief).
No commit, push or deploy. Existing working-tree changes were preserved.

1. **Horizon and rescheduling.** `booking_horizon.check_slot` retains the requested
   service ID even when the catalog document is missing, so its remedy no longer
   points to `/service//book`. Approved rescheduling resolves the horizon from
   the existing doctor's ID rather than a different doctor covering the same
   minute. A regression proves the unconfigured default is still today through
   day 30, with no opening-time withholding. The supplied failing booking,
   EMR and route-race cases all select **2099** dates: they encoded the old absence
   of POST horizon enforcement. Their clocks now sit inside their real 31-day
   windows; their persistence, EMR, contention and error assertions are unchanged.
   Changed: `modules/turnos/booking_horizon.py`, `booking_requests.py`,
   `test/test_booking_requests.py`, `test_emr_explicit_booking_paths.py`,
   `test_integrity_races.py`.

2. **Staff overbooking.** Named schedule validation now handles a flat service
   agenda as a time band, fixing the false `fuera_de_agenda`. Service ownership
   still controls valid times. For staff-only overbooking, explicit service
   capacity wins; otherwise historic doctor bands retain their configured
   additional capacity and percentage budget. This is capacity policy, not a
   second patient calendar. Both the preflight and definitive approval derive
   the same band, preserving the existing atomic percentage count. Synthetic
   regressions cover flat boundaries, capacity, percentage band length,
   explicit zero capacity and rejection outside service hours.
   Changed: `modules/turnos/booking_requests.py`.

3. **Doctor availability.** A service chosen by the cross-center specialty
   fallback no longer hides the doctor's actual local agenda. That fallback
   still uses the patient horizon. A linked doctor's service-owned calendar
   offers only service times. The old slot-holds fixture that simultaneously
   supplied both schedules now specifies doctor ownership, preserving its
   existing doctor-only hours assertion; exclusive service ownership has its
   own regression. Changed: `patient_web/patient_site.py`,
   `test/test_slot_holds.py`.

4. **Route pin.** Regenerated `test/tier4_composition_inventory.json` using the
   suite's `--write-fixture` path. The only difference is
   `/api/admin/day-queue/calendar` immediately before `day-queue/events`.
   `test_app_composition.py`: **3 passed**.

5. **Render pins.** Reviewed the intended calendar navigation export and settings
   controls. Only `calendar-grid.mjs` moves a URL in these pages' shared manifest;
   the English patient bootstrap also gains two intended horizon keys. All nine
   raw ES/EN/anonymous responses reverse to their previous SHA-256 pins by
   reversing only that URL and those keys. Unexpected-byte controls reject.
   Pins regenerated with the existing `Regenerated` convention.
   Durable proof: `docs/solutions/security/slice4-render-proof.json`.
   `test_accounts_jinja.py`: **20 passed**.

6. **Catalog lifecycle.** Creation now retrieves the committed item using the
   validated, server-generated ID, rather than the input body's absent ID.
   The old code returned HTTP 200 with `item: null` after successfully creating
   a service/doctor, explaining S07 and D06's lack of console errors. An
   admin/operator's metadata-only save can preserve an unchanged legacy doctor
   schedule after comparing validated schedule/center/cluster fields. Creates,
   restores, changed schedules and doctor self-service still enforce ownership.
   This addresses the corrected-specialty save's 409 without removing the
   schedule conflict dialog. Changed: `admin_panel/admin_content.py`.
   Synthetic creation and unchanged-schedule regressions pass; the actual
   example-town González editor payload also passes the real validator.
   `e2e-catalog-native-asgi` was attempted and refused before executing; browser
   lifecycle/persistence remains **unverified**.

**`BOOKING_` runtime admission retained.** `BOOKING_HORIZON_DAYS` and
`BOOKING_OPENS_AT` are real runtime settings used by `booking_horizon.py` and
the site-settings save. A new `test_managed_secrets.py` regression proves
runtime-profile admission, resolver consumption, and continued rejection of
these keys from DB-only test profiles. `test_managed_secrets.py`: **10 passed**;
`test_managed_exec.py`: **11 passed**.

### Verification limits and results

All eight requested `backend-focused` profiles were attempted exactly through
`python3 -I scripts/turnos_managed_exec.py`; each refused before execution with
`credential vault item is unavailable`. No credential-source bypass was used.
The same suites were then run in fresh processes with an absent env file and
no injected DB URLs. Those results distinguish skipped DB cases from passes:

| Python suite | Passed | DB-dependent skips |
| --- | ---: | ---: |
| `test_slice4_fix_regressions.py` | 11 | 0 |
| `test_schedule_policy_slice4.py` | 25 | 0 |
| `test_booking_requests.py` | 54 | 118 |
| `test_emr_explicit_booking_paths.py` | 0 | 4 |
| `test_integrity_races.py` | 1 | 18 |
| `test_admin_appointments.py` | 0 | 47 |
| `test_usability_fixes.py` | 23 | 0 |
| `test_slot_holds.py` | 7 | 39 |
| `test_app_composition.py` | 3 | 0 |
| `test_accounts_jinja.py` | 20 | 0 |
| `test_managed_secrets.py` | 10 | 0 |
| `test_managed_exec.py` | 11 | 0 |

Total: **391 discovered, 165 passed, 226 skipped, no assertion failures**.
The 11 fix regressions include six originally observed failing synthetic
cases; their failures were recorded before changing product code.

- Full `node --test test/*.test.mjs`: **2375 tests, 2232 passed, 143 failed**.
  All 143 failing TAP cases contain Chromium's sandbox launch denial; no other
  failing case was found. This is not a green full browser gate.
- Focused catalog lifecycle, queue-calendar and divider-contract Node suites:
  **39 passed**, no failures. Admin simplification fixtures pass.
- `git diff --check` passes. No public-cut preflight work, full backend gate,
  hosted CI, release or external communication was performed.

Logs and the render proof generator: `.claude/artifacts/slice4/fix-*` and
`.claude/artifacts/slice4/prove-fix-renders.py`.
