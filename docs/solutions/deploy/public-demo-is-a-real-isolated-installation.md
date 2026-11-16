---
title: Public demo is a real isolated installation
scope: repo
area: deploy
tags: [demo, sandbox, admin, integrations, ai, i18n]
created: 2026-07-30
refs: [demo_installation.py, packs/example-town, docs/demo-installation.md]
status: active
---

## Problem

A static showcase cannot prove real workflows. Exposing Pinamar risks municipal
data and integrations; faking AI makes the demo visibly non-functional.

## Root cause

Product, locality data, DB and integrations were treated as one boundary even
though the data-pack layer already separates engine from locality.

## Solution

Run the app with `packs/example-town`, its own PostgreSQL DB and
`TURNOS_DEMO_MODE=1`. Demo mode marks fictional data, adds signed passwordless
access, and locks integrations before imports or DB overrides. Chat uses a
dedicated capped key; referral/OCR and visitor intake remain deterministic.
The guarded seed is the second exception: it writes a versioned fictional Sofía
transcript and uses a Python-only network override for one real clinical brief.

The shared demo bar is compact navigation, not a prose banner. Its ES/EN links
append `location.hash` before the normal `?lang=` round trip; otherwise an admin
language change silently drops `#consulta/<id>` and closes the patient view.

## How to apply

Never reuse Pinamar DB, volume, secret or credentials. Set a unique admin secret,
capped AI key and public origin. Verify `/api/demo`, fictional catalogs,
passwordless access, real chat, Sofía's real model/brief, hash-preserving ES/EN,
and zero overflow at 7 standard widths. See `docs/demo-installation.md`.
