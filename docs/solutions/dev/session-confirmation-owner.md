---
title: Pending session confirmation must retain its navigation owner
scope: repo
area: dev
tags: [authentication, lifecycle, async]
created: 2026-09-07
refs: [2026-09-07-human-rounds-native-settings-controller-lifecycle]
status: active
---

## Problem
A disposed Settings controller could still redirect the fresh screen when its
already-started session confirmation finished waiting for `/api/admin/whoami`.
A caller-side stale-response guard did not cover this later asynchronous step.

## Root cause
The shared session module coalesced the identity check but retained no ownership
predicate for each caller's eventual navigation. A valid expired-session result
was treated as permission to navigate even after that caller ceased to own UI.

## Solution
The optional third argument to `confirmar(response, parsedBody, isCurrent)` is
checked before classification and immediately before 2FA/login navigation.
Each consumer retains its own predicate while sharing only the whoami request.
Omitting the predicate preserves existing callers; undefined body means parse
the response. Settings passes mount/view ownership; System passes its lifetime.

## How to apply
`node --test test/admin-session-owner.test.mjs` uses the actual whole module:
hold whoami, expire the caller, then deliver confirmed expiry. The old source
fails; an active-owner control must still navigate. The Settings browser suite
repeats the pending-confirmation case through the real template and modules.
Changing this shared asset changes every emitted client manifest: validate its
literal source delta and reverse only that URL in all eight ES/EN Account pages
plus the anonymous page before updating their raw-response pins.

The native staff Account candidate exposed the same problem in logout: a
`.finally()` redirected after disposal and left transport rejection unhandled.
Settle both outcomes through a current-route predicate; on remount, restore the
shared sign-out button disabled by the former owner. Real-browser tests hold
the old logout across remount and release success/rejection. Assert both that
the fresh route remains and that its sign-out control is usable.
