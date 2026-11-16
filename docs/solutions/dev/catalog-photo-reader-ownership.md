---
title: Catalog photo readers own intent before submission
scope: repo
area: dev
tags: [catalog, lifecycle, upload, browser-tests]
created: 2026-09-10
refs: [catalog-served-journeys]
status: active
---

## Problem
A retained FileReader could paint a new doctor editor, replace the last chosen
photo, or start a POST after navigation or full disposal. Malformed HTTP200
photo acknowledgements appeared successful; even explicit failure warnings
were immediately overwritten by the generic saved toast.

## Root cause
Removing change listeners does not cancel an already-running reader. Its
callback mutated shared editor state without checking its route, original
avatar or selection sequence. Photo success accepted anything except literal
false, and create completion always emitted a second success notification.

## Solution
Guard both new/existing photo callbacks by route ownership, latest selection
sequence and the original connected avatar. Clearing selection invalidates
older reads. Already-submitted create/photo requests retain their separate
mutation lifetime; unsubmitted readers do not inherit that authority.
Require exact body.ok=true for the dependent upload and retain one truthful
partial-success notification rather than overwriting it with generic success.

## How to apply
Use `node --test test/admin-create-appointment-native.test.mjs`: eleven new
actual-module Chromium controls failed before correction and passed afterward;
the full fifty-test file passes without skips. Controls cover late reads,
clear/new selection, fresh editor, disposal, route change, malformed/missing/
nonboolean acknowledgement and explicit failure. The server's authoritative
success shape is in patient_web/doctor_photos.py upload_payload.
Related: [[catalog-dependent-mutation-lifetime]].
