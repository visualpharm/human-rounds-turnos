---
title: Catalog and queue generated locale recovery
scope: repo
area: dev
tags: [i18n, catalog, queue, recovery]
created: 2026-09-14
refs: [archive/catalog-validation-recovery-wip-20260910]
status: active
---

## Problem
Catalog and queue recovery added six messages to Spanish and English while eight generated language catalogs still lacked them.

## Root cause
Integrating source changes does not update generated catalogs. The translation runner also tracks each canonical Spanish string in the language's `_source_hash.json` sidecar.

## Solution
Add the six reviewed translations to their owning `admin.content.json` or `admin.queue.json` namespace in each generated language. Add the matching first 16 hex characters of SHA-256 of the canonical Spanish UTF-8 value to its source-hash sidecar. Preserve every existing value and hash.

## How to apply
Compare the before and after JSON objects, requiring that all prior entries remain unchanged and only the reviewed keys are added. Check all six keys across all eight languages and their source hashes. Retain safety messages about unconfirmed writes. A source-hash match records which source was translated; it does not independently prove translation quality.

For catalog validation recovery, run the actual-module ownership tests against the previous module as a negative control, then the integrated module. Verify literal source changes and all eight ES/EN Account pages without repinning unchanged renders. Finish with the managed browser and full verification profiles on frozen inputs before release.
