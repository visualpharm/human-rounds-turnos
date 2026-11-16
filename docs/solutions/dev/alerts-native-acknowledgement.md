---
title: Native Alerts acknowledgement and recovery proof
scope: repo
area: dev
tags: [alerts, lifecycle, acknowledgement, browser]
created: 2026-09-07
refs: [2026-09-07-human-rounds-native-alerts-lifecycle]
status: active
---

## Problem
The initial native Alerts handoff treated malformed HTTP 200 payloads as
successful acknowledgements, zero counts, or empty lists. Required session
imports could reject outside the readiness barrier.

## Root cause
Transport success was mistaken for the endpoint's actual result contract.
Fixture defaults hid a misordered count/list response. Mock-localized recovery
text did not prove the server's real translation key existed.

## Solution
Require `ok === true` for acknowledgement, an integer nonnegative count, and an
actual items array. Keep known alerts on failed polling. Include the session
module in readiness and propagate rejection to the shell recovery owner.
Use the existing `admin.content.common.reload` translation key.

## How to apply
Run the whole-module negative payload tests and the actual template bootstrap
tests. Real browser proof must preserve the alert after malformed acknowledgement,
then remove it only after a valid response. Recovery captures must wait for the
unaffected appointments screen to hydrate; empty first-paint captures are not proof.
Retire the classic file and its exact old fingerprint, not just its global name.
Local focused proof is not full refactor acceptance or deployment evidence.

Zero document overflow did not prove the shared header readable: the hydrated
360px title broke inside a word, the pending count was ellipsized, and the create
label wrapped. Measure real per-word Range rectangles, count/title scroll width,
and action line count. The unchanged old CSS failed both normal/recovery cases.
Shared wrapping plus a deliberate phone title row preserves every control and
count; ten geometry rows and screenshots cover both states at five widths.
Remove the redundant header divider against the already bounded content.
Do not confuse this fit correction with approval of a separate menu redesign.
