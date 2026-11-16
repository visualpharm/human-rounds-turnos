---
title: Appointment success requires a typed acknowledgement
scope: repo
area: dev
tags: [appointments, acknowledgements, browser-tests]
created: 2026-09-10
refs: [2026-09-09-human-rounds-native-day-queue-lifecycle]
status: active
---

## Problem
The appointment overlay said created before the POST responded, and reported
success for HTTP200 with false, missing, malformed or nonboolean acknowledgement.

## Root cause
The API helper's ok field represented HTTP status, not persistence. A source
regex test explicitly required the premature success message.

## Solution
Say Saving while pending. Require body.ok exactly true before a success toast
or caller refresh. Distinguish a known rejection from an unconfirmed response
or transport failure; the latter asks the operator to check the queue first.
Preserve the patient's entry and existing conflict recovery. Never restore
a disposed overlay or infer that abort rolled back the server write.

## How to apply
GLM added six actual-module Chromium controls; root independently reproduced
five failures and one pass before correcting source. Root extended malformed
types and lost-response coverage: the full file passed39 tests,0skips.
Replace the obsolete regex requirement only after this behavioral proof.
Shared-source edits also need literal source patch review and eight ES/EN
Account raw-render reversals plus anonymous default before updating hash pins.
Related: [[catalog-dependent-mutation-lifetime]].
