# Admin forms audit — save, cold reload, the value is on the page (TASK-3)

Inventory of every admin-panel write (form, dialog, inline toggle, delete/disable, saved
preference) and the Playwright e2e that proves it the way staff experience it: the real
control saves to the real server (ASGI + an isolated PostgreSQL), the page is cold-reloaded
or re-opened, and the stored value is what the page shows. A test that mocks the API
(`page.route` fulfilling the write) or only reads the API after the reload does not count.

Taken 2026-10-25 (inventory on main `80323d3f`, final runs on `1c8b2c87` plus this change). Specs added by the audit:
`tests/e2e/admin-forms-audit.spec.mjs` (system / account screens),
`tests/e2e/admin-forms-audit-desk.spec.mjs` (Solicitudes, Pacientes de hoy, Pacientes),
`tests/e2e/admin-forms-audit-catalog.spec.mjs` (services, centers, doctors, preparations,
onboarding, entrevistas). Run one file through the managed executor:
`python3 scripts/turnos_managed_exec.py --profile e2e-forms-audit-asgi` (also
`-desk-asgi`, `-catalog-asgi`, and `e2e-forms-roundtrip-asgi` for the older file).

Legend: **FULL** = covered as above · **ADDED** = covered by the audit · **OPEN** = still without
that proof (reason given) · **n/a** = writes nothing a person sees again. Preferences
(filters, sort, columns, last role) are also checked from a second browser signed in as
the same admin, so a value replayed by the tab alone cannot pass.

## Lost or stale writes found

- **Saved preferences (filters, sort, columns, «last chosen role») could come back old.**
  `static/platform/admin-preferences.mjs` debounces each PUT 180 ms and flushes on
  `pagehide`; the next page's `GET /api/admin/preferences` raced that PUT and, when it
  answered first, the screen showed the previous value (the «last chosen role» check failed
  2 runs of 3). Fix: an unacknowledged change stays in the tab's `sessionStorage` outbox
  until its PUT answers; the next page lays it over what it read and sends it again. Failing
  test first: `tests/admin-preferences.test.mjs` «a change flushed on pagehide…».
- **«Volver a procesar» on a doctor's portrait could not be pressed.** The two portrait
  buttons sat in a second `.cnt-foto-actions` grid nested inside the 280 px column of the
  first, so the second button slid under the upload hint, which took the click. They now
  have their own full-width row (`.cnt-foto-portrait`, `admin-direct.css`). Failing test
  first: admin-forms-audit-catalog «Doctors: a new doctor's photo…» (the click was
  intercepted; the test now also checks each visible button receives its own click at
  1280 and 390 px).

- **Bulk approve gave every row the same time, so all but one failed.** A request with no
  time chosen by the patient and no suggested slot defaulted to the next weekday 09:00
  (`turnoDefault`, `admin-app.js`); every such row of a bulk approval got that same time,
  the second answered 409, and once any approval had taken that 09:00 every later one
  failed too. Decided 2026-10-25 (pinamar-turnos): each row of the batch takes the next
  free time of its service (`turnoDeLote`: the patient's chosen time first, then a free
  suggestion the batch has not used, then the next free slot from the desk agenda).
  Failing test first: desk «bulk approve of two same-service requests…» (409 before).
- **One incomplete doctor blocked a whole bulk «Activar captura de síntomas».** A doctor
  without a center is not in `catalog.doctors()`, and `intake_admin.set_doctors_effective`
  answered 404 for the whole selection and saved nobody. Decided 2026-10-25: such a doctor
  is skipped and counted as «con datos pendientes», like the other bulk actions; only a
  selection with no catalog doctor at all is still 404. Failing tests first:
  `tests/test_intake_settings.py` and catalog «bulk symptom capture saves…» (404 before).

## Coverage by screen

### Solicitudes, Pacientes de hoy, Pacientes (desk)

| Form | Endpoint | Status | Proof |
|---|---|---|---|
| Approve (detail) | POST requests/{id}/approve | FULL | journey-j11 (seen on /admin/mis-turnos) |
| Reject (detail) | POST requests/{id}/reject | ADDED | admin-forms-audit-desk «rejected request…» |
| Doctor picked in the detail | POST requests/{id}/assign | ADDED | desk «doctor picked…» |
| Bulk approve / bulk-reject dialog | approve / reject | ADDED | desk «bulk approve and bulk reject…», «bulk approve of two same-service requests…» |
| Status filter (saved preference) | PUT preferences | ADDED | desk «status filter…» |
| Attendance in the Solicitudes dialog | POST requests/{id}/attendance | OPEN | needs an approved appointment already in the past; the same endpoint is ADDED through Pacientes de hoy |
| Retry HSI | POST requests/{id}/retry-hsi | OPEN | needs an HSI stub that accepts the push |
| Inline correction of an AI-read order field | POST requests/{id}/campos | OPEN | needs a seeded request with an order reading |
| «Crear turno» modal + new patient inside it | POST appointments, POST patients | FULL | admin-forms-roundtrip (from /admin/cola; the same component on the other screens) |
| Attendance buttons | POST requests/{id}/attendance | ADDED | desk «attendance… cancelled…» |
| Cancel appointment | POST requests/{id}/cancel | ADDED | desk «attendance… cancelled…» |
| Column choice | PUT preferences | ADDED | desk «column choice…» |
| Doctor's own symptom-capture switch | POST intake/my-settings | FULL | admin-forms-roundtrip |
| New / edit patient, dependents | POST/PUT patients, dependents | FULL | admin-forms-roundtrip |
| Archive / reactivate patient | DELETE / PUT patients/{dni} | ADDED | desk «archive and reactivate…» |
| Patient search (saved) | PUT preferences | ADDED | desk «archive and reactivate…» |

### Catalog: services, centers, doctors, preparations, onboarding, entrevistas

| Form | Endpoint | Status | Proof |
|---|---|---|---|
| Service / center / doctor create and edit | POST/PUT content/* | FULL | catalog-screens-lifecycle, service-save-booking-window, doctor-edit-service-owned, catalog-editor |
| Service-owned schedule + reset dialog | PUT services/{id} | ADDED | admin-forms-audit-catalog «service-owned schedule…» |
| Edit → «Restaurar original» (editor) | POST services/{id}/restaurar | ADDED | catalog «edit, Restaurar original, Deshabilitar…» |
| Deshabilitar (editor) and restore from the list row | DELETE / POST restaurar | ADDED | same test (centers' disable FULL in catalog-screens-lifecycle) |
| Center photo before the first save; remove photo | POST/DELETE centers/{slug}/photo | ADDED | catalog «Centers: a photo…» |
| Existing center photo, doctor photo upload | POST …/photo | FULL | center-photo-create, admin-forms-roundtrip |
| New doctor's photo; «Restaurar original» / «Volver a procesar»; Quitar | POST/DELETE doctors/{id}/photo | ADDED | catalog «Doctors: a new doctor's photo…» |
| «Invitar» after creating a doctor; User tab «Desactivar» | POST users/actions/invite, disable | ADDED | catalog «Invitar after creating…» |
| User tab: create with password, link existing user, reset password, reset 2FA | POST users/actions/* | OPEN | reset password needs a doctor email; the rest is the same panel as invite/disable |
| «Invitar médico» form | POST content/doctors {_invitar} | ADDED | catalog «Invitar médico form…» |
| Bulk invite, bulk symptom capture | POST users/actions/invite, intake/settings | ADDED | catalog «bulk invite and bulk symptom capture…», «bulk symptom capture saves the complete doctors…» |
| Síntomas tab switch | POST intake/settings | ADDED | catalog «Síntomas tab…» |
| Entrevistas per-doctor checkbox | POST intake/settings | FULL | admin-forms-roundtrip |
| Entrevistas shift-range and select-all | POST intake/settings | ADDED | catalog «Entrevistas…» |
| Preparations: edit | PUT preparations/{id} | FULL | admin-screen |
| Preparations: new, Deshabilitar, Habilitar | POST / DELETE / POST restaurar | ADDED | catalog «Preparations…» |
| Catalog filters, sort, column choice | PUT preferences | ADDED | catalog «Catalog lists…» |
| Onboarding: profile, schedule, portrait upload | PUT doctors, POST onboarding/complete, photo | FULL | admin-forms-roundtrip |
| Onboarding: rotate portrait | POST doctors/{id}/photo {rotate} | OPEN | the rotation is baked into the image; nothing on the page shows the angle |
| Consult: confirm documents | POST intake/consult/{id}/confirm | FULL | admin-screen, journey-j12 |
| Consult: patient summary «Actualizar» | POST patient-summary | OPEN | stores a regenerated summary; the page only says «Generado recién», so a proof needs a fixture whose summary text changes between generations |
| HSI: resolve conflict, «Volver a revisar» | POST hsi-sync/conflicts, resolver | OPEN | needs a real conflict fixture; today only mocked (admin-hsi-lifecycle) |

### Account, users, roles, settings, appearance, content screens

| Form | Endpoint | Status | Proof |
|---|---|---|---|
| Account email, 2FA on/off, photo upload | account/*, mi/foto | FULL | admin-forms-roundtrip |
| Account password change | POST account/password | ADDED | admin-forms-roundtrip (now signs in through the form) |
| Account username rename | POST account/password {usuario} | ADDED | admin-forms-audit «username field renames…» |
| Account photo remove | DELETE mi/foto | ADDED | admin-forms-roundtrip (Quitar hidden after reload) |
| Passkey register / delete | passkeys/* | ADDED / FULL | passkeys (list reloaded) |
| Appearance: theme, sign-in mode, Turnstile, brand color, logo upload | theme, auth-mode, turnstile, branding | FULL | admin-forms-roundtrip, admin-appearance-lifecycle |
| Appearance: logo remove | DELETE branding/logo | ADDED | admin-appearance-lifecycle (reloaded) |
| Wiki create, update | POST/PUT content/knowledge | FULL | admin-forms-roundtrip |
| Wiki disable, restore | DELETE / POST restaurar | ADDED | admin-forms-audit «Wiki: a disabled article…» |
| Legal save, restore | PUT/DELETE legal/{id} | FULL | admin-forms-roundtrip |
| Login: forgot password → link page | POST reset-password | ADDED | admin-forms-audit «Mail links…» |
| Prompts: save English / Spanish, restore | POST prompts/{k}/guardar, DELETE | FULL | admin-forms-roundtrip |
| Prompts: «Esta versión» from the history | POST prompts/{k}/guardar | ADDED | admin-forms-audit «Prompts…» |
| Roles: create, edit (name, permissions), enabled | POST/PUT roles | FULL | admin-roles-save |
| Settings: each general card's first field, one text field per integration card, one secret, HSI toggle | POST settings | FULL | admin-forms-roundtrip, hsi-disabled (the other integration fields — toggles, choices, the remaining secrets — share the same card save but are not each asserted) |
| Settings: the other location, languages and booking-window fields | POST settings | ADDED | admin-forms-audit «location, languages and booking-window fields…» |
| Settings: holidays add / rename / remove | POST settings {feriados} | ADDED | admin-forms-audit «holidays…» |
| Settings: backup recovery key, generate and rotate | POST settings {backup_key} | ADDED | admin-forms-audit «backup recovery key…» |
| Usability: add, edit finding | usability/findings | FULL | admin-forms-roundtrip |
| Usability: bulk status | usability/findings/{id} | ADDED | admin-forms-roundtrip (status shown after reload) |
| Usability: «Analizar respuestas» | POST usability/analyze | OPEN | stores findings derived from study sessions; needs seeded study results to produce any |
| Users: create, edit email/role | POST/PUT users | FULL | admin-roles-save |
| Users: centers, forced 2FA, remove 2FA, admin-set password, active, Desactivar | PUT/DELETE users/{u} | ADDED | admin-forms-audit «Users: centers…» |
| Users: create by email invitation → link page | POST users {access_mode: invite} | ADDED | admin-forms-audit «Mail links…» |
| Users: role/status filters, sort, column choice, last chosen role | PUT preferences | ADDED | admin-forms-audit «Users list…» |
| Users: doctor link on a médico account | PUT users/{u} {medico_id} | OPEN | doctor accounts are created and linked from Médicos since 2026-10-19 |
| Column widths (drag) on every table | PUT preferences table.widths.* | OPEN | the same preference module as the column choice; the drag itself is untested |
| AI register «Notified» | POST ai-register/notify | FULL | admin-ai-register |
| System, Plan, Reports, My schedules, Chats | — | n/a | read-only screens |
