---
title: Native navigation owns the early boot and page lifetime
scope: repo
area: dev
tags: [navigation, esm, lifecycle, bfcache]
created: 2026-09-10
refs: [ce3e4e36]
status: active
---

## Problem
Moving the navigation into a native module did not by itself own requests,
avatar callbacks or an early import waiting for the document.

## Root cause
A late DOMContentLoaded callback could mount after pagehide. The singleton
emergency observer also disposed a different live header unnecessarily.
Avatar refresh needed both current-host and per-refresh ownership.
Disposing a temporary emergency owner lost the preserved header's observer.

## Solution
Retain a small pre-DOM lifecycle until the page is visible and parsed, then
hand off to the mounted controller. Transfer only the emergency observer;
dispose a previous controller only for the same header or a hostless owner.
Retain live leases across nested transfers, reserve ownership before disposing
a predecessor, and restore the newest eligible header immediately. Hidden
headers reserve a lease but scan only after their own page restoration.
Abort owned GETs, clear image callbacks and reject stale refresh generations.
Consumers use the native module directly, including the Account avatar action.
Retired classic URLs return no-store misses, not a redirect to ES-module syntax.

## How to apply
Import the actual module in tests, not source-stripped VM extracts. Two new
regressions first failed before the ownership fixes and then passed. Keep
browser evidence separate: five-width cold navigation geometry passed with
real observer entries; screenshots taken during search settlement do not prove
the whole page is ready. Account literal-source proof and full release gates
remain necessary before this candidate can be integrated or deployed.
