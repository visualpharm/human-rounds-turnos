---
title: Required private packs fail closed before either HTTP server starts
scope: repo
area: security
tags: [data-packs, private-data, readiness, symlinks]
created: 2026-08-10
refs: [2026-08-10-capture-human-rounds-tier-6-private-pack-resolution-and-re]
status: active
---

## Problem

A missing or corrupt private clinical pack could silently fall back to public
content, while ASGI proxy mode could start Uvicorn after its legacy backend
thread failed readiness.

## Root cause

Optional pack-chain resolution is deliberately degrade-not-crash, and a
realpath check alone cannot prevent a symlink/directory swap before open.

## Solution

Keep optional fallback, but admit an optional private replacement only after
the same identity/inventory integrity checks pass. Gate `TURNOS_REQUIRED_PACKS`
at both legacy and ASGI pre-acceptance boundaries. Read safety knobs only from
process/effective `.env`, and use dirfd descent with `O_NOFOLLOW` to serve an
external verified private root.

## How to apply

Never configure required-pack safety in DB Settings. Add a synthetic test for
each new inventory or static-serving behavior, including private-root absence,
symlink escape, directory swap, and both `/webdata` and `/data` URLs.
