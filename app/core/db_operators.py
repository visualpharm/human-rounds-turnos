"""core/db_operators.py — the panel operators of the persistence layer: accounts of the staff, their access overlay (`operator_access`), doctor links, portal binding, rename and deactivation.

Split out of `core/db.py` in stage 5 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md):
the statements are Core, built from `core/schema_model.py`.

Callers write `db_operators.<name>(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below as
`db_wire._wire` / `db_values.<name>`, a sibling as `db_accounts._user_public` and their own names bare, all at CALL time, so
a test that patches the owner patches what every caller uses. `seed_builtin_roles` came over from `core/db.py` with this stage.
"""

import contextlib
import re
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.auth import password_hashing
from app.core import db
from app.core import db_values
from app.core import db_wire
from app.core import db_locks
from app.core import db_schema
from app.core import feed_tokens
from app.core import schema_model as sm
from app.core import sqlcore


# ---- operadores del panel admin (admin_users.py) -------------------------
#
# Same password library and transparent legacy upgrade as patient accounts
# de más arriba). El dueño (login por env, ver admin_auth.py) NO vive acá.

NO_CHANGE = object()  # sentinel: "no tocar este campo" (distinto de None="vaciarlo")


class OperatorConflict(ValueError):
    """Safe conflict keys for the account domain to translate."""


# Los cuatro valores que admite el CHECK congelado de operators.role. Un rol
# con otro slug (gestor_agendas, los roles propios) se guarda en
# operator_access.role_slug sobre esta base (core/permissions.py).
_OPERATOR_TABLE_ROLES = ("admin", "medico", "admisionista", "reportes")
_OPERATOR_BASE_ROLE = "admisionista"


def _operator_public(row):
    """Copia del registro de operador SIN pass_hash ni totp_secret crudo —
    sólo `tiene_2fa` (bool). El secreto en texto plano SÓLO se devuelve una
    vez, en el momento de generarlo (admin_users.py), nunca en una lectura."""
    if row is None:
        return None
    d = dict(row)
    d.pop("pass_hash", None)
    # The portal account binding is the login's business, not the panel's.
    d.pop("portal_user_id", None)
    d.pop("cuit", None)
    # The calendar address is a credential: only the doctor's own «Mis turnos»
    # endpoint speaks about it, never a panel row (2026-11-06, alembic 0040).
    d.pop("feed_token", None)
    d.pop("feed_token_hint", None)
    d["tiene_2fa"] = bool(d.pop("totp_secret", None))
    creado = d.get("creado")
    if isinstance(creado, datetime):
        d["creado"] = creado.isoformat()
    ultimo_acceso = d.get("ultimo_acceso")
    if isinstance(ultimo_acceso, datetime):
        d["ultimo_acceso"] = ultimo_acceso.isoformat()
    completed = d.get('onboarding_completed_at')
    if isinstance(completed, datetime):
        d['onboarding_completed_at'] = completed.isoformat()
    invited = d.get("invitado_en")
    if isinstance(invited, datetime):
        d["invitado_en"] = invited.isoformat()
    # `centros` NULL y `centros` vacío significan lo mismo — todos — y el
    # cliente no tiene por qué distinguirlos: siempre viaja como lista.
    d["centros"] = list(d.get("centros") or [])
    d["cuenta_estado"] = operator_account_state(d)
    return d


def operator_account_state(d):
    """Where an account stands, one word per step (the product owner, 2026-10-19):
    sin_invitar → invitado (an email or a copied link, or an active account
    whose credentials were handed over) → ingreso (logged in at least once)
    → perfil_completo (finished the doctor onboarding). `activo` stays a
    separate yes/no: a backfilled account is «sin invitar» AND inactive."""
    if d.get("onboarding_completed_at"):
        return "perfil_completo"
    if d.get("ultimo_acceso"):
        return "ingreso"
    if d.get("invitado_en") or d.get("activo"):
        return "invitado"
    return "sin_invitar"


def _operator_base_role(rol):
    return rol if rol in _OPERATOR_TABLE_ROLES else _OPERATOR_BASE_ROLE


# Built once: every panel request reads its operator (see `sqlcore.Prepared`).
_ACCESS_OF = sqlcore.prepare(sa.select(
    sm.operator_access.c.username, sm.operator_access.c.role_slug, sm.operator_access.c.granted,
    sm.operator_access.c.denied,
).where(sm.operator_access.c.username.in_(sa.bindparam("names", expanding=True, type_=sa.Text))))
_OPERATOR_BY_USERNAME = sqlcore.prepare(sa.select(sm.operators).where(
    sm.operators.c.username == sa.bindparam("usuario", type_=sa.Text)))


def _with_access(cur, rows):
    """Superpone operator_access a filas ya `_wire`adas: `rol` pasa a ser el
    rol efectivo de la cuenta y se agregan sus permisos agregados/quitados."""
    rows = [r for r in rows if r]
    if not rows:
        return
    extra = {r["username"]: r for r in _ACCESS_OF.run(cur, names=[r["usuario"] for r in rows]).fetchall()}
    for r in rows:
        a = extra.get(r["usuario"]) or {}
        if a.get("role_slug"):
            r["rol"] = a["role_slug"]
        r["permisos_agregados"] = list(a.get("granted") or [])
        r["permisos_quitados"] = list(a.get("denied") or [])


def _set_access_role(cur, usuario, rol):
    """Guarda el slug cuando no entra en operators.role; lo limpia si entra."""
    slug = None if rol in _OPERATOR_TABLE_ROLES else rol
    a = sm.operator_access
    ins = _pg.insert(a).values(username=usuario, role_slug=slug)
    sqlcore.run(cur, ins.on_conflict_do_update(
        index_elements=[a.c.username],
        set_={"role_slug": ins.excluded.role_slug, "updated": sa.func.now()}))


def operators_list():
    """Todos los operadores (activos e inactivos), orden de alta. No incluye
    al dueño (ese vive en el env, no en esta tabla)."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        rows = [db_wire._wire(r, "operators") for r in sqlcore.run(
            cur, sa.select(t).order_by(t.c.created)).fetchall()]
        _with_access(cur, rows)
        return [_operator_public(r) for r in rows]


def operator_get(usuario):
    """Registro COMPLETO (incluye pass_hash y totp_secret) — sólo para uso
    interno de admin_auth.login()/_authenticate. Nunca exponer tal cual a un
    endpoint; usar operator_get_public para eso."""
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(_OPERATOR_BY_USERNAME.run(cur, usuario=usuario).fetchone(), "operators")
        _with_access(cur, [row])
        return row


def operator_pass_hash(usuario):
    """The stored password hash of one operator (None when there is no such row): the
    session cookie is bound to it, so a new password closes the sessions opened with the old one.
    Internal to `admin_auth`; never exposed."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.pass_hash).where(t.c.username == usuario)).fetchone()
        return (row or {}).get("pass_hash")


def operator_get_public(usuario):
    return _operator_public(operator_get(usuario))


def _eq(column, value):
    """`column = <value>` that never turns into `IS NULL`: a Python `None` compared with `==` would
    match the rows whose column IS NULL, the hand-written `column=%s` with a NULL matched nothing."""
    return column == sa.literal(value, column.type)


def _email_matches(email):
    """`lower(trim(email)) = lower(<email>)` over the operators table."""
    t = sm.operators
    return sa.func.lower(sa.func.trim(t.c.email)) == sa.func.lower(email)


def operator_by_email(email):
    """Return a public account only for an unambiguous, case-insensitive email.

    Inactive duplicates count too: a password must never select the account.
    """
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        rows = [db_wire._wire(r, "operators") for r in sqlcore.run(
            cur, sa.select(t).where(_email_matches(str(email or "").strip())).limit(2)).fetchall()]
        if len(rows) != 1:
            return None
        _with_access(cur, rows)
        return _operator_public(rows[0])


def operator_active_email_taken(email, except_usuario=None):
    """Whether an ACTIVE account other than `except_usuario` has this email
    (any case) — the portal login's first match needs exactly one."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(sa.literal_column("1")).where(
            t.c.active, _email_matches(str(email or "").strip()),
            t.c.username.is_distinct_from(except_usuario),
        ).limit(1)).fetchone() is not None


def operator_verify_password(usuario, password):
    """Login de un operador: devuelve el registro COMPLETO (incl. totp_secret)
    si el usuario existe, está ACTIVO y la contraseña matchea; si no, None.
    admin_auth.login() decide después si además exige el código TOTP."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(sqlcore.run(cur, sa.select(t).where(
            t.c.username == usuario).with_for_update()).fetchone(), "operators")
        if not row or not row.get("activo"):
            password_hashing.dummy_verify(password)
            return None
        _with_access(cur, [row])
        valid, replacement = password_hashing.verify_and_update(
            row.get("pass_hash"), password
        )
        if not valid:
            return None
        if replacement:
            sqlcore.run(cur, sa.update(t).values(pass_hash=replacement).where(
                t.c.username == usuario, t.c.pass_hash == row.get("pass_hash")))
            row["pass_hash"] = replacement
        return row


def operator_mark_access(usuario):
    """Registra una visita completada (contraseña y, si corresponde, TOTP
    válidos). El dueño vive en variables de entorno y no tiene fila acá."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.update(t).values(last_access=sa.func.now()).where(t.c.username == usuario))


def operator_by_portal_id(portal_user_id):
    """The COMPLETE record bound to this portal account id (`X-User-Id`), or
    None. Active or not — the caller decides; the unique index allows one."""
    portal_user_id = str(portal_user_id or "")
    if not portal_user_id:
        return None
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(sqlcore.run(cur, sa.select(t).where(
            t.c.portal_user_id == portal_user_id)).fetchone(), "operators")
        _with_access(cur, [row])
        return row


def operator_claim_portal(email, portal_user_id, cuit):
    """First portal login: bind the portal account id and its validated CUIT
    to the ONE active operator with this email (any case) that is not bound to
    a portal account yet, and return its complete record.

    None — and nothing written — when no active operator or more than one has
    that email, when the only one is already bound to another portal account,
    or when the account id or the CUIT is already someone else's (the unique
    indexes decide a race)."""
    email = str(email or "").strip()
    portal_user_id = str(portal_user_id or "")
    cuit = str(cuit or "")
    if not email or not portal_user_id or not re.fullmatch(r"[0-9]{11}", cuit):
        return None
    from psycopg.errors import UniqueViolation
    t = sm.operators
    try:
        with db._connect() as conn, conn.cursor() as cur:
            rows = [db_wire._wire(r, "operators") for r in sqlcore.run(cur, sa.select(t).where(
                t.c.active, _email_matches(email)).limit(2).with_for_update()).fetchall()]
            if len(rows) != 1 or rows[0].get("portal_user_id"):
                return None
            row = rows[0]
            if sqlcore.run(cur, sa.update(t).values(portal_user_id=portal_user_id, cuit=cuit).where(
                    t.c.id == row["id"], t.c.portal_user_id.is_(None))).rowcount != 1:
                return None
            row.update(portal_user_id=portal_user_id, cuit=cuit)
            _with_access(cur, [row])
            return row
    except UniqueViolation:
        return None


def operator_unbind_portal(usuario):
    """Forget an account's portal account id and CUIT, so the next portal
    login with its email binds afresh — the emergency script's lever when a
    person's portal account changed."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            portal_user_id=sa.null(), cuit=sa.null()).where(t.c.username == usuario)).rowcount == 1


def _guard_operator_doctor(cur, usuario, rol, medico_id):
    # A doctor promoted to administrator keeps the link, so it counts too.
    if rol not in ("medico", "admin") or not medico_id:
        return
    # Serialize all account writers for this doctor across workers, including
    # Users edits, invitation, linking and direct creation. No legacy data rewrite.
    t = sm.operators
    db_locks._advisory_xact_lock(cur, "turnos:operator-doctor:", db_values._text_value(str(medico_id)))
    if sqlcore.run(cur, sa.select(t.c.username).where(
            _eq(t.c.doctor_id, medico_id), t.c.username != usuario).limit(1)).fetchone():
        raise OperatorConflict("medico_con_usuario")


def _doctor_link_violation(exc):
    """True when a UniqueViolation is the one-account-per-doctor index."""
    diag = getattr(exc, "diag", None)
    return getattr(diag, "constraint_name", None) == "operators_doctor_id_uniq"


def operator_create(usuario, password=None, rol="medico", totp_secret=None, medico_id=None,
                   email=None, centros=None, pass_hash=None, activo=True, connection=None):
    """Alta de un operador nuevo. Levanta si el usuario ya existe (constraint
    UNIQUE) — admin_users.py chequea antes para responder 409 con un
    mensaje claro. `medico_id` sólo tiene sentido para rol='medico'
    (admin_users.py valida contra el catálogo antes de llamar acá).

    `pass_hash`: reuse an already calculated current Argon2id hash instead of hashing
    `password` — setup.py (el wizard de instalación) hashea la contraseña
    apenas la recibe y sólo guarda el hash en la cookie firmada del wizard,
    nunca la contraseña en texto plano; el alta final llama acá con
    `pass_hash=` en vez de `password=`."""
    if pass_hash is not None:
        if not password_hashing.is_current_hash(pass_hash):
            raise ValueError("pass_hash must be a current Argon2id hash")
        ph = pass_hash
    else:
        ph = password_hashing.hash_password(password)
    t, a = sm.operators, sm.operator_access
    # `connection`: the doctor create writes the catalog row and this account
    # in ONE transaction (admin_content.create_item) — a failure here rolls
    # the doctor back too, so neither is left without the other.
    with (contextlib.nullcontext(connection) if connection is not None else db._connect()) as conn, \
            conn.cursor() as cur:
        from psycopg.errors import UniqueViolation
        _guard_operator_username(cur, usuario)
        _guard_operator_doctor(cur, usuario, rol, medico_id)
        try:
            sqlcore.run(cur, sa.insert(t).values(
                username=usuario, pass_hash=ph, role=_operator_base_role(rol),
                totp_secret=totp_secret, doctor_id=medico_id, email=email,
                centers=list(centros) if centros else None, active=bool(activo),
            ).returning(*t.c))
        except UniqueViolation as exc:
            raise OperatorConflict("medico_con_usuario" if _doctor_link_violation(exc)
                                   else "nombre_ya_existe") from exc
        row = db_wire._wire(cur.fetchone(), "operators")
        # A brand-new account never inherits a leftover access row.
        sqlcore.run(cur, sa.delete(a).where(a.c.username == usuario))
        if rol not in _OPERATOR_TABLE_ROLES:
            _set_access_role(cur, usuario, rol)
        _with_access(cur, [row])
        return _operator_public(row)


@contextlib.contextmanager
def connection_scope(connection=None):
    """Use the caller's open transaction, or open (and commit) a new one."""
    if connection is not None:
        yield connection
        return
    with db._connect() as conn:
        yield conn


def _lock_operators(cur):
    """Serializes creates and renames of accounts: `LOCK TABLE operators`. There is no Core
    construct for LOCK, so it is a `text()` run like SAVEPOINT."""
    sqlcore.run(cur, sa.text("LOCK TABLE operators IN SHARE ROW EXCLUSIVE MODE"))


def operator_name_taken(cur, usuario):
    """Same test the create guard applies (case-insensitive, retired names
    stay reserved), under the same table lock, so a suggested name is free
    when the insert runs in this transaction."""
    t, h = sm.operators, sm.operator_username_history
    _lock_operators(cur)
    if sqlcore.run(cur, sa.select(sa.literal_column("1")).where(
            sa.func.lower(t.c.username) == sa.func.lower(usuario))).fetchone():
        return True
    return sqlcore.run(cur, sa.select(sa.literal_column("1")).where(
        sa.func.lower(h.c.old_username) == sa.func.lower(usuario))).fetchone() is not None


def _guard_operator_username(cur, usuario, current=None):
    # Serialize creates and renames, including case variants and retired names.
    # Retired names stay reserved: an old signed session must never acquire a
    # different person's account if its former name is reused.
    t, h = sm.operators, sm.operator_username_history
    _lock_operators(cur)
    if sqlcore.run(cur, sa.select(t.c.username).where(
            sa.func.lower(t.c.username) == sa.func.lower(usuario),
            t.c.username != (current or ""))).fetchone():
        raise OperatorConflict("nombre_ya_existe")
    if sqlcore.run(cur, sa.select(h.c.old_username).where(
            sa.func.lower(h.c.old_username) == sa.func.lower(usuario))).fetchone():
        raise OperatorConflict("nombre_ya_existe")


# Every (table, column) that holds an operator's username, moved by a rename.
_RENAMED_REFERENCES = (
    ("operator_access", "username"), ("admin_preferences", "username"),
    ("admin_account_security", "username"), ("admin_reset_tokens", "username"),
    ("passkeys", "username"), ("passkey_challenges", "username"),
    ("booking_requests", "resolved_by"), ("booking_requests", "attendance_updated_by"),
    ("appointment_events", "actor"), ("attendance_events", "operator"),
    ("hsi_sync_conflicts", "resolved_by"), ("interviews", "alert_seen_by"),
    ("consultations", "created_by"), ("ai_prompts", "updated_by"),
    ("ai_prompts_history", "updated_by"), ("translations", "updated_by"),
    ("legal_docs", "updated_by"),
)


def operator_rename(usuario, nuevo, *, password=None, current_password):
    """Move the account and all username references atomically; retain rename audit."""
    from app.auth import admin_auth
    from app.core import usernames
    if (not usernames.valid(nuevo) or nuevo.casefold() == admin_auth.owner_usuario().casefold()
            or usuario.casefold() == admin_auth.owner_usuario().casefold()):
        raise OperatorConflict("nombre_ya_existe")
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        _guard_operator_username(cur, nuevo, usuario)
        row = sqlcore.run(cur, sa.select(t).where(t.c.username == usuario).with_for_update()).fetchone()
        if not row or not row["active"]:
            raise OperatorConflict("credenciales")
        valid, replacement = password_hashing.verify_and_update(row["pass_hash"], current_password)
        if not valid:
            raise OperatorConflict("credenciales")
        new_hash = password_hashing.hash_password(password) if password else (replacement or row["pass_hash"])
        sqlcore.run(cur, sa.update(t).values(username=nuevo, pass_hash=new_hash).where(t.c.username == usuario))
        for table, column in _RENAMED_REFERENCES:
            ref = sm.metadata.tables[table]
            sqlcore.run(cur, sa.update(ref).values({ref.c[column]: nuevo}).where(ref.c[column] == usuario))
        sqlcore.run(cur, sa.insert(sm.operator_username_history).values(
            old_username=usuario, new_username=nuevo, operator_id=row["id"]))


def operator_update(usuario, rol=None, activo=None, password=None,
                        totp_secret=NO_CHANGE, medico_id=NO_CHANGE,
                        email=NO_CHANGE, centros=NO_CHANGE, link_only=False, invited=False):
    """Edita sólo los campos pasados explícitamente. `totp_secret`:
    NO_CHANGE (default) = no tocar 2FA; None = quitarlo; un string = fijar/
    regenerar el secreto. `medico_id` sigue el mismo contrato NO_CHANGE/
    None/valor (admin_users.py lo vacía explícitamente al sacar a alguien
    del rol 'medico'). Devuelve el registro actualizado, o None si el
    usuario no existe."""
    t = sm.operators
    values = {}
    if rol is not None:
        values["role"] = _operator_base_role(rol)
    if activo is not None:
        values["active"] = bool(activo)
    if password:
        values["pass_hash"] = password_hashing.hash_password(password)
    if totp_secret is not NO_CHANGE:
        values["totp_secret"] = totp_secret
    if medico_id is not NO_CHANGE:
        values["doctor_id"] = medico_id
    if email is not NO_CHANGE:
        values["email"] = email
    if centros is not NO_CHANGE:
        # lista vacía = sin restricción, se guarda NULL (una sola forma de
        # decir "todos" en la base)
        values["centers"] = list(centros) if centros else None
    if invited:
        # An invitation (email or copied link) was just issued: «invitado».
        values["invited_at"] = sa.func.now()
    if not values:
        return operator_get_public(usuario)
    with db._connect() as conn, conn.cursor() as cur:
        if rol is not None or medico_id is not NO_CHANGE:
            existing = sqlcore.run(cur, sa.select(t.c.role, t.c.doctor_id).where(
                t.c.username == usuario).with_for_update()).fetchone()
            if not existing:
                return None
            if link_only and existing["doctor_id"] and existing["doctor_id"] != medico_id:
                raise OperatorConflict("usuario_ya_vinculado")
            _guard_operator_doctor(cur, usuario, rol if rol is not None else existing["role"],
                                      medico_id if medico_id is not NO_CHANGE else existing["doctor_id"])
        from psycopg.errors import UniqueViolation
        try:
            sqlcore.run(cur, sa.update(t).values(values).where(
                t.c.username == usuario).returning(*t.c))
        except UniqueViolation as exc:
            if _doctor_link_violation(exc):
                raise OperatorConflict("medico_con_usuario") from exc
            raise
        row = db_wire._wire(cur.fetchone(), "operators")
        if row and rol is not None:
            _set_access_role(cur, usuario, rol)
        _with_access(cur, [row])
        return _operator_public(row)


def operator_doctor_id(usuario):
    """medico_id vinculado a esa cuenta de operador, o None (incluye el caso
    dueño, que no vive en esta tabla, y el de una cuenta sin ese rol)."""
    row = operator_get(usuario)
    return row.get("medico_id") if row else None


def operator_complete_onboarding(usuario):
    """Durable self-service completion; an unlinked/inactive account cannot finish."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            onboarding_completed_at=sa.func.coalesce(t.c.onboarding_completed_at, sa.func.now()),
        ).where(
            t.c.username == usuario, t.c.active, t.c.role == 'medico', t.c.doctor_id.is_not(None),
        ).returning(t.c.id)).fetchone() is not None


def operator_doctor_ids():
    """Set de medico_id que tienen ALGUNA cuenta de operador con rol 'medico'
    (activa o no — 'tiene_login' del listado de médicos del admin sólo
    pregunta si existe la cuenta, no si está activa). Una query para todo el
    catálogo en vez de una por médico (doctor_appointments._doctors_stats)."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        return {r["doctor_id"] for r in sqlcore.run(cur, sa.select(t.c.doctor_id).where(
            t.c.doctor_id.is_not(None))).fetchall()}


def operator_holds_doctor_id(medico_id, connection=None):
    """True when any account — active or not — still points at `medico_id`.
    A hard-deleted doctor keeps its inactive account (audit trail), so its id
    stays taken for `operators_doctor_id_uniq` and a new doctor never gets it."""
    t = sm.operators
    with connection_scope(connection) as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(sa.literal_column("1")).where(
            _eq(t.c.doctor_id, medico_id)).limit(1)).fetchone() is not None


def operator_by_doctor_id(medico_id):
    """Cuenta pública vinculada al médico, activa o no."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        return _operator_public(db_wire._wire(sqlcore.run(cur, sa.select(t).where(
            _eq(t.c.doctor_id, medico_id),
        ).order_by(t.c.created).limit(1)).fetchone(), "operators"))


def operator_feed_token_issue(usuario):
    """Dirección del feed ICS del médico: {"token", "hint"}, o None si la
    cuenta no existe — mismo patrón que db_accounts.feed_token_issue. Sin
    dirección todavía → la crea y devuelve el token en claro (la única vez que
    se ve, junto con operator_rotate_feed_token); con dirección → {"token":
    None, "hint": últimos 4}: la base guarda solo su hash (core/feed_tokens.py)."""
    t = sm.operators
    token, values = feed_tokens.new()
    with db._connect() as conn, conn.cursor() as cur:
        # One atomic UPDATE: two tabs opening «Mis turnos» at once issue ONE address.
        row = sqlcore.run(cur, sa.update(t).where(
            t.c.username == usuario, sa.or_(t.c.feed_token.is_(None), t.c.feed_token == ""),
        ).values(**values).returning(t.c.username)).fetchone()
        if row is not None:
            return {"token": token, "hint": values["feed_token_hint"]}
        # A token the previous build wrote in the clear mid-deploy is hashed, not replaced.
        feed_tokens.hash_in_place(cur, t, t.c.username == usuario)
        row = sqlcore.run(cur, sa.select(t.c.feed_token_hint).where(t.c.username == usuario)).fetchone()
        if row is None:
            return None
        return {"token": None, "hint": row["feed_token_hint"]}


def operator_rotate_feed_token(usuario):
    """Emite un token NUEVO para el feed ICS del médico y deja de resolver el
    anterior en la misma escritura: la dirección vieja (copiada a un
    calendario, compartida por error) contesta 404 desde ahora. Devuelve el
    token en claro (la base guarda su hash y sus últimos 4), o None si la
    cuenta no existe."""
    t = sm.operators
    token, values = feed_tokens.new()
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.update(t).values(**values).where(
            t.c.username == usuario).returning(t.c.username)).fetchone()
        return token if row else None


def operator_revoke_feed_token(usuario):
    """Borra la dirección del feed ICS del médico (el admin, «Cambiar la
    dirección del calendario»): la vieja contesta 404 desde ahora y la próxima
    vez que el médico abre «Agregar a mi calendario» se crea una nueva y se le
    muestra entera — rotarla acá la perdería, la base solo guarda su hash.
    Devuelve True si la cuenta existe."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.update(t).values(feed_token=None, feed_token_hint=None).where(
            t.c.username == usuario).returning(t.c.username)).fetchone()
        return row is not None


def operator_by_feed_token(token):
    """Resuelve un token de feed de médico a su fila pública (sin pass_hash
    ni totp_secret), o None. Compara por hash: el hash guardado no sirve de
    dirección."""
    if not token:
        return None
    t = sm.operators
    stmt = sa.select(t).where(t.c.feed_token == feed_tokens.digest(token))
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, stmt).fetchone()
        if row is None and feed_tokens.heal_raw(cur, t, token):
            row = sqlcore.run(cur, stmt).fetchone()
        return _operator_public(db_wire._wire(row, "operators"))


def operator_deactivate(usuario):
    """Soft-delete: activo=false. Nunca se borra en duro — la auditoría de
    `solicitudes.resuelta_por` sigue apuntando a este usuario. Devuelve el
    registro actualizado, o None si no existía."""
    t = sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        return _operator_public(db_wire._wire(sqlcore.run(cur, sa.update(t).values(
            active=sa.false()).where(t.c.username == usuario).returning(*t.c)).fetchone(), "operators"))


def seed_builtin_roles():
    """Vuelve a sembrar los roles de fábrica en la base activa.

    Los roles son datos de referencia, no datos de una instalación: cualquier
    cosa que vacíe la tabla (el TRUNCATE entre tests, una limpieza manual)
    tiene que volver a dejarlos. Idempotente."""
    with db._connect() as conn:
        db_schema._seed_builtin_roles(conn)
