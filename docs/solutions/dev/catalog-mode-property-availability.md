---
title: Catalog mode changes hide unavailable properties without losing choices
scope: repo
area: dev
tags: [catalog, forms, state, browser]
created: 2026-09-10
refs: [test/e2e/catalog-screens-lifecycle.spec.mjs]
status: active
---

## Problem
Walk-in service editors displayed a faded hospital-approval checkbox even
though the property did not apply. Scheduled services had the symmetric
problem with same-day opening.

## Root cause
The shared toggle disabled inputs and faded their labels without hiding
them. Agenda visibility also read the previous mode's disabled state.

## Solution
Hide the entire unavailable label, including its hint, while retaining the
disabled submission guard and checked value. Update availability before
computing the dependent agenda visibility. No backend contract changes.

## How to apply
Served S04 covers both directions at five widths: unavailable rows absent,
choices retained across mode changes, and same-day agenda immediately
restored. Its initial red run reproduced the visible unavailable row.
The test uses the actual ASGI template, module, and synthetic catalog.
Screenshots still need visual review; green geometry is not usability proof.
