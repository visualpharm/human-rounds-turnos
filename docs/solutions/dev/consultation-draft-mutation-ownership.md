---
title: Consultation draft mutation ownership across navigation
scope: repo
area: dev
tags: [consultation, lifecycle, mutations, regression-tests]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-consultation-lifecycle]
status: active
---

## Problem
A native consultation candidate passed11 tests but left Confirm disabled forever
when a pending submission's interview was revisited after navigation. A retained
old Confirm control could also submit again after a successful acknowledgement.

## Root cause
Read-generation ownership was incorrectly reused for persistent draft mutations.
Changing the read generation discarded both success and failure continuations,
without settling the original draft. The submission guard omitted confirmed state.

## Solution
Keep a mutation owned by its original draft within the live mount. Cache its real
acknowledgement/error there, but repaint only if that draft is currently displayed.
Give session navigation the narrower initiating-route predicate. Reject submission
from confirmed drafts or old controls during a new read/error; clear drafts on
disposal. Do not claim aborting a client request rolls back a server mutation.
The page entry also owns its import watchdog: a timed-out module may evaluate
later, but a generation check must prevent it from mounting over recovery.

## How to apply
Run `node --test test/admin-consult-lifecycle.test.mjs`: preserve A→B→A success,
failure, transport rejection, pending single-flight, stale401 and obsolete-control
positive/negative controls. Root reproduced11PASS/3FAIL before the correction;
the corrected suite has17PASS/0SKIP. These are synthetic runtime tests, not browser
or clinical integration evidence. Presentation parity and full acceptance remain
separate gates. Related: [[session-confirmation-owner]].
Actual whole-entry Chromium tests now cover import rejection/reload, exit/remount,
and a held module resolved after the watchdog. The late-module proof awaits actual
module evaluation instead of using a sleep as evidence that obsolete work ended.
Retirement also needs physical absence and raw/fingerprinted 404 tests. Migrated
source guards alone do not prove behavior: retain actual name fallbacks, custom
medication payloads and date-format calls. Check shared asset bytes and Account
raw pins unchanged; reject a one-byte mutation as a negative control.
