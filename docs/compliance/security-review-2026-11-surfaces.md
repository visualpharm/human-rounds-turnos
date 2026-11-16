# Security review of patient data — external surfaces and trust boundaries (2026-11-04)

Scope: what a stranger, a patient, a staff member or a provider can reach from outside,
who vouches for them at each boundary, and which file enforces it. Source for the sprint
review of 2026-11-04 (findings, severities and cards: see the end of this file).

Installations (trust differs): `salud.pinamar.gob.ar` is live; patients and staff come in
only through the municipal portal (`TURNOS_PATIENT_PORTAL_LOGIN=1`, `TURNOS_STAFF_PORTAL_LOGIN=1`,
identity header `X-User-Cuit` set by the portal gateway). `pinamar.humanrounds.org` (test portal
`test-portal-v1`) and `demo.humanrounds.org` (DNI-photo sign-up, read-only demo access) are test
installations; the code default for any other installation is password mode.

## Boundaries and who enforces them

| # | Surface | Who vouches | Enforced in |
|---|---|---|---|
| 1 | Patient sign-up, login, recover, reset (`/api/account/register`, `login`, `recover`, `reset`, `reset-otp`) | portal on salud (routes closed with 403 `acceso_por_portal`); password / test portal elsewhere | `app/cuentas/accounts_router.py` (`_portal_only`), `app/cuentas/accounts.py` (`_register_core`, `_login_core`, `portal_entry_closed`), `app/cuentas/portal_identity.py` |
| 2 | Password reset links and OTP codes | single-use random token, stored as sha256; OTP stored as sha256, 5 tries, 10 min | `app/core/db_accounts.py` (`create_reset_token`, `consume_reset_token`, `set_password`, `create_otp`, `verify_otp`) |
| 3 | Patient account and its own data (`/api/account/*`, `/account/lab-results/*`, booking, dependents) | patient session cookie; dependents are verified by an affidavit, not by a document | `app/cuentas/accounts.py` (`current_user`, `_add_dependent_core`, `_update_core`), `app/ai/lab_results_router.py`, `app/modules/turnos/booking_requests.py` (`_step_identity`, `_patient_detail_core`) |
| 4 | Staff login (`/admin/login`, `/api/admin/login`) | password + optional TOTP + Turnstile, passkey, or the portal on salud | `app/auth/admin_auth.py`, `app/auth/panel_login.py`, `app/auth/*` |
| 5 | Staff routes by role (`/admin/*`, `/api/admin/*`) | role read from the database on every request; unmapped routes fail closed | `app/core/permissions.py` (`_PREFIXES`, `required_permission`, `access_for`, `legacy_denial`), `app/core/deps.py`, snapshot `tests/snapshots.json` (`admin_access`) |
| 6 | Doctor sees only own patients | `access_log.scoped`, `db_clinical.doctor_has_patient` (assigned or booked request, or interview addressed to the doctor) | `app/core/access_log.py`, `app/core/db_clinical.py`, `app/admin_panel/admin_patients.py`, `app/modules/turnos/doctor_appointments.py` |
| 7 | Clinical access log (append-only, denied attempts too) | trigger on `clinical_access_log` | `app/core/access_log.py`, [registro-de-accesos.md](../architecture/registro-de-accesos.md) |
| 8 | Demo access (`/admin/demo-access`, `/admin/demo-doctor-access`) | demo installation only; session is read-only | `app/auth/admin_auth.py` (`_es_cuenta_demo`, `evaluate_admin_gate`) |
| 9 | WhatsApp webhook (`/api/whatsapp/webhook`) | shared secret (`TURNOS_WA_WEBHOOK_SECRET`) or Meta HMAC-SHA256 (`META_WA_APP_SECRET`); 404 without any secret; 1 MB cap | `app/modules/chat/whatsapp_chat_router.py`, `app/modules/chat/whatsapp_chat.py` |
| 10 | PII toward external AI | scrubbed and restored; the referral image is the one deliberate exception | `pii.py`, [legal.md](../architecture/legal.md) |
| 11 | Data-subject export and erasure | admin permission, access-logged | `app/core/subject_export.py`, `app/core/subject_erase.py`, `app/core/personal_data.py` |
| 12 | Backups and remote storage | on `*.gob.ar` the application never deletes or overwrites a backup | `app/core/backup_lock.py`, `tests/test_salud_guards.py`, `tests/test_backup_lock.py` |

## What was exercised and what was only read

Read from code (no live traffic, nothing written to any live installation): surfaces 1-3, 5-9.
The candidates that survived are listed below with their reproduction.

## Findings

No critical finding. Reproduction means a test on a local installation (red before the fix,
green after) or a code path read end to end; the live test sites were only read.

| ID | Severity | Surface | Reproduction | Status |
|---|---|---|---|---|
| C-1 | High | 5 / referral file of a request (`/api/admin/order/{id}`, `/api/admin/my-appointments/{id}/order`) | `tests/test_referral_served_type.py`: a file with HTML or SVG bytes and a patient-declared `text/html` was served inline with that type under the site origin (stored XSS against staff) | Fixed: the served type comes only from the sniffed bytes, anything else is an attachment `application/octet-stream` with `nosniff` |
| C-2 | High | 4 / staff session | `tests/test_session_revocation.py::TestPasswordChangeClosesOtherSessions`: a copied `st_admin` cookie kept working after the user or an admin changed the password | Fixed: the cookie carries a short stamp of the password hash and dies when it changes; the browser that changed the password gets a fresh cookie; owner cookies minted before the deploy stay valid |
| M-1 | Medium | 1 / sign-up in password mode | Read: the first person to register a DNI owns the account (no proof of possession beyond the photo) | Card TASK-68 |
| M-2 | Medium | 6 / doctor scope | Read: a booking made by a doctor assigns the patient to them; no access-log row at assignment | Card TASK-69 |
| M-3 | Medium | 7 / access log | Read: the patient-history POST and the billing CSV read clinical data without a `clinical_access_log` row | Card TASK-70 |
| M-4 | Medium | 5 / role `asistente` | Read: the role can read interview text | Closed 2026-11-15 (TASK-71): «Chats» shows interview rows only to the administrator or `todos_los_pacientes`; the interview APIs answer 403 to `asistente` alone |
| M-5 | Medium | 1, 4 / login and recovery | Read: Turnstile host taken from the request, no lockout by DNI, OTP check race and no rate limit on reset-by-code, part of the cookies without `Secure` | Card TASK-72 |
| L-1 | Low | several | Read: prescription links without expiry, TOTP re-enrol without password, upload read before the size check, `/setup`, demo sessions, DNI/email occupancy disclosure, TOTP change does not close sessions | Card TASK-73 |
| Closed | - | 2 | TASK-46 (a password change kills the other reset links) and TASK-47 (reset tokens and OTP hashed) are already in `main` with tests | Cards marked done |
| Not a finding | - | 9 | TASK-23 is about routing demo and prod replies through one Meta callback; signature and sender checks hold | Card stays open as a functional item |

Not reviewed (state it to whoever plans the next pass): the data-subject export routes,
`settings_admin`, `system_status`, `client_errors`.

For `salud.pinamar.gob.ar` only the release package carries C-1 and C-2 (no schema change,
no migration, no new environment key).
