---
title: Catalog response body ownership
scope: repo
area: dev
tags: [catalog, lifecycle, fetch, cancellation]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-day-queue-lifecycle]
status: active
---

## Problem
Catalog disposal rejected stale UI work but left response streams alive after
fetch returned headers. Navigating to another item had the same resource leak.

## Root cause
The controller removed its AbortController from the owned set at header arrival,
before `response.json()` finished. A generation check protects DOM ownership but
does not stop the underlying stream. Node also accepts some Response facades
whose inherited getters throw Illegal invocation in Chromium.

## Solution
Keep the abort handle through body settlement, release it on success or failure,
and abort remaining handles on route replacement and disposal. Keep generation
checks as protection against transports that ignore abort. Use a small bound
Response interface rather than Object.create(Response).

## How to apply
`test/admin-content-lifecycle.test.mjs` holds headers/body separately and checks
the actual signal after disposal and navigation. Both disposal assertions and
the navigation assertion failed before the corrections. A Chromium whole-module
check confirms real Response methods and actual center rendering. Bootstrap-only
tests separately prove timeout recovery and synthetic persisted-page remount;
they do not prove actual BFCache admission or full catalog acceptance.

The shared appointment overlay must also stop calling the retired calendar
global after its native caller supplies an owned `onCreated` callback. The
actual-module browser test installs a tripwire old global: a current calendar
save failed before the call was removed, then passed with exactly one fresh
feed/render. A save settling after catalog disposal causes no feed read. Keep
these executable controls when retiring a producer; a new callback alone does
not prove the old consumer has stopped calling a legacy global.
