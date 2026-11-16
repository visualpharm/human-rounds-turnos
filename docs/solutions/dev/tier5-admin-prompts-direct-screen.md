---
title: Tier 5 AI prompts direct screen
scope: repo
area: dev
tags: [tier5, admin, prompts, routing]
created: 2026-08-21
refs: []
status: active
---

## Problem

The admin prompt editor only existed behind the legacy `#prompts` hash, so
direct server-routed screens had no equivalent safe deep link.

## Solution

Give `prompts` a turnos-manifest `/admin/prompts` screen with the same
admin-only gate and shared shell. The existing controller keeps all prompt API
behavior. In direct mode, its selected prompt is an exact `?prompt=<key>`
state with canonical replaceState, pushState, and popstate; legacy hashes stay
unchanged.

Load the direct controller in dependency order: fingerprinted `i18n.js`, then
fingerprinted `format.js`, then fingerprinted `admin-prompts.js`. The
controller reads `SanaI18n.lang`, with the server-rendered `<html lang>` as a
fallback, so the English shell shows only the original prompt while dates keep
using the shared formatter.

## How to apply

For a direct screen that previously relied on `admin-app.js` only for a toast
or expired-session gate, keep a small local status boundary instead of loading
the full application controller. The server shell remains the authorization
boundary; the client only handles a session that expires after initial render.
