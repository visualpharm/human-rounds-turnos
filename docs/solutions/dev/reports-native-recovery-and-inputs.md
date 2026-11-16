---
title: Reports native recovery and explicit country input
scope: repo
area: dev
tags: [esm, reports, lifecycle, currency]
created: 2026-09-07
refs: [625e86a9]
status: active
---

## Problem
The native Reports module handled data failures without rejecting `.ready`,
so a loader-only catch could not provide the promised recovery action.
The retired shell's country-currency input was also absent from the direct page.

## Root cause
Readiness covers dependency loading and initial dispatch; individual data
failures deliberately render permission/database-specific feedback. Those
handled failures resolve readiness. Retained globals are not an input contract.

## Solution
Keep specific data-error messages and add an owned deliberate Reload action.
Disposal removes its listener and makes a retained callback inert. The template
separately handles failed/hanging imports and page-exit/remount boundaries.
Pass the pack-derived currency through the existing script-safe serializer
directly to mount options, without another browser global.

## How to apply
Exercise actual data-error Reload and import-error Reload separately. Pin the
rendered currency argument, escaping of closing-script text and removal of
private metadata. Test a non-default currency at the real formatter-to-chart
boundary; a chart fake that omits values cannot prove formatted DOM output.
This repository's Spanish bootstrap intentionally uses caller fallbacks:
reuse the catalog's Spanish literal so a recovery action does not say Reload
on a Spanish page. The same key supplies the English translation.
The prior lifecycle regressions also guard superseded response authentication
and cache publication, not just final painting. Keep those tests on migration.

## Query-key negative control
An adversarial review found a pre-existing dispatch bug: a plain object map
treated `constructor`, `__proto__`, `toString` and `hasOwnProperty` as known
report views, then called a missing `cargar` method. A generic unknown string
did not exercise that boundary. Four real-module negative tests failed with
the original map. Use a null-prototype dispatch map, and prove these values
render the real eleven-card overview in both runtime and browser tests.
The preceding full green gate remains historical after this source correction;
freeze and rerun the combined gate before accepting the updated candidate.

## Cross-runtime dependency fixture
The actual commit hook also runs Node20; managed CI runs Node26. An unescaped
question mark in a dependency fixture's data URL was interpreted as a URL query
by Node20, truncating the session-confirmation ternary to its false predicate.
The live401 positive control correctly failed there, even though Node26 passed.
Encode the complete data-URL source with encodeURIComponent; never change the
production session logic or weaken the positive control to make a fake pass.
