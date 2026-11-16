---
title: In-flight settings and catalog reads must not outlive invalidation
scope: repo
area: dev
tags: [configuration, catalog, concurrency, connectors]
created: 2026-09-05
refs: [test/test_config_cache_race.py, test/test_catalog_cache_race.py]
status: active
---

## Problem
A connector could resolve as selected after saving, then resolve as absent
when an older background read finally published its settings snapshot.
The same mechanism hid a newly written QR door token in the doctor catalog.

## Root cause
The cache reader performed database I/O and unconditionally published its
result. A concurrent save/reset cleared the cache but could not supersede
that in-flight read. A controlled interleaving reproduced the reversal.
This is a demonstrated mechanism, not proof of a historic failing schedule.

## Solution
Protect cache metadata with a lock and generation counter. Invalidation
increments the generation. Readers perform I/O outside the lock and only
publish/return data if their generation is still current; otherwise they
read again. Never hold the cache lock while waiting for the database.

## How to apply
Run `python3 -m unittest discover -s test -p 'test_config_cache_race.py'`.
The save-only and reset-only cases block an old read, change persisted
settings, then require both the delayed caller and a later connector
resolution to see the new setting. These tests do not claim cross-process
instant cache coherence or atomic merging of concurrent settings writes.
Run `test_catalog_cache_race.py` likewise: both delayed success and delayed
DbUnavailable must retry after a token write; neither stale rows nor a stale
failure cooldown may replace the new state. Strict admin reads remain uncached.
Run controlled scenarios in fresh processes so unrelated suite threads cannot
warm the cold cache before the designated reader reaches its barrier.
