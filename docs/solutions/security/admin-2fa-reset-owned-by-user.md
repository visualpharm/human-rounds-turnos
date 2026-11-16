---
title: Admin 2FA resets stay user-owned
scope: repo
area: security
tags: [admin, 2fa, totp, account-security]
created: 2026-07-27
refs: []
status: active
---

## Problem

The Users screen generated replacement TOTP secrets for other staff accounts,
and the first-run installer activated an unconfirmed secret of its own. Both
made a workflow other than Account handle the user's authentication secret.

## Root cause

`admin_usuarios.py` predated the self-service account-security flow. It created
TOTP secrets directly instead of using `admin_account_security.totp_deadline`,
which already blocks every route except Account when the deadline has passed.

## Solution

The Users screen now exposes `forzar_2fa`. Enabling it:

- clears the account's current TOTP secret;
- sets `totp_deadline` to now;
- lets login succeed but routes the person to Account;
- blocks the rest of the panel until self-service setup is confirmed.

`admin_account._set_totp` stores the user-generated secret and clears the
deadline automatically. Lists expose only `tiene_2fa` and
`requiere_configurar_2fa`, never the secret.

The installer follows the same rule: choosing 2FA stores only a signed-wizard
boolean, creates the operator without a TOTP secret, sets the deadline to now,
and routes first sign-in to `#cuenta/2fa`. Account alone renders the responsive
QR/manual key and persists the secret after a valid six-digit confirmation.

## How to apply

Use `admin_security_set_deadline(usuario, now)` for a forced reset. Do not
generate or activate a TOTP secret in Users or `/setup`. Verify the full
transition: active or newly installed → forced/pending → confirmed/active,
including QR containment at every supported viewport.
