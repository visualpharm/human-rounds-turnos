---
title: Pending queue actions belong to semantic screen state
scope: repo
area: dev
tags: [queue, esm, asynchronous, browser-tests]
created: 2026-09-05
refs: [6d5e96f3]
status: active
---

## Problem
Creating an appointment from the doctor detail pane could silently do nothing
while its native appointment module was loading.

## Root cause
The continuation required the clicked button to remain connected. Queue updates
replace that disposable button even when the user is still on the same doctor
and day. Checking only the persistent screen instead would preserve obsolete
intent after the user selected another doctor or day.

## Solution
Capture the mounted queue owner, selected doctor and day when accepting the
action. After module readiness, require that semantic context and the latest
intent sequence to remain current. Same-context rendering may replace controls;
actual navigation cancels the old intent.

## How to apply
Hold the real module import. Replace the doctor detail action and verify its
appointment form still opens for the captured doctor. In a separate case, change
the selected doctor before release and verify no obsolete form appears. Keep the
whole managed browser gate: its real doctor-detail journey exposed this failure.
Source verification passed 198 browser cases with eight declared mode skips.
