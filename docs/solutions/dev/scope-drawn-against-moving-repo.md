---
title: El alcance de un reparto se desactualiza si el repo se sigue moviendo
scope: repo
area: dev
tags: [parallel-agents, scope, worktrees, i18n]
created: 2026-08-05
refs: []
status: active
---

## Problem
`admin-solicitudes.js` — la cola de Solicitudes del panel, la pantalla de
más tráfico del staff — terminó con CERO traducción tras el proyecto de
i18n de ~31 PRs. Ninguna de las unidades lo tocó porque el archivo no
existía cuando se repartieron los límites: otra sesión paralela lo extrajo
de `admin.html` a un archivo nuevo DESPUÉS de que el alcance ya estaba
fijado.

## Root cause
El reparto se hizo contra una lista de archivos tomada en un momento dado.
Con ~29 agentes trabajando en paralelo sobre el mismo repo, esa lista es un
snapshot que puede quedar obsoleto minutos después: un refactor de otra
unidad crea o mueve un archivo y nadie queda dueño de la versión nueva —
no por descuido de una unidad puntual, sino porque el archivo literalmente
no existía cuando se dibujaron los límites.

## Solution
No hay fix de una vez — es una propiedad estructural del reparto contra un
árbol en movimiento. Se encontró recién en un audit final, no durante el
trabajo mismo.

## How to apply
En cualquier reparto de trabajo sobre archivos con varias sesiones
paralelas (traducción, refactor grande, migración): re-derivar la lista de
archivos objetivo contra el árbol ACTUAL antes de declarar cobertura
completa, no contra la lista con la que arrancó el reparto. Un archivo
nuevo creado a mitad de camino no tiene dueño hasta que alguien lo busca
explícitamente al cerrar.
