# El botón «Reintentar» de HSI no hacía nada — outbox reset + causa real

**Fecha:** 2026-07-21 · **Módulos:** solicitudes.py `_reintentar_hsi`, hsi_sync.py, db.py `hsi_outbox_reset`, admin.html `hsiChip`

## Síntoma
Pocas solicitudes aprobadas quedaban integradas en HSI; la cola mostraba chips
"HSI omitido"/"HSI error"/"HSI pendiente" y el botón Reintentar respondía
siempre el mismo toast genérico sin cambiar nada.

## Causas (tres, apiladas)
1. **Enqueue idempotente + claim selectivo = reintento muerto.** El retry
   re-encolaba con la MISMA idempotency key → `ON CONFLICT` devolvía la fila
   existente de la outbox; si esa fila estaba en `conflict` (o en `error` con
   `next_attempt_at` futuro por el backoff exponencial), `hsi_outbox_claim_id`
   no la tomaba → cero I/O → 502 genérico, para siempre.
2. **Un conflicto permanente no se veía en la solicitud.** `_replay_one`
   marcaba la outbox como `conflict` pero `solicitudes.hsi_estado` quedaba en
   `pendiente` — el chip mentía "HSI pendiente" sin que nada fuera a pasar.
3. **El HSI local de demo sólo tenía agendas de Cardiología y Kinesiología**
   (`tools/seed_hsi_demo.py`): cualquier otra especialidad conflictuaba con
   `no diary matches` — por eso "muy pocas se integran" en dev. Además
   `<workspace>/sana/scripts/hsi-up.sh` hace `docker rm -f`: cada corrida
   RECREA HSI vacío (institución incluida — crearla vía
   `POST /api/backoffice/institutions` con la sesión de `HsiClient`, después
   correr los seeds).

## Seeds ampliados (mismo día — dev integra casi todo el catálogo)
- `tools/seed_hsi_demo.py` ahora es data-driven (ROSTER): 29 especialidades
  con un profesional DEMO cada una, Lu-Vi 08-17 (el par legacy conserva 08-12).
  Gotchas HSI: la validación de solapamiento de agendas es POR consultorio
  (una oficina compartida da 400 en la segunda agenda → un consultorio por
  profesional), y con nombre duplicado en el catálogo conviene el id tipo 2
  «Especialidad» (criterio del Cardiología=131 original).
- `tools/seed_hsi_medicos.py` (nuevo): espeja los ~77 profesionales REALES con
  `agenda` de `medicos.json` — persona/professional/user/rol + UNA agenda con
  las franjas exactas de Sana (`create_diary(windows=…)`, cada_min real,
  franjas del mismo día se fusionan: HSI rechaza ventanas superpuestas).
  Sin esto, toda aprobación con médico asignado (lo que hace la cola por
  default vía `medico_sugerido`) conflictuaba: los nombres reales no existían.
- `hsi_client._match_diary`: los tokens honoríficos («Dra./Lic.») se descartan
  del filtro por profesional — HSI no los guarda y exigirlos hacía fallar TODO
  push con médico; y el mapa práctica→especialidad cubre imágenes
  (ecografía/mamografía/tomografía/radiografía→Diagnóstico por imágenes),
  laboratorio→Bioquímica clínica, neumología→Neumonología,
  audiometría→Fonoaudiología, pie diabético→Diabetología, niño sano/enfermo→
  Pediatría.

## Fix (contrato actual, commit 585221f)
- `db.hsi_outbox_reset(id)`: el click del operador rearma el intento YA
  (`pending`, `next_attempt_at=now()`), pisando backoff y rescatando
  `conflict`; nunca rearma `confirmed`.
- `_replay_one` → `_conflict()`: conflicto permanente en create/reassign
  también marca `hsi_estado='error'` en la solicitud (attendance/cancel no —
  su turno SÍ llegó a HSI).
- `_reintentar_hsi`: 409 `hsi_desactivado` en modo skip; 400 `turno_pasado`;
  una fila con `hsi_appointment_id` se reprograma (enqueue_reassign), jamás se
  re-crea; en fallo responde `detalle` = `hsi_sync.motivo_operador(outcome)`
  (HSI caído / falta agenda especialidad / falta agenda profesional / sin
  hueco / credenciales), que el toast muestra tal cual.
- `hsiChip`: botón sólo con `hsi_activo` (nuevo campo de
  `/api/admin/requests`) + turno futuro + estado ≠ ok; «Enviar a HSI» para
  filas `skip`/nunca empujadas.

## Verificación
End-to-end contra HSI v2.71.2 local: approve → appointment creado sync;
«Enviar a HSI» de una fila skip → ok; especialidad sin agenda → 502 con el
motivo accionable; turno pasado → 400. Tests: `test_solicitudes.py`
(`test_reintentar_hsi_*`), staged-tree green.
