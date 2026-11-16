# modules/turnos/ — booking, queue, HSI

- Walk-in never offers appointments on any surface; the sole exception is `apertura_mismo_dia` (walk-in only, horizon capped at one day).
- How many appointments a patient may hold is a number resolved doctor → service → site (`booking_limits.py`, default 1, kinesiología 10), counting only pending plus approved-future of the same clinical DNI and service, enforced only on the patient's own POST — never the front desk's.
- The AI order reading is one pass (`app/ai/ai_referral.py`, N=1, Gemini 3 Flash via OpenRouter).
- Nothing breaks because of email or HSI: approving persists locally even when the HSI push fails (the journal and `hsi_fallback.py` retry); a missing email-provider key is a silent skip.
- HSI: `hsi_sync` (durable bidirectional journal, `hsi_journal.py`) is the only HSI job in the worker registry and self-reschedules — each tick durably defers its own next run, never cron. Every external kick goes through `hsi_journal_queue.rekick()`, which holds the Postgres session advisory lock and schedules no earlier than the last success plus the full interval. `hsi_resync` (`hsi_fallback.py`) stays thread-only in every topology, never joins the registry, reuses `hsi_sync`'s exact-item outbox replay per legacy row and owns only flags, cadence and stop-first; `TURNOS_HSI_SYNC_OFF=1` disables inbound reconciliation and `hsi_resync` never runs it.
- Identity comes from the session, never the request body — bookings, cancellations, chat tools, MCP. `db_booking.create_booking_request` serializes through a transaction-scoped advisory lock; a plan that touches concurrent bookings proves itself in `tests/test_integrity_races.py`.
- Datetimes convert to the installation timezone (`app/core/tz.py`, `America/Argentina/Buenos_Aires`) before use; never trust the offset Postgres returns.
- Deshabilitar, never eliminar, an entity with appointments (`db_slots.count_appointments_of`).
