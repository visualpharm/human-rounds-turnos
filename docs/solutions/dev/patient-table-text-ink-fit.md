---
title: Cell text must fit without hiding the column under test
scope: repo
area: dev
tags: [tables, responsive, browser, acceptance]
created: 2026-09-08
refs: [4879d845]
status: active
---

## Problem
A desktop patient email was clipped while viewport overflow checks passed.
Its text extended9–10px beyond the cell at1280px in both locales.

## Root cause
The flexible email column could shrink below its rendered text plus padding.
Checking only element bounds missed the text ink. Increasing its minimum alone
made the table engine drop email and coverage, yielding a vacuous test pass.

## Solution
Allocate172px to email and108px to the short attendance field through the
existing column registry. Keep the shared renderer and all default columns.
Measure text-node Range rectangles against visible cell bounds, and explicitly
require email and coverage to remain visible in desktop fixtures.

## How to apply
Pair no-clipping assertions with required-content visibility. Preserve the red
baseline and inspect screenshots even after tests pass: a disappearing target
must not turn a failure into success. Check both locales and all five widths.
