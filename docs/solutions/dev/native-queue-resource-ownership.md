---
title: Native queue resource ownership requires actual teardown
scope: repo
area: dev
tags: [queue, esm, lifecycle, runtime-tests]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-day-queue-lifecycle]
status: active
---

## Problem
The unserved native queue candidate leaked document listeners, created tables
after disposal, and doubled resources on remount. A document pagehide handler
did not receive the actual window event.

## Root cause
Wrapping the old controller in mount() did not establish resource ownership.
An independent tableReady.then() also orphaned dependency rejection. Checking
for a disposed variable in source could not prove any of these behaviors.

## Solution
Track the current owner per root, dispose the prior mount, register real
listener cleanup, own timers and overlays, and bind pagehide to the window.
Await table creation in readiness and guard it before creating resources.
Release SSE listeners and close the stream, retaining fresh-owner operation.
Keep presentation document-owned through a fingerprinted template stylesheet.
Disposal may preserve readable SSR/current content after enhancement failure;
removing controller-injected CSS then makes that preserved content overflow.
Do not remove document styles when disposing controller resources.
Mutation operations also need per-key ownership and abort signals: guard after
response parsing and delayed session imports, pass the current-owner predicate
to session confirmation, and suppress stale success/failure UI. Check ownership
on both success and rejection of a held create-appointment import.
The shared create overlay also depended on the retired sanaRouteCola global.
Pass onCreated explicitly from the mounted queue; refresh only its current day
and never after disposal. Isolate callback errors from POST failure handling,
so a saved appointment cannot reopen as a failed save. Browser proof disables
EventSource: saving must update the actual queue row without navigation.
Return an owner-scoped overlay release function. Disposal closes that opening
and invalidates its pending save response; a late409 must not restore it.
Current-owner409 still restores the patient's entry and offers another slot.

## How to apply
Import the actual module and exercise mount/dispose with counted event targets
and deferred dependencies. Real RED controls exposed resource, mutation,
style and session-import failures; all 35 native runtime tests now pass.
Invalidate old loads at typed-date intent, not debounce expiry; cancel cleared
dates and coalesce SSE until the current requested day finishes loading.
Retire day-scoped attendance operations both at date intent and at new-day
data application (old rows may remain usable while loading); release controls.
Keep screen-wide settings operations alive across date changes.
First-load readiness follows the current request, not a superseded AbortError.
Only current data application or a current session transition settles readiness;
a current failure rejects it. Recovery uses compact normal-flow flex layout,
16px text and a 44px reload target, retaining readable content. Do not use a
fixed toast to hide layout shift; measure the actual inline recovery state.

The local template now serves the native candidate. The real ASGI browser gate
passed 15 cases at five widths, with 21 unique captures, maximum observed CLS
0.0505234568 and zero horizontal overflow. It covers import refusal, watchdog/late import,
disposal with no old POST, a fresh-owner POST, failed persisted restart with
stylesheet retention, and an aborted first fetch followed by a successful date
takeover. Actual browser BFCache admission remains unproved.
Evidence: day-queue-native-lifecycle/2026-09-09T11-22-21-812Z-run-3cdc805f.
End the login landing document before installing case-specific failure routes;
otherwise its late import can consume the next page's one-shot interception.
Classic contracts were transferred and the classic file/aliases retired locally.
Full frozen gates and release verification remain unfinished; local success
is not deployment. Repeat browser measurements against the final frozen tree.
