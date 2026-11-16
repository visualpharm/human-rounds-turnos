---
title: Staff Account page lifetime must clear previously rendered profile data
scope: repo
area: dev
tags: [account, lifecycle, browser, privacy]
created: 2026-09-09
refs: [2026-09-08-human-rounds-native-staff-account-lifecycle]
status: active
---

## Problem
Staff Account stopped its callbacks on pagehide but left the old profile form
visible while a persisted pageshow waited for its fresh account response.

## Root cause
Disposing reads and listeners is not the same as removing already-rendered DOM.
The native controller cleared the TOTP setup secret, but not the profile.

## Solution
The template's stop function disposes its controller and clears its host before
a fresh mount. A real ASGI/browser negative control held the replacement read:
the old implementation exposed the profile, while the corrected one did not.
The new owner then rendered from exactly one fresh response.

## How to apply
Prove both delayed-callback ownership and prior-DOM clearance. Open collapsed
manual-key details before asserting visible text. Pass evaluation arguments
explicitly across the browser boundary. Keep failed artifacts; require all five
viewport measurements and recovery-button execution, not just recovery markup.
Synthetic page-transition events do not prove actual browser BFCache admission;
a synthetic QR shape proves layout only, not authenticator enrollment.
