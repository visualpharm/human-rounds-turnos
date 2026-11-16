---
title: Editorial remount drafts and cancelled translation lifetime
scope: repo
area: dev
tags: [esm, lifecycle, bfcache, drafts, regression]
created: 2026-09-06
status: active
refs: [test/e2e/admin-simplification.spec.mjs]
---

## Problem
Prompts and legal editors lost unsaved text on destroy/remount. A pending
Spanish translation could remain permanently busy after remount.

## Root cause
Mount rendered cached server records over editable DOM state. The translation
map retained a cancelled generation's promise, preventing a fresh request.

## Solution
Capture editable values, selection and active language on destroy; restore
only the matching document. Reset the translation promise map when invalidating
the lifecycle generation. Old success and rejection callbacks must settle but
must not mutate the new generation or remove its pending request.

## How to apply
Run the real-module browser fixture with distinct held old and fresh responses.
Wait for transport completion and browser frames before asserting stale data did
not paint. Test prompt languages, legal drafts and pending translation separately.
Removing draft restoration and the cache reset makes the regression tests fail;
restoring the source passes. Source-pattern checks are not behavioral proof.
