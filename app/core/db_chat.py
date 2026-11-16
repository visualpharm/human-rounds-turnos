"""core/db_chat.py — the chat statements of the persistence layer.

The multi-turn conversation of the web widget and WhatsApp (`chat_save_message`, `chat_history*`, the
first-message / new-session probes, the spend-guard count, the admin «Chats» list and the nightly
export), its metadata (`chat_metadata_get`, `chat_disclosure_sent`, `chat_take_booking_proposal`),
the cross-session memory (`chat_summary*`, `chat_compaction_*`), its retention period (`purge_chat_*_by_age`,
`chat_retention_preview`, `chat_totals`), the web -> WhatsApp hand-off codes
(`chat_handoff_*`) and the phone -> account resolution of WhatsApp (`user_by_phone`,
`user_by_handoff_phone`, `linked_whatsapp_claves`, `web_claves_for_dni`). Split out of `core/db.py`
in stage 4 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md): the statements are
Core, built from `core/schema_model.py`.

Callers write `db_chat.chat_history(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below
as `db_wire._wire` / `db_values.<name>`, a sibling as `db_x.<name>` and their own names bare, all at CALL
time, so a test that patches the owner patches what every caller uses.
"""

import contextlib
import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import db_accounts
from app.core import db_values
from app.core import db_wire
from app.core import schema_model as sm
from app.core import sqlcore


# The roles that are part of the conversation the model sees (the rest are markers and metadata).
_CONVERSATION_ROLES = ("user", "assistant", "tool", "system")
_DISCLOSURE_MARKER = "AI disclosure delivered v1"

# Conversations that scripts/smoke_demo.py opens against the demo carry this prefix in their
# session id (`<base>:chat:<prefix>…`): synthetic, so the admin «Chats» list leaves them out.
SYNTHETIC_SESSION_PREFIX = "smoke_demo_"


# ---- teléfono -> cuenta (WhatsApp, ver whatsapp_chat.py) --------------------

def user_by_phone(tel):
    """Resuelve un teléfono (cualquier formato: '+54 9 2255...', '2255-...',
    '549225...@c.us' ya despojado del sufijo) a un ÚNICO usuario, comparando
    los ÚLTIMOS 10 dígitos contra `usuarios.contacto` normalizado igual. Un
    número que matchea 0 o MÁS DE 1 cuenta devuelve None — ambiguo se trata
    como desconocido, nunca se adivina (la identidad de reserva en WhatsApp
    ES este match; adivinar mal sería reservar a nombre de otra persona)."""
    digits = "".join(ch for ch in str(tel or "") if ch.isdigit())[-10:]
    if len(digits) < 10:
        return None
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        rows = db_wire._wire_all(sqlcore.run(cur, sa.select(t).where(
            t.c.contact.is_not(None), db_values._last_ten_digits(t.c.contact) == digits)).fetchall(), "users")
    if len(rows) != 1:
        return None
    return db_accounts._user_public(rows[0])


def user_by_handoff_phone(tel, grant_days=30):
    """Resuelve un teléfono a la cuenta que lo autorizó vía un código de
    traspaso ya consumido (ver whatsapp_chat.py, responsable del producto 2026-08-02: "ya estoy
    logueado en la web, no me pidas de nuevo confirmar mi identidad por
    WhatsApp"). SOLO cuenta un `chat_handoffs.web_key` que empiece con
    "dni:" — una redención de un código emitido a una conversación anónima
    ("ip:...") nunca autoriza nada, sin importar quién lo redima. El más
    reciente gana si el mismo teléfono redimió códigos de más de una cuenta.
    `grant_days` es cuánto dura la autorización DESPUÉS de la redención (el
    código en sí es de un solo uso y vence en `expires`; esto es una ventana
    aparte para no pedir el código de nuevo en cada mensaje siguiente)."""
    digits = "".join(ch for ch in str(tel or "") if ch.isdigit())[-10:]
    if len(digits) < 10:
        return None
    t = sm.chat_handoffs
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.web_key).where(
            t.c.used == sa.true(), t.c.used_phone.is_not(None),
            db_values._last_ten_digits(t.c.used_phone) == digits,
            t.c.used_at > db_values._days_ago(grant_days),
            t.c.web_key.like("dni:%"),
        ).order_by(t.c.used_at.desc()).limit(1)).fetchone()
    if not row:
        return None
    dni = row["web_key"][len("dni:"):].split(":", 1)[0]
    return db_accounts.get_user(dni)


# ---- chat multi-turno (chat_agent.py, canales web + WhatsApp) -------------

def chat_save_message(canal, clave, mensaje):
    """Persiste un mensaje de la conversación (`mensaje` = dict OpenAI-style
    {"role":...,"content":...,...}). Levanta DbUnavailable como el resto de
    db.py si la base no está — el caller (chat_agent._save) decide
    degradar en silencio."""
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.insert(sm.chat_messages).values(
            channel=canal, key=clave, role=str(mensaje.get("role") or ""),
            content=db_values._jsonb(mensaje)))


def chat_metadata_get(canal, clave, kind):
    """Conversation metadata is never sent to a model or compacted."""
    if kind not in ('ai_disclosure', 'health_profile_proposal', 'booking_proposal', 'case_summary'):
        raise ValueError('unknown conversation metadata')
    t = sm.chat_messages
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.content).where(
            t.c.channel == canal, t.c.key == clave, t.c.role == kind,
        ).order_by(t.c.id.desc()).limit(1)).fetchone()
    return row['content'] if row else None


def chat_disclosure_sent(canal, clave):
    # Recognize earlier system markers as well, without a bounded history scan.
    t = sm.chat_messages
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(sa.exists().where(
            t.c.channel == canal, t.c.key == clave,
            sa.or_(t.c.role == 'ai_disclosure',
                   sa.and_(t.c.role == 'system',
                           t.c.content["content"].astext == _DISCLOSURE_MARKER))).label("sent")
        )).fetchone()['sent']


# ---- «Contanos tu caso» (modules/intake/case_chat.py) ---------------------
# The anonymous case chat lives in chat_messages (channel 'caso') until the
# request is sent; then its transcript and summary move into
# booking_requests.case_chat and the anonymous rows go.

def chat_delete_conversation(canal, clave):
    """Every row of one conversation (all roles), e.g. a case chat whose
    transcript already travelled into its request."""
    t = sm.chat_messages
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.delete(t).where(t.c.channel == canal, t.c.key == clave)).rowcount


def case_chat_set(request_id, data):
    """Stores `{"resumen", "mensajes"}` on the request it was written for."""
    b = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(b).where(b.c.id == int(request_id)).values(
            case_chat=db_values._jsonb(data))).rowcount


def case_chat_get(request_id):
    """The whole conversation of one request (summary + messages), or None."""
    b = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(b.c.case_chat).where(b.c.id == int(request_id))).fetchone()
    return (row or {}).get("case_chat") or None


def case_chat_summaries(request_ids):
    """`{request_id: resumen}` for the requests of a queue page that carry a
    case chat — only the summary, never the messages (those are opened one
    request at a time, a logged read)."""
    ids = [int(i) for i in request_ids or [] if str(i).isdigit()]
    if not ids:
        return {}
    b = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(b.c.id, b.c.case_chat["resumen"].label("resumen")).where(
            b.c.id.in_(ids), b.c.case_chat.is_not(None))).fetchall()
    return {r["id"]: r["resumen"] for r in rows if r["resumen"]}


def chat_take_booking_proposal(canal, clave):
    """Consume exactly one proposal, serializing repeated user replies."""
    t, newest = sm.chat_messages, sm.chat_messages.alias("newest")
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(
            db_values._text_value(f'booking_proposal:{canal}:{clave}')))))
        latest = sa.select(newest.c.id).where(
            newest.c.channel == canal, newest.c.key == clave, newest.c.role == 'booking_proposal',
        ).order_by(newest.c.id.desc()).limit(1).scalar_subquery()
        row = sqlcore.run(cur, sa.update(t).values(role='booking_consumed').where(
            t.c.id == latest).returning(t.c.content)).fetchone()
        # Superseded proposals can never be confirmed by a later reply.
        sqlcore.run(cur, sa.update(t).values(role='booking_consumed').where(
            t.c.channel == canal, t.c.key == clave, t.c.role == 'booking_proposal'))
    return row['content'] if row else None


def chat_history(canal, clave, limit=20):
    """Los últimos `limit` mensajes de una conversación, en orden
    CRONOLÓGICO (más viejo primero) — listo para anteponer al mensaje nuevo
    en messages[]."""
    t = sm.chat_messages
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c.content.label("contenido")).where(
            t.c.channel == canal, t.c.key == clave, t.c.role.in_(_CONVERSATION_ROLES),
            sa.func.coalesce(t.c.content["content"].astext, "") != _DISCLOSURE_MARKER,
        ).order_by(t.c.created.desc()).limit(limit)).fetchall()
    rows.reverse()
    return [r["contenido"] for r in rows]


def chat_new_session_marker_at(canal, clave):
    """When the most recent «Nuevo chat» boundary marker was written on this
    (channel, key), or None. `chat_agent.NEW_SESSION_MARKER` is the row shape;
    this is the timestamp `chat_first_user_message` has to start counting
    from, so the button also resets the conversation's LANGUAGE lock and not
    only its visible transcript."""
    t = sm.chat_messages
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.created).where(
            t.c.channel == canal, t.c.key == clave, t.c.content["marker"].astext == 'new_session',
        ).order_by(t.c.created.desc()).limit(1)).fetchone()
    return row.get("created") if row else None


def chat_first_user_message(canal, clave, since=None):
    """Return the conversation's original user text, outside context limits.

    `since` (a timestamp, normally a «Nuevo chat» marker's) restricts the
    search to messages written after it — the original first message of a
    conversation the patient has since restarted is not this conversation's
    first message any more."""
    t = sm.chat_messages
    query = sa.select(t.c.content.label("contenido")).where(
        t.c.channel == canal, t.c.key == clave, t.c.role == 'user')
    if since is not None:
        query = query.where(t.c.created > sa.cast(since, sm.TIMESTAMPTZ))
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, query.order_by(t.c.created.asc()).limit(1)).fetchone()
    if not row:
        return ""
    message = row.get("contenido") or {}
    return str(message.get("content") or "").strip()


def chat_conversations(limit=200, days=90, exclude_channels=()):
    """Recent conversations for the admin «Chats» list (the product owner, 2026-09-05):
    one row per (channel, key) with the first patient message, the message
    count and first/last timestamps. Read-only; the admin sees who asked
    what and when, never the AI transcript itself. `exclude_channels` are
    left out before the limit, so a reader who may not see them still gets
    `limit` rows of the others."""
    try:
        limit = max(1, min(int(limit or 200), 1000))
        days = max(1, min(int(days or 90), 3650))
    except (TypeError, ValueError):
        limit, days = 200, 90
    t, m = sm.chat_messages, sm.chat_messages.alias("m")
    conv = (sa.select(t.c.channel, t.c.key,
                      sa.func.min(t.c.created).label("started"),
                      sa.func.max(t.c.created).label("last_at"),
                      sa.func.count().filter(t.c.role == 'user').label("user_messages"),
                      sa.func.count().label("messages")))
    excluded = [str(c) for c in exclude_channels or ()]
    if excluded:
        conv = conv.where(t.c.channel.not_in(excluded))
    # The smoke's chats exist only on the demo; the session id is client-supplied,
    # so on any other installation a visitor could hide a chat with this prefix.
    from app.ops import demo_installation
    if demo_installation.enabled():
        conv = conv.where(sa.not_(t.c.key.like(
            "%:chat:" + SYNTHETIC_SESSION_PREFIX.replace("_", "!_") + "%", escape="!")))
    conv = (conv.group_by(t.c.channel, t.c.key)
            .having(sa.func.max(t.c.created) >= db_values._days_ago(days))
            .cte("conv"))
    first_message = (sa.select(m.c.content)
                     .where(m.c.channel == conv.c.channel, m.c.key == conv.c.key, m.c.role == 'user')
                     .order_by(m.c.created.asc()).limit(1).scalar_subquery())
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            conv.c.channel, conv.c.key, conv.c.started, conv.c.last_at, conv.c.user_messages,
            conv.c.messages, first_message.label("first_message"),
        ).order_by(conv.c.last_at.desc()).limit(limit)).fetchall()
    out = []
    for row in rows:
        first = row.get("first_message") or {}
        out.append({
            "channel": row.get("channel"), "key": row.get("key"),
            "started": row.get("started"), "last_at": row.get("last_at"),
            "user_messages": int(row.get("user_messages") or 0),
            "messages": int(row.get("messages") or 0),
            "first_message": str(first.get("content") or "").strip() if isinstance(first, dict) else str(first or ""),
        })
    return out


def chat_count_user_messages(canal, clave, hours=24):
    """Count user turns in a bounded recent window for the chat spend guard."""
    try:
        hours = max(1, min(int(hours or 24), 24 * 7))
    except (TypeError, ValueError):
        hours = 24
    t = sm.chat_messages
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sa.func.count().label("n")).where(
            t.c.channel == canal, t.c.key == clave, t.c.role == 'user',
            t.c.created > sa.func.now() - db_values._interval(hours, "hour"),
        )).fetchone()
    return int((row or {}).get("n") or 0)


def chat_adopt_history(canal, clave_origen, clave_destino):
    """Traspaso de historial al loguearse (chat_api.py, 2026-07-23: "cuando me
    logueo, lo agarra de ahí"). Si `clave_destino` (la conversación recién
    autenticada, "dni:<dni>") TODAVÍA no tiene mensajes propios pero
    `clave_origen` (la anónima previa, "ip:<ip>") sí, copia esos mensajes a
    la nueva clave — así la persona no repite lo que ya escribió antes de
    iniciar sesión. Best-effort y UNA sola vez: si el destino ya tiene algo
    (esta adopción ya corrió, o la persona ya venía chateando logueada),
    no hace nada — nunca duplica ni pisa historial propio. Mismo criterio
    que whatsapp_chat._copiar_historial_web, generalizado acá porque ahora
    lo usan los dos canales."""
    if not clave_origen or not clave_destino or clave_origen == clave_destino:
        return
    t = sm.chat_messages
    with db._connect() as conn, conn.cursor() as cur:
        if sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(t).where(
                t.c.channel == canal, t.c.key == clave_destino).limit(1)).fetchone():
            return
        rows = sqlcore.run(cur, sa.select(
            t.c.role.label("rol"), t.c.content.label("contenido"),
        ).where(t.c.channel == canal, t.c.key == clave_origen).order_by(t.c.created)).fetchall()
        for r in rows:
            sqlcore.run(cur, sa.insert(t).values(
                channel=canal, key=clave_destino, role=r["rol"], content=db_values._jsonb(r["contenido"])))


def chat_transcripts(hours=24):
    """Conversaciones (cualquier canal) con actividad en las últimas `hours`
    horas, para el export nocturno de prompt-learning (chat_api.py, GET
    /api/chat-transcripts). [{"canal","clave","mensajes":[{"rol","contenido",
    "created"},...]}], mensajes en orden cronológico dentro de cada
    conversación.

    Was named `chat_conversations` until 2026-09-15, which silently shadowed
    the OTHER `chat_conversations(limit, days)` defined above it (the admin
    «Chats» list's `channel`/`key`-shaped rows) — same module, same name,
    Python keeps only the later definition, so every admin `/admin/chats`
    request called THIS one instead and 500'd on `r["channel"]`."""
    try:
        hours = max(1, min(int(hours or 24), 24 * 30))
    except (TypeError, ValueError):
        hours = 24
    t = sm.chat_messages
    recent = sa.select(t.c.channel, t.c.key).where(
        t.c.created > sa.func.now() - db_values._interval(hours, "hour")).distinct()
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            t.c.channel.label("canal"), t.c.key.label("clave"), t.c.role.label("rol"),
            t.c.content.label("contenido"), t.c.created,
        ).where(sa.tuple_(t.c.channel, t.c.key).in_(recent)
                ).order_by(t.c.channel, t.c.key, t.c.created)).fetchall()
    out = []
    actual = None
    for r in rows:
        clave_conv = (r["canal"], r["clave"])
        if actual is None or actual["_k"] != clave_conv:
            actual = {"_k": clave_conv, "canal": r["canal"], "clave": r["clave"], "mensajes": []}
            out.append(actual)
        actual["mensajes"].append({
            "rol": r["rol"], "contenido": r["contenido"],
            "created": r["created"].isoformat() if r["created"] else None,
        })
    for c in out:
        c.pop("_k", None)
    return out


# ---- una conversación por paciente entre web y WhatsApp (2026-08-03) -------
# Ver chat_agent._conversation_pairs: una vez que un teléfono está vinculado
# a una cuenta (contacto registrado, o un código de traspaso de sesión
# logueada ya redimido), el motor funde el contexto de los dos canales en
# vez de tratarlos como conversaciones separadas. Estas funciones son el
# lado de LECTURA — la escritura sigue siendo por (channel, key) exactos,
# sin tocar chat_save_message ni el resto de la persistencia existente.

def linked_whatsapp_claves(dni, contact=None, grant_days=30):
    """`chat_messages.key` de WhatsApp ("tel:<dígitos>") ya vinculados a esta
    cuenta -- dos fuentes, unidas:
      1. Cualquier conversación de WhatsApp cuya key matchea, en sus últimos
         10 dígitos, el `contact` REGISTRADO de la cuenta (misma
         normalización que user_by_phone) -- el teléfono con el que la
         persona se dio de alta.
      2. Cualquier teléfono que redimió un código de traspaso emitido por
         una sesión LOGUEADA de este mismo dni, todavía dentro de
         `grant_days` -- el reverso de user_by_handoff_phone.
    Nunca adivina más allá de eso: un teléfono no vinculado no aparece acá
    (es la garantía de privacidad de la fusión de conversaciones)."""
    dni_digits = re.sub(r"\D", "", str(dni or ""))
    claves = set()
    messages, handoffs = sm.chat_messages, sm.chat_handoffs
    with db._connect() as conn, conn.cursor() as cur:
        contact_digits = "".join(ch for ch in str(contact or "") if ch.isdigit())[-10:]
        if len(contact_digits) == 10:
            # 'whatsapp' stays a literal: the partial index is `WHERE channel = 'whatsapp'`
            claves.update(r["key"] for r in sqlcore.run(cur, sa.select(messages.c.key).where(
                messages.c.channel == sa.literal_column("'whatsapp'"),
                db_values._last_ten_digits(messages.c.key) == contact_digits).distinct()).fetchall())
        if dni_digits:
            for r in sqlcore.run(cur, sa.select(handoffs.c.used_phone).where(
                    handoffs.c.used == sa.true(), handoffs.c.used_phone.is_not(None),
                    sa.or_(handoffs.c.web_key == f"dni:{dni_digits}",
                           handoffs.c.web_key.like(f"dni:{dni_digits}:%")),
                    handoffs.c.used_at > db_values._days_ago(grant_days)).distinct()).fetchall():
                tel = re.sub(r"\D", "", str(r["used_phone"] or ""))
                if tel:
                    claves.add(f"tel:{tel}")
    return sorted(claves)


def web_claves_for_dni(dni, limit=3):
    """Las `limit` conversaciones web más recientemente activas de este dni
    ("dni:<dni>" y sus sub-conversaciones por pestaña "dni:<dni>:chat:<id>",
    ver chat_api.py) -- lado web de la fusión para un turno de WhatsApp,
    acotado chico para que una pestaña vieja y ajena al tema no inunde una
    respuesta por WhatsApp."""
    dni_digits = re.sub(r"\D", "", str(dni or ""))
    if not dni_digits:
        return []
    t = sm.chat_messages
    ultimo = sa.func.max(t.c.created).label("ultimo")
    with db._connect() as conn, conn.cursor() as cur:
        return [r["key"] for r in sqlcore.run(cur, sa.select(t.c.key, ultimo).where(
            t.c.channel == sa.literal_column("'web'"),
            sa.or_(t.c.key == f"dni:{dni_digits}", t.c.key.like(f"dni:{dni_digits}:%")),
        ).group_by(t.c.key).order_by(sa.desc("ultimo")).limit(limit)).fetchall()]


def chat_history_multi(pares, limit=200):
    """Mensajes de VARIOS pares (channel, key), fusionados cronológicamente
    -- lado de lectura de "una conversación por paciente entre web y
    WhatsApp" (chat_agent._conversation_pairs). `pares` es una lista de
    tuplas (channel, key); duplicados se ignoran. Cada fila trae 'canal'/
    'created' junto a 'contenido' (el dict OpenAI-style tal cual lo guardó
    chat_save_message) -- el caller aplica el corte de sesión por brecha y,
    para el widget, distingue de qué canal vino cada turno."""
    vistos = []
    for par in pares or []:
        if par and par not in vistos:
            vistos.append(par)
    if not vistos:
        return []
    t = sm.chat_messages
    with db._connect() as conn, conn.cursor() as cur:
        filas = sqlcore.run(cur, sa.select(
            t.c.channel.label("canal"), t.c.content.label("contenido"), t.c.created,
        ).where(
            sa.or_(*[sa.and_(t.c.channel == canal, t.c.key == clave) for canal, clave in vistos]),
            t.c.role.in_(_CONVERSATION_ROLES),
            sa.func.coalesce(t.c.content["content"].astext, "") != _DISCLOSURE_MARKER,
        ).order_by(t.c.created.desc()).limit(limit)).fetchall()
    filas.reverse()
    return filas


# ---- memoria entre sesiones (RAG-lite, ver chat_agent._memory_block) ------

def chat_summaries_latest_covers(patient_key):
    """El `covers_until` más reciente ya guardado para este paciente, o None
    -- guardia de idempotencia: una sesión cerrada se resume UNA sola vez
    (chat_agent._maybe_summarize_closed_session)."""
    t = sm.chat_summaries
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sa.func.max(t.c.covers_until).label("hasta")).where(
            t.c.patient_key == patient_key)).fetchone()
    return (row or {}).get("hasta")


def chat_summary_save(patient_key, summary, covers_until, message_count=0, channel="merged"):
    """Persiste el resumen de UNA sesión cerrada (memoria entre sesiones,
    2026-08-03) -- mismo patrón best-effort que el resto de la persistencia
    de chat: el caller decide degradar en silencio si esto levanta."""
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.insert(sm.chat_summaries).values(
            patient_key=patient_key, channel=channel, summary=str(summary or "")[:2000],
            message_count=int(message_count or 0), covers_until=covers_until))


def chat_summaries_recent(patient_key, limit=3):
    """Los `limit` resúmenes más recientes de este paciente, del más nuevo
    al más viejo -- lo que chat_agent._memory_block inyecta en el prompt de
    una sesión nueva para que el asistente recuerde charlas anteriores sin
    releer la transcripción cruda (RAG-lite: recencia, sin embeddings)."""
    t = sm.chat_summaries
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t.c.summary, t.c.created, t.c.covers_until).where(
            t.c.patient_key == patient_key).order_by(t.c.covers_until.desc()).limit(limit)).fetchall()


# ---- autocompactación de una sesión larga (chat_agent, 2026-08-03) ---------
#
# UNA fila por paciente, que se pisa: la compactación N+1 se arma con la N más
# lo que vino después, así que la fila siempre describe "todo hasta
# covers_until". Tabla aparte de `chat_summaries` a propósito — ver
# la revisión Alembic 0006_legacy_runner_tables: un resumen de sesión cerrada se inyecta
# en la sesión siguiente, una compactación NO (es la cabeza de la sesión que
# sigue abierta).

def chat_compaction_get(patient_key):
    t = sm.chat_compactions
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(t.c.patient_key == patient_key)).fetchone()


def chat_compaction_save(patient_key, summary, covers_until, message_count=0):
    t = sm.chat_compactions
    with db._connect() as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(
            patient_key=patient_key, summary=str(summary or "")[:4000],
            message_count=int(message_count or 0), covers_until=covers_until, updated=sa.func.now())
        new = insert.excluded
        sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.patient_key],
            set_={"summary": new.summary, "message_count": new.message_count,
                  "covers_until": new.covers_until, "updated": sa.func.now()}))


def chat_compaction_clear(patient_key):
    """La sesión se cerró (corte por hueco): la compactación de esa sesión ya
    no aplica a la siguiente, que arranca en blanco."""
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(sm.chat_compactions).where(
            sm.chat_compactions.c.patient_key == patient_key))


# ---- plazo de conservación (problems.md №12, ops/housekeeping.py) ----------

# Canales de `chat_messages` que NO son charla con el asistente: la transcripción de
# la entrevista previa es parte de la historia clínica (la lee el médico, la
# consulta y la receta se arman con ella) y no vence por el plazo del chat.
CHAT_RETENTION_EXEMPT_CHANNELS = ("interview",)


def _exempt_channels():
    """The exempt channels as one `text[]` parameter (`channel <> ALL(...)` / `= ANY(...)`)."""
    return sa.literal(list(CHAT_RETENTION_EXEMPT_CHANNELS), _pg.ARRAY(sa.Text))


def purge_chat_by_age(days, limit=2000):
    """Problems.md №12: mensajes de chat (`chat_messages`) más viejos que
    `days`, de TODOS los canales de charla (web con sesión, web sin sesión,
    WhatsApp, MCP), con o sin resumen que los cubra — a diferencia de
    `purge_summarized_chat_messages`, que sólo toca lo que ya capturó un
    resumen del mismo `patient_key` y por eso nunca alcanza a WhatsApp (clave
    = teléfono) ni al chat anónimo. Excluye `CHAT_RETENTION_EXEMPT_CHANNELS`.
    El plazo lo decide la institución (TURNOS_CHAT_RETENTION_DAYS, apagado
    por defecto, ops/housekeeping.py). Acotado por `limit`."""
    t = sm.chat_messages
    doomed = sa.select(t.c.id).where(
        t.c.created < db_values._days_ago(int(days)), t.c.channel != sa.all_(_exempt_channels())).limit(int(limit))
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.delete(t).where(t.c.id.in_(doomed))).rowcount


def purge_chat_summaries_by_age(days, limit=2000):
    """Los resúmenes de sesiones cerradas (`chat_summaries`) y las
    compactaciones (`chat_compactions`) cuyo último mensaje cubierto
    (`covers_until`) es más viejo que `days`. Se llama DESPUÉS de purgar los
    mensajes (el resumen es la condición de la limpieza vieja de mensajes).
    Devuelve cuántas filas borró entre las dos tablas."""
    s, c = sm.chat_summaries, sm.chat_compactions
    borradas = 0
    with db._connect() as conn, conn.cursor() as cur:
        borradas += sqlcore.run(cur, sa.delete(s).where(s.c.id.in_(
            sa.select(s.c.id).where(s.c.covers_until < db_values._days_ago(int(days))).limit(int(limit))))).rowcount
        borradas += sqlcore.run(cur, sa.delete(c).where(c.c.patient_key.in_(
            sa.select(c.c.patient_key).where(c.c.covers_until < db_values._days_ago(int(days))).limit(int(limit))))).rowcount
    return borradas


def chat_retention_preview(days, summary_days="same", connection=None):
    """Ensayo de `purge_chat_by_age` + `purge_chat_summaries_by_age`: cuántas
    filas se borrarían con estos plazos, sin borrar ni leer contenido. Sólo
    conteos. `days` None = los mensajes no se tocan; `summary_days` "same" =
    el mismo plazo que `days`, None = los resúmenes no se tocan.
    `connection`: una conexión de sólo lectura propia (el informe de solo
    lectura no pasa por `_connect`, que puede migrar el esquema)."""
    if summary_days == "same":
        summary_days = days
    t, s, c = sm.chat_messages, sm.chat_summaries, sm.chat_compactions
    n = sa.func.count().label("n")
    por_canal, conversaciones, exenta, resumenes, compactaciones = {}, 0, 0, 0, 0
    with (contextlib.nullcontext(connection) if connection is not None else db._connect()) as conn, conn.cursor() as cur:
        if days is not None:
            old = t.c.created < db_values._days_ago(int(days))
            charla = t.c.channel != sa.all_(_exempt_channels())
            por_canal = {r["channel"]: r["n"] for r in sqlcore.run(cur, sa.select(t.c.channel, n).where(
                old, charla).group_by(t.c.channel)).fetchall()}
            conversaciones = sqlcore.run(cur, sa.select(n).select_from(
                sa.select(t.c.channel, t.c.key).where(old, charla).distinct().subquery("c"))).fetchone()["n"]
            exenta = sqlcore.run(cur, sa.select(n).select_from(t).where(
                old, t.c.channel == sa.any_(_exempt_channels()))).fetchone()["n"]
        if summary_days is not None:
            resumenes = sqlcore.run(cur, sa.select(n).select_from(s).where(
                s.c.covers_until < db_values._days_ago(int(summary_days)))).fetchone()["n"]
            compactaciones = sqlcore.run(cur, sa.select(n).select_from(c).where(
                c.c.covers_until < db_values._days_ago(int(summary_days)))).fetchone()["n"]
    return {"dias": days, "dias_resumenes": summary_days,
            "mensajes": sum(por_canal.values()), "conversaciones": conversaciones,
            "por_canal": por_canal, "entrevista_exenta": exenta,
            "resumenes": resumenes, "compactaciones": compactaciones}


def chat_totals(connection=None):
    """Lo que hay hoy en las tres tablas de memoria del chat (sólo conteos y
    el mensaje más viejo por canal) — la base del informe de retención."""
    t, s, c = sm.chat_messages, sm.chat_summaries, sm.chat_compactions
    n = sa.func.count().label("n")
    with (contextlib.nullcontext(connection) if connection is not None else db._connect()) as conn, conn.cursor() as cur:
        canales = sqlcore.run(cur, sa.select(t.c.channel, n, sa.func.min(t.c.created).label("mas_viejo"))
                              .group_by(t.c.channel).order_by(t.c.channel)).fetchall()
        resumenes = sqlcore.run(cur, sa.select(n, sa.func.min(s.c.covers_until).label("mas_viejo"))).fetchone()
        compactaciones = sqlcore.run(cur, sa.select(n, sa.func.min(c.c.covers_until).label("mas_viejo"))).fetchone()
    return {"mensajes": sum(c["n"] for c in canales),
            "por_canal": {c["channel"]: {"n": c["n"], "mas_viejo": c["mas_viejo"]} for c in canales},
            "resumenes": resumenes["n"], "resumenes_mas_viejo": resumenes["mas_viejo"],
            "compactaciones": compactaciones["n"],
            "compactaciones_mas_viejo": compactaciones["mas_viejo"]}


# ---- traspaso web -> WhatsApp (código de handoff, whatsapp_chat.py) --------

# 2026-07-23, ronda 2 (responsable del producto: el código tiene que ser difícil de adivinar):
# 12 chars sobre un alfabeto de 32 sin ambigüedades (sin 0/O ni 1/I/L) son
# ~60 bits de entropía — de los 6 chars (~30 bits) originales. `secrets`,
# no `random`: es el código que autoriza copiar un historial de chat, tiene
# que ser criptográficamente impredecible, no sólo "no repetible".
_HANDOFF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I
_HANDOFF_LEN = 12


def _generate_handoff_code():
    import secrets
    return "ST-" + "".join(secrets.choice(_HANDOFF_ALPHABET) for _ in range(_HANDOFF_LEN))


def chat_handoff_create(clave_web, ttl_horas=48):
    """Código de traspaso para ESTA conversación web (get-or-create: si ya
    hay uno vigente y sin usar, se reusa — no se emite uno nuevo cada vez que
    el widget muestra el link). Traspasa HISTORIAL siempre; si `clave_web`
    viene de una sesión logueada también autoriza reservas al redimirse
    (ver user_by_handoff_phone, whatsapp_chat.py)."""
    t = sm.chat_handoffs
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.code).where(
            t.c.web_key == clave_web, t.c.used == sa.false(), t.c.expires > sa.func.now(),
        ).order_by(t.c.created.desc()).limit(1)).fetchone()
        if row:
            return row["code"]
        for _ in range(5):
            code = _generate_handoff_code()
            try:
                sqlcore.run(cur, sa.insert(t).values(
                    code=code, web_key=clave_web,
                    expires=sa.func.now() + db_values._interval(ttl_horas, "hour")))
                return code
            except Exception:  # noqa: BLE001 — colisión de PK (rarísima), reintentar
                conn.rollback()
        raise RuntimeError("no se pudo generar un código de traspaso único")


def chat_handoff_use(code, tel):
    """Consume un código de traspaso (single-use, vía UPDATE condicional
    atómico): válido, no vencido, no usado -> lo marca usado y devuelve
    {code, clave_web}; si no resuelve (inexistente/vencido/ya usado),
    devuelve None. `tel` queda auditado en `usado_tel`, no participa de la
    condición — un código sólo se usa una vez, sin importar quién lo mande."""
    code = str(code or "").strip().upper()
    if not code:
        return None
    t = sm.chat_handoffs
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            used=True, used_phone=tel, used_at=sa.func.now(),
        ).where(t.c.code == code, t.c.used == sa.false(), t.c.expires > sa.func.now(),
                ).returning(t.c.code, t.c.web_key.label("clave_web"))).fetchone()
