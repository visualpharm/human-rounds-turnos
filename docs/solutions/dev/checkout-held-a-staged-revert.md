---
title: Un checkout puede tener stageado un revert sin que nadie lo pida
scope: repo
area: dev
tags: [git, merge, conflict, worktrees, i18n]
created: 2026-08-05
refs: []
status: active
---

## Problem
Tras integrar varias de las ~31 branches de traducción, el checkout
principal tenía en el índice contenido PRE-merge para 74 archivos y 21
catálogos de idioma borrados. `search.js` en el árbol no tenía nada de la
morfología nueva en inglés mientras `HEAD` sí; `patient_web/site_*.py` tenía 60
llamadas a `i18n.t_or` contra 101 en `HEAD`. Commitear desde ese estado
deshacía el proyecto entero.

## Root cause
Un conflicto de merge se resolvió AL REVÉS — se quedó con "ours" (la
versión vieja) en vez de "theirs" (la traducida) en varios archivos — y ese
resultado quedó stageado sin commitear. `git status` no distingue "un merge
normal a mitad de camino" de "un merge resuelto para atrás": ambos se ven
igual, cambios staged listos para commitear.

## Solution
Detección: comparar el archivo sospechoso contra `git show
origin/main:<archivo>` — si el archivo del checkout es MÁS CHICO o le
faltan símbolos que sí están en `origin/main`, el conflicto se resolvió
para atrás. Contar rápido un símbolo de progreso conocido (llamadas a
`i18n.t_or`, keys de catálogo) por archivo sirvió de proxy antes de ir
línea por línea.

## How to apply
Después de resolver cualquier conflicto no trivial, sobre todo uno que
toca decenas de archivos: antes de commitear, comparar el conteo de un
símbolo de progreso conocido entre el checkout y `origin/main` — un número
que BAJÓ es la señal de un merge resuelto al revés. Nunca asumir que "no
marcó conflicto" significa que el resultado es el correcto.
