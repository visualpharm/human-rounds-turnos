---
title: Integrating anonymous research after the AI-register migration
scope: repo
area: dev
tags: [alembic, merge-conflicts, route-inventory, cache-pins, privacy]
created: 2026-10-14
refs: [task/usability-study]
status: active
---

## Problem
An uncommitted study patch reapplied after backup and AI-register slices
collided with the migration number and route/schema inventories.

## Root cause
Both branches added a revision numbered 0019, independent routes and
documentation. The study locale also enters generic English bootstraps.

## Solution
Preserve both manifest and documentation additions. Study becomes
`0020_real_study` after `0019_ai_register_changes`, including `_SCHEMA_SQL`,
the frozen bridge's forward-table classification and the literal head pin.
Regenerate composition through `test_app_composition.py --write-fixture`;
prove the only delta is eleven study routes, with old order and middleware
unchanged, then update census counts deliberately.

## How to apply
Use `scripts/check_alembic_single_head.py`. Keep the study outside the AI
register; its analysis is deterministic. Run the existing inventory scan.
Keep new template-only study assets outside `CLIENT_LOADED`. Before changing
English byte pins, remove only study keys from the loaded catalog and prove
all prior hashes return. Keep privacy detectors unchanged; anonymize leaked
attribution and construct synthetic test handles instead of secret-shaped
literals. Use a hermetic environment so tests cannot migrate a dev database.
See `docs/usability/validation.md` for current results and unresolved gates.
