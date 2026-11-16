---
title: Catalog dependent mutations need an explicit lifetime
scope: repo
area: dev
tags: [catalog, lifecycle, mutation, browser-tests]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-day-queue-lifecycle]
status: active
---

## Problem
A new doctor's selected photo was lost after navigating during creation.
After correcting the missing photo argument, delayed acknowledgements could
also start a new photo POST after timeout or full controller disposal.

## Root cause
The dependent upload omitted its captured payload. Racing a request against a
deadline settled the caller but did not stop the transport's acknowledgement
callback. Abort alone cannot prevent a late server acknowledgement.

## Solution
Pass the captured photo and require the create acknowledgement's explicit
success. Mark the mutation ended on deadline/disposal and check that state
before invoking dependent work. Dispose settles pending ownership and clears
the timer. Same-document navigation alone does not cancel the accepted workflow.
Remove the unused prior pending-photo helper instead of retaining two paths.

## How to apply
Run `node --test test/admin-create-appointment-native.test.mjs`.
Actual Chromium controls use the production module with synthetic HTTP; two
late-ack controls failed with one unexpected POST each before correction, then
the full file passed 27 tests without skips. Keep both deadline and disposal
controls, plus normal navigation with exactly one create and photo upload.
An unknown outcome is not a rollback and must not trigger automatic retries.
This proof does not close the broader served/refactor acceptance gate.
Related: [[consultation-draft-mutation-ownership]].
