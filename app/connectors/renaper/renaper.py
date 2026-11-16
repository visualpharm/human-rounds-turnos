"""Identity-provider seam and local test-identity registry.

Production provider integrations deliberately fail closed until the
municipality supplies an approved contract.  The local ``stub`` provider is a
small identity-provider simulator: it starts with three visibly synthetic
people, persists created/edited identities, and never queries a real registry.

Moved here from repo-root renaper.py in the Phase 6b connector extraction
(2026-07-30) — this is now the `identity_verification` capability's
implementation (see connectors/renaper/connector.json, connectors/renaper/
adapter.py, and connectors/__init__.py for the framework it plugs into).
The repo-root `renaper.py` compat shim was retired in Tier 2 batch L
(2026-08-07): every consumer now imports `connectors.renaper.renaper`
directly, and the bare `import renaper` no longer resolves
(test_connector_shims.py asserts exactly that).

ProviderUnavailable/IdentityNotFound/InvalidIdentity used to be defined HERE;
they moved to connectors/__init__.py (the framework's single owner for the
generic "connector can't fulfill this" contract) in the slice that added the
framework — imported back below so this module's public surface (including
`renaper.ProviderUnavailable` through the shim) is unchanged.
"""

from __future__ import annotations

import calendar
import json
import os
import secrets
import threading

from app.core import format
from app.core import paths
from app import connectors as _connectors_pkg
from app.connectors import ProviderUnavailable, InvalidIdentity
# Deliberate re-export of this module's historical public surface (see the
# module docstring): renaper.IdentityNotFound keeps resolving.
from app.connectors import IdentityNotFound  # noqa: F401

# Repo root, NOT this file's own directory — this module now lives two
# levels deep (connectors/renaper/renaper.py) but its data file is a
# repo-root-relative runtime path, same as every other module's `data/...`.
ROOT = paths.PROJECT_ROOT
DEFAULT_STORE_PATH = os.path.join(ROOT, "data", "test-identities.json")
_LOCK = threading.RLock()

_NOMBRES_F = ("Agustina", "Camila", "Elena", "Julieta", "Lucía", "Mara", "Nora", "Sofía")
_NOMBRES_M = ("Agustín", "Bruno", "Damián", "Emilio", "Julián", "Leandro", "Mateo", "Tomás")
_NOMBRES_X = ("Alex", "Ariel", "Dani", "Noa", "Sasha")
_APELLIDOS = ("Acosta", "Benítez", "Cabrera", "Domínguez", "Ferrer", "Gómez", "Herrera", "Ibarra")
_CALLES = ("De las Artes", "Del Bosque", "Del Mar", "Júpiter", "Shaw", "Valparaíso")
_LOCALIDADES = ("Pinamar", "Ostende", "Valeria del Mar", "Cariló")

# The 99M range makes the bundled identities unmistakably synthetic in local
# HSI and screenshots.  Manually created identities may use any 7-9 digit DNI.
_SEED_IDENTITIES = (
    {
        "dni": "99000001", "nombre": "Sofía", "apellido": "Herrera",
        "nacimiento": "1953-08-11", "sexo": "F", "contacto": "2254990001",
        "email": "sofia.99000001@example.invalid",
        "domicilio": "De las Artes 101", "localidad": "Pinamar",
        "origen": "predefinida",
    },
    {
        "dni": "99000002", "nombre": "Marcos", "apellido": "Ibarra",
        "nacimiento": "1986-02-24", "sexo": "M", "contacto": "2254990002",
        "email": "marcos.99000002@example.invalid",
        "domicilio": "Del Bosque 202", "localidad": "Ostende",
        "origen": "predefinida",
    },
    {
        "dni": "99000003", "nombre": "Noa", "apellido": "Benítez",
        "nacimiento": "2014-11-03", "sexo": "X", "contacto": "2254990003",
        "email": "noa.99000003@example.invalid",
        "domicilio": "Júpiter 303", "localidad": "Valeria del Mar",
        "origen": "predefinida",
    },
)


def _store_path():
    return os.environ.get("TURNOS_TEST_IDENTITIES_PATH") or DEFAULT_STORE_PATH


def _clean(value, limit):
    return str(value or "").strip()[:limit]


def _valid_date(value):
    text = _clean(value, 10)
    try:
        year, month, day = (int(part) for part in text.split("-"))
        if year < 1900 or year > 2026:
            raise ValueError
        if not 1 <= day <= calendar.monthrange(year, month)[1]:
            raise ValueError
    except (TypeError, ValueError):
        raise InvalidIdentity("fecha_de_nacimiento_invalida") from None
    return text


def normalize_identity(value, *, origin=None):
    """Return the canonical, public shape for one synthetic identity."""
    value = value if isinstance(value, dict) else {}
    dni = "".join(ch for ch in str(value.get("dni") or "") if ch.isdigit())
    if not format.dni_valid_length(dni):
        raise InvalidIdentity("dni_invalido")
    nombre = _clean(value.get("nombre"), 80)
    apellido = _clean(value.get("apellido"), 80)
    if not nombre or not apellido:
        raise InvalidIdentity("nombre_y_apellido_requeridos")
    sexo = _clean(value.get("sexo"), 1).upper()
    if sexo not in ("F", "M", "X"):
        raise InvalidIdentity("sexo_invalido")
    email = _clean(value.get("email"), 160)
    if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
        raise InvalidIdentity("email_invalido")
    contacto = "".join(ch for ch in str(value.get("contacto") or "") if ch.isdigit() or ch == "+")[:24]
    return {
        "dni": dni,
        "nombre": nombre,
        "apellido": apellido,
        "nacimiento": _valid_date(value.get("nacimiento")),
        "sexo": sexo,
        "contacto": contacto,
        "email": email,
        "domicilio": _clean(value.get("domicilio"), 160),
        "localidad": _clean(value.get("localidad"), 80),
        "origen": _clean(origin or value.get("origen") or "manual", 24),
    }


def _use_db():
    """DB-backed storage is the default from 2026-08-03 on: no persistent
    volume is mounted at /app/data on the prod deployment (confirmed against
    Coolify's own API — `persistent_storages: []` — after the product owner's registered
    identity vanished the morning after a redeploy), so the plain JSON file
    below is wiped on every deploy. Postgres is the one thing this app
    already trusts to survive a redeploy — every other bit of durable admin
    state (`ai_prompts`, the `catalog` overlay table) lives there too.
    `TURNOS_TEST_IDENTITIES_PATH` stays as an explicit escape hatch for a
    fully file-isolated run (the existing unit tests use it on purpose, to
    stay DB-free)."""
    return not os.environ.get("TURNOS_TEST_IDENTITIES_PATH")


_CATALOG_TYPE = "renaper_test_identity"

# Origin of the copies of a real account the simulator made until 2026-10-23
# (see `authenticate`). Such a row is no test person: it never logs in to the
# account it copied, never shows in the simulator's list, and is never edited.
ACCOUNT_COPY_ORIGIN = "cuenta_existente"


def is_account_copy(item):
    return bool(item) and item.get("origen") == ACCOUNT_COPY_ORIGIN


def _read_file():
    path = _store_path()
    if not os.path.exists(path):
        return [normalize_identity(item) for item in _SEED_IDENTITIES]
    try:
        with open(path, encoding="utf-8") as source:
            raw = json.load(source)
    except (OSError, json.JSONDecodeError):
        return [normalize_identity(item) for item in _SEED_IDENTITIES]
    items = []
    seen = set()
    for value in raw if isinstance(raw, list) else []:
        try:
            item = normalize_identity(value)
        except InvalidIdentity:
            continue
        if item["dni"] not in seen:
            seen.add(item["dni"])
            items.append(item)
    return items or [normalize_identity(item) for item in _SEED_IDENTITIES]


def _write_file(items):
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.tmp-%s" % (path, secrets.token_hex(5))
    try:
        with open(temporary, "w", encoding="utf-8") as target:
            json.dump(items, target, ensure_ascii=False, indent=2)
            target.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def _migrate_from_legacy_file():
    """One-time import of a pre-2026-08-03 install's file registry into the
    DB, run only when the DB has nothing yet. Never deletes the old file —
    harmless to leave behind, and the next redeploy erases it anyway."""
    path = DEFAULT_STORE_PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as source:
            raw = json.load(source)
    except (OSError, json.JSONDecodeError):
        return []
    from app.core import db_catalog
    items = []
    seen = set()
    for value in raw if isinstance(raw, list) else []:
        try:
            item = normalize_identity(value)
        except InvalidIdentity:
            continue
        if item["dni"] in seen:
            continue
        seen.add(item["dni"])
        db_catalog.catalog_upsert(_CATALOG_TYPE, item["dni"], item)
        items.append(item)
    return items


def _read_db():
    from app.core import db_catalog
    items = []
    for row in db_catalog.catalog_list(_CATALOG_TYPE):
        if row.get("borrado"):
            continue
        try:
            items.append(normalize_identity(row["doc"]))
        except InvalidIdentity:
            continue
    if items:
        return items
    migrated = _migrate_from_legacy_file()
    return migrated or [normalize_identity(item) for item in _SEED_IDENTITIES]


def _write_db(items):
    from app.core import db_catalog
    existing_dnis = {row["id"] for row in db_catalog.catalog_list(_CATALOG_TYPE)}
    kept = set()
    for item in items:
        db_catalog.catalog_upsert(_CATALOG_TYPE, item["dni"], item)
        kept.add(item["dni"])
    for stale_dni in existing_dnis - kept:
        db_catalog.catalog_delete_row(_CATALOG_TYPE, stale_dni)


def _read_unlocked():
    return _read_db() if _use_db() else _read_file()


def _write_unlocked(items):
    _write_db(items) if _use_db() else _write_file(items)


def list_test_identities():
    with _LOCK:
        return [dict(item) for item in _read_unlocked()]


def get_test_identity(dni):
    needle = "".join(ch for ch in str(dni or "") if ch.isdigit())
    with _LOCK:
        for item in _read_unlocked():
            if item["dni"] == needle:
                return dict(item)
    return None


def save_test_identity(value, *, original_dni=None):
    """Create or edit one identity and return the persisted row."""
    item = normalize_identity(value, origin=value.get("origen") if isinstance(value, dict) else None)
    original = "".join(ch for ch in str(original_dni or "") if ch.isdigit())
    with _LOCK:
        items = _read_unlocked()
        for existing in items:
            if existing["dni"] == item["dni"] and existing["dni"] != original:
                raise InvalidIdentity("dni_ya_existe")
        replaced = False
        for index, existing in enumerate(items):
            if existing["dni"] == (original or item["dni"]):
                items[index] = item
                replaced = True
                break
        if not replaced:
            items.append(item)
        _write_unlocked(items)
    return dict(item)


def create_random_identity(*, dni=None):
    """Generate, persist, and return one visibly synthetic random identity.

    `dni` pins the generated identity to a specific document number instead
    of picking a free 99M-range one — used by `authenticate()` below to
    synthesize a stable persona the first time a never-seen DNI logs in
    through the stub (the product owner, 2026-08-03: any DNI has to work)."""
    with _LOCK:
        items = _read_unlocked()
        used = {item["dni"] for item in items}
        if dni is not None:
            pinned = "".join(ch for ch in str(dni) if ch.isdigit())
            if not format.dni_valid_length(pinned):
                raise InvalidIdentity("dni_invalido")
            dni = pinned
        else:
            for _ in range(100):
                dni = str(99010000 + secrets.randbelow(990000))
                if dni not in used:
                    break
            else:
                raise InvalidIdentity("sin_dni_disponible")
        sexo = secrets.choice(("F", "M", "X"))
        nombres = _NOMBRES_F if sexo == "F" else _NOMBRES_M if sexo == "M" else _NOMBRES_X
        year = 1940 + secrets.randbelow(80)
        month = 1 + secrets.randbelow(12)
        day = 1 + secrets.randbelow(calendar.monthrange(year, month)[1])
        street = secrets.choice(_CALLES)
        locality = secrets.choice(_LOCALIDADES)
        item = normalize_identity({
            "dni": dni,
            "nombre": secrets.choice(nombres),
            "apellido": secrets.choice(_APELLIDOS),
            "nacimiento": "%04d-%02d-%02d" % (year, month, day),
            "sexo": sexo,
            "contacto": "22549%s" % dni[-5:],
            "email": "qa+%s@example.invalid" % dni,
            "domicilio": "%s %d" % (street, 100 + secrets.randbelow(8900)),
            "localidad": locality,
            "origen": "aleatoria",
        })
        if item["dni"] in used:
            raise InvalidIdentity("dni_ya_existe")
        items.append(item)
        _write_unlocked(items)
    return dict(item)


def authenticate(dni, provider, *, existing_user=None):
    """Authenticate an identity through the configured provider seam.

    The stub accepts ANY plausible DNI (the product owner, 2026-08-03) — nobody testing
    the simulator should get stuck behind "that identity doesn't exist".
    Resolution order:
      1. a HAND-REGISTERED test identity for this DNI wins as-is (Sistema's
         synthetic-identities admin);
      2. no registered identity, but this DNI already has a real account
         (`existing_user`, passed in by the caller since this module has no
         DB access) — REFUSED with `IdentityNotFound` (2026-10-23). Until
         then the persona was built from that account's own civil data and
         persisted here as origen 'cuenta_existente': the simulator opened a
         real patient's account to anyone who typed their DNI and copied
         their phone, email and address into this registry, which
         `GET /api/account/test-identities` lists. The simulator only ever
         enters with test people; nothing of a real account is copied;
      3. neither exists — synthesize a plausible new identity pinned to this
         DNI and persist it, so a second login with the same number is
         stable instead of re-rolling a new person every time.
    """
    if provider == "stub":
        needle = "".join(ch for ch in str(dni or "") if ch.isdigit())
        if not format.dni_valid_length(needle):
            raise InvalidIdentity("dni_invalido")
        person = get_test_identity(needle)
        if person and not (existing_user and is_account_copy(person)):
            return person
        # A pre-2026-10-23 copy of a real account (kept by the purge script
        # when that DNI has imported history) is no test person: refused too.
        if existing_user:
            raise IdentityNotFound("cuenta_real_no_simulable")
        return create_random_identity(dni=needle)
    if provider == "real":
        raise ProviderUnavailable("La integración real no está configurada")
    raise ProviderUnavailable("Proveedor de identidad no reconocido")


# Backward-compatible name for callers outside this module.
autenticar = authenticate
