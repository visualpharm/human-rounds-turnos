---
title: SSE readiness waits must survive partial observations
scope: repo
area: dev
tags: [sse, browser, verification]
created: 2026-09-09
refs: [2026-09-09-human-rounds-native-day-queue-lifecycle]
status: active
---

## Problem
The native queue browser test timed out although its real SSE connection worked.
A proposed delivery assertion could also accept an old or unrelated event.

## Root cause
The protocol observer shifted every waiter out of its queue on any network
notification, even if its predicate still lacked the server ready frame.
It then accepted any queue-update date without a pre-mutation checkpoint.

## Solution
Keep unresolved waiters in a Set until resolution, timeout or disposal.
Match a successful event-stream response and valid ready cursor by request ID.
Require a later queue-update from that same ready stream for the exact day.
Observe through CDP, not application globals; release observers on failed setup.

## How to apply
The actual helper is test/e2e/lib/queue-stream-observer.mjs. Its four pure
tests exercise partial readiness, error/unrelated responses, stale/wrong-day
frames and cleanup. The fixed e2e-queue-sse-asgi managed profile proves two
authenticated browsers and a real attendance update without navigation.
Root reproduced RED (one timeout) then GREEN (two browser cases), preserving
both artifacts. This does not prove real BFCache admission or production release.
