---
title: One-click doctor access without duplicate accounts or lost credentials
scope: repo
area: security
tags: [admin, doctors, clipboard, concurrency]
created: 2026-09-04
refs: [design-plans/doctor-existing-user-link-requirements.md]
status: active
---

## Problem
Creating a doctor's account needs one click, while linking should suggest an
existing account. Concurrent requests must not create multiple doctor accounts.

## Root cause
An application-level lookup before insert can race. Clipboard writes initiated
only after a network response can lose browser user activation.

## Solution
The shared operator create/update writers take a transaction advisory lock for
the target doctor before checking assignments. Linking also locks the user row
and refuses a concurrent reassignment. Expected conflicts become localized errors.
The UI starts a promise-backed ClipboardItem within the click, resolves it with
server-generated credentials, and keeps visible access details plus a copy action
if permission is denied. Plaintext is not stored or returned in account lists.

## How to apply
Use operator_create/operator_update, not direct account SQL. Reuse the existing
create_user domain for validation and the normal 2FA deadline. Matching is a
suggestion only: exact email wins, name ties stay unselected.
Run test/test_admin_users.py, test/doctor-user-defaults.test.mjs and
test/doctor-user-onboarding.browser.mjs for concurrency and browser recovery.

## Doctor email onboarding and recovery (2026-10-13)

The Doctors access form accepts email in both modes: generated credentials
shown once, or an invitation that lets the doctor choose their own password.
Users creation also supports `access_mode: "invite"`. Invitation passwords
are generated privately and never returned. Both entry points reuse
`create_user`, including its 2FA deadline and validation.

Bulk `create`/`invite` accepts `email`; `invite` also accepts
`emails: {doctor_id: email}` with per-row failures. Supplied addresses win;
otherwise an existing account's email wins over the catalog. Account creation
fills an empty doctor email using a conditional JSON merge, preserving existing
email and concurrent overlay edits. A failed mail send leaves the account intact
and returns `email_enviado: false`.

Login resolves an email only when it identifies exactly one operator, including
inactive rows in the ambiguity check. The canonical username still owns password
verification, TOTP and the session. Self-service recovery uses the existing
`POST /api/admin/reset-password` with `action: "request"` and `usuario` (login or
email). It responds neutrally before background delivery, skips inactive,
ambiguous and email-less accounts, and uses a bounded process-local throttle
(5 requests per IP and 3 per identifier per 15 minutes). It does not recover
the environment owner or add DNI authentication. Token consumption cannot
reactivate disabled accounts; explicit admin invitation remains the activation
path. Password hashes and reset-token digests remain the only persisted secrets.

Focused evidence for this slice: `test/test_admin_users.py`,
`test/test_password_reset_router.py`, `test/admin-login-direct.test.mjs`,
`test/admin-users-native.test.mjs`, and the onboarding browser fixture above.
Database and browser tests require an execution environment that permits
Postgres connections and Chromium launch; skipped/blocked runs are not proof.
