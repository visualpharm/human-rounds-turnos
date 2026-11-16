---
title: Admin login reveals MFA only after account verification
scope: repo
area: security
tags: [admin, login, 2fa, totp, progressive-disclosure]
created: 2026-07-28
refs: []
status: active
---

## Problem

The shared admin login displayed a 6-digit Authenticator field to every user
when the environment-owned administrator had TOTP enabled. Staff accounts
without MFA reasonably stopped because the page appeared to require a code.

## Root cause

`GET /api/admin/config` exposes only the owner account's TOTP state. The client
used that global value to pre-reveal the field before it knew which account was
signing in.

## Solution

The first step always contains only username and password. After those
credentials are valid, `POST /api/admin/login` returns `need_totp` only when
that specific account has a TOTP secret. The client then replaces the credential
fields with the 6-digit code step.

## How to apply

Never use `CFG.totp` to decide whether the login form displays MFA. Keep
`#gate-2fa` hidden until the current login response includes `need_totp`.
Protect both directions: accounts without TOTP log in directly; accounts with
TOTP receive the second step.
