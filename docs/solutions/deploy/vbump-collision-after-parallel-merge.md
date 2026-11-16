---
title: Historical: duplicate manual ?v= values after a parallel merge
scope: repo
area: deploy
tags: [cache, cloudflare, vbump, merge]
created: 2026-08-05
refs: []
status: superseded
---

## Current state (2026-08-21)
This is a historical incident. The deleted vbump guard and manual `?v=`
numbers are not part of the current workflow. Content-hash fingerprints give
changed bytes a new URL automatically; contributors should verify
registry-rendered URLs and must not reintroduce a manual query version.

## Problem
Tras integrar varias branches en paralelo (proyecto de i18n, ~31 PRs), dos
versiones distintas del CUERPO de `search.js` reclamaban el mismo `?v=7`.
Un browser que ya tenía cacheada una de las dos nunca iba a pedir la
otra — Cloudflare cachea `.js`/`.css` 4 horas e ignora el `no-cache` de
origen ([[cloudflare-js-cache]]) — así que la mitad de los visitantes
seguían viendo código viejo sin ningún error visible ni en logs ni en un
smoke test que sólo pega una vez por asset.

## Root cause
`vbump-drift-guard.py` sólo detecta DRIFT (referencias al MISMO archivo con
`?v=` distintos entre sí); no detecta COLISIÓN (dos commits que suben el
mismo número para contenidos distintos), típico cuando dos branches
paralelas bumpean el mismo archivo de forma independiente y el merge no
las hace chocar en texto porque tocaron partes distintas del archivo.

## Historical solution (superseded)
`grep -rn '\.js?v=\|\.css?v='` no alcanza solo; hay que comparar además que
ningún `?v=N` referenciado hoy corresponda a dos CUERPOS de archivo
distintos entre el estado pre-merge y post-merge de cada branch integrada.

## Historical procedure (do not apply)
Después de cualquier merge que integre varias branches en paralelo:
`grep -rn '\.js?v=\|\.css?v='` sobre todo el árbol, verificar (a) que cada
archivo tiene un único `?v=` en todas sus referencias — lo que ya chequea
`vbump-drift-guard.py` — y (b) que ningún número de versión quedó reusado
para dos cuerpos de archivo distintos entre los commits que se integraron.
Ante la duda, bumpear de nuevo — nunca reusar un número que ya salió a
producción con otro contenido.
