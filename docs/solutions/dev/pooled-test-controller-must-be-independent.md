---
title: A pool-failure test needs an independent controller connection
scope: repo
area: dev
tags: [postgres, psycopg-pool, tests, ci]
created: 2026-08-20
refs: [test/test_db_pool.py]
status: active
---

## Problem

The full backend suite occasionally failed in the connection-recovery test
with `AdminShutdown` on the statement that was supposed to terminate an idle
pooled backend. Focused reruns usually passed.

## Root cause

The test returned the target connection to the pool, then obtained its control
connection through the same pool. A valid LIFO checkout could return the target
itself, so `pg_terminate_backend(target_pid)` terminated the controller issuing
the command. Test order and pool state decided whether the test passed.

## Solution

Issue the termination through a short-lived direct `psycopg.connect()` to the
same disposable database. Assert that its backend PID differs from the target,
assert that termination succeeded, then verify the next pooled checkout has a
new PID and remains usable.

## How to apply

Any test that kills, locks, restarts, or exhausts a pooled resource must keep
its controller outside the resource under test. Never assume a second checkout
from the same pool is a distinct connection.
