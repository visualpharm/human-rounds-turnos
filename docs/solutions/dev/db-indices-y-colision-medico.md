# DB indexes + the collision check that scanned the whole history

**2026-07-22 · db.py, solicitudes.py**

## The problem

`solicitudes` is the only table that grows without bound and the most-filtered in
the app, yet it had **no index beyond its primary key**. Every operator-queue load
(`listar_admin(estado)`), every `reportes_*` (all filter `created > now() - N days`),
every "Mis turnos" (`WHERE dni=%s OR solicitante_dni=%s`), and the approved-future /
HSI / reminder scans were **sequential scans of the full history**.

Worse, a query-shape bug: `solicitudes._turnos_medico_en_hora` (the doctor/minute
collision check that runs on **every approval**) called `db.list_admin("aprobada")`
— i.e. `SELECT * FROM solicitudes WHERE estado='aprobada'` with **no time bound** —
pulling the entire set of ever-approved requests into Python to check one minute.

## The fix

**Indexes** (all `CREATE INDEX IF NOT EXISTS`, applied on boot):

- `solicitudes(estado, created DESC)` — admin queue + every report's `created` range.
- `solicitudes(estado, turno_dt)` — approved-future, HSI-pending, reminders, and the
  collision check below.
- `solicitudes(dni)` + `solicitudes(solicitante_dni)` — "Mis turnos" `OR` needs both
  for Postgres to BitmapOr.
- `solicitudes(hsi_appointment_id) WHERE ... IS NOT NULL` — the correlated `NOT EXISTS`
  subqueries (else quadratic as both tables grow).
- `sesiones(dni)`, `turno_holds(solicitud_id)`, `lista_espera(solicitud_id)`,
  `hsi_patients(dni)`, `hsi_appointments(hsi_patient_id/hsi_diary_id)` — join/sweep keys.

**Query shape:** new `db.approved_in_range(desde, hasta)` returns only approved rows
whose `turno_dt` is in `[desde, hasta)` (uses `solicitudes_estado_turno_idx`).
`_turnos_medico_en_hora` now asks for the one-minute window instead of the whole
history; the doctor-name normalization runs over a handful of rows. Same semantics —
comparison is instant-based on the `timestamptz` column, so the ART minute boundaries
capture exactly what the old Python `== wanted_key` accepted. Guarded by
`test/test_solicitudes.py::TestColisionMedicoAcotada`.

## Watch out

- The `_ART` timezone logic stays in `solicitudes.py`; `db.approved_in_range` takes
  tz-aware bounds and never assumes a timezone (don't pass naive datetimes).
- No foreign keys anywhere in the schema is **deliberate** for `asistencia_eventos`
  (documented at db.py:108) so tests can truncate `solicitudes` in isolation; the
  others are soft references by omission.
- `sesiones` still has **no server-side expiry** (prototype gap, db.py comment) — it
  will be the fastest-growing table; `sesiones(dni)` helps the lookups but a TTL sweep
  is still open work.
