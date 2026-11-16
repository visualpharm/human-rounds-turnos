---
title: Delayed chat mount must preserve ongoing typing
scope: repo
area: dev
tags: [chat, focus, browser, search]
created: 2026-09-10
refs: [2026-09-06-human-rounds-home-cold-load-layout-shifts-and-compact-target]
status: active
---

## Problem
The full ASGI browser gate intermittently returned an empty home query immediately
after filling it. The trace showed focus entering the input, then leaving for BODY,
without an input event, value assignment, replacement node or navigation.

## Root cause
Delayed chat boot wrapped existing body children with appendChild. Removing and
reinserting the focused subtree blurred its original input. This was real lost
typing, not evidence that the search test should wait longer or refill the field.

## Solution
Move connected Element/Text nodes atomically with moveBefore when available.
Comments use appendChild. Older browsers restore the original page control's focus
without scrolling and restore its selected range; never replace the control/value.

## How to apply
Hold the actual widget module, type with real browser keys, select a range, then
release mounting. Prove node identity, focus, selection and the next keystroke
without refocusing or refilling in the test. Cover input and textarea, forced
fallback and normal unfocused mounting. Root reproduced two failing controls,
then all13 actual-module tests passed, including zero atomic blur events and
both fields' selections in fallback; unchanged served delayed-search6 and
listing checks passed. Full combined acceptance remains a separate gate.
Shared widget fingerprints require a literal source-delta and all raw Account
render reversals before repinning snapshots, not blessing new output directly.
