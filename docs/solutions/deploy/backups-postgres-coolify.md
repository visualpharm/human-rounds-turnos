---
title: Backups del Postgres de prod (Coolify) — configurado, con dos gaps
scope: repo
area: deploy
tags: [backups, postgres, coolify, phi, ops]
created: 2026-07-17
refs: []
status: active
---

## Problem

El Postgres de prod (`sana_turnos`, container Coolify `sana-turnos-db`, uuid
`<prod-db-uuid>`) **no tenía ningún backup programado** — cero. Para
una app con PHI (solicitudes, cobertura, domicilio, órdenes) es un agujero.

## Solution (lo que quedó configurado, 2026-07-17)

Vía la API de Coolify (`COOLIFY_ACCESS_TOKEN` en the operator's private secrets file):

- `POST /api/v1/databases/<uuid>/backups` con `{"frequency":"0 3 * * *"}` creó el
  schedule (backup uuid `<backup-schedule-uuid>`).
- `PATCH .../backups/<bk>` con `{"save_s3":false, retention_amount_locally:7,
  retention_days_locally:30}` — **local-only** (no hay S3 destination configurado
  en este Coolify) y retención 7 copias / 30 días.
- Estado verificado: `enabled=true`, `frequency=0 3 * * *`, `save_s3=false`,
  `disable_local_backup=false`.

**Primer dump: aún NO observado.** El endpoint de "backup ahora" no está en la
API pública v1 (probé `/backup`, `/execute`, `/run` → 404); el primer backup
corre en el próximo 03:00 ART, o se dispara a mano con **"Backup Now"** en la UI
de Coolify (base de datos → Backups). Verificar ahí que la primera ejecución sea
verde antes de darlo por cerrado.

## Gaps que quedan (NO resueltos)

1. **Backup local = mismo servidor.** Protege contra `DROP TABLE`/corrupción/borrado
   accidental, NO contra pérdida del servidor. Para off-site hace falta un S3
   destination en Coolify (Settings → S3 Storages) y después `save_s3=true` +
   `s3_storage_id` en el schedule. Decisión del responsable del producto: la muni va a self-hostear
   ("servidores propios"), así que invertir en S3 propio ahora puede ser prematuro
   — pero mientras viva en el Coolify del responsable del producto, un solo servidor es un SPOF.
2. **El volumen `data/` no está en ningún backup.** Las **órdenes médicas
   subidas** (`/app/data/ordenes/*.png|pdf`) y las **fotos de pacientes/médicos**
   (`/app/data/cuenta-fotos`, `/app/data/medico-fotos`) viven en un volumen
   persistente de Coolify, NO en Postgres. El backup de DB no los toca. Si se
   pierde el volumen, se pierden las órdenes. Opciones: incluir el volumen en un
   backup de Coolify, o (mejor a futuro) mover los uploads a object storage.
   Nota: la DB guarda el `orden_path` pero no los bytes.

## How to apply

- Ver estado: `GET /api/v1/databases/<uuid>/backups` con
  `Authorization: Bearer $COOLIFY_ACCESS_TOKEN` (base `coolify.brieflysites.com`).
- La API acepta cron crudo SÓLO en el create; el PATCH rechaza `frequency` cruda
  ("selected frequency is invalid") — para cambiar horario, hacerlo por UI o
  recrear el schedule.
- Antes de confiar en un backup: restaurarlo una vez en un Postgres de prueba.
  Un backup no verificado no es un backup.
