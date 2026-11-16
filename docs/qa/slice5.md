# Slice 5 — doctor onboarding, portraits and own schedules

Implemented locally in this checkout. No commit, push, deploy or browser e2e.
Source task: `codex-slice5.md` (private task brief).

## 1. First-login onboarding

Files: admin_panel/doctor_workspace.py, doctor_workspace_screen.py,
admin_navigation.py, admin_content.py; templates/admin/doctor-workspace.html;
static/platform/doctor-workspace.mjs and doctor-workspace.css;
core/db.py, core/static_fingerprint.py; alembic/versions/0017_doctor_onboarding.py;
modules/turnos/module.json; locales/{es,en}/admin.doctor.json.

- The manifest declares /admin/onboarding and its completion API. A linked
  medico with a null operator completion timestamp lands there, ahead of a
  saved screen or explicit continuation. Después goes directly to the ordinary
  landing; other pages are not intercepted. Completion is persisted on the
  current operator and returns the ordinary landing only after a successful
  database acknowledgement.
- One page contains first/last name, the same catalog specialty choices, email,
  phone, portrait upload/preview/rotation/replacement and weekly schedule.
  Saves use the existing doctor PUT and validator. Doctor IDs, licenses,
  booking policy and center membership remain protected.
- Existing portraits, including the offline portrait fallback, render in the
  initial HTML. Empty doctor-owned schedules immediately expose seven day
  controls and hours. Service-owned schedules show authoritative hours and the
  slice 4 read-only sentence once.
- Completion uses nullable operators.onboarding_completed_at, not a second
  flag in the doctor catalog. Fresh DDL, forward migration after 0016,
  test_alembic_cutover's head pin, DATA_MAP and manifest classification agree.
  Completion is idempotent. Spanish/English catalogs only were edited.
- Client operations own their requests, readers and listeners; disposal
  suppresses late acknowledgements. Save acknowledgement must contain the
  requested doctor's actual item. Unsaved changes prevent completion.

Tests added: first-login versus completed landing, seven-day empty editor,
service ownership, real ASGI screen output and completion role matrix,
canonical self-profile validation/protected fields; client false/missing/wrong
acknowledgement, disposal and independent unsaved profile/schedule handling;
an existing-0016 migration/idempotent completion regression.

Not verified: actual Postgres upgrade/completion and catalog persistence;
rendered phone/desktop geometry and full login/skip/finish browser journey.
The new DB upgrade case is present but skipped without the managed credential.

## 2. Local photo pipeline

Files: patient_web/portrait_processing.py, doctor_photos.py,
doctor_photos_router.py; admin_panel/settings_admin.py;
static/platform/admin-content.mjs, admin-settings.mjs and doctor-workspace.mjs;
locales/{es,en}/admin.doctor.json; DATA_MAP and catalog architecture notes.

- JPG/PNG/WebP uploads use the existing doctor photo endpoint. Pillow applies
  EXIF transpose, explicit quarter rotation, mild contrast/exposure/sharpening,
  center-top square framing and a 512px WebP. Small avatars retain the existing
  avatar_sprite.py atlas path; no new per-avatar file is introduced.
- Private version bundles retain the original bytes and status. One atomic
  public symlink commits portrait, source and metadata together. Failed
  publishing preserves the previous image and original. Legacy uploaded
  photos can be adopted without modifying their source. Private original
  files have no public route. Removing a doctor photo removes its bundles.
- Original / Procesada appears in the doctor form, with Restaurar original and
  Volver a procesar. Rotation always starts from the preserved original.
  The Settings checkbox Mejorar fotos automáticamente defaults on; disabling
  enhancement produces the oriented/resized original. Explicit reprocessing
  can still apply enhancement.
- The doctor endpoint admits medico only for the session's linked doctor;
  a foreign doctor or center photo is rejected. No external AI, optional
  connector or cost was introduced.

Tests added: synthetic EXIF/user rotation, dark-image brightness, square
output, byte-identical source, restore/reprocess, invalid image, actual
setting-off upload, failed atomic publication, legacy adoption, bundle
removal and real native own/foreign photo permissions. Existing photo tests
now expect the processed WebP and separately verify original preservation.
Existing atlas and atlas-router suites also pass.

Not verified: live filesystem/volume deployment and real institution photos;
actual browser rotation/replace/restore and settings persistence against DB.

## 3. Mis agendas

Files: admin_panel/doctor_workspace.py and doctor_workspace_screen.py;
templates/admin/doctor-workspace.html; static/platform/doctor-workspace.css;
modules/turnos/module.json; core/screen_nav.py and permissions.py;
locales/{es,en}/admin.doctor.json.

- /admin/my-schedules is a real manifest route, under the medical agenda
  permission and an explicit medico/admin role check. Its sidebar destination
  is medico-only. Each assigned center/consultorio lists weekly days/hours;
  editable rows link to the existing doctor's agenda editor.
- Decision: use a compact center list at both phone and desktop widths; the
  optional desktop week grid was not needed. Schedule authority comes from
  the existing slice 4 policy. Any applicable managed service keeps the
  doctor's whole schedule editor read-only, preserving that prior decision.

Tests added: synthetic multi-center/service-owned projection and real screen
role matrix. Updated Spanish/English doctor navigation goldens; unchanged
admin/admission navigation remains pinned.

Not verified: actual multi-center catalog records and rendered geometry.

## 4. Remove doctor requests navigation, preserve scoped access

Files: core/permissions.py, auth/admin_auth.py, admin-app.js,
modules/turnos/module.json, core/screen_nav.py, templates/admin/base.html,
locales/{es,en}/admin.system.json; the 0017 migration.

- Solicitudes is hidden from medico navigation, including permission-aware
  navigation and explicit grants. The role retains its requests API and direct
  screen permission; existing own-doctor filters still apply. The forward
  revision leaves stored role permissions and account overrides intact. Old
  default role descriptions are updated only if unchanged.
- Request access stays reachable during permission-store degradation. The
  shared brand link retains its existing /admin/solicitudes contract. Login
  landing remains role-aware: first-login onboarding, then Pacientes de hoy;
  saved or explicit Solicitudes destinations do not become doctor landings.
  Own doctor editor continuations remain valid after login.

Tests added: role defaults, direct screen and scoped API gates with
normal/unavailable permission store, navigation absence and preserved
admin/admission permissions. Updated the
permission parity expectation to include the current role for own-doctor APIs.

Not verified: persisted migration and DB-backed full role parity.

## Verification

- 22 relevant Python suites: 489 discovered, 349 passed, 140 database-dependent
  skips, no failures. Includes 20 new synthetic/native workspace regressions.
- The requested node --test test/*.test.mjs ran: 2386 tests, 2243 passed,
  143 failed. Every failing case contains Chromium's sandbox-denied
  MachPortRendezvous launch error; no other assertion failure was found.
  This is not a green full browser gate.
- Nine new client regressions and both divider contracts pass. The admin
  simplification fixture evaluator passes. New CSS/template surfaces contain
  no separators or border-top/border-bottom dividers; touched existing photo,
  settings and shared-shell contexts were inspected.
- Three intended manifest routes are the sole route-inventory additions.
  app.py remains unchanged at 99 lines. Module manifest, dependency-direction,
  Alembic single-head and git diff --check checks pass.
- One existing queue calendar test assumed 2026-10-14 could not be Today.
  Its exact occupied-day assertion now permits the independent medcal-hoy
  class; the selection/date/occupied requirements are unchanged.
- The managed backend-focused profile was attempted and refused because its
  credential vault item was unavailable. No alternate credential source was
  used. DB-less runs use an absent env file, blank DB URLs and synthetic data.
- Claude Design get_claude_design_prompt was refused: the tool requires
  approval and this environment's policy is never. No Design round trip,
  screenshot parity or rendered visual verification is claimed. The primary
  agent continued locally after the required GLM wrapper could not write its
  protected attempt ledger; no external GLM execution is claimed.

Proposed e2e profiles, NOT run:

- e2e-admin-auth-asgi — first login, skip, finish and later login landing.
- e2e-catalog-native-asgi — profile validation, portrait controls and agenda save.
- e2e-doctor-native-asgi — own scope, center schedules and role navigation.
- e2e-settings-asgi — the automatic photo checkbox and persistence.
- e2e-day-queue-asgi — normal doctor landing after skip/completion.

Execution logs: .claude/artifacts/slice5/. Requirements:
design-plans/slice5-requirements.md. Decisions are recorded in docs/decisions/2026-10-w3a.md;
atomic storage recovery is recorded in docs/solutions/dev/doctor-portrait-atomic-source.md.

## Slice 5 corrective verification (2026-10-14)

Task: `codex-slice5-fix.md` (private task brief).
No commit, push or deploy.

- Restored medico requests permission and manifest direct-screen eligibility,
  including the degraded auth path. Navigation hides only its Solicitudes
  destination; saved/explicit Requests landings still resolve to Pacientes de
  hoy after onboarding. The migration preserves role permissions. The shared
  brand link is restored to its pinned literal destination.
- Added real ASGI regressions for a hidden medico nav plus 200 requests API
  returning only the linked doctor's rows, with both readable and unavailable
  permission stores. The direct screen remains 200 in both cases. Updated only
  tests that pinned slice 5's mistaken access removal or new section inventory.
- Census pins agree with the composed inventory: composition 376, historical
  route-parity 231 (unchanged), native 643, unique native 457.
- The photo-enhancement checkbox now copies clinical-sync's set-toggle/span
  markup. Both toggles share the existing 20px checkbox size and font; their
  labels remain 16px. Added their eval 3 pair; no dividers were introduced.

Verification: 12 Python suites, 537 discovered, 311 passed and 226 DB-dependent
skips. Suites: slice5_doctor_workspace, booking_requests_router_native,
doctor_appointments, booking_requests, app_modules, admin_screen_contract,
usability_fixes, roles_parity, legacy_fallback_census, app_composition,
admin_screen_nav and alembic_cutover. All executed cases passed. roles_parity's
two cases and every doctor_appointments case were skipped, so no DB-backed
parity claim is made. Four Node suites (admin-settings-ui, admin-settings-direct,
ui-evals and admin-divider-contract): 46 passed. Admin simplification fixtures
and git diff --check passed. Logs are fix-*.log in .claude/artifacts/slice5/.

Managed backend-focused, e2e-settings-asgi and local-ci were attempted; each
refused because the managed credential vault item is unavailable. Real DB
scope/patient edits, stored-role upgrade, and browser geometry at
360/390/768/1024/1280px remain unverified. No private-pack or public-preflight
changes were made.
