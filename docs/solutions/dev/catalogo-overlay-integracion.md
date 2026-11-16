---
title: catalogo.py overlay — gotchas de integración con lectores y cliente
scope: repo
area: dev
tags: [catalogo, overlay, monkeypatch, admin_list, validar]
created: 2026-07-11
refs: [catalogo.py, hospital.py:_con_overlay, admin-contenido.js]
status: active
---

## Problem
Al enchufar el overlay Postgres del catálogo (tabla `catalogo`) a los lectores
existentes aparecieron dos roturas no obvias: (1) tests de hospital.py que
monkeypatchean `hospital._load` quedaban inertes; (2) los metadatos de lectura
`_origen`/`_borrado` se persistían dentro del doc jsonb.

## Root cause
1. Si `hospital._centros()` llama a `catalogo.centros()`, la base JSON la carga
   catalogo con SU propio `_load` — el monkeypatch de `hospital._load` en
   `test_centros_tiles.py` ya no intercepta nada.
2. El form del admin arma el doc con `Object.assign({}, item)` sobre lo que
   devolvió `admin_list()` — que incluye `_origen`/`_borrado` — y `validar()`
   dejaba pasar campos desconocidos (passthrough deliberado).

## Solution
1. `catalogo.merge_with_overlay(tipo, base)` existe SEPARADO de
   `catalogo.centros()`: hospital.py sigue siendo dueño de cargar su base
   (monkeypatcheable) y sólo delega el paso de merge (`hospital._con_overlay`).
2. `validar()` pela toda key que empiece con `_` antes de validar — los
   metadatos de lectura nunca entran al jsonb. Cubierto por
   `test_metadatos_de_lectura_se_pelan_del_doc`.

## How to apply
Lector nuevo del catálogo: cargá tu base y pasala por `merge_with_overlay`
(fallback tolerante), no llames a los accessors de catalogo si tus tests
monkeypatchean la carga. Metadato de respuesta nuevo → prefijo `_`.
