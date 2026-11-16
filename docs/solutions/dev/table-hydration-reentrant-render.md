---
title: Preference hydration under reentrant rendering
scope: repo
area: dev
tags: [preferences, hydration, browser, regression]
created: 2026-09-04
refs: [a05ccc12, fc812477]
status: active
---

# Preference hydration must be safe under reentrant rendering

## Problem

The full browser journey froze on the requests screen.

## Root cause

The first table preference hydration repair notified every column picker from
the shared load promise, including pickers constructed after that promise had
resolved. The requests screen constructs its picker inside `render` and passes
`render` as `onChange`. The resulting resolved-promise notification loop starved
the browser event loop. A component test that constructed only one picker
passed; the complete browser journey froze.

## Solution

Only a picker constructed before loading completes needs deferred hydration.
After loading, initialize synchronously from the cached value. Notify an
existing owner only when its provisional selection changed, and never overwrite
an explicit user edit made while loading.

## How to apply

`test/admin-table-hydration.test.mjs` and
`test/e2e/table-preferences.spec.mjs` recreate the picker from its callback.
Recursion is bounded inside the test so broken code fails an assertion instead
of hanging the test runner. The tests fail against `a05ccc12` and pass after
`fc812477`; the real requests screen subsequently completes row/detail,
rejection persistence and 360px layout journeys.

Use `python3 scripts/turnos_managed_exec.py --profile e2e-asgi` for a fresh full
browser surface. This standalone gate is not the complete release ladder.

For delayed native module loading, `admin-table-ownership.spec.mjs` starts with
three-header tables eligible for automatic discovery. It proves explicit
selector reservation preserves the same-key sibling and early edits. Existing
RESERVADAS filtering suffices; deactivating the shared automatic owner does not.
