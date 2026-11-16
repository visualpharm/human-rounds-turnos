---
title: A photo with two documents makes Gemini return a JSON array — pick the orden by score
scope: repo
area: ai
tags: [gemini, json, ordenes, multi-documento, 3-pass]
created: 2026-07-08
refs: [1c021ba]
status: active
---

## Problem
The real handwritten orden photo (2026-06-16 CEM: receta + solicitud de ergometría on one image) produced `revision_manual` with `pases: 0` yet non-zero cost — all three passes ran but every result was discarded.

## Root cause
The photo contains TWO recetarios, so Gemini answered with a JSON **array** of two objects (one per document) despite the prompt asking for a single object. `extract_json` sliced from the first `{` to the last `}`, producing `{...},{...}` → `JSONDecodeError` → `{}` → pass dropped. With 0 parsed passes the reconciler falls back to manual.

## Solution
Two reinforcing fixes in `ai_orden.py`: (1) `extract_json` now parses top-level arrays and picks the element that most looks like the orden via `_doc_score` (es_orden_medica +10, especialidad +3, +1 per non-null field); (2) `_PROMPT` explicitly says "si la foto contiene MÁS DE UN documento, devolvé UN SOLO objeto con la orden/solicitud de estudios". Verified: the same photo now reads 3/3 with paciente/médico/matrícula at 1.0 agreement.

## How to apply
Never assume a vision model honors "return one object" when the image shows several documents — parse defensively for arrays. Real patients WILL photograph the whole recetario pair; keep the test file `~/Downloads/ordenes-ejemplo/orden-ergometria-2026-06-16.jpeg` as the regression case.
