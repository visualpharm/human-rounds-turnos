---
title: A zombie Coolify build wedges the server-wide queue; asset-less smokes can't see it
scope: repo
area: deploy
tags: [coolify, deploy, queue, smoke]
created: 2026-07-30
refs: [57577ee, a1861f6]
status: active
---

## Problem
Two pushes "deployed" (webhook 200, smoke VERDE) but prod kept serving the old build for 40+ minutes. A later smoke failed only because the newer commit happened to move assets.

## Root cause
1. A build failed on a transient `apt-get install` error but stayed `in_progress` (zombie) — and Coolify's build queue is serialized **server-wide across apps**: a stuck lira build blocked sana-turnos builds for hours.
2. `smoke_prod.py` compares prod assets against the local checkout and probes consistency — but when a commit changes NO assets, the old build passes every check, so a stalled deploy is invisible to it.

## Solution
- Diagnose: `GET /api/v1/deployments` (all apps! not just `?uuid=<app>`) with `COOLIFY_ACCESS_TOKEN`; look for `in_progress` older than ~10 min anywhere on the server. Read its logs via `GET /api/v1/deployments/<deployment_uuid>`.
- Kill zombies: `POST /api/v1/deployments/<deployment_uuid>/cancel`. Cancelling only stops the build container, never the running app. Queued builds start after the zombie clears.
- Re-trigger if needed: `POST /api/v1/deploy?uuid=<current-app-uuid>&force=true`.
- Verify a deploy actually LANDED by a marker unique to the new commit (a new file path, a bumped `?v=`, a changed string probed on a page) — never by smoke alone when the diff has no asset changes.

## Recurrence 2026-08-05 — clear the QUEUE too, not just the zombie
Same shape on the **demo** app: one `in_progress` stuck ~9 h, and **8 stale `queued`
deploys piled up behind it**, each pinned to an old commit. Two things this added:

- **Cancelling the zombie is not enough.** Every queued entry carries its OWN commit,
  so releasing the queue would have built 8 old commits serially and left the demo on
  a stale build — the exact symptom you were trying to fix. Cancel all queued entries
  except the newest (`POST /api/v1/deployments/<uuid>/cancel` each), then
  `POST /api/v1/deploy?uuid=<app>&force=true`.
- **`smoke_demo.py` reported VERDE the whole time**, on a build 9 h old, including its
  "12/12 requests serve the same build" consistency check — consistently stale is still
  consistent. Same blind spot as `smoke_prod.py` above. The marker that actually caught
  it: `curl -sI <asset>` and read `last-modified` against the push time, plus grepping
  the served asset for a string unique to the new commit.
- `GET /api/v1/deployments` returns a **dict keyed by id**, not a list — but not always;
  handle both (`d.values() if isinstance(d, dict) else d`).

## How to apply
Any "pushed but prod unchanged" symptom: check the server-wide deployment list FIRST, before suspecting webhooks, Cloudflare, or code. Consider adding a build-SHA marker (e.g. meta tag) so smoke can assert the exact commit. Related: [[cloudflare-js-cache]], the stale-container round-robin note in docs/architecture/deploy.md.
