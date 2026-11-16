---
title: Preserve complete local CI evidence after a coverage rejection
scope: repo
area: dev
tags: [ci, diagnostics, test-coverage]
created: 2026-09-04
refs: [scripts/ci_local.sh, test/test_ci_log_retention.py]
status: active
---

## Problem
A full backend run passed assertions but the coverage guard rejected two
unexpected skips. The runner printed only its final 60 lines and deleted the
temporary log, losing the test IDs and reasons needed to diagnose the rejection.

## Root cause
Console tailing was treated as evidence preservation. The pre-commit hook had
already been repaired independently, but the full ladder still discarded logs.

## Solution
Create a private durable run directory before any surface starts. Keep separate
backend, frontend, routes, replay, audit and browser logs regardless of status.
The guard also prints bounded unmatched skip-result lines on rejection. A
real unittest test reproduces stderr splitting a verbose skip line: the text
parser rejects it, while actual TestResult.skipped records retain its identity
and reason. Local and hosted backend commands now use the same thin stdlib
runner and atomic result JSON; the one-reason allowlist is unchanged. Hosted
triggers were not enabled and no hosted success is claimed.

## How to apply
Read the printed full-log directory, not only the console tail. The default is
under ~/.claude/artifacts/human-rounds/local-ci; TURNOS_CI_ARTIFACTS_DIR is an
explicit alternate root. The behavioral test executes the real shell runner
with offline surface fixtures and the real coverage guard, asserting rejection,
continued execution, private directory mode and all six surviving logs plus
the structured backend result. Missing/malformed records fail closed. Historical
skip IDs from a deleted log cannot be reconstructed with certainty; verify the
next retained report before calling the backend accepted.
