---
title: Historical: Cloudflare edge cache and manual ?v= cache busting
scope: repo
area: deploy
tags: [cloudflare, cache, deploy, stale-js]
created: 2026-07-09
refs: [6580429]
status: superseded
---

## Current state (2026-08-21)
This is historical incident evidence. Manual first-party `?v=` cache busting
and the vbump hook are retired. Contributors must use the platform or module
fingerprint registry; a first-party source invariant rejects a manual `?v=`
reference. Immutable URLs eliminate the collision described below.

## Problem
After a deploy, the product owner's browser kept showing the OLD nav.js (menu with "Clínicas privadas") while the origin already served the new one.

## Root cause
brieflysites.com is proxied by Cloudflare, which caches static extensions (.js/.css) at the edge with `max-age=14400` (4 h) and **keeps serving its own TTL even after the origin started sending `Cache-Control: no-cache`** (zone-level Browser Cache TTL override; `cf-cache-status: HIT`, `age` in the thousands). Server-side header fixes alone don't reach users until the edge object expires, and there is no brieflysites-scoped CF API token on the dev machine to purge.

## Historical solution (superseded)
Two layers: (1) `server.py:end_headers` sends `Cache-Control: no-cache` for .js/.css (helps direct/origin access); (2) **every `<script src>`/stylesheet reference carries a version query — currently `?v=21`** — in index.html, hospital.py `_shell`/pages, cuentas.py, admin.html, and the dynamic `ensureScript` loads in mis-solicitudes.js. A changed URL is always a CF MISS → fresh fetch.

## Historical procedure (do not apply)
When you change any shipped .js/.css in this repo, **bump the `?v=` number everywhere** (grep for `?v=` — one sed pass). If a deploy "didn't take" in a browser, check `curl -sI .../file.js | grep cf-cache-status` before debugging code.

## Historical deployment lesson: do not probe the versioned URL before a rolling deploy settles (2026-07-16)

El bump `?v=N` sólo salva si NADIE pide la URL nueva **durante** el deploy: Coolify hace rolling deploy (contenedor viejo drenando + nuevo sirviendo) y un fetch de `foto.jpg?v=N` que cae en el contenedor viejo deja los bytes VIEJOS cacheados en Cloudflare bajo la URL NUEVA por 4 h — pasó dos veces seguidas con las tiles de talleres (`?v=2` y `?v=4` quedaron envenenadas por probes de verificación demasiado tempranas).

Orden correcto para verificar un asset tras el push:
1. Esperar a que la PÁGINA muestre el `?v=` nuevo (el HTML no se cachea).
2. Confirmar el ORIGEN con query descartable única (`?probe=$RANDOM`) hasta que el hash coincida con el repo, y repetir 3-4 probes hasta que no haya mezcla de contenedores.
3. Recién entonces tocar la URL versionada real.

Si una URL versionada ya quedó envenenada: no hay token CF con scope de purge en la máquina de desarrollo (el de 1P es sólo DNS) → bump a `?v=N+1` y verificar con el orden de arriba.
