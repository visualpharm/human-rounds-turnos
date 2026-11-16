# Resolutor automático de conflictos HSI + sección «HSI» del admin

**Fecha:** 2026-07-22 · **Módulos:** hsi_resolver.py (nuevo), hsi_sync.py `tick`,
db.py (`hsi_conflicto_resolver`/`limpiar_hsi_vinculo`/`hsi_metricas`/…),
solicitudes.py (`/api/admin/hsi-sync*`), admin.html (`#hsi`)

## Contexto
La lista "22 conflictos de sincronización" incrustada en la cola era ilegible
para el personal (jerga del journal en inglés) y nadie podía actuar: no se
puede pedirle a un administrativo que "mergee duplicados". El responsable del producto: pensar como
el departamento de IT del hospital — agrupar por causa raíz, resolver solo lo
resoluble, y mostrar únicamente lo que de verdad necesita una persona.

## Diagnóstico de los clusters reales
- **`duplicate_local_hsi_appointment_id` (13/22):** `hsi-up.sh` hace
  `docker rm -f` → HSI de dev se re-crea y sus appointment ids ARRANCAN EN 1;
  solicitudes viejas quedan apuntando al turno de OTRO paciente.
- **Profesional/especialidad sin agenda (8/22):** conflictos VIEJOS de antes
  de los seeds — el turno ya estaba ok pero nada cerraba la fila del journal.
- **`no free slot` (2):** turnos a las 17:00 cuando todas las ventanas
  terminan 17:00 — genuinamente sin hueco.

## Diseño (hsi_resolver.py)
`resolver_pass()` corre en cada tick de hsi_sync y a demanda:
1. **Obsoletos:** solicitud ya ok / rechazada / turno pasado → se cierra.
   Conflictos de ruteo (profesional/especialidad/sin_hueco) se deduplican POR
   SOLICITUD (son etapas del mismo push: sólo el más nuevo describe la
   realidad); el resto por (solicitud, causa).
2. **Duplicados:** el dueño legítimo = coincide fecha/hora (+DNI si el mirror
   lo trae) con lo observado en HSI; a igual puntaje gana el id más nuevo
   (creado contra la instancia actual). Los demás pierden el vínculo
   (`db.clear_hsi_link`): futuros → `pendiente` + re-encolados (consiguen
   SU turno), históricos conservan su estado.
3. **Sin agenda del profesional:** reintento tal cual (pudo cargarse) →
   OTRO profesional de la especialidad a la MISMA hora (`diaries_for_specialty`
   en hsi_client, sinónimos extraídos a `_SPECIALTY_SYNONYMS`) con
   `asignar_medico_hsi` + nota. Igual para especialidad con médico de otra
   rama (Ecografía asignada a una cardióloga).
4. **Agenda llena:** otro profesional misma hora → sobreturno con el original.
5. **Drift/carrera:** si la edición local terminó, se re-encola con checkpoint
   fresco / se deja reconciliar. `hsi_appointment_missing` + cancelada = ya está.
6. **No-IT (quedan abiertos con propuesta):** crear una agenda clínica nueva o
   revertir una cancelación de HSI no se decide solo.

Toda resolución queda auditada en `hsi_sync_conflicts.resolution` jsonb
(`{accion, texto}`, columnas nuevas `resolution`/`resolved_by`).

## Sección «HSI» (admin, round 2 mismo día por pedido del responsable del producto)
Hero = **% de turnos futuros aprobados agendados en HSI** (sin sub-métricas ni
línea de cola — las quitó explícitamente). H2 **Conflictos** + botón **«Volver
a revisar»** (una pasada del resolutor). Cada conflicto = checkbox MARCADO;
botones de resolución rankeados (el 1º es el mejor): **Mover al primer horario
libre** (no-exacto, avisa al paciente por email) / **Agendar como sobreturno**
(mantiene la hora prometida) / **Rechazar la solicitud** (motivo estándar +
email). `POST /api/admin/hsi-sync/conflictos {accion, conflictos:[ids]}` →
`hsi_resolver.aplicar_resolucion` devuelve resultado POR conflicto (lo que no
se pudo dice por qué). El router de la sección es un listener `hashchange` en
el script inline (se registra DESPUÉS de admin-content/users/reports, así que
en `#hsi` gana). El nav «HSI» sólo aparece con `hsi_activo` (en Coolify con
`TURNOS_SKIP_HSI=1` no existe).

## Verificación
22 conflictos reales → pasada automática → 3 abiertos honestos → botón «Mover
al primer horario libre» con los 3 marcados → los 3 agendados en HSI (appts
13/14/15, médicos correctos: Varela y Montes en sus propias agendas
seedeadas, la Ecografía reasignada al profesional de imágenes) → sección vacía,
78% en el hero. Tests: `test/test_hsi_resolver.py` (23) + guard actualizado en
`test_usability_fixes.py`.

## Gotchas
- Un turno a las HH:00 exactas del cierre de ventana NUNCA entra (HSI exige
  `hora + duración <= hasta`) — ni como sobreturno: el sobreturno necesita un
  boundary válido dentro de la ventana.
- `git`-side: reiniciar el server en :7700 con `kill` + sleep corto pierde la
  carrera del bind ("Address already in use") y te deja probando el server
  VIEJO — verificar el bind antes de atribuir un 404 al código.
