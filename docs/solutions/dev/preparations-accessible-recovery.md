---
title: Preserve native editor semantics and recovery authority
scope: repo
area: dev
tags: [preparations, accessibility, lifecycle, errors]
created: 2026-09-08
refs: [82fe90d5]
status: active
---

## Problem
The native editor lost a fasting-note label and keyboard focus rule while
old source-only tests still passed against the classic controller. Initial
API failures also ended in a message without a recovery action.

## Root cause
Tests inspected the wrong implementation. Import readiness and handled data
errors are separate boundaries: loader recovery cannot catch a resolved read.

## Solution
Test labels, numeric input mode, button types and computed focus/target sizes
on the real native DOM. Restore the existing catalog label without adding
the old explanatory paragraph. Keep explicit live-region semantics. Use
the same scoped input styles with16px fields and44px buttons.
Give initial data errors an explicit Reload button. Its callback must match
both mount generation and the currently rendered button, so a retained
callback cannot refetch over a later editor. Disposal alone is insufficient.
Translate wire error codes to the shared generic failure, but preserve
meaningful server validation sentences and current fields.

## How to apply
Prove missing label/recovery/readable error with red controls before fixing.
Also invoke a saved retry callback after successful recovery, before disposal:
it must not fetch. Capture both ES/EN editors at five widths, retain complete
measurement and assertion records, and inspect screenshots. Scoped green
does not waive remaining text-density or full-refactor acceptance gates.
For remount proofs, await each real GET completion rather than an old editor
selector that may already exist. Count reads and mutations to reject duplicate
owners. Isolate Account enrollment when proving only the2FA repair redirect.
Window load does not join dynamic imports. Join the exact import and its
dependencies before checking a synchronous mount effect (such as owned CSS).
Prove the absence predicate rejects a served-template missing-owner-guard
mutant; adding a fixed sleep merely moves the race to a slower machine.
