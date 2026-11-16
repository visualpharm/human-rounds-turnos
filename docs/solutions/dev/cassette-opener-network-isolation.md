---
title: Cassette replay must intercept opener dispatch
scope: repo
area: dev
tags: [testing, urllib, isolation]
created: 2026-09-08
refs: [2026-09-08-human-rounds-cassette-opener-network-isolation]
status: active
---

## Problem
A nominally offline PUCO cassette test contacted the public fallback after a
corrupted WS131 result. Its ordinary false-result assertion passed despite an
HTTP 500 and an ignored response-cleanup warning.

## Root cause
Replay patched `urlopen`, but the fallback used `OpenerDirector.open`.
The connector legitimately caught ordinary transport exceptions. Exit status
alone could not establish either network isolation or clean resource ownership.

## Solution
Intercept both urllib entrypoints, restore both on every context exit, and
remember unmatched calls so swallowed exceptions still fail the replay context.
Match explicit Request data overrides with urllib semantics. Keep the WS131
corruption assertion scoped; separately exercise the real fallback with
transport-construction stubs and verify zero attempts.

## How to apply
Run `TURNOS_ENV_FILE=/dev/null TURNOS_FAKE_AI=1 python3 -W
error::ResourceWarning -m unittest discover -s test -p 'test_*_cassette.py'`
as one shell command. Root verified 49 cases with marker-free stderr.
Two baseline negative controls fail with transport stubs, never a real service.
Additional root negatives caught Request override matching and an unclosed
original recorder HTTPError. Recorder consumes and closes original responses;
the caller owns the returned replay response. This is local test-harness proof,
not production connector validation or full refactor acceptance.
