"""connectors/hsi — the emr_sync connector (Argentina: HSI, Historia de
Salud Integrada — historiaclinica/hsi).

Four files, moved verbatim from the repo root (Phase 6b, 2026-07-30):

    hsi_client.py    stdlib HTTP client — login, ensure_patient, create
                      appointment, push_turno() (the historical single-shot
                      entry point solicitudes/booking_requests still call
                      on approval).
    hsi_sync.py       the durable outbox/inbox journal — the live sync path.
    hsi_resolver.py   automatic conflict resolution over that journal; only
                      what genuinely needs a human stays open.
    hsi_resync.py     background daemon that retries approved requests stuck
                      in hsi_estado 'error'/'pendiente'.

See connector.json (registry manifest) and adapter.py (the ABC-conforming
wrapper around push_turno). The repo-root compat shims over these files were
retired in Tier 2 batch L (2026-08-07): the bare top-level names no longer
resolve — import `connectors.hsi.<mod>` directly.
"""
