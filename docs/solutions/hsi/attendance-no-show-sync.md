# Attendance and no-show synchronization

## Problem

The clinical attendance decision belongs to HSI, while Sana needs a usable
operator control and a web-booking safeguard. A future-only HSI poll cannot see
an absence because staff records it after the appointment date. Counting both
an HSI mirror and its linked Sana request also risks blocking a patient twice.

## Durable model

- `solicitudes.asistencia` stores the current operational value.
- `asistencia_eventos` is append-only and preserves corrections, operator,
  source, and time.
- HSI states 3/9 map to absent and state 5 maps to present.
- Outbound attendance uses the existing Postgres journal and checkpoint drift
  rules; unresolved concurrent edits become visible conflicts.
- Reconciliation defaults to a 90-day lookback via
  `TURNOS_HSI_LOOKBACK_DAYS`, bounded to 1–365 days.
- Imported HSI-only absences count only when no local request links the same
  HSI appointment id.

## API boundary gotcha

Postgres returns audit timestamps as `datetime`. Convert them to ISO strings in
the summary function before attaching nested history to an otherwise JSON-safe
admin row. The first browser run caught this because persistence succeeded but
response serialization closed the connection and looked like a network error.

## Verification

Use a past approved local QA appointment, mark absent through the admin UI,
then correct to present. Verify active counts `1 -> 0`, audit history `1 -> 2`,
clean browser/server logs, and delete only the isolated QA record afterward.
Unit coverage must also prove the HSI lookback, state mapping, durable intent,
inbound conflict, and HSI-only de-duplication.
