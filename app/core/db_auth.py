"""core/db_auth.py — the authentication stores of the persistence layer: the owner's account security, passkeys and their challenges, admin preferences and the admin password-reset tokens.

Split out of `core/db.py` in stage 5 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md):
the statements are Core, built from `core/schema_model.py`.

Callers write `db_auth.<name>(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below as
`db_wire._wire` / `db_values.<name>`, a sibling as `db_x.<name>` and their own names bare, all at CALL time, so
a test that patches the owner patches what every caller uses.
"""

import hashlib
import secrets
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.auth import password_hashing
from app.core import db
from app.core import db_operators
from app.core import db_values
from app.core import db_wire
from app.core import schema_model as sm
from app.core import sqlcore


# ---- seguridad de la cuenta admin actual ----------------------------------

ADMIN_NO_CHANGE = object()


_SECURITY_OF = sqlcore.prepare(sa.select(sm.admin_account_security).where(
    sm.admin_account_security.c.username == sa.bindparam("usuario", type_=sa.Text)))


def admin_security_get(usuario):
    """Fila completa de seguridad de la cuenta, o None."""
    with db._connect() as conn, conn.cursor() as cur:
        row = _SECURITY_OF.run(cur, usuario=usuario).fetchone()
        return db_wire._wire(row, "admin_account_security")


def _ensure_deadline_statements():
    t = sm.admin_account_security
    usuario = sa.bindparam("usuario", type_=sa.Text)
    span = sa.bindparam("dias", type_=sa.Integer) * sa.literal_column("interval '1 day'", sa.Interval)
    clear = _pg.insert(t).values(username=usuario, totp_deadline=None)
    clear = clear.on_conflict_do_update(
        index_elements=[t.c.username], set_={"totp_deadline": None, "updated": sa.func.now()},
    ).returning(*t.c)
    start = _pg.insert(t).values(username=usuario, totp_deadline=sa.func.now() + span)
    start = start.on_conflict_do_update(
        index_elements=[t.c.username],
        set_={"totp_deadline": sa.func.coalesce(t.c.totp_deadline, sa.func.now() + span),
              "updated": sa.func.now()},
    ).returning(*t.c)
    return sqlcore.prepare(clear), sqlcore.prepare(start)


# Built once: every panel request ensures the owner's 2FA deadline (see `sqlcore.Prepared`).
_CLEAR_DEADLINE, _START_DEADLINE = _ensure_deadline_statements()


def admin_security_ensure_deadline(usuario, tiene_2fa, dias=7):
    """Crea el plazo inicial de 2FA una sola vez. Con 2FA activo, lo limpia."""
    with db._connect() as conn, conn.cursor() as cur:
        if tiene_2fa:
            row = _CLEAR_DEADLINE.run(cur, usuario=usuario).fetchone()
        else:
            row = _START_DEADLINE.run(cur, usuario=usuario, dias=dias).fetchone()
        return db_wire._wire(row, "admin_account_security")


def admin_security_owner_update(usuario, password=None,
                                totp_secret=ADMIN_NO_CHANGE,
                                email=ADMIN_NO_CHANGE):
    """Actualiza los overrides editables del dueño de emergencia."""
    t = sm.admin_account_security
    sets = {"updated": sa.func.now()}
    if password:
        sets["owner_pass_hash"] = password_hashing.hash_password(password)
    if totp_secret is not ADMIN_NO_CHANGE:
        sets["owner_totp_secret"] = totp_secret
        sets["owner_totp_set"] = True
    if email is not ADMIN_NO_CHANGE:
        sets["owner_email"] = email
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, _pg.insert(t).values(username=usuario).on_conflict_do_nothing(
            index_elements=[t.c.username]))
        row = sqlcore.run(cur, sa.update(t).where(t.c.username == usuario)
                          .values(**sets).returning(*t.c)).fetchone()
        return db_wire._wire(row, "admin_account_security")


def admin_security_verify_owner_password(usuario, password):
    """Verify and, when needed, upgrade the locked owner-override row."""
    t = sm.admin_account_security
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.owner_pass_hash).where(
            t.c.username == usuario).with_for_update()).fetchone()
        stored_hash = row["owner_pass_hash"] if row else None
        valid, replacement = password_hashing.verify_and_update(stored_hash, password)
        if not valid:
            return False
        if replacement:
            sqlcore.run(cur, sa.update(t).where(t.c.username == usuario).values(
                owner_pass_hash=replacement, updated=sa.func.now()))
        return True


def admin_security_owner_access(usuario):
    """Registra la visita del dueño (no tiene fila en `operadores`)."""
    t = sm.admin_account_security
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, _pg.insert(t).values(
            username=usuario, owner_last_access=sa.func.now()
        ).on_conflict_do_update(
            index_elements=[t.c.username],
            set_={"owner_last_access": sa.func.now(), "updated": sa.func.now()}))


def admin_security_set_deadline(usuario, deadline, connection=None):
    t = sm.admin_account_security
    with db_operators.connection_scope(connection) as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(username=usuario, totp_deadline=deadline)
        row = sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.username],
            set_={"totp_deadline": insert.excluded.totp_deadline, "updated": sa.func.now()},
        ).returning(*t.c)).fetchone()
        return db_wire._wire(row, "admin_account_security")


# ---- passkeys (WebAuthn) ---------------------------------------------------

def _passkey_public(row):
    """Lo que ve el panel: nunca la clave pública cruda ni el contador, que no
    le dicen nada a nadie. El credential_id sí viaja: es el identificador con
    el que se borra una llave puntual."""
    if row is None:
        return None
    d = dict(row)
    d.pop("public_key", None)
    d.pop("sign_count", None)
    for k in ("creado", "ultimo_uso"):
        v = d.get(k)
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def passkey_create(usuario, credential_id, public_key, sign_count=0, nombre=None):
    t = sm.passkeys
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.insert(t).values(
            username=usuario, credential_id=credential_id, public_key=public_key,
            sign_count=int(sign_count or 0), name=nombre or None).returning(*t.c)).fetchone()
        return _passkey_public(db_wire._wire(row, "passkeys"))


def passkeys_of(usuario):
    t = sm.passkeys
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t).where(t.c.username == usuario)
                           .order_by(t.c.created)).fetchall()
        return [_passkey_public(db_wire._wire(r, "passkeys")) for r in rows]


def passkey_by_credential(credential_id):
    """Fila COMPLETA (incluye la clave pública) — sólo para verificar una
    firma. El credential_id es lo único que manda el navegador al ingresar, y
    de acá sale de quién es la llave."""
    t = sm.passkeys
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t).where(t.c.credential_id == credential_id)).fetchone()
        return db_wire._wire(row, "passkeys")


def passkey_mark_use(credential_id, sign_count=None):
    t = sm.passkeys
    with db._connect() as conn, conn.cursor() as cur:
        if sign_count is None or int(sign_count) <= 0:
            cur = sqlcore.run(cur, sa.update(t).where(t.c.credential_id == credential_id)
                              .values(last_used=sa.func.now()).returning(t.c.id))
        else:
            # This is the second, database-side half of clone detection.  A
            # verifier can race with another ASGI process that saw the same
            # old count; only one may advance it.  Never move a counter back.
            cur = sqlcore.run(cur, sa.update(t).where(
                t.c.credential_id == credential_id,
                sa.or_(t.c.sign_count == 0, t.c.sign_count < int(sign_count)),
            ).values(last_used=sa.func.now(), sign_count=int(sign_count)).returning(t.c.id))
        return cur.fetchone() is not None


def passkey_challenge_issue(challenge_digest, purpose, username=None, ttl_seconds=300):
    """Create a digest-only expiring challenge and collect expired rows.

    No raw WebAuthn challenge is accepted or stored here.  Cleanup occurs at
    issuance, avoiding an unbounded replay ledger without a background job.
    """
    if purpose not in {"registration", "login"}:
        raise ValueError("invalid passkey challenge purpose")
    ttl_seconds = int(ttl_seconds)
    if ttl_seconds <= 0:
        raise ValueError("invalid passkey challenge ttl")
    t = sm.passkey_challenges
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(sa.or_(
            t.c.expires_at <= sa.func.now(),
            sa.and_(t.c.consumed_at.is_not(None),
                    t.c.consumed_at <= sa.func.now() - db_values._interval(1, "day")))))
        sqlcore.run(cur, sa.insert(t).values(
            challenge_digest=bytes(challenge_digest), purpose=purpose, username=username,
            expires_at=sa.func.now() + db_values._interval(ttl_seconds, "second")))


def passkey_challenge_consume(challenge_digest, purpose, username=None):
    """Atomically consume one non-expired challenge across every process."""
    if purpose not in {"registration", "login"}:
        return False
    t = sm.passkey_challenges
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).where(
            t.c.challenge_digest == bytes(challenge_digest),
            t.c.purpose == purpose,
            t.c.username.is_not_distinct_from(username),
            t.c.consumed_at.is_(None),
            t.c.expires_at > sa.func.now(),
        ).values(consumed_at=sa.func.now()).returning(t.c.challenge_digest)).fetchone() is not None


def passkey_delete(credential_id, usuario=None):
    """Borra UNA llave. Con `usuario` sólo borra si es de esa persona (nadie
    saca la llave de otro sin pasar por el borrado por usuario, que es admin).
    Sólo `None` significa «de cualquiera»: un `usuario` vacío no coincide con
    ninguna llave."""
    t = sm.passkeys
    stmt = sa.delete(t).where(t.c.credential_id == credential_id)
    if usuario is not None:
        stmt = stmt.where(t.c.username == usuario)
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, stmt.returning(t.c.id)).fetchone() is not None


def passkeys_delete_of(usuario):
    """Todas las llaves de una cuenta — el "reset" del panel: quien perdió el
    teléfono vuelve a entrar con contraseña y registra una nueva."""
    t = sm.passkeys
    with db._connect() as conn, conn.cursor() as cur:
        return len(sqlcore.run(cur, sa.delete(t).where(t.c.username == usuario)
                               .returning(t.c.id)).fetchall())


def passkeys_count():
    """Cuántas llaves hay por usuario — la lista de Usuarios muestra el dato
    sin pedir una consulta por fila."""
    t = sm.passkeys
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c.username, sa.func.count().label("n"))
                           .group_by(t.c.username)).fetchall()
        return {r["username"]: int(r["n"]) for r in rows}


# ---- preferencias del panel -------------------------------------------------

def admin_preferences(usuario):
    """Mapa completo de preferencias del panel para una identidad."""
    t = sm.admin_preferences
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c["key"], t.c["value"]).where(
            t.c.username == usuario)).fetchall()
        return {r["key"]: r["value"] for r in rows}


def admin_preference_save(usuario, clave, valor):
    t = sm.admin_preferences
    with db._connect() as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(username=usuario, key=clave, value=valor)
        return sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.username, t.c["key"]],
            set_={"value": insert.excluded["value"], "updated": sa.func.now()},
        ).returning(t.c["value"])).fetchone()["value"]


# ---- enlaces de restablecimiento de contraseña del panel --------------------

def admin_reset_token_create(usuario, horas=24):
    """Crea un token de contraseña de un solo uso; devuelve sólo el token raw."""
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    t = sm.admin_reset_tokens
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.update(t).where(
            t.c.username == usuario, t.c.used == sa.false()).values(used=True))
        sqlcore.run(cur, sa.insert(t).values(
            token_hash=digest, username=usuario,
            expires=sa.func.now() + db_values._interval(int(horas), "hour")))
    return raw


def admin_reset_token_status(raw):
    """Sólo-lectura: ¿este token existe, no se usó y no venció? Para que la
    página GET /admin/reset-password muestre el estado vencido arriba y no
    deje escribir una contraseña que el POST iba a rechazar igual."""
    return admin_reset_token_username(raw) is not None


def admin_reset_token_username(raw):
    """Sólo-lectura: el usuario de un token vigente (no usado, no vencido), o
    None. La página del enlace lo muestra: un médico invitado con un enlace
    copiado no tiene email con el que entrar y necesita saber su usuario."""
    digest = hashlib.sha256(str(raw or "").encode()).hexdigest()
    t = sm.admin_reset_tokens
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.username).where(
            t.c.token_hash == digest, t.c.used == sa.false(),
            t.c.expires > sa.func.now())).fetchone()
        return row["username"] if row else None


def admin_reset_token_consume(raw, password):
    """Cambia la contraseña si el token sigue vigente. Devuelve el usuario."""
    digest = hashlib.sha256(str(raw or "").encode()).hexdigest()
    t, operators = sm.admin_reset_tokens, sm.operators
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.username).where(
            t.c.token_hash == digest, t.c.used == sa.false(),
            t.c.expires > sa.func.now()).with_for_update()).fetchone()
        if not row:
            return None
        if sqlcore.run(cur, sa.update(operators).where(
                operators.c.username == row["username"], operators.c.active == sa.true()
        ).values(pass_hash=password_hashing.hash_password(password))).rowcount != 1:
            return None
        sqlcore.run(cur, sa.update(t).where(t.c.token_hash == digest).values(used=True))
        return row["username"]


# ---- sesiones del panel cerradas con «Cerrar sesión» ------------------------

# Built once: `_verify_token` asks this on every panel request (see `sqlcore.Prepared`).
_SESSION_REVOKED = sqlcore.prepare(sa.select(sm.admin_session_revocations.c.sid).where(
    sm.admin_session_revocations.c.sid == sa.bindparam("sid", type_=sa.Text)))


def admin_session_revoke(sid, usuario, expires_epoch):
    """Marca la sesión `sid` como cerrada hasta `expires_epoch` (segundos desde
    1970), y borra de paso las filas que ya no pueden afectar a ninguna cookie."""
    t = sm.admin_session_revocations
    until = sa.func.to_timestamp(sa.literal(float(expires_epoch), sa.Float))
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(t.c.expires_at < sa.func.now()))
        insert = _pg.insert(t).values(sid=sid, username=usuario, expires_at=until)
        sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.sid],
            set_={"expires_at": sa.func.greatest(t.c.expires_at, insert.excluded.expires_at)}))


def admin_session_is_revoked(sid):
    """True si `sid` fue cerrada con «Cerrar sesión». Levanta DbUnavailable si la base no se lee."""
    with db._connect() as conn, conn.cursor() as cur:
        return _SESSION_REVOKED.run(cur, sid=sid).fetchone() is not None
