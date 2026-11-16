---
title: Account dashboard renders from the first HTML response
scope: repo
area: design
tags: [account, performance, ssr, demo, navigation]
created: 2026-08-02
refs: []
status: active
---

## Problem

Opening `/account` showed an empty shell or loader while the browser fetched
the session and then fetched appointments. The demo patient link added a 302
before that waterfall, and clicking “My account” restarted it.

## Root cause

The server already knew the authenticated user but returned only a generic
HTML shell. The client rediscovered the same identity and data sequentially.
The account nav also linked to `/account` instead of the active hash view.

## Solution

Embed the session-owned public user and appointment projection in
`window.__ACCOUNT_BOOTSTRAP__` on the no-store HTML response. Render the account
and appointment list from that payload immediately, then enrich service and
center labels asynchronously. Serve `/account/demo-access` directly with its
new session cookie, replace its visible URL with `/account#turnos` in place,
and point the signed-in nav there too.

## How to apply

- Bootstrap only data derived from the server session, never a browser DNI.
- Reuse the same public projection as `/api/requests`; do not fork serializers.
- Escape `<` in inline JSON and keep the HTML response `Cache-Control: no-store`.
- Retain the API fallback for signed-out, unavailable-database, and login flows.
- Verify a same-view account click makes zero document or request-list calls.
