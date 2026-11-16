"""ops/ — lifecycle + demo package (Tier 2 batch J).

Not a disable-able module (no module.json) — a domain package the same
footing as auth/, cuentas/, ai/, patient_web/, admin_panel/. Owns the
first-run installer wizard (setup.py), the daily demo-installation gate
and safety defaults (demo_installation.py), the demo's own day-rollover
daemon (demo_refresh.py) plus its persistence boundary (demo_refresh_store.py),
periodic DB hygiene (housekeeping.py), the public-cache and
translation-cache warmers (cache_warmup.py, translate_warmup.py), the
local-directory watcher daemon (watcher.py) and the off-box data/ backup
to Backblaze B2 (data_backups.py).
"""
