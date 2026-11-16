---
title: Simulator login ownership spans all activation paths
scope: repo
area: dev
tags: [authentication, browser-tests, lifecycle]
created: 2026-09-07
refs: [2026-09-07-human-rounds-auth-simulator-login-ownership]
status: active
---

## Problem
Mixed login activation issued five session writes instead of one. Old list/save
responses replaced newer UI; remount retained stale Saving text despite green
draft/lock tests. Local phone formatting also diverged from the shared module.

## Root cause
Button disabling did not own the operation across row, keyboard and lifecycle
paths. Render-local format copies silently bypassed the canonical country pack.

## Solution
The native controller owns one write through response.json(), including remount.
Generations reject stale list/form callbacks; destroy removes listeners, timers
and only its own transient progress, preserving drafts and validation feedback.
Successful handoff stays latched for that mount; fresh mounts can act again.
Use actual format.mjs/dates.mjs and the server's country_phone_json() bootstrap.
Await every dependency before reads, display or mutation; timeout/failure owns
visible reload recovery. Preserve same-origin messages and return destinations.

## How to apply
Drive complete handlers and actual router-rendered ES/EN pages at all five
widths. Hold/reject dependencies and responses; inspect remount screenshots.
Prove negative controls, not merely an import string or an empty error list.
Enable LANGUAGES=es,en plus the real language-switch mode; assert redirect and
cookie behavior. A language query alone is ignored when switching is off.
Native topology has no signed legacy peer marker: first prove simulator404
without opt-in. Set TURNOS_RENAPER_STUB_DEPLOYED only in disposable fixtures,
never on a live installation to obtain a passing test.
Exact raw Account comparisons permit only reviewed source/loader/bootstrap
deltas; unrelated-byte controls must fail before replacing two auth-page pins.
Retire obsolete classic URLs; current native raw URLs remain no-cache for
relative imports. Keep local gates distinct from frozen and deployed proof.
The URL tests missed a restored classic file: add its basename to the physical
retirement guard and prove the real pre-commit rejects it before running tests.
