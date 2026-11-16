---
title: Demo refresh completion is not process liveness
scope: repo
area: deploy
tags: [demo, asgi, postgres, readiness]
created: 2026-08-10
refs:
  - docs/demo-installation.md
  - docs/roadmap/tier4-replatform-design.md
status: active
---

# Demo refresh completion is not process liveness

## Problem

A public-demo daemon thread, boot log, or `/health` response can survive while
the current fictional day, pack assets, or referrals have not regenerated.
Those transient signals cannot admit an ASGI soak or a later production flag.

## Root cause

The old refresher recorded no durable postcondition. A prior successful run
could therefore be mistaken for the current one after a partial retry, restart,
or rolling-deploy overlap.

## Solution

Keep one non-sensitive Postgres row (`demo_refresh_completion`) through a
forward migration. Serialize refresh work with a session advisory lock; write
`success` only after asset work, referral verification, and current-day marker
read-back. A later partial/failed attempt overwrites the active outcome. Show
only a safe, current, fresh result in the existing admin-only System surface;
missing, stale, unreadable, and materially future records stay non-green.

## How to apply

- Do not add this to `/health`: liveness and completed business work differ.
- Keep the guard before every DB/filesystem action; non-demo installs expose no
  demo-refresh item and perform no refresh work.
- Test disposable-DB upgrade/rerun, restart persistence, lock contention,
  partial/failure/stale/DB-down, and authenticated legacy/ASGI serialization.
- This is demo admission evidence, not production rollout or physical-passkey
  proof. Production rollback remains `TURNOS_ASGI` unset plus redeploy.
