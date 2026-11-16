---
title: Shared table instances need real release before controller remount
scope: repo
area: dev
tags: [table, lifecycle, native, regression]
created: 2026-09-07
refs: [2026-09-07-human-rounds-doctor-feed-render-ownership]
status: active
---

## Problem
The native shared table module had document-level dispatch but no way for a
screen owner to release its fitting instance or column picker before remount.

## Root cause
Instances remained in the resize registry, selectors remained reserved, and
pending preference hydration could notify an obsolete picker.

## Solution
Idempotent dispose cancels pending fitting, disconnects the fitting-medium
observer, releases registry/reservation/bindings and ends only its own drag.
Late callbacks and mutation methods reject a disposed instance. A replaced
same-key picker is disposed without deleting its replacement registration.
Shared document listeners remain intentional singletons, not per-screen work.

## How to apply
Native controller disposal must call both table and picker dispose. Import the
complete module for runtime tests; prove registry removal rather than merely
no-op scheduling. Browser proof exercises active drag, disposal, usable remount
and an unaffected sibling. Shared asset changes require exact source-validation
and raw Account response reversal before snapshot changes. This primitive alone
does not retire or migrate its callers.
