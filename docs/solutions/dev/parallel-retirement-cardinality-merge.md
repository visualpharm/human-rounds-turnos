---
title: Identical count edits can hide a second retirement during merge
scope: repo
area: dev
tags: [merge, retirement, acceptance]
created: 2026-09-08
refs: [2a9bfa69, ca662e01]
status: active
---

## Problem
Patients and Preparations each passed full verification in isolation. Their
combined cache contract still expected eight classic admin assets, but served seven.

## Root cause
Each branch independently changed the same cardinality from nine to eight.
Git accepted that identical edit without a conflict even though both independent
deletions survived. Resolving only conflict markers missed the stale expectation.

## Solution
Keep both deletions, their native registry entries and their live route/API
survivor tests. Verify the executable registry and census, then compose the
count changes: seven root assets and census 362/628/442. The cache gate failed
before correction and passed all 58 tests afterward; neither runtime was restored.

## How to apply
Review auto-merged count and inventory edits alongside explicit conflicts.
Check against executable registrations, not arithmetic alone. Run both parents'
survivor/retirement tests, exact shared-source and Account render checks, then
freeze the combined candidate for its own full gate. Parent greens are not
combined-candidate acceptance.

The Alerts/HSI union repeated this trap: both controllers now boot as native
modules, leaving only one classic script in the HSI HTML, not two. Its real
render test failed before correction and passed afterward. A merged loader
matcher also must retain the actual Alerts `typeof window` safety guard;
changing the runtime to fit the other parent's textual matcher is not a fix.
