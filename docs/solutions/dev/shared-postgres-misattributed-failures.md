---
title: Un Postgres compartido entre worktrees paralelos disfraza fallos reales
scope: repo
area: dev
tags: [testing, worktrees, postgres, flaky]
created: 2026-08-05
refs: []
status: active
---

## Problem
Con varios worktrees corriendo tests contra el MISMO Postgres durante el
proyecto de i18n, dos fallos de `test_booking_requests.py` en una misma
tanda se le atribuyeron a "contención de base de datos" en todo el batch.
La causa real era otra: una fecha de calendario hardcodeada en el test,
sin relación alguna con la concurrencia. Por el mismo motivo — nadie
ejercitó ese camino en un entorno compartido y ruidoso — un `NameError` en
vivo (`remedies.py` llamaba a `_mismo_servicio_otro_centro()`, indefinida
en todo el árbol) pasó semanas sin detectarse.

## Root cause
Cuando varias sesiones comparten una base, cualquier fallo intermitente se
explica con la hipótesis más disponible ("seguro es contención") y esa
hipótesis nunca se verifica porque encaja con lo que ya se sabe del
entorno. La explicación cómoda reemplaza la lectura del test que falló.

## Solution
Ninguna implementada en este proyecto — se detectó al leer el test caso
por caso, no por tener una base separada por unidad.

## How to apply
Una base de test POR unidad (o al menos por sesión) sería barata comparada
con el tiempo perdido diagnosticando "contención" que no existe. Mientras
eso no exista: ante un fallo intermitente en un entorno con Postgres
compartido, leer el test que falló ANTES de asumir la causa ambiental — la
sospecha de concurrencia es la más cómoda y la menos verificada.
