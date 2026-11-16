---
title: El cloud no tiene data/ — snapshot de turnos MSD en webdata/
scope: repo
area: deploy
tags: [msd, turnos, coolify, snapshot]
created: 2026-07-10
refs: [72e48f4]
status: active
---

## Problem
En el cloud NINGÚN médico/servicio mostraba turno real ("Te confirma el centro" en todos lados); local sí.

## Root cause
`data/` está excluido de git (runtime) y `data/msd_turnos.json` lo refresca un LaunchAgent local cada 30 min — el deploy de Coolify no tiene ni el archivo ni el refresco.

## Solution
Snapshot commiteado: `webdata/msd_turnos.json`; `msd_data._load()` prueba `data/` (vivo) y cae al snapshot. `edad_horas` en la respuesta dice qué tan viejo es.

## How to apply
Antes de un push que el responsable del producto vaya a demo-ear en el cloud: `cp data/msd_turnos.json webdata/msd_turnos.json` y commitear si cambió. Si algún día el contenedor puede correr `tools/msd_pull.py`, esto se reemplaza por un cron del contenedor.
