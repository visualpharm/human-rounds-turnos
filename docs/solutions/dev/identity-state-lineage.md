---
title: Browser mirrors retained cleared identity and stale request state
scope: repo
area: dev
tags: [identity, lineage, appointments, browser-state]
created: 2026-09-16
refs: [lineage-tests]
status: active
---

## Problem
Seven lineage assertions exposed retained identity fields, resurrected booking
contact, partial mutation reconciliation, consumed slots, and browser timestamps.

## Root cause
Truthy-only writes, localStorage fallbacks, partial merges, and invented
timestamps treated browser copies as authoritative.

## Solution
Replace each identity projection from the account response, including nulls
and missing fields. Booking contact displays and submission use the account
response. Merge complete assignment/approval/rejection responses into the
request store and invalidate slot caches after successful mutations.
Attendance reads the returned state and timestamp, converting the timestamp
to ART instead of extracting raw UTC hours or using the browser clock.

## How to apply
Run `TURNOS_FAKE_AI=1 node --test test/lineage-*.test.mjs` and both full suites.
For account render snapshots, verify that only shared asset fingerprints
changed before refreshing exact hashes. Keep all behavioral assertions intact.

## Full-suite environment finding
The unchanged baseline's real worker proof failed before readiness because
`HTTPServer.server_bind()` performed reverse DNS for 127.0.0.1, taking 35 s
against a 20 s startup deadline. The worker health server now binds through
`TCPServer.server_bind()` and retains the bound address as its server name.
A DNS-failing direct HTTP probe and the unchanged real AI-worker proof both
pass; no worker deadlines or behavioral assertions were relaxed.

A second full-suite finding was a layered translation cache: explicit
`pack_l10n.invalidate_memo()` dropped rendered catalogs but retained source
translation rows. Preloading a real override reproduced the fallback failure
on the unchanged baseline. Explicit invalidation now clears both layers, so
a rebuild cannot resurrect the previous translation; tests remain unchanged.
