---
title: Historical: premature manual-version probe caches an old asset
scope: repo
area: deploy
tags: [cloudflare, cache, deploy-verification, vbump]
created: 2026-07-29
refs: [8d6e6df]
status: superseded
---

## Current state (2026-08-21)
This is historical manual-version evidence. First-party assets now use content
fingerprint URLs; contributors must not create or bump a `?v=` URL. The
remaining operational lesson is to wait for deployed HTML to reference a
current immutable URL before verifying that URL.

## Problem
After pushing a `?v=` bump (css v29→v30), a verification `curl .../sacoturno.css?v=30`
ran BEFORE the Coolify deploy finished. Cloudflare cached the response — the OLD css —
under the NEW URL for 4 h. Every later check of `?v=30` returned the old bytes
(`cf-cache-status: HIT`), making the deploy look broken while the origin was correct
(a cache-busted request served the new content, `MISS`).

## Root cause
Cloudflare keys the 4h asset cache by full URL including the query string. A never-seen
`?v=N` URL is only "fresh" until the FIRST request; if that first request arrives while
the old container still serves, the stale body gets cached under the new version tag.
The prober creates the very staleness it is checking for.

## Historical remediation (superseded)
Bump again to a never-requested `?v=N+1` and push (deterministic, no CF purge/creds
needed) — commit `8d6e6df`.

## Historical procedure (do not apply)
Order-safe deploy verification: poll the HTML page (not CF-cached) until it references
the new `?v=`; only THEN request the versioned asset itself. Never curl a fresh `?v=`
URL "to see if it's up yet". If a poisoned entry already exists: new bump > CF purge.
Related: [[cloudflare-js-cache]], [[cached-401-poisons-an-asset]].
