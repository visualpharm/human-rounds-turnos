---
title: Password recovery emails go out via gogx (the product owner's Gmail) — no SMTP infra exists yet
scope: repo
area: deploy
tags: [email, auth, gogx, coolify, cuentas]
created: 2026-07-11
refs: []
status: deprecated
---

**Deprecated 2026-07-11, same day:** the product owner rejected routing app email through his
personal Gmail ("don't use my gmail"). Replaced by
[[password-reset-email-via-mailgun]] — `email_mailgun.py` sends via the shared
Mailgun account instead. Left here for history; don't reintroduce the gogx path.

## Problem
"Olvidé mi contraseña" needs to email a reset link. This repo has zero email/SMS
sending code (checked: no `smtplib`, `twilio`, `sendgrid`, `waha` anywhere) and
accounts only carry DNI + optional phone — no verified channel existed.

## Root cause
The account system (`cuentas.py`/`db.py`) was built DNI-only; nothing in the
prototype ever needed to send mail before.

## Solution
`cuentas.py:_enviar_email()` shells out to `~/.claude/bin/gogx gmail send`
(the product owner's own Gmail, same mechanism the harness itself uses) when the binary
exists locally, and returns `False` silently if it doesn't — same
lazy-degrade shape as `hsi_client` (see CLAUDE.md: module absent → 'pendiente',
never blocks the caller). `POST /api/cuenta/recuperar` always returns the same
`{ok:true}` regardless of whether the email actually sent, so the endpoint
can't be used to enumerate which DNIs have accounts.

## How to apply
- This ONLY works on the dev machine (where `gogx` lives) — **on Coolify cloud this
  silently no-ops** (`_enviar_email` returns `False`, request still "succeeds").
  Password recovery is effectively local-only until real SMTP/transactional
  email is wired up for the cloud deploy — flag this before relying on it in
  production. See `deploy/cloud-needs-msd-snapshot.md` for the same
  local-vs-cloud capability-gap pattern.
- Any future transactional email (turno confirmations, etc.) should reuse
  `_enviar_email()` rather than adding a second ad hoc sender.
