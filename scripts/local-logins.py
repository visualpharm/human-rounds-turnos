#!/usr/bin/env python3
"""local-logins.py — alta/refresh IDEMPOTENTE de las dos cuentas de login
local de desarrollo: un admin de plataforma (con 2FA/TOTP, entra a /admin)
y una cuenta de paciente (entra a /account). Imprime por stdout UN único objeto JSON con usuario/contraseña/TOTP
secret/path de login de cada una; TODO otro mensaje va a stderr.

    python3 scripts/local-logins.py           # crea o verifica, no rota nada
    python3 scripts/local-logins.py --rotate  # regenera ambas contraseñas y TOTP secrets

Reglas:
  * Sólo bases LOCALES: si el host de DATABASE_URL (env, o el .env del repo —
    la MISMA resolución que hace core.db) no es localhost/127.0.0.1/::1 o un
    socket unix, el script se niega y no toca nada. Nunca apuntar a prod.
  * Contraseñas: dos palabras legibles + 2 dígitos (p.ej. "roble-marea42").
    Se generan una vez y quedan anotadas en .local-logins.json (gitignored,
    permisos 600) junto a las credenciales derivadas — re-correr el script
    imprime SIEMPRE las mismas credenciales y no cambia nada en la base,
    salvo que se pase --rotate o que se defina LOCAL_ADMIN_PASSWORD /
    LOCAL_USER_PASSWORD (que ganan sobre todo lo demás). Borrar el archivo
    de estado hace que la próxima corrida genere contraseñas nuevas y las
    sincronice en la base.
  * TOTP secrets: base32 aleatorio de 16+ chars. Se generan una vez y quedan
    en .local-logins.json con clave admin_totp_secret. Re-correr imprime el
    mismo secret, salvo que se pase --rotate o LOCAL_ADMIN_TOTP_SECRET por env.
  * La cuenta queda SIEMPRE con la contraseña impresa en el JSON: si alguien
    la cambió a mano por la UI, la próxima corrida la vuelve a dejar como
    dice el JSON (antes de escribir, verifica con las funciones de login del
    propio proyecto — no re-hashes si no hace falta). El admin se reafirma
    con rol 'admin', activa y con su TOTP secret.
  * Reusa las funciones de alta y el hashing del proyecto (core.db →
    auth.password_hashing, Argon2id). Nada de un segundo esquema de hashes.

Las cuentas son de conveniencia para desarrollo; el admin real (el dueño)
sigue viniendo de TURNOS_ADMIN_USER/TURNOS_ADMIN_PASS y no vive en la tabla
operators — este script no lo toca.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from urllib.parse import urlsplit

import pyotp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402

# Usernames fijos (español, como el producto): la idempotencia es "mismo
# usuario, misma contraseña en cada corrida".
ADMIN_USER = "admin-local"
USER_DNI = "36555444"
USER_EMAIL = "demo.local@ejemplo.test"

_HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1", ""}  # "" = socket unix

# Sustantivos castizos, ASCII (sin tildes ni ñ) para que la contraseña se
# pueda tipear en cualquier teclado.
_PALABRAS = [
    "roble", "marea", "sierra", "laguna", "cumbre", "arroyo", "pampa",
    "ceibo", "bruma", "salina", "duna", "junco", "loma", "monte", "noria",
    "talas",
]


def _estado_path():
    """Dónde vive el registro de contraseñas generadas. El override por env
    existe para que el test corra hermético (no toque el archivo real)."""
    return (os.environ.get("TURNOS_LOCAL_LOGINS_STATE")
            or os.path.join(ROOT, ".local-logins.json"))


def _exigir_base_local(url):
    """Se niega a correr contra cualquier base que no sea de esta máquina."""
    p = urlsplit(url or "")
    scheme = (p.scheme or "").split("+")[0].lower()  # postgresql+psycopg → postgresql
    host = (p.hostname or "").lower()
    if scheme == "sqlite":
        return  # archivo local
    if scheme in ("postgres", "postgresql") and host in _HOSTS_LOCALES:
        return
    raise SystemExit(
        f"local-logins: la base no es LOCAL (host={host or 'sin host'}, "
        f"esquema={scheme or 'sin esquema'}); me niego a crear cuentas ahí.")


def _generar_password():
    rnd = secrets.SystemRandom()
    a, b = rnd.sample(_PALABRAS, 2)  # dos palabras DISTINTAS
    return "%s-%s%02d" % (a, b, rnd.randrange(10, 100))


def _generar_totp_secret():
    """Genera un base32 TOTP secret aleatorio."""
    return pyotp.random_base32()


def _password_efectiva(slot, guardada):
    """Env > estado guardado > nueva. `slot` es 'ADMIN' o 'USER'."""
    por_env = (os.environ.get(f"LOCAL_{slot}_PASSWORD") or "").strip()
    if por_env:
        return por_env
    return guardada or _generar_password()


def _totp_secret_efectivo(guardado):
    """Env > estado guardado > nuevo."""
    por_env = (os.environ.get("LOCAL_ADMIN_TOTP_SECRET") or "").strip()
    if por_env:
        return por_env
    return guardado or _generar_totp_secret()


def _leer_estado():
    try:
        with open(_estado_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _guardar_estado(estado):
    path = _estado_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.chmod(path, 0o600)  # por si el archivo ya existía con permisos laxos


def _preparar_admin(password, totp_secret, log):
    fila = db_operators.operator_verify_password(ADMIN_USER, password)
    if fila is not None:
        db_operators.operator_update(ADMIN_USER, rol="admin", activo=True, totp_secret=totp_secret)
        log.append(f"admin: {ADMIN_USER} ya entra con esa contraseña — no se rotó nada")
        return
    if db_operators.operator_get(ADMIN_USER) is None:
        db_operators.operator_create(ADMIN_USER, password, rol="admin", totp_secret=totp_secret)
        log.append(f"admin: cuenta {ADMIN_USER} creada (rol admin, con TOTP)")
    else:
        db_operators.operator_update(ADMIN_USER, rol="admin", activo=True, totp_secret=totp_secret,
                           password=password)
        log.append(f"admin: contraseña de {ADMIN_USER} sincronizada (rol admin, con TOTP)")


def _preparar_paciente(password, log):
    if db_accounts.verify_password(USER_DNI, password) is not None:
        log.append(f"paciente: {USER_DNI} ya entra con esa contraseña — no se rotó nada")
        return
    usuario = db_accounts.get_user(USER_DNI)
    if usuario is None:
        db_accounts.create_user(USER_DNI, "Demo", "Local", "1990-06-15", "F",
                       "+5492254000000", password, email=USER_EMAIL)
        log.append(f"paciente: cuenta {USER_DNI} creada")
        return
    db_accounts.set_password(USER_DNI, password)
    if usuario.get("activo") is False:
        db_accounts.user_admin_update(USER_DNI, {"activo": True})
    log.append(f"paciente: contraseña de {USER_DNI} sincronizada")


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Alta idempotente de las dos cuentas de login local (admin + paciente).")
    p.add_argument("--rotate", action="store_true",
                   help="regenerar ambas contraseñas y TOTP secret (las env, si hay, siguen ganando)")
    args = p.parse_args(argv)

    # Misma resolución que la app: env DATABASE_URL, si no el .env del repo.
    url = db._db_url()
    if not url:
        raise SystemExit("local-logins: DATABASE_URL no configurada (env o .env).")
    _exigir_base_local(url)

    estado = _leer_estado()
    pw_admin = _password_efectiva("ADMIN", estado.get("admin_password") if not args.rotate else None)
    pw_user = _password_efectiva("USER", estado.get("user_password") if not args.rotate else None)
    totp_secret = _totp_secret_efectivo(estado.get("admin_totp_secret") if not args.rotate else None)

    log = []
    try:
        _preparar_admin(pw_admin, totp_secret, log)
        _preparar_paciente(pw_user, log)
    except db.DbUnavailable as exc:
        raise SystemExit(f"local-logins: base no disponible: {exc}")
    finally:
        db.close_pools()

    estado["admin_password"] = pw_admin
    estado["admin_totp_secret"] = totp_secret
    estado["user_password"] = pw_user
    _guardar_estado(estado)

    for linea in log:
        print(f"local-logins: {linea}", file=sys.stderr)

    print(json.dumps({
        "project": "turnos",
        "admin": {"username": ADMIN_USER, "password": pw_admin, "totp_secret": totp_secret,
                  "login_path": "/admin", "role": "admin"},
        "user": {"username": USER_DNI, "password": pw_user,
                 "login_path": "/account", "role": "regular"},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
