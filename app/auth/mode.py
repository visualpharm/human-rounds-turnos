"""auth_mode.py — patient sign-in mode (password vs RENAPER).

A SINGLE global setting, editable from admin (Settings), same as the visual
theme: saved in the `catalogo` table (tipo="config", id="auth_mode",
doc={"auth_mode": id}) via db_catalog.catalog_get/upsert — no new table. If the DB
is down or nothing's saved, falls back to the default (env TURNOS_AUTH_MODE,
or "password"). Never raises: DB down = default, login never breaks over this.

What actually gets SHOWN is still decided by accounts._effective_auth_mode:
with no real RENAPER provider under agreement yet, choosing "renaper" here
only changes the login where the provider can complete it (local simulator).
"""

from app.core import catalog_setting, config

MODES = {
    "password": {
        "id": "password",
        "label": "DNI y contraseña",
        "desc": "El paciente crea una cuenta con su DNI y una contraseña propia.",
    },
    "renaper": {
        "id": "renaper",
        "label": "Identidad digital (RENAPER)",
        "desc": "El paciente confirma su identidad con Mi Argentina o RENAPER; sin contraseña propia.",
    },
}
DEFAULT = "password"

def _env(key, default=""):
    """Same contract as accounts._env: os.environ first, local .env after (the
    server doesn't export the .env into os.environ)."""
    return config.get(key, default)


def _default():
    d = _env("TURNOS_AUTH_MODE", DEFAULT).strip().lower()
    return d if d in MODES else DEFAULT


_setting = catalog_setting.CatalogSetting(
    "auth_mode", "auth_mode", MODES, lambda: _default(), "modo de ingreso inválido: %r")
_cache = _setting.cache  # the {"v", "t"} dict the cached read keeps (tests seed and reset it)


def current():
    """Configured sign-in mode. The value saved in DB wins; if missing (or the
    DB is unavailable), the default. Cached ~3s."""
    return _setting.current()


def save(mode_id):
    """Persists the sign-in mode. Invalidates the local cache."""
    return _setting.save(mode_id)
