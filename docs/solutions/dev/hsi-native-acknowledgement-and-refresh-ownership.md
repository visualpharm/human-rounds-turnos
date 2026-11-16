---
title: Native HSI needs typed acknowledgements and per-read ownership
scope: repo
area: dev
tags: [hsi, lifecycle, validation]
created: 2026-09-08
refs: [2026-09-08-human-rounds-native-hsi-lifecycle]
status: active
---

## Problem
The first native HSI candidate passed36 runtime tests but still displayed
success for malformed acknowledgements and allowed an older refresh to replace
a newer panel within the same mounted screen.

## Root cause
Truthy `ack.ok` accepted strings and numbers. Resolver count coercion converted
missing or invalid values into a successful zero-change result. Mount generation
alone did not distinguish consecutive GETs; aborting a request does not guarantee
an already settled response or delayed JSON continuation disappears.

## Solution
Require a literal top-level true and boolean per-conflict acknowledgement.
Resolver counts must be nonnegative safe integers, without coercion. Each GET
retains its controller identity as well as mount generation through response
handling, session confirmation, and rendering.

## How to apply
Run `node --test test/admin-hsi-lifecycle.test.mjs`. Thirteen new invalid-response
cases and one ignored-abort stale refresh failed on the initial candidate;
all50 cases passed after the repair; select-reset and malformed-metric controls
subsequently raised the suite to55. Preserve these negative controls.
Real-browser verification also caught12px mobile labels and tablet overflow.
Keep16px action/field text, size selects for their actual options, and stack the
table by its own container width, not a phone-only viewport breakpoint. Measure
rendered option widths and side gutters; page overflow alone misses clipping.
Use `e2e-hsi-asgi` for the20 native browser cases and10 unique ES/EN captures.
Neither runtime nor synthetic-browser success proves deployment or actual BFCache.
Physical deletion also needs the retired-client checker entry: restored HSI files,
directories and dangling symlinks each failed a new negative control before the
guard was added. The full retirement suite then passed30 tests with no skips.
See also [session ownership](session-confirmation-owner.md).
