---
title: HSI patient search/create requires gender — capture sexo at the form
scope: repo
area: hsi
tags: [hsi, solicitudes, gender, integration]
created: 2026-07-08
refs: [c554dcf]
status: active
---

## Problem
Approving a solicitud recorded `hsi_estado: error` on every push while the direct `hsi_client.push_turno(...)` call worked in isolation.

## Root cause
HSI's patient endpoints (`GET /api/patient/minimalsearch`, `POST /api/patient/institution/{id}`) require `genderId` — there is no gender-less lookup. The solicitud flow didn't capture sexo, so `_push_hsi` passed `sexo=None` and `_gender_id(None)` raised before any network call. Two modules built against the same interface by different owners collided exactly on the optional-looking argument.

## Solution
Sexo (M/F/X, "como figura en tu DNI") is captured in the request form (`servicio.js`), validated and stored (`solicitudes.py` → `db.py` column `sexo`), and passed to `push_turno` with fallback `"X"`. `_push_hsi` now also `traceback.print_exc()`s to the server log instead of swallowing.

## How to apply
Any new flow that ends in an HSI patient operation must carry sexo end-to-end from user input. Full endpoint semantics: [[../..//docs/HSI-API-NOTES.md]] (docs/HSI-API-NOTES.md). Never treat `sexo` as optional metadata here.
