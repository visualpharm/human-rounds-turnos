#!/usr/bin/env python3
"""Crea (o pone al día) el rol de Postgres con el que corre la aplicación:
puede leer y escribir datos, NO puede cambiar el esquema (Ley 25.326 art. 9,
Res. AAIP 47/2018 — K10, 2026-10-23).

Hasta ahora la aplicación entraba a la base con un único login que además es
dueño del esquema: un fallo en el código, o alguien con la credencial de la
app, podía borrar o alterar tablas enteras y el registro de accesos. Con este
rol:

  * SELECT/INSERT/UPDATE/DELETE en las tablas, uso de secuencias y funciones;
    nada de CREATE en el esquema, ni TRUNCATE, ni ser dueño de nada.
  * En `clinical_access_log` sólo SELECT/INSERT/DELETE: el UPDATE ni siquiera
    está concedido (el trigger de sólo-agregado es la segunda barrera, y el
    DELETE sólo pasa en la purga por antigüedad).
  * Lo que el dueño cree después (revisiones Alembic nuevas) le llega solo por
    ALTER DEFAULT PRIVILEGES.

Después, la instalación arranca con dos URLs: `DATABASE_URL` con este rol y
`TURNOS_DB_MIGRATION_URL` con el dueño (lo usa sólo el arranque para llevar
el esquema a head). Paso a paso: docs/architecture/registro-de-accesos.md.

Seguro por construcción:
  * Por defecto NO escribe: imprime el SQL que correría (dry-run).
  * --apply corre todo en UNA transacción, conectado como el dueño del esquema.
  * La contraseña del rol sale de TURNOS_DB_APP_PASSWORD, nunca de la línea de
    comandos, y no se imprime; sin ella, un rol existente conserva la suya.

Uso:
    TURNOS_DB_MIGRATION_URL=postgresql://dueño@host/base \\
        python3 scripts/db_app_role.py [--role turnos_app]          # dry-run
    TURNOS_DB_MIGRATION_URL=... TURNOS_DB_APP_PASSWORD=... \\
        python3 scripts/db_app_role.py --apply
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlalchemy as sa  # noqa: E402

from app.core import sqlcore  # noqa: E402
from app.core.schema_model import pg_roles  # noqa: E402

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PASSWORD = "{password}"


def statements(role, database, existe, with_password):
    """El SQL, en orden. `role` ya validado por _ROLE_RE (se cita a mano);
    `{password}` marca dónde va la contraseña, que se arma como literal."""
    q = '"%s"' % role
    db_q = '"%s"' % database.replace('"', '""')
    attrs = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    out = []
    if not existe:
        out.append("CREATE ROLE %s LOGIN %s" % (q, attrs))
    out.append("ALTER ROLE %s LOGIN %s" % (q, attrs))
    if with_password:
        out.append("ALTER ROLE %s PASSWORD %s" % (q, _PASSWORD))
    out += [
        "GRANT CONNECT ON DATABASE %s TO %s" % (db_q, q),
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        "REVOKE CREATE ON SCHEMA public FROM %s" % q,
        "GRANT USAGE ON SCHEMA public TO %s" % q,
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %s" % q,
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %s" % q,
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %s" % q,
        "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO %s" % q,
        "REVOKE UPDATE ON clinical_access_log FROM %s" % q,
        # The trail of the data-subject requests only grows (0028): neither
        # UPDATE nor DELETE is granted; the trigger is the second barrier.
        "REVOKE UPDATE, DELETE ON data_subject_request_events FROM %s" % q,
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %s" % q,
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %s" % q,
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO %s" % q,
    ]
    return out


def _run_statement(cur, stmt):
    """One of `statements()`: utility SQL (GRANT, REVOKE, ALTER ROLE ...) that Core has no construct
    for, as `text()`. A `:` is escaped so `text()` does not read it as a bind parameter."""
    sqlcore.run(cur, sa.text(stmt.replace(":", "\\:")))


def _set_password(cur, role, password):
    """`ALTER ROLE ... PASSWORD <literal>`: the password is a literal of the statement, which cannot
    take a bind parameter, so it travels as the value of a transaction-local setting (a bound
    parameter) and `format('%L')` writes the literal inside the server. It is never part of any
    SQL text, so neither the client nor the server log can carry it."""
    sqlcore.run(cur, sa.select(
        sa.func.set_config(sa.literal("turnos.app_role", sa.Text), sa.literal(role, sa.Text), sa.true()),
        sa.func.set_config(sa.literal("turnos.app_password", sa.Text), sa.literal(password, sa.Text), sa.true())))
    sqlcore.run(cur, sa.text(
        "DO $$ BEGIN EXECUTE format('ALTER ROLE %I PASSWORD %L', "
        "current_setting('turnos.app_role'), current_setting('turnos.app_password')); END $$"))


def apply(owner_url, role, password=None, dry_run=True, out=sys.stdout):
    if not _ROLE_RE.match(role or ""):
        raise SystemExit("rol inválido: %r (minúsculas, dígitos y _)" % (role,))
    import psycopg
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        database, owner, existe = sqlcore.run(cur, sa.select(
            sa.func.current_database(), sa.func.current_user(),
            sa.exists(sa.literal_column("1")).where(pg_roles.c.rolname == role))).fetchone()
        if role == owner:
            raise SystemExit("el rol de la app no puede ser el dueño del esquema (%s)" % owner)
        if not sqlcore.run(cur, sa.select(
                sa.func.to_regclass("public.clinical_access_log").is_not(None))).fetchone()[0]:
            raise SystemExit("falta clinical_access_log: arrancá una vez la versión nueva "
                             "(o alembic upgrade head) antes de crear el rol")
        print("-- base %s, dueño %s, rol %s (%s)" % (database, owner, role,
              "ya existe" if existe else "nuevo"), file=out)
        for stmt in statements(role, database, existe, bool(password)):
            print(stmt.replace(_PASSWORD, "'***'") + ";", file=out)
            if dry_run:
                continue
            if _PASSWORD in stmt:
                _set_password(cur, role, password)
            else:
                _run_statement(cur, stmt)
        if dry_run:
            conn.rollback()
            print("-- dry-run: nada escrito. Repetí con --apply.", file=out)
        else:
            conn.commit()
            print("-- listo.", file=out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--role", default="turnos_app")
    p.add_argument("--apply", action="store_true", help="escribir (por defecto, dry-run)")
    args = p.parse_args(argv)
    owner_url = (os.environ.get("TURNOS_DB_MIGRATION_URL") or "").strip()
    if not owner_url:
        raise SystemExit("falta TURNOS_DB_MIGRATION_URL (el login del dueño del esquema)")
    apply(owner_url, args.role, os.environ.get("TURNOS_DB_APP_PASSWORD") or None,
          dry_run=not args.apply)


if __name__ == "__main__":
    main()
