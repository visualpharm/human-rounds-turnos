---
title: Consultation first-paint font settlement
scope: repo
area: dev
tags: [consultation, browser, fonts, lifecycle]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-consultation-lifecycle]
status: active
---

## Problem
The real consultation page intermittently exceeded CLS0.1 at1024px even though
the final screenshot and horizontal geometry passed. A font swapped after the
long clinical text painted; visible blocks moved about23px, yielding CLS0.2066.

## Root cause
API readiness was treated as layout readiness. The shared theme's font request
could still be pending. Waiting for fonts only inside the test hid nothing from
the observer, but did not prevent the user's intermediate fallback-font paint.

## Solution
Fetch data concurrently, await the initial font cohort before the first form
paint, then recheck route ownership. Cache that readiness promise per mount:
later unrelated font loads cannot block navigation after initial readiness.
Failed fonts settle to the browser fallback. The template's existing8s watchdog
owns a hung initial load, disposes the controller and offers Reload.

## How to apply
The managed e2e-consultation-asgi gate holds real font requests in C11/C12 and
checks both successful settlement and font failure. Both failed with the old
first-paint behavior. Keep actual observer records and source rectangles.
The native-entry Chromium test holds font readiness past the real watchdog and
proves its late resolution cannot repaint over recovery. Pure tests separately
cover disposal, route replacement and a later unrelated never-settling font.
These local proofs do not establish production deployment or actual BFCache use.
