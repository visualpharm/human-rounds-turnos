---
title: Staff Account cold paint and security-notice layout
scope: repo
area: dev
tags: [account, lifecycle, browser, cls]
created: 2026-09-09
refs: [2026-09-08-human-rounds-native-staff-account-lifecycle]
status: active
---

## Problem
The full frozen gate caught phone CLS0.2182 despite earlier green Account runs.
Final screenshots alone hid the intermediate jump.

## Root cause
Observer source rectangles showed sign-out moving213px horizontally when the
asynchronous security notice became visible. Fonts were already loaded for
that shift. Separately, held-font tests exposed a fallback-font first paint.

## Solution
Account's scoped narrow-header footer keeps sign-out right-aligned before and
after the security read. No warning or security state is removed. Import the
native entry concurrently with the initial font cohort, but mount after both
settle. The existing8s watchdog/page owner rejects late mounts after timeout
or pagehide. Failed fonts use the browser fallback.

## How to apply
Keep source rectangles with observer CLS; do not infer every shift is a font.
The managed staff Account C13/C14 hold real fonts through success/failure;
C15 holds the actual account response and compares sign-out coordinates.
Bootstrap regressions cover held readiness, timeout and pagehide.
Capture full pages from scrollTop0 so fixed chrome is not stranded mid-image.
These local tests do not prove deployment, authenticator enrollment or BFCache.
