---
title: Routes gate passed without database coverage
scope: repo
area: dev
tags: [testing, pre-commit, database, worktrees]
created: 2026-09-04
refs: [f25d0365]
status: active
---

## Problem
In a clean isolated worktree, pre-commit returned success while database-backed
route tests skipped because neither database URL variable was set.

## Root cause
Unittest considers skipped tests successful. The shell runner checked only exit
codes; the isolated worktree correctly lacked the primary checkout's credentials.

## Solution
The route runner rejects missing configuration, empty discovery, and per-file
coverage failures. It applies the existing unittest coverage policy to each
file's output. Pre-commit obtains the fixed managed routes profile when URLs
are absent, rather than copying a checkout's credential file.

## How to apply
Run `scripts/pre-commit.sh` from the worktree. Missing managed prerequisites must
fail, not become skips. The frontend regression test clears both URL variables
and proves the direct route command exits 2. A successful managed run must show
executed tests and cleanup of its own run-scoped databases.
