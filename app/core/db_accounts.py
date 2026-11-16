"""core/db_accounts.py — the patient accounts of the persistence layer: registration, profile, password, sessions, reset tokens, OTP codes, portal binding, calendar-feed and MCP tokens, archive and DNI change.

Split out of `core/db.py` in stage 5 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md):
the statements are Core, built from `core/schema_model.py`.

Callers write `db_accounts.<name>(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below as
`db_wire._wire` / `db_values.<name>`, a sibling as `db_x.<name>` and their own names bare (`_user_public`, which came over
from `core/db.py` with this stage), all at CALL time, so a test that patches the owner patches what every caller uses.
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone

import sqlalchemy as sa
from psycopg.errors import UniqueViolation
from sqlalchemy.dialects import postgresql as _pg

from app.auth import password_hashing
from app.core import db
from app.core import db_wire
from app.core import db_locks
from app.core import db_values
from app.core import feed_tokens
from app.core import ids
from app.core import schema_model as sm
from app.core import sqlcore
from app.core import tz as _tz


def _one():
    """`SELECT 1 ...`: the existence probes only look at whether a row came back."""
    return sa.literal_column("1")


# ---- housekeeping.py: barrido periódico de higiene de tablas --------------

def purge_idle_sessions(idle_days=30, limit=500):
    """Sesiones de login (`sessions`) sin actividad (`last_seen`) hace más de
    `idle_days`. El prototipo no tiene expiración server-side (ver el
    comentario de la tabla en alembic/baseline/0001_schema.sql) -- esto ES esa expiración,
    aplicada de forma perezosa en vez de en cada request. Devuelve cuántas
    filas borró."""
    t = sm.sessions
    idle = t.alias("idle")  # the subquery reads the table being deleted from: its own alias
    with db._connect() as conn, conn.cursor() as cur:
        stale = sa.select(idle.c.token).where(
            idle.c.last_seen < db_values._days_ago(int(idle_days))).limit(int(limit))
        return sqlcore.run(cur, sa.delete(t).where(t.c.token.in_(stale))).rowcount


def legacy_password_hash_inventory():
    """Return value-free counts of password rows still using legacy scrypt.

    This is deliberately an aggregate-only operational boundary: it never
    returns an account identifier or a hash.  Release operators can prove when
    the compatibility reader is removable without exposing credential data.
    """
    def scrypt_rows(table, column):
        # `left(<hash>, 7) = 'scrypt$'`, the constants as SQL literals
        return sa.select(sa.func.count()).select_from(table).where(
            sa.func.left(column, sa.literal_column("7")) == sa.literal_column("'scrypt$'")
        ).scalar_subquery()

    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(
            scrypt_rows(sm.users, sm.users.c.pass_hash).label("patients"),
            scrypt_rows(sm.operators, sm.operators.c.pass_hash).label("staff"),
            scrypt_rows(sm.admin_account_security,
                        sm.admin_account_security.c.owner_pass_hash).label("owners"),
        )).fetchone() or {}
        return {
            "patients": int(row.get("patients") or 0),
            "staff": int(row.get("staff") or 0),
            "owners": int(row.get("owners") or 0),
        }


def _new_user_values(dni, nombre, apellido, nacimiento, sexo, contacto, email, ph,
                     cobertura, domicilio, lang, identity_kind):
    """The columns `create_user` and `create_user_and_session` insert. `coverage` / `address`
    are nullable JSONB columns that took `_coerce_jsonb(x)`: None is the SQL NULL."""
    return dict(
        dni=dni, first_name=nombre, last_name=apellido, birth_date=db_values._coerce_date(nacimiento),
        sex=sexo, contact=contacto, email=email, pass_hash=ph,
        coverage=db_values._jsonb(cobertura), address=db_values._jsonb(domicilio), lang=lang,
        identity_kind=sa.func.coalesce(sa.literal(identity_kind, sa.Text), sa.literal_column("'dni'")))


def create_user(dni, nombre, apellido, nacimiento, sexo, contacto, password, email=None,
                  cobertura=None, domicilio=None, lang=None, identity_kind=None):
    """Inserta un usuario nuevo y devuelve su id. Levanta si el DNI ya existe
    (constraint UNIQUE) — la ruta chequea antes para responder 409.

    `lang` (2026-08-04, `users.lang` — ver alembic/baseline/0001_schema.sql): la preferencia de
    idioma para notifications.py — accounts.py la manda en `i18n.active()`
    al momento del alta (el idioma que el visitante ya estaba viendo), nunca
    se infiere acá. None = "no se sabe todavía", y notifications.py cae al
    idioma canónico — el mismo comportamiento que tenía toda cuenta antes de
    que esta columna existiera.

    `identity_kind` (2026-09-11, `users.identity_kind` — ver alembic/baseline/0001_schema.sql):
    'synthetic' cuando la clave la acuñó el server (alta sin DNI de un
    visitante no hispanohablante); None deja el default de la columna
    ('dni', un documento real)."""
    ph = password_hashing.hash_password(password)
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.insert(t).values(**_new_user_values(
            dni, nombre, apellido, nacimiento, sexo, contacto, email, ph,
            cobertura, domicilio, lang, identity_kind)).returning(t.c.id)).fetchone()
        return row["id"]


class UserKeyTaken(Exception):
    """Registration lost a conflict on users.dni; the existing row is intact."""


class UserEmailTaken(Exception):
    """A no-DNI registration's email already belongs to an account."""


def create_user_and_session(dni, nombre, apellido, nacimiento, sexo, contacto,
                            password, email=None, cobertura=None, domicilio=None,
                            lang=None, identity_kind=None, claim=None):
    """Commit a new account and its first session together, or neither.

    Return the public user plus token without a fallible post-commit read.
    Email locks serialize registration's existing no-DNI uniqueness rule;
    DNI accounts can still share family emails. No global email constraint
    or change to other account writers is introduced.

    `claim` = (status, dni_claimed) writes the desk's `dni_claims` row in the
    same transaction (TASK-68): the account never exists without its claim. A
    claim on a number someone else holds lives under a minted key and may share
    that person's email, so the no-DNI email rule does not apply to it.
    """
    ph = password_hashing.hash_password(password)
    token = secrets.token_urlsafe(32)
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        if email:
            # pg_advisory_xact_lock(hashtext('account-registration-email:' || lower(<email>)))
            db_locks._advisory_xact_lock(cur, "account-registration-email:",
                                   sa.func.lower(db_values._text_value(email)))
            if identity_kind == "synthetic" and claim is None:
                if sqlcore.run(cur, sa.select(_one()).where(
                        sa.func.lower(t.c.email) == sa.func.lower(db_values._text_value(email))
                ).limit(1)).fetchone():
                    raise UserEmailTaken()
        row = sqlcore.run(cur, _pg.insert(t).values(**_new_user_values(
            dni, nombre, apellido, nacimiento, sexo, contacto, email, ph,
            cobertura, domicilio, lang, identity_kind)
        ).on_conflict_do_nothing(index_elements=[t.c.dni]).returning(*t.c)).fetchone()
        if row is None:
            raise UserKeyTaken()
        sqlcore.run(cur, sa.insert(sm.sessions).values(token=token, dni=dni))
        if claim is not None:
            sqlcore.run(cur, sa.insert(sm.dni_claims).values(
                dni_claimed=claim[1], claimant_key=dni, status=claim[0]))
        user = _user_public(db_wire._wire(row, "users"))
    return user, token


# ---- claims on a document number (TASK-68, alembic 0045) -------------------
# `dni_claims` rows are the desk's work list: an account whose number nobody
# has checked yet ('unverified', the first one on a number; 'pending', a second
# sign-up on a number already taken, living under a minted key). No row, or a
# closed one, means the account is confirmed. Nothing here ever deletes a row.

OPEN_CLAIM = ("unverified", "pending")


def open_claim_of(key):
    """The open claim of the account with this key ({id, dni_claimed, status}) or None."""
    t = sm.dni_claims
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.id, t.c.dni_claimed, t.c.status).where(
            t.c.claimant_key == key, t.c.status.in_(OPEN_CLAIM)).limit(1)).fetchone()
        return dict(row) if row else None


def pending_claimants(dni):
    """Keys of the accounts holding a PENDING claim on this number (a second sign-up)."""
    t = sm.dni_claims
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c.claimant_key).where(
            t.c.dni_claimed == dni, t.c.status == "pending").order_by(t.c.id).limit(20)).fetchall()
        return [r["claimant_key"] for r in rows]


def add_claim(dni_claimed, claimant_key, status):
    """Record an open claim for an account that already exists (sign-up writes it in the same
    transaction through `create_user_and_session(claim=...)`)."""
    t = sm.dni_claims
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, _pg.insert(t).values(
            dni_claimed=dni_claimed, claimant_key=claimant_key, status=status
        ).on_conflict_do_nothing().returning(t.c.id)).fetchone()
        return row["id"] if row else None


class ClaimError(Exception):
    """The desk's decision on a claim could not be applied; nothing was written. `code` is the answer."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# What the desk's approval of a PENDING claim does with the number's current holder (the first account on it,
# which nobody checked). The number's CLINICAL identity stays with the number — it is the real owner's, and the
# claimant is now proven to be them — so only the holder's own ACCOUNT leaves for a minted `ANON-` key:
#   * the database moves the four children of `users(dni)` (`FOREIGN_KEYS`, ON UPDATE CASCADE) with the row;
#   * the three manual columns below are the holder's own too (their claim, their assistant cost, their chats).
# `test_dni_claims_resolve` pins that every column of `DNI_MOVED_COLUMNS` is either here or in
# `CLAIM_HOLDER_STAYS`, so a new column with a DNI forces the decision.
CLAIM_HOLDER_CASCADES = (("users", "dni"), ("sessions", "dni"), ("reset_tokens", "dni"),
                         ("dependents", "adult_dni"), ("patient_lab_uploads", "uploaded_by_dni"))
CLAIM_HOLDER_MOVES = (("dni_claims", "claimant_key"), ("ai_costs", "user_dni"))
CLAIM_HOLDER_STAYS = (
    ("booking_requests", "dni"), ("booking_requests", "requester_dni"), ("waitlist", "dni"),
    ("attendance_events", "dni"), ("interviews", "dni"), ("patient_health_profile", "dni"),
    ("patient_summaries", "dni"), ("clinical_history_records", "dni"), ("lab_report_analysis", "dni"),
    ("patient_lab_uploads", "dni"),
)


def open_claims(limit=200):
    """The desk's list: open claims, oldest first, with who asks and who holds the number now."""
    c, u, h = sm.dni_claims, sm.users, sm.users.alias("holder")
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            c.c.id, c.c.dni_claimed, c.c.claimant_key, c.c.status, c.c.created,
            u.c.first_name.label("nombre"), u.c.last_name.label("apellido"),
            u.c.contact.label("contacto"), u.c.email,
            h.c.dni.label("holder_dni"), h.c.first_name.label("holder_nombre"),
            h.c.last_name.label("holder_apellido"),
        ).select_from(c.join(u, u.c.dni == c.c.claimant_key).outerjoin(
            h, sa.and_(h.c.dni == c.c.dni_claimed, c.c.status == "pending"))
        ).where(c.c.status.in_(OPEN_CLAIM)).order_by(c.c.id).limit(int(limit))).fetchall()
        return [dict(r) for r in rows]


def open_claim_number(claim_id):
    """The number an OPEN claim asks for, or None (one row; the desk's access row names it before resolving)."""
    c = sm.dni_claims
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(c.c.dni_claimed).where(
            c.c.id == int(claim_id), c.c.status.in_(OPEN_CLAIM))).fetchone()
        return row["dni_claimed"] if row else None


def _close_claim(cur, claim_id, status, operator, note):
    c = sm.dni_claims
    sqlcore.run(cur, sa.update(c).where(c.c.id == claim_id).values(
        status=status, resolved_at=sa.func.now(), resolved_by=str(operator or "")[:120],
        note=str(note or "")[:500] or None))


def _move_holder_account(cur, holder, anon):
    """The holder's account (and only that) to a minted key, inside the caller's transaction."""
    tables = sm.metadata.tables
    sqlcore.run(cur, sa.update(sm.users).where(sm.users.c.dni == holder).values(
        dni=anon, active=sa.false(), updated=sa.func.now()))  # cascades; the account leaves archived
    sqlcore.run(cur, sa.delete(sm.sessions).where(sm.sessions.c.dni == anon))
    sqlcore.run(cur, sa.delete(sm.reset_tokens).where(sm.reset_tokens.c.dni == anon))  # an archived account is not recoverable
    for table, column in CLAIM_HOLDER_MOVES:
        t = tables[table]
        sqlcore.run(cur, sa.update(t).where(t.c[column] == holder).values({t.c[column]: anon}))
    old_key, new_key = f"dni:{holder}", f"dni:{anon}"
    for table, column in DNI_PREFIXED_KEY_COLUMNS:
        t = tables[table]
        col = t.c[column]
        sqlcore.run(cur, sa.update(t).where(
            sa.or_(col == old_key, sa.func.left(col, len(old_key) + 1) == old_key + ":")
        ).values({col: db_values._text_value(new_key).op("||")(sa.func.substr(col, len(old_key) + 1))}))


def resolve_claim(claim_id, approve, operator, note=""):
    """The desk's decision on one open claim, ONE transaction (TASK-68 step c). Returns {status, dni}.

    approve + `unverified`: the account is confirmed (the claim closes). approve + `pending`: the claimant
    becomes the account of the number — the current holder's account leaves for a minted `ANON-` key
    (`_move_holder_account`; the number's clinical rows stay), then the claimant's whole key moves to the number
    (`change_dni_in`, the same accounting as a DNI correction) and the claim closes; every other open claim on
    the number stays open. reject: the claim closes and the account is archived (no login, sessions gone) —
    a closed claim means «confirmed», so a rejected one must not stay active. A conflict (a profile or a
    dependent link on both sides) raises `ClaimError("conflicto")` with nothing written."""
    c = sm.dni_claims
    with db._connect() as conn, conn.cursor() as cur:
        claim = sqlcore.run(cur, sa.select(c).where(c.c.id == int(claim_id)).with_for_update()).fetchone()
        if claim is None:
            raise ClaimError("no_encontrada")
        if claim["status"] not in OPEN_CLAIM:
            raise ClaimError("ya_resuelta")
        key, number = claim["claimant_key"], claim["dni_claimed"]
        if not approve:
            _close_claim(cur, claim["id"], "rejected", operator, note)
            sqlcore.run(cur, sa.update(sm.users).where(sm.users.c.dni == key).values(
                active=sa.false(), updated=sa.func.now()))
            sqlcore.run(cur, sa.delete(sm.sessions).where(sm.sessions.c.dni == key))
            sqlcore.run(cur, sa.delete(sm.reset_tokens).where(sm.reset_tokens.c.dni == key))
            return {"status": "rejected", "dni": key}
        if claim["status"] == "unverified":
            _close_claim(cur, claim["id"], "approved", operator, note)
            return {"status": "approved", "dni": key}
        db_locks._advisory_xact_lock(cur, "change_dni:", db_values._text_value(number))
        holder = sqlcore.run(cur, sa.select(sm.users.c.dni).where(sm.users.c.dni == number)).fetchone()
        if holder is not None:
            holder_claim = sqlcore.run(cur, sa.select(c.c.id).where(
                c.c.claimant_key == number, c.c.status.in_(OPEN_CLAIM)).with_for_update()).fetchone()
            if holder_claim is not None:
                _close_claim(cur, holder_claim["id"], "rejected", operator, "el numero pasa a quien lo reclamo")
            _move_holder_account(cur, number, "ANON-" + secrets.token_hex(8))
        if not change_dni_in(cur, key, number):
            raise ClaimError("conflicto")  # rolls the holder's move back too
        _close_claim(cur, claim["id"], "approved", operator, note)
        return {"status": "approved", "dni": number}


def get_user_by_email(email):
    """Registro del usuario (sin pass_hash) por email, o None. Case-insensitive:
    alguien que tipea Ana@ejemplo.com al ingresar es la misma cuenta que
    ana@ejemplo.com al registrarse."""
    email = str(email or "").strip()
    if not email:
        return None
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t).where(
            sa.func.lower(t.c.email) == sa.func.lower(db_values._text_value(email))
        ).order_by(t.c.id).limit(1)).fetchone()
        return _user_public(db_wire._wire(row, "users"))


def _sin_nombre(tab):
    """Verdadero cuando la cuenta no tiene ni nombre ni apellido (vacío o solo
    espacios). Ordena False antes que True: las con nombre primero."""
    vacio = sa.literal_column("''")
    return sa.and_(sa.func.coalesce(sa.func.btrim(tab.c.first_name), vacio) == vacio,
                   sa.func.coalesce(sa.func.btrim(tab.c.last_name), vacio) == vacio)


_CON_ACENTO = "ÁÀÄÂÉÈËÊÍÌÏÎÓÒÖÔÚÙÜÛáàäâéèëêíìïîóòöôúùüû"
_SIN_ACENTO = "AAAAEEEEIIIIOOOOUUUUaaaaeeeeiiiioooouuuu"


def _clave_alfabetica(col):
    """Clave de orden alfabético que no depende de la sortación del servidor:
    en una base con sortación C (postgres:16-alpine) «Álvarez» caía después
    de «Zárate» y las minúsculas ("de la Torre") después de todas las
    mayúsculas. Sin acento y en minúscula, «Álvarez» ordena junto a la A."""
    return sa.func.lower(sa.func.translate(
        sa.func.coalesce(col, sa.literal_column("''")),
        sa.literal_column("'" + _CON_ACENTO + "'"), sa.literal_column("'" + _SIN_ACENTO + "'")))


def _con_nombre_o_con_pedidos(tab):
    """Tiene nombre, o al menos una solicitud/turno a su DNI."""
    b = sm.booking_requests
    return sa.or_(sa.not_(_sin_nombre(tab)),
                  sa.exists().where(b.c.dni == tab.c.dni))


def users_admin_list(q="", limit=200, offset=0, recientes=False):
    """Pacientes para la consola admin (admin_patients.py): búsqueda por DNI
    o nombre/apellido (ILIKE, sin ranking — la mesa busca un dato exacto),
    archivados incluidos (la UI los distingue por `activo`). Sin pass_hash.

    Devuelve (items, total): `total` es cuántas filas matchean `q` en TODA la
    tabla (sin el LIMIT/OFFSET de esta página) — sin esto la consola no puede
    mostrar "N de M" ni saber si hay una página siguiente que pedir. Antes la
    lista se cortaba en 200 filas sin rastro de que hubiera más (auditoría
    2026-08-06): un padrón de 300+ pacientes mostraba sólo los primeros 200 y
    nada avisaba que faltaban 100. `limit` queda acotado a 200 (el tope de
    antes, ahora por página en vez de total) para no dejar pedir páginas
    gigantes desde el query string.

    `recientes` (reunión hospital 2026-08-27, la contraparte técnica probando el panel como
    admisionista: "que le aparezcan a pedido, no que aparezcan todos" — el
    padrón entero, alfabético, es una molestia de privacidad y de uso).
    Sólo aplica CON `q` vacío: una búsqueda siempre recorre el padrón
    completo y ordena alfabético, como siempre — "recientes" es apenas el
    default de la pantalla vacía, nunca un filtro sobre lo que se busca.
    Con `recientes=True` y sin `q`, el orden es por ÚLTIMO CONTACTO: el
    turno/solicitud más nueva de esa persona (booking_requests, cualquiera
    de sus columnas de fecha) o la ficha editada hace poco (users.updated),
    lo que sea más reciente."""
    q = str(q or "").strip()
    limit = max(1, min(int(limit or 200), 200))
    offset = max(0, int(offset or 0))
    t = sm.users
    # `cobertura` y `domicilio` viajan para que la lista pueda mostrar (y
    # filtrar por) obra social y localidad — son dos de las columnas que la
    # mesa pide. La FICHA sigue siendo la única que trae personas a cargo:
    # eso sí es una consulta por paciente.
    conditions = []
    if q:
        like = f"%{q}%"
        space = sa.literal_column("' '")
        # «30.009.042» como lo muestra la cola: el DNI guardado es sólo dígitos.
        dni_digits = ids.dni_query(q)
        dni_like = f"%{dni_digits}%" if dni_digits else like
        conditions = [sa.or_(
            t.c.dni.ilike(dni_like), t.c.first_name.ilike(like), t.c.last_name.ilike(like),
            t.c.first_name.op("||")(space).op("||")(t.c.last_name).ilike(like),
            t.c.last_name.op("||")(space).op("||")(t.c.first_name).ilike(like))]
    solo_recientes = recientes and not q
    if solo_recientes:
        # Salud (30.10: «hay gente registrada sin nombre, es mucha»): una cuenta
        # sin nombre que nunca pidió un turno es ruido en «recientes»; el DNI
        # la encuentra igual en la búsqueda, que recorre el padrón entero.
        conditions = [_con_nombre_o_con_pedidos(t)]
    with db._connect() as conn, conn.cursor() as cur:
        total = sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(t).where(
            *conditions)).fetchone()["n"]
        if not solo_recientes:
            stmt = sa.select(
                t.c.dni, t.c.first_name, t.c.last_name, t.c.birth_date, t.c.sex, t.c.contact,
                t.c.email, t.c.active, t.c.created, t.c.coverage, t.c.address
            ).where(*conditions).order_by(
                # Las cuentas sin nombre al final: en el orden alfabético
                # abrían la lista (apellido vacío ordena primero) y tapaban a
                # los pacientes con nombre.
                _sin_nombre(t).asc(),
                t.c.last_name.is_(None).asc(), _clave_alfabetica(t.c.last_name),
                _clave_alfabetica(t.c.first_name), t.c.dni
            ).limit(limit).offset(offset)
        else:
            b = sm.booking_requests
            last_contact = sa.select(
                b.c.dni,
                sa.func.max(sa.func.greatest(
                    b.c.created,
                    sa.func.coalesce(b.c.resolved_at, b.c.created),
                    sa.func.coalesce(b.c.attendance_updated_at, b.c.created),
                    sa.func.coalesce(b.c.confirmation_at, b.c.created),
                    sa.func.coalesce(b.c.cancelled_at, b.c.created))).label("ultimo"),
            ).where(b.c.dni.is_not(None)).group_by(b.c.dni).subquery("br")
            u = t.alias("u")
            ultimo_contacto = sa.func.greatest(
                sa.func.coalesce(u.c.updated, u.c.created),
                sa.func.coalesce(last_contact.c.ultimo, u.c.created)).label("ultimo_contacto")
            stmt = sa.select(
                u.c.dni, u.c.first_name, u.c.last_name, u.c.birth_date, u.c.sex, u.c.contact,
                u.c.email, u.c.active, u.c.created, u.c.coverage, u.c.address, ultimo_contacto
            ).select_from(u.outerjoin(last_contact, last_contact.c.dni == u.c.dni)).where(
                _con_nombre_o_con_pedidos(u)
            ).order_by(sa.desc(ultimo_contacto), u.c.dni).limit(limit).offset(offset)
        items = [_user_public(db_wire._wire(r, "users")) for r in sqlcore.run(cur, stmt).fetchall()]
        return items, total


def user_admin_update(dni, campos):
    """Actualización PARCIAL de un paciente desde la consola admin: sólo las
    claves presentes en `campos` se tocan (a diferencia de update_user,
    que reescribe todo el perfil — ese es el contrato de la cuenta propia).
    Claves permitidas: nombre, apellido, nacimiento, sexo, contacto, email,
    activo, cobertura. Devuelve el usuario actualizado (sin pass_hash) o None."""
    permitidas = {"nombre", "apellido", "nacimiento", "sexo", "contacto",
                  "email", "activo", "cobertura"}
    # `campos` sigue llegando con las claves VIEJAS (contrato de wire format
    # congelado) — acá se traduce a la columna nueva recién al armar el SQL.
    columna_nueva = {
        "nombre": "first_name", "apellido": "last_name", "nacimiento": "birth_date",
        "sexo": "sex", "contacto": "contact", "email": "email",
        "activo": "active", "cobertura": "coverage",
    }
    t = sm.users
    sets = {}
    identidad = {}
    for k, v in campos.items():
        if k not in permitidas:
            continue
        if k == "nacimiento":
            v = db_values._coerce_date(v)
        if k in ("nombre", "apellido", "nacimiento", "sexo"):
            identidad[k] = v
        if k == "cobertura":
            sets["coverage"] = db_values._jsonb(v)
            continue
        sets[columna_nueva[k]] = v
    if not sets:
        return get_user(dni)
    sets["updated"] = sa.func.now()
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(sqlcore.run(cur, sa.update(t).where(t.c.dni == dni).values(**sets)
                                   .returning(*t.c)).fetchone(), "users")
        if row:
            _sync_identity_copies(cur, dni, **identidad)
    return _user_public(row) if row else None


def _live_request(b):
    """A request still alive for its patient: pending, or approved with the appointment still
    ahead (or not yet timed). Status values stay inline literals, as in the hand-written SQL."""
    return sa.or_(
        b.c.status == sa.literal_column("'pendiente'"),
        sa.and_(b.c.status == sa.literal_column("'aprobada'"),
                sa.or_(b.c.appointment_at.is_(None), b.c.appointment_at > sa.func.now())))


def _sync_identity_copies(cur, dni, nombre=None, apellido=None, nacimiento=None, sexo=None):
    """Problems.md №9, paso 1: la cuenta (`users`) es la fuente del nombre,
    la fecha de nacimiento y el sexo; la solicitud de turno guarda una COPIA
    de cada uno al reservar. Cuando se corrige la cuenta, las copias de las
    solicitudes PENDIENTES o FUTURAS de ese paciente se reescriben en la misma
    transacción (la cola, la agenda del médico y el envío a HSI las leen).
    Lo pasado queda como estaba. El teléfono de la solicitud es suyo (decisión
    del hub del responsable del producto, 2026-10-23) y no se toca. Un dato vacío nunca borra una
    copia, y sólo se escribe donde el valor realmente cambia.

    La entrevista previa guarda el nombre que el paciente tenía al entrar
    (`entered_name`/`entered_surname`, sólo si había sesión): se actualiza la
    de una solicitud viva y la entrada por QR de hoy que sigue abierta.
    Devuelve (solicitudes, entrevistas) tocadas."""
    campos = {}
    for columna, valor in (("first_name", nombre), ("last_name", apellido),
                           ("birth_date", nacimiento), ("sex", sexo)):
        if isinstance(valor, str):
            valor = valor.strip()
        if valor not in (None, ""):
            campos[columna] = valor
    if not campos or not dni:
        return 0, 0
    b, i = sm.booking_requests, sm.interviews
    solicitudes = sqlcore.run(cur, sa.update(b).values(campos).where(
        b.c.dni == dni, _live_request(b),
        sa.or_(*(b.c[c].is_distinct_from(v) for c, v in campos.items())))).rowcount
    entrevistas = 0
    vivas = sa.select(b.c.id).where(b.c.dni == dni, _live_request(b))
    for columna, entrevista_col in (("first_name", "entered_name"), ("last_name", "entered_surname")):
        if columna not in campos:
            continue
        col = i.c[entrevista_col]
        entrevistas += sqlcore.run(cur, sa.update(i).values({col: campos[columna]}).where(
            i.c.dni == dni, col.is_not(None), col.is_distinct_from(campos[columna]),
            sa.or_(i.c.request_id.in_(vivas),
                   sa.and_(i.c.request_id.is_(None), i.c.status != sa.literal_column("'done'"),
                           i.c.created > sa.func.now() - sa.literal_column("interval '1 day'", sa.Interval))))
        ).rowcount
    return solicitudes, entrevistas


def update_self_booking_coverage(request_id, dni, expected, coverage):
    """Only coverage, CAS-protected and still bound to an own booking at write."""
    t = sm.users
    b = sm.booking_requests.alias("b")
    own_booking = sa.exists(sa.select(_one()).where(
        b.c.id == request_id, b.c.dni == t.c.dni,
        sa.or_(b.c.requester_dni.is_(None), b.c.requester_dni == sa.literal_column("''"),
               b.c.requester_dni == t.c.dni)).correlate(t))
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).where(
            t.c.dni == dni, t.c.coverage.is_not_distinct_from(db_values._jsonb(expected)), own_booking
        ).values(coverage=db_values._jsonb(coverage)).returning(t.c.dni)).fetchone() is not None


def update_user(dni, nombre=None, apellido=None, nacimiento=None, sexo=None,
                        contacto=None, email=None, cobertura=None, domicilio=None,
                        avisos=None, lang=None, coverage_write=True,
                        coverage_guard=False, coverage_expected=None):
    """Edita los datos de la cuenta ya logueada. El DNI nunca se toca acá — es
    la clave de login/HSI, se cambia en otro lado si hace falta.

    `lang`: igual que en create_user — el caller (accounts.py) es quien
    decide "mantener lo guardado" vs "el valor nuevo", mismo criterio que ya
    usa para `cobertura`/`domicilio`/`avisos`; acá siempre se escribe lo que
    llega."""
    nacimiento = db_values._coerce_date(nacimiento)
    t = sm.users
    # coverage = CASE WHEN <write> THEN <new> ELSE coverage END; the flags travel as bound booleans
    new_coverage = sa.null() if cobertura is None else sa.literal(cobertura, t.c.coverage.type)
    coverage = sa.case((sa.literal(coverage_write, sa.Boolean), new_coverage), else_=t.c.coverage)
    guard = sa.or_(sa.not_(sa.literal(coverage_guard, sa.Boolean)),
                   t.c.coverage.is_not_distinct_from(db_values._jsonb(coverage_expected)))
    with db._connect() as conn, conn.cursor() as cur:
        actualizado = sqlcore.run(cur, sa.update(t).where(t.c.dni == dni, guard).values(
            first_name=nombre, last_name=apellido, birth_date=nacimiento,
            sex=sexo, contact=contacto, email=email, coverage=coverage,
            address=db_values._jsonb(domicilio), notifications=db_values._jsonb(avisos), lang=lang,
        ).returning(t.c.dni)).fetchone() is not None
        if actualizado:
            _sync_identity_copies(cur, dni, nombre, apellido, nacimiento, sexo)
        return actualizado


# Every column that stores a person's DNI, and what `change_dni` does with it
# (verificación formal 2026-09-24, DniMove.lean: antes se movían 7 de 18 y la
# historia clínica quedaba huérfana bajo el DNI viejo — y la heredaba quien
# se registrara después con ese DNI). test_change_dni_closure lee el esquema
# vivo (toda columna cuyo nombre contiene "dni") y falla si una columna no
# está en NINGUNA de las dos listas: una tabla nueva con DNI no se puede
# olvidar en silencio.
DNI_MOVED_COLUMNS = (
    ("users", "dni"),
    ("sessions", "dni"),
    ("reset_tokens", "dni"),
    ("dependents", "adult_dni"),
    ("booking_requests", "dni"),
    ("booking_requests", "requester_dni"),
    ("waitlist", "dni"),
    ("attendance_events", "dni"),
    ("interviews", "dni"),
    ("patient_health_profile", "dni"),
    ("patient_summaries", "dni"),
    ("clinical_history_records", "dni"),
    ("lab_report_analysis", "dni"),
    ("patient_lab_uploads", "dni"),
    ("patient_lab_uploads", "uploaded_by_dni"),
    ("ai_costs", "user_dni"),
    ("dni_claims", "claimant_key"),
)
DNI_KEPT_COLUMNS = {
    # Un dependiente es OTRA persona, cargada por OTRA cuenta (su adulto a
    # cargo). Una fila (adulto X, dni = viejo) dice "X tiene a cargo a quien
    # tiene el DNI viejo": reescribirla editaría los datos de otra cuenta a
    # partir de lo que declara ésta — y si el DNI viejo era un error de
    # tipeo, esa persona es justamente otra. Sólo se mueve el vínculo de
    # esta cuenta como adulto (`adult_dni`).
    ("dependents", "dni"): "otra persona, declarada por otra cuenta",
    # Espejo de lo que HSI dice (PK hsi_patient_id): el paciente HSI con ese
    # DNI sigue teniéndolo en HSI. Reescribirlo afirmaría algo que el sistema
    # de registro no dice, y el próximo sync entrante lo pisaría igual.
    ("hsi_patients", "dni"): "espejo de HSI, el sistema de registro",
    # Registro de accesos (K10): sólo se agrega, nunca se reescribe — un
    # acceso hecho bajo el DNI viejo queda registrado bajo el hash del viejo.
    # Guarda un HMAC, no el DNI, y el trigger rechaza el UPDATE de todos modos.
    ("clinical_access_log", "dni_hash"): "registro de accesos de sólo agregado",
    # Lo que declaró quien se registró («este documento es mío»): lo cierra
    # la mesa, no un cambio de clave de la cuenta.
    ("dni_claims", "dni_claimed"): "lo declarado al registrarse; lo cierra la mesa",
}
# Claves de conversación armadas con el DNI: "dni:<dni>" y sus
# sub-conversaciones "dni:<dni>:chat:<id>". Incluye el permiso de WhatsApp
# (`chat_handoffs.web_key`): si se quedara en el DNI viejo, lo heredaría el
# próximo que se registre con ese DNI.
DNI_PREFIXED_KEY_COLUMNS = (
    ("chat_messages", "key"),
    ("chat_summaries", "patient_key"),
    ("chat_compactions", "patient_key"),
    ("chat_handoffs", "web_key"),
)
# Choques de unicidad al mover: contenido idéntico por hash o un cache
# regenerable → la copia del DNI viejo se descarta (no se pierde nada); datos
# que alguien escribió (perfil de salud, vínculo de dependiente) → el cambio
# se rechaza entero y lo resuelve la mesa, nunca se mezclan dos personas.
_DNI_DEDUP_ON_MOVE = {
    "clinical_history_records": ("source_system", "record_hash"),
    "lab_report_analysis": ("report_hash",),
    "patient_summaries": ("doctor_id",),
}

# Qué hace ARCHIVAR un paciente (`archive_user`) con cada columna que guarda
# su DNI (2026-10-23, problems.md №6: archivar era sólo users.active=false y
# sus turnos seguían en la cola). test_patient_archive_consistency exige que
# estén todas las de `DNI_MOVED_COLUMNS` y `DNI_KEPT_COLUMNS`: una columna
# nueva con DNI no se olvida tampoco acá.
DNI_ON_ARCHIVE = {
    ("users", "dni"): "active=false; sus tokens de calendario y de asistente dejan de resolver",
    ("sessions", "dni"): "se borran",
    ("reset_tokens", "dni"): "se borran",
    ("booking_requests", "dni"): "las vivas se cancelan ANTES, con confirmación de la mesa "
                                 "(admin_patients.archive_patient); la historia queda",
    ("booking_requests", "requester_dni"): "quedan: son turnos de otra persona (su dependiente)",
    ("waitlist", "dni"): "se desactiva y sus ofertas vivas vencen (el lugar pasa al siguiente)",
    ("dependents", "adult_dni"): "queda: el vínculo vuelve al restaurar",
    ("dependents", "dni"): "queda: lo declaró otra cuenta",
    ("hsi_patients", "dni"): "queda: espejo de HSI",
    ("attendance_events", "dni"): "queda: historia",
    ("interviews", "dni"): "queda: historia clínica",
    ("patient_health_profile", "dni"): "queda: historia clínica",
    ("patient_summaries", "dni"): "queda: historia clínica",
    ("clinical_history_records", "dni"): "queda: historia clínica",
    ("lab_report_analysis", "dni"): "queda: historia clínica",
    ("patient_lab_uploads", "dni"): "queda: historia clínica",
    ("patient_lab_uploads", "uploaded_by_dni"): "queda: historia clínica",
    ("ai_costs", "user_dni"): "queda: contabilidad",
    ("clinical_access_log", "dni_hash"): "queda: registro de accesos de sólo agregado",
    ("dni_claims", "claimant_key"): "queda: la mesa todavía puede cerrar la solicitud",
    ("dni_claims", "dni_claimed"): "queda: lo declarado al registrarse",
}

# The foreign keys of the schema (Alembic 0032_foreign_keys, 2026-10-25): the
# 18 links whose parent is a real row of the database. Every one is
# `ON DELETE RESTRICT` — the database refuses to delete a parent that still
# has children, the same rule as «se deshabilitan, no se eliminan»; the links
# to `users(dni)` and `operators(username)` are also `ON UPDATE CASCADE`, so a
# DNI correction or an operator rename is ONE update of the parent row.
# `test_foreign_keys` reads the live schema and fails when a foreign key is
# missing here or listed here and absent: a new link is added by a revision AND
# an entry below. Deliberately NOT foreign keys (no parent row exists):
# `DNI_MOVED_COLUMNS` of a clinical identity without an account, catalog ids,
# staff names written as text in logs, HSI mirrors — see
# docs/architecture/claves-foraneas.md.
# (constraint, child table, child column, parent table, parent column, ON UPDATE)
FOREIGN_KEYS = (
    ("fk_attendance_events_request", "attendance_events", "request_id", "booking_requests", "id", None),
    ("fk_appointment_events_request", "appointment_events", "request_id", "booking_requests", "id", None),
    ("fk_waitlist_request", "waitlist", "request_id", "booking_requests", "id", None),
    ("fk_appointment_offers_waitlist", "appointment_offers", "waitlist_id", "waitlist", "id", None),
    ("fk_slot_holds_request", "slot_holds", "request_id", "booking_requests", "id", None),
    ("fk_interviews_request", "interviews", "request_id", "booking_requests", "id", None),
    ("fk_consultations_interview", "consultations", "interview_id", "interviews", "id", None),
    ("fk_consultations_superseded_by", "consultations", "superseded_by", "consultations", "id", None),
    ("fk_patient_health_profile_interview", "patient_health_profile", "source_interview_id",
     "interviews", "id", None),
    ("fk_operator_username_history_operator", "operator_username_history", "operator_id",
     "operators", "id", None),
    ("fk_operator_access_username", "operator_access", "username", "operators", "username", "CASCADE"),
    ("fk_operator_access_role", "operator_access", "role_slug", "roles", "slug", None),
    ("fk_study_task_results_session", "study_task_results", "session_id", "study_sessions", "id", None),
    ("fk_study_events_session", "study_events", "session_id", "study_sessions", "id", None),
    ("fk_sessions_dni", "sessions", "dni", "users", "dni", "CASCADE"),
    ("fk_reset_tokens_dni", "reset_tokens", "dni", "users", "dni", "CASCADE"),
    ("fk_dependents_adult_dni", "dependents", "adult_dni", "users", "dni", "CASCADE"),
    ("fk_patient_lab_uploads_uploaded_by", "patient_lab_uploads", "uploaded_by_dni",
     "users", "dni", "CASCADE"),
)


def archive_user(dni):
    """Archiva la cuenta en UNA transacción según `DNI_ON_ARCHIVE` (las
    solicitudes vivas las cancela antes quien llama). Devuelve el usuario
    público, o None si el DNI no existe."""
    users, sessions, tokens = sm.users, sm.sessions, sm.reset_tokens
    offers, waitlist = sm.appointment_offers, sm.waitlist
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(sqlcore.run(cur, sa.update(users).where(users.c.dni == dni).values(
            active=sa.false(), updated=sa.func.now()).returning(*users.c)).fetchone(), "users")
        if row is None:
            return None
        sqlcore.run(cur, sa.delete(sessions).where(sessions.c.dni == dni))
        sqlcore.run(cur, sa.delete(tokens).where(tokens.c.dni == dni))
        # Vencer (no marcar 'expirada'): así el barrido de la lista de espera
        # la cierra y ofrece el lugar al siguiente, igual que una oferta que
        # nadie contestó. Todas sus filas, no sólo las activas: cancelar el
        # turno (el paso previo) ya desactivó la suya sin tocar la oferta.
        sqlcore.run(cur, sa.update(offers).where(
            offers.c.status == sa.literal_column("'ofrecida'"),
            offers.c.expires_at > sa.func.now(),
            offers.c.waitlist_id.in_(sa.select(waitlist.c.id).where(waitlist.c.dni == dni))
        ).values(expires_at=sa.func.now()))
        sqlcore.run(cur, sa.update(waitlist).where(waitlist.c.dni == dni, waitlist.c.active)
                    .values(active=sa.false()))
    return _user_public(row)


def change_dni(viejo, nuevo):
    """Cambia el DNI (clave de login/HSI) de una cuenta y arrastra TODA su
    identidad clínica en una sola transacción — la base mueve sola las
    filas con FK a `users(dni)` (ON UPDATE CASCADE, por eso `users` va PRIMERO
    en `DNI_MOVED_COLUMNS`); el resto del cascade es manual y lo gobiernan
    `DNI_MOVED_COLUMNS`/`DNI_PREFIXED_KEY_COLUMNS`.
    Devuelve True si el cambio se aplicó (o no hacía falta), False si `nuevo`
    ya pertenece a otra cuenta o ya tiene datos propios que no se pueden
    fusionar sin mezclar dos personas (perfil de salud, un vínculo de
    dependiente igual). El responsable del producto 2026-07-18: la gente tiene que poder corregir un
    DNI mal guardado sin perder su historia."""
    viejo = str(viejo or "").strip()
    nuevo = str(nuevo or "").strip()
    if not viejo or not nuevo or viejo == nuevo:
        return True  # nada que cambiar
    with db._connect() as conn, conn.cursor() as cur:
        return change_dni_in(cur, viejo, nuevo)


def change_dni_in(cur, viejo, nuevo):
    """El cuerpo de `change_dni` sobre un cursor ajeno: lo usa también el
    borrado/anonimización de un titular (core/subject_erase.py), que mueve la
    identidad a una clave al azar DENTRO de su propia transacción. Misma
    contabilidad (`DNI_MOVED_COLUMNS`, `DNI_PREFIXED_KEY_COLUMNS`), mismo
    resultado: True si se aplicó, False si no se pudo sin mezclar dos personas
    (entonces no escribió nada)."""
    tables = sm.metadata.tables
    users, profile, dependents = sm.users, sm.patient_health_profile, sm.dependents
    # Serializa dos cambios concurrentes hacia el mismo DNI nuevo.
    db_locks._advisory_xact_lock(cur, "change_dni:", db_values._text_value(nuevo))
    if sqlcore.run(cur, sa.select(_one()).where(users.c.dni == nuevo)).fetchone():
        return False  # el DNI nuevo ya es de otra cuenta
    other_profile = profile.alias("old_profile")
    if sqlcore.run(cur, sa.select(_one()).where(
            profile.c.dni == nuevo,
            sa.exists(sa.select(_one()).where(other_profile.c.dni == viejo))
    )).fetchone():
        return False
    a, b = dependents.alias("a"), dependents.alias("b")
    if sqlcore.run(cur, sa.select(_one()).select_from(a.join(b, a.c.dni == b.c.dni)).where(
            a.c.adult_dni == viejo, b.c.adult_dni == nuevo)).fetchone():
        return False
    for table, extra in _DNI_DEDUP_ON_MOVE.items():
        t = tables[table]
        o, n = t.alias("o"), t.alias("n")
        sqlcore.run(cur, sa.delete(o).where(
            o.c.dni == viejo, n.c.dni == nuevo,
            *[n.c[c].is_not_distinct_from(o.c[c]) for c in extra]))
    for table, column in DNI_MOVED_COLUMNS:
        t = tables[table]
        sqlcore.run(cur, sa.update(t).where(t.c[column] == viejo).values({t.c[column]: nuevo}))
    old_key, new_key = f"dni:{viejo}", f"dni:{nuevo}"
    # La compactación es un resumen regenerable: si el DNI nuevo ya tenía
    # una, gana la suya.
    compactions = sm.chat_compactions
    o, n = compactions.alias("o"), compactions.alias("n")
    sqlcore.run(cur, sa.delete(o).where(o.c.patient_key == old_key, n.c.patient_key == new_key))
    for table, column in DNI_PREFIXED_KEY_COLUMNS:
        t = tables[table]
        c = t.c[column]
        sqlcore.run(cur, sa.update(t).where(
            sa.or_(c == old_key, sa.func.left(c, len(old_key) + 1) == old_key + ":")
        ).values({c: db_values._text_value(new_key).op("||")(sa.func.substr(c, len(old_key) + 1))}))
    return True


def set_password(dni, password):
    """Recuperación de contraseña: fija un hash nuevo, cierra toda sesión
    abierta de ese DNI (fuerza a re-loguearse con la contraseña nueva) y vence
    todos los links de recuperación que seguían sin usar: el de un correo
    anterior no puede volver a cambiar una contraseña ya recuperada."""
    ph = password_hashing.hash_password(password)
    t = sm.reset_tokens
    with db._connect() as conn, conn.cursor() as cur:
        # El token MCP también es una credencial de la cuenta: tras una
        # recuperación no puede seguir vivo el de quien entró (2026-11-13).
        sqlcore.run(cur, sa.update(sm.users).where(sm.users.c.dni == dni).values(
            pass_hash=ph, mcp_token=None, mcp_token_hint=None, mcp_token_at=None, mcp_token_used=None))
        sqlcore.run(cur, sa.delete(sm.sessions).where(sm.sessions.c.dni == dni))
        sqlcore.run(cur, sa.update(t).where(t.c.dni == dni, t.c.used == sa.false()).values(used=sa.true()))


def _reset_token_digest(token):
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def create_reset_token(dni, ttl_minutos=60):
    """Token opaco de un solo uso para el link de 'olvidé mi contraseña'.
    Devuelve el token crudo (va sólo en el link del correo); la base guarda
    únicamente su SHA-256, en la columna `token` (mismo criterio que
    `admin_reset_tokens.token_hash`): leer la base no alcanza para armar el link."""
    token = secrets.token_urlsafe(32)
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.insert(sm.reset_tokens).values(
            token=_reset_token_digest(token), dni=dni,
            expires=sa.func.now() + db_values._interval(ttl_minutos, "minute")))
    return token


def consume_reset_token(token):
    """Valida y quema el token (atómico: sólo un consumidor gana la carrera).
    Se busca por el hash del token recibido. Devuelve el DNI si era válido y no
    estaba vencido/usado, si no None."""
    if not token:
        return None
    t = sm.reset_tokens
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.update(t).where(
            t.c.token == _reset_token_digest(token), t.c.used == sa.false(), t.c.expires > sa.func.now()
        ).values(used=sa.true()).returning(t.c.dni)).fetchone()
        return row["dni"] if row else None


# ---- OTP (WhatsApp/SMS — ver otp_send.py) -----------------------------------
# Máximo 5 intentos de verificación por código, 10 minutos de vida, cooldown
# de reenvío de 45s (lo aplica accounts.py leyendo otp_recent antes de crear
# uno nuevo). El ticket "verificado" dura 15 minutos y se consume una sola vez.

_OTP_MAX_ATTEMPTS = 5
_OTP_VERIFIED_MINUTES = 15


def _hash_otp(contacto, proposito, code):
    return hashlib.sha256(f"{contacto}:{proposito}:{code}".encode("utf-8")).hexdigest()


def _norm_code(code):
    return re.sub(r"\D", "", str(code or ""))


def otp_recent(contacto, proposito, segundos=45):
    """True si ya se creó un código para ese contacto/propósito hace menos de
    `segundos` — accounts.py lo usa para no dejar spamear el botón reenviar."""
    t = sm.otp_codes
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(_one()).where(
            t.c.contact == contacto, t.c.purpose == proposito,
            t.c.created > sa.func.now() - db_values._interval(segundos, "second")
        ).limit(1)).fetchone() is not None


def create_otp(contacto, proposito, ttl_minutos=10):
    """Genera un código de 6 dígitos, invalida los códigos previos sin usar de
    ese contacto/propósito y guarda el hash del nuevo. Devuelve el código en
    texto plano (sólo esta llamada lo ve — se lo pasa a otp_send.enviar_otp)."""
    code = "%06d" % secrets.randbelow(1_000_000)
    t = sm.otp_codes
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.update(t).where(
            t.c.contact == contacto, t.c.purpose == proposito, t.c.used == sa.false()
        ).values(used=sa.true()))
        sqlcore.run(cur, sa.insert(t).values(
            contact=contacto, purpose=proposito,
            code_hash=_hash_otp(contacto, proposito, code),
            expires=sa.func.now() + db_values._interval(ttl_minutos, "minute")))
    return code


def verify_otp(contacto, proposito, code):
    """Valida el código contra el más reciente sin usar/vencido/agotado de ese
    contacto+propósito. Éxito -> lo marca usado y emite un ticket 'verificado'
    de `_OTP_VERIFIED_MINUTES` minutos (ver `otp_esta_verificado` /
    `consume_verified_otp`). Cada intento (ok o no) cuenta contra el tope
    de reintentos, para no permitir fuerza bruta sobre el código de 6 dígitos."""
    code = _norm_code(code)
    if not code:
        return False
    t = sm.otp_codes
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.id, t.c.code_hash, t.c.attempts).where(
            t.c.contact == contacto, t.c.purpose == proposito, t.c.used == sa.false(),
            t.c.expires > sa.func.now()
        ).order_by(t.c.created.desc()).limit(1)).fetchone()
        if not row:
            return False
        # The attempt is claimed in ONE statement that checks the cap and the unused flag, so parallel
        # guesses cannot all slip under the cap (security review 2026-11, M-5).
        claimed = sqlcore.run(cur, sa.update(t).where(
            t.c.id == row["id"], t.c.used == sa.false(), t.c.attempts < _OTP_MAX_ATTEMPTS
        ).values(attempts=t.c.attempts + 1).returning(t.c.id)).fetchone()
        if not claimed:
            return False
        if not hmac.compare_digest(row["code_hash"], _hash_otp(contacto, proposito, code)):
            return False
        burned = sqlcore.run(cur, sa.update(t).where(
            t.c.id == row["id"], t.c.used == sa.false()).values(used=sa.true()).returning(t.c.id)).fetchone()
        if not burned:
            return False
        ticket = _pg.insert(sm.otp_verified).values(
            contact=contacto, purpose=proposito,
            verified_until=sa.func.now() + db_values._interval(_OTP_VERIFIED_MINUTES, "minute"))
        sqlcore.run(cur, ticket.on_conflict_do_update(
            index_elements=[sm.otp_verified.c.contact, sm.otp_verified.c.purpose],
            set_={"verified_until": ticket.excluded.verified_until}))
        return True


def consume_verified_otp(contacto, proposito):
    """Un solo uso: True + borra el ticket si `contacto` verificó un código
    para `proposito` dentro de la ventana vigente. `_register`/`reset_otp` lo
    llaman recién al confirmar la acción, así el ticket no queda reusable."""
    if not contacto:
        return False
    t = sm.otp_verified
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.delete(t).where(
            t.c.contact == contacto, t.c.purpose == proposito,
            t.c.verified_until > sa.func.now()
        ).returning(_one())).fetchone() is not None


# Built once: the patient site resolves its session on every request (see `sqlcore.Prepared`).
_USER_BY_DNI = sqlcore.prepare(sa.select(sm.users).where(sm.users.c.dni == sa.bindparam("dni", type_=sa.Text)))
_TOUCH_SESSION = sqlcore.prepare(sa.update(sm.sessions).where(
    sm.sessions.c.token == sa.bindparam("token", type_=sa.Text)).values(
    last_seen=sa.func.now()).returning(sm.sessions.c.dni))


def get_user(dni):
    """Registro del usuario (sin pass_hash) o None."""
    with db._connect() as conn, conn.cursor() as cur:
        return _user_public(db_wire._wire(_USER_BY_DNI.run(cur, dni=dni).fetchone(), "users"))


def user_by_portal_id(portal_user_id):
    """The patient bound to this portal account id (`X-User-Id`), or None.
    Active or not — the caller decides; the unique index allows one."""
    portal_user_id = str(portal_user_id or "")
    if not portal_user_id:
        return None
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        return _user_public(db_wire._wire(sqlcore.run(
            cur, sa.select(t).where(t.c.portal_user_id == portal_user_id)).fetchone(), "users"))


def _blank_to_none(value):
    return str(value or "").strip() or None


def _fill_if_blank(column, value):
    """`coalesce(nullif(trim(<column>), ''), <value>)`: the portal's value
    fills an empty column only — what the person saved stays."""
    return sa.func.coalesce(
        sa.func.nullif(sa.func.trim(column), sa.literal_column("''")),
        sa.literal(value, sa.Text))


def user_claim_portal(portal_user_id, dni, email=None, nombre=None, apellido=None):
    """First portal request of a patient: bind the portal account id to the
    account with this DNI, creating it (active, no password) when there is
    none, and return it.

    None — and nothing written — when that account is disabled or already
    bound to another portal account, or when a concurrent request won the
    DNI or the account id (the next request finds the winner). The portal
    email and the name it asserted (`nombre`/`apellido`, when it sends one)
    fill empty fields only: what the person saved stays. An account created
    with no name asserted shows «—» in the patients list until the person
    (or a later portal request, `user_fill_name`) supplies one."""
    portal_user_id = str(portal_user_id or "")
    dni = str(dni or "")
    email = _blank_to_none(email)
    nombre, apellido = _blank_to_none(nombre), _blank_to_none(apellido)
    if not portal_user_id or not dni.isdigit():
        return None
    t = sm.users
    try:
        with db._connect() as conn, conn.cursor() as cur:
            row = db_wire._wire(sqlcore.run(
                cur, sa.select(t).where(t.c.dni == dni).with_for_update()).fetchone(), "users")
            if row is None:
                created = sqlcore.run(cur, _pg.insert(t).values(
                    dni=dni, email=email, portal_user_id=portal_user_id,
                    first_name=nombre, last_name=apellido
                ).on_conflict_do_nothing(index_elements=[t.c.dni]).returning(*t.c)).fetchone()
                return _user_public(db_wire._wire(created, "users"))
            if row.get("portal_user_id") or row.get("activo") is False:
                return None
            bound = sqlcore.run(cur, sa.update(t).where(
                t.c.id == row["id"], t.c.portal_user_id.is_(None)
            ).values(
                portal_user_id=portal_user_id,
                email=_fill_if_blank(t.c.email, email),
                first_name=_fill_if_blank(t.c.first_name, nombre),
                last_name=_fill_if_blank(t.c.last_name, apellido),
            ).returning(*t.c)).fetchone()
            return _user_public(db_wire._wire(bound, "users"))
    except UniqueViolation:
        return None


def user_fill_name(dni, nombre=None, apellido=None):
    """A portal request that carries the person's name, for an account that
    lacks it: each EMPTY name field takes the portal's value, a field the
    person (or the desk) saved is never touched. Returns the public user, or
    None when there was nothing to fill (no value, or both already set)."""
    nombre, apellido = _blank_to_none(nombre), _blank_to_none(apellido)
    dni = str(dni or "")
    if not dni or not (nombre or apellido):
        return None
    t = sm.users
    empty = lambda column: sa.func.coalesce(sa.func.trim(column), sa.literal_column("''")) == sa.literal_column("''")
    wanted = [empty(t.c[c]) for c, v in (("first_name", nombre), ("last_name", apellido)) if v]
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.update(t).where(t.c.dni == dni, sa.or_(*wanted)).values(
            first_name=_fill_if_blank(t.c.first_name, nombre),
            last_name=_fill_if_blank(t.c.last_name, apellido),
        ).returning(*t.c)).fetchone()
        return _user_public(db_wire._wire(row, "users")) if row else None


def login_renaper(dni, datos, claims):
    """Login con identidad nacional (auth_renaper.py): crea el usuario si no
    existe (pass_hash NULL — no puede loguearse por contraseña) o actualiza el
    existente SIN pisar lo que la persona ya curó a mano: los campos de
    identidad sólo se completan donde están vacíos. Los claims crudos del IDP
    se guardan siempre en usuarios.renaper (jsonb) con el timestamp."""
    info = {"claims": claims,
            "last_login": datetime.now(timezone.utc).isoformat()}
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(sqlcore.run(cur, sa.select(t).where(t.c.dni == dni)).fetchone(), "users")
        if row is None:
            return sqlcore.run(cur, sa.insert(t).values(
                dni=dni, first_name=datos.get("nombre"), last_name=datos.get("apellido"),
                birth_date=db_values._coerce_date(datos.get("nacimiento")), sex=datos.get("sexo"),
                email=datos.get("email"), renaper=db_values._jsonb(info)
            ).returning(t.c.id)).fetchone()["id"]
        sqlcore.run(cur, sa.update(t).where(t.c.dni == dni).values(
            first_name=sa.func.coalesce(t.c.first_name, datos.get("nombre")),
            last_name=sa.func.coalesce(t.c.last_name, datos.get("apellido")),
            birth_date=sa.func.coalesce(t.c.birth_date, db_values._coerce_date(datos.get("nacimiento"))),
            sex=sa.func.coalesce(t.c.sex, datos.get("sexo")),
            email=sa.func.coalesce(t.c.email, datos.get("email")),
            renaper=db_values._jsonb(info)))
        return row["id"]


def verify_password(dni, password):
    """Verify a patient and upgrade a historical hash in the same transaction."""
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(sqlcore.run(
            cur, sa.select(t).where(t.c.dni == dni).with_for_update()).fetchone(), "users")
        if not row or row.get("activo") is False:
            password_hashing.dummy_verify(password)
            return None
        valid, replacement = password_hashing.verify_and_update(
            row.get("pass_hash"), password
        )
        if not valid:
            return None
        if replacement:
            # compare-and-swap on the hash read above (`pass_hash = <bound>`, never `IS NULL`)
            sqlcore.run(cur, sa.update(t).where(
                t.c.dni == dni, t.c.pass_hash == sa.literal(row.get("pass_hash"), sa.Text)
            ).values(pass_hash=replacement))
            row["pass_hash"] = replacement
        return _user_public(row)


def create_session(dni):
    """Crea una sesión y devuelve el token opaco (para la cookie)."""
    token = secrets.token_urlsafe(32)
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.insert(sm.sessions).values(token=token, dni=dni))
    return token


def get_session(token):
    """Resuelve un token a {dni, usuario} (join usuarios) y refresca last_seen.
    Devuelve None si el token no existe o no tiene usuario asociado."""
    if not token:
        return None
    with db._connect() as conn, conn.cursor() as cur:
        row = _TOUCH_SESSION.run(cur, token=token).fetchone()
        if not row:
            return None
        dni = row["dni"]
        u = db_wire._wire(_USER_BY_DNI.run(cur, dni=dni).fetchone(), "users")
    if u is None:
        return None
    if u.get("activo") is False:  # archivada: las sesiones vivas dejan de valer
        return None
    return {"dni": dni, "usuario": _user_public(u)}


def delete_session(token):
    """Cierra una sesión (logout). No falla si el token no existe."""
    if not token:
        return
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(sm.sessions).where(sm.sessions.c.token == token))


# ---- feed de calendario (suscripción ICS) -----------------------------------

# The database keeps only the SHA-256 of the token (users.feed_token) and its
# last 4 characters (users.feed_token_hint), see core/feed_tokens.py: a dump or
# a backup hands out no working calendar address, and the patient sees the full
# address once, when it is created or replaced (2026-11-06, alembic 0040).

def feed_token_issue(dni):
    """Dirección de calendario del usuario: {"token", "hint"}, o None si el DNI no existe.

    Sin dirección todavía → la crea y devuelve el token en claro (la única vez
    que se ve, junto con regenerar). Con dirección → {"token": None, "hint":
    últimos 4}: la base no la guarda en claro, así que no se puede volver a mostrar."""
    t = sm.users
    token, values = feed_tokens.new()
    with db._connect() as conn, conn.cursor() as cur:
        # One atomic UPDATE: two tabs opening the popup at once issue ONE address.
        row = sqlcore.run(cur, sa.update(t).where(
            t.c.dni == dni, sa.or_(t.c.feed_token.is_(None), t.c.feed_token == ""),
        ).values(**values).returning(t.c.id)).fetchone()
        if row is not None:
            return {"token": token, "hint": values["feed_token_hint"]}
        # A token the previous build wrote in the clear mid-deploy is hashed, not replaced.
        feed_tokens.hash_in_place(cur, t, t.c.dni == dni)
        row = sqlcore.run(cur, sa.select(t.c.feed_token_hint).where(t.c.dni == dni)).fetchone()
        if row is None:
            return None
        return {"token": None, "hint": row["feed_token_hint"]}


def feed_token_regenerate(dni):
    """Rota la dirección de calendario (la anterior deja de actualizarse).
    Devuelve el token nuevo en claro, o None si el DNI no existe; la base
    guarda solo su hash y sus últimos 4."""
    token, values = feed_tokens.new()
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.update(t).where(t.c.dni == dni).values(
            **values).returning(t.c.id)).fetchone()
        return token if row else None


def user_by_feed_token(token):
    """Resuelve un token de feed a su usuario (sin pass_hash), o None. Una
    cuenta archivada no resuelve, igual que sus sesiones (2026-10-23). Compara
    por hash: el hash guardado no sirve de dirección. Un token todavía en claro
    (escrito por el build anterior durante un deploy) se hashea en el lugar y
    resuelve (feed_tokens.heal_raw)."""
    if not token:
        return None
    t = sm.users
    stmt = sa.select(t).where(t.c.feed_token == feed_tokens.digest(token), t.c.active.is_not(sa.false()))
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, stmt).fetchone()
        if row is None and feed_tokens.heal_raw(cur, t, token):
            row = sqlcore.run(cur, stmt).fetchone()
        return _user_public(db_wire._wire(row, "users"))


# ---- token del conector MCP (asistentes de IA, ver mcp_api.py) --------------

# The database keeps only the SHA-256 of the token (users.mcp_token) and its
# last 4 characters (users.mcp_token_hint): a dump or a backup hands out no
# working token. The token is 192 random bits, so a plain hash is enough (no
# salt, no slow KDF) and the lookup stays an indexed equality. The patient sees
# the token once, when it is issued or regenerated (2026-11-06, alembic 0038).

def _mcp_token_digest(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


# Owner's security decision (2026-11-13): the token dies after 30 days WITHOUT
# USE. users.mcp_token_at is when it was issued, users.mcp_token_used when an
# assistant last used it (written at most every _MCP_USED_EVERY); alive means
# coalesce(used, at) is newer than the lifetime. A row with a token and no
# clock (an old build issued it) is alive and gets its clock on first use.
# Alembic 0043.
_MCP_LIFETIME = sa.literal_column("interval '30 days'")
_MCP_USED_EVERY = sa.literal_column("interval '10 minutes'")


def _mcp_alive(t):
    # A token with no clock at all (issued by a build from before 0043 during a
    # rolling deploy or a rollback) is alive and gets its clock on first use.
    clock = sa.func.coalesce(t.c.mcp_token_used, t.c.mcp_token_at)
    return sa.or_(clock.is_(None), clock > sa.func.now() - _MCP_LIFETIME)


def _new_mcp_token():
    token = secrets.token_urlsafe(24)
    return token, {"mcp_token": _mcp_token_digest(token), "mcp_token_hint": token[-4:],
                   "mcp_token_at": sa.func.now(), "mcp_token_used": None}


def mcp_token_issue(dni):
    """Conector MCP del usuario: {"token", "hint", "last_used"}, o None si el DNI
    no existe.

    Sin token todavía (o vencido: 30 días sin uso) → lo crea y devuelve el token
    en claro (la única vez que se ve, junto con regenerar). Con token vivo →
    {"token": None, "hint": últimos 4, "last_used": último uso}: la base no lo
    guarda en claro, así que no se puede volver a mostrar."""
    t = sm.users
    token, values = _new_mcp_token()
    with db._connect() as conn, conn.cursor() as cur:
        # One atomic UPDATE: two tabs opening the screen at once issue ONE token.
        row = sqlcore.run(cur, sa.update(t).where(
            t.c.dni == dni,
            sa.or_(t.c.mcp_token.is_(None), t.c.mcp_token == "",
                   sa.not_(_mcp_alive(t))),
        ).values(**values).returning(t.c.id)).fetchone()
        if row is not None:
            return {"token": token, "hint": values["mcp_token_hint"], "last_used": None}
        row = sqlcore.run(cur, sa.select(t.c.mcp_token_hint, t.c.mcp_token_used).where(
            t.c.dni == dni)).fetchone()
        if row is None:
            return None
        return {"token": None, "hint": row["mcp_token_hint"],
                "last_used": _tz.iso(row["mcp_token_used"])}


def mcp_token_regenerate(dni):
    """Rota el token MCP (revoca el anterior). Devuelve el nuevo en claro, o
    None si el DNI no existe; la base guarda solo su hash y sus últimos 4."""
    token, values = _new_mcp_token()
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.update(t).where(t.c.dni == dni).values(
            **values).returning(t.c.id)).fetchone()
        return token if row else None


def mcp_token_revoke(dni, only_if_set=False):
    """Revoca el token MCP del usuario (el asistente conectado deja de servir).
    True si el usuario existe. Idempotente. `only_if_set=True` cambia el sentido:
    toca la fila solo si hay un token que revocar y True = se revocó uno (False
    no dice si el usuario existe); quien lo llama en cada request no escribe en
    la base una vez revocado."""
    t = sm.users
    cond = [t.c.dni == dni]
    if only_if_set:
        cond.append(t.c.mcp_token.isnot(None))
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.update(t).where(*cond).values(
            mcp_token=None, mcp_token_hint=None, mcp_token_at=None, mcp_token_used=None,
        ).returning(t.c.id)).fetchone()
        return row is not None


def user_by_mcp_token(token):
    """Resuelve un token MCP a su usuario (sin pass_hash), o None. Una cuenta
    archivada no resuelve: el asistente ya no pide turnos en su nombre
    (2026-10-23); un token con 30 días sin uso tampoco (2026-11-13). Compara por
    hash: el hash guardado no sirve de token. El uso queda anotado como mucho
    cada 10 minutos."""
    if not token:
        return None
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t).where(
            t.c.mcp_token == _mcp_token_digest(token), t.c.active.is_not(sa.false()),
            _mcp_alive(t))).fetchone()
        if row is None:
            return None
        sqlcore.run(cur, sa.update(t).where(
            t.c.id == row["id"],
            sa.or_(t.c.mcp_token_used.is_(None), t.c.mcp_token_used < sa.func.now() - _MCP_USED_EVERY),
        ).values(mcp_token_used=sa.func.now()))
        return _user_public(db_wire._wire(row, "users"))


def _user_public(row):
    """Copia del registro de usuario SIN el pass_hash (nunca sale de db.py)."""
    if row is None:
        return None
    d = dict(row)
    d.pop("pass_hash", None)
    # The portal account id is a login key, like the password: it never
    # travels to a screen (docs/portal-gateway-identity.md).
    d.pop("portal_user_id", None)
    # The MCP token and the calendar address are credentials too: only their
    # own «Mi cuenta» endpoints speak about them, never a users row on its way
    # to a screen (2026-11-06).
    d.pop("mcp_token", None)
    d.pop("mcp_token_hint", None)
    d.pop("mcp_token_at", None)
    d.pop("mcp_token_used", None)
    d.pop("feed_token", None)
    d.pop("feed_token_hint", None)
    return d
