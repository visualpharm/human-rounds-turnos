---
title: Immutable B2 receipts and operator-only recovery
scope: repo
area: deploy
tags: [b2, backups, restore, retention, alembic]
created: 2026-08-10
refs: [Tier 4 batch 5c]
status: active
---

## Problem

A path-level backup journal can prove latest local bytes but cannot identify
the exact remote B2 version.  Treating it as a retention record risks deleting
the only good copy or restoring a different same-name version.

## Root cause

B2 version identity is `(fileId, fileName)`, while uploads can succeed before
the database journal commits.  Legacy rows and accepted-but-unjournaled remote
versions therefore lack a safe deletion identity; B2 list pagination also has
two cursors at repeated names.

## Solution

Validate B2's returned id/name/length/SHA-1 against the exact uploaded bytes,
then save an immutable receipt and the current journal pointer in one
savepoint.  Old rows keep NULL `remote_file_id` and are non-prunable.
Reconciliation protects legacy, missing, orphan, hide, unknown, outside-prefix
and tombstone-reappearance evidence.  Restore verifies id/name/size/SHA-1 and
SHA-256 in a disjoint scratch tree before atomic no-replace placement.

## How to apply

Keep catalog/restore/retention operator-only: no worker/route/boot hook, dry
run by default, and exact `(fileName, fileId)` deletion only after a reviewed
plan.  Carry both B2 pagination cursors.  Respect `TURNOS_OUTBOUND_KILL=1`
before authorization, transport, local restore output, or tombstones.  Never
relax symlink checks; permit only fixed verified macOS `/var`/`/tmp`/`/etc`
system aliases while walking components before creating them.
