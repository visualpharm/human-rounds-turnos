---
title: Fotos de centros vía Brila/Google Places — media_url sin auth, maxWidthPx falla a veces
scope: repo
area: webdata
tags: [brila, google-places, fotos, galeria, centros]
created: 2026-07-10
refs: [971491a]
status: active
---

## Problem
Poblar la galería de fotos de los centros (`webdata/centro-photos/gallery/` +
`centros-galeria.json`) con fotos reales de Google Maps, sin API key de Places.

## Root cause
Los JSONs de `webdata/centros-brila-info/*.json` (endpoint business-info de Brila,
sin auth — ver `scripts/fetch_brila_business_info.py`) traen `photos[].media_url`
que sirve el JPEG directo. PERO `?maxWidthPx=N` devuelve 400 en varias fotos
(no en todas), así que un solo intento con parámetro pierde la mayoría.

## Solution
`scripts/fetch_brila_photos.py`: intenta `media_url?maxWidthPx=1400`, cae a la
URL pelada si falla; valida magic bytes JPEG; mergea en `centros-galeria.json`
(idempotente, dedup por `src`); crédito = authorAttributions + "· Google Maps".
Después achicar con `sips --resampleWidth 1400` (las peladas vienen full-size).

## How to apply
Nuevo centro o refresh: correr `python3 scripts/fetch_brila_business_info.py` y
`python3 scripts/fetch_brila_photos.py`. Curar a mano: las fotos de usuarios
traen ruido (una canasta de tazas de regalo apareció en caps-valeria — mirarlas
con Read antes de shippear y borrar entrada + archivo). Las citas/reseñas de esos
JSONs se curan a mano en `centros-resenas.json` (las de 1 estrella no van).
