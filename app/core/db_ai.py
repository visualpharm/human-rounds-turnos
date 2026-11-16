"""core/db_ai.py — the AI statements of the persistence layer.

The editable prompts of the panel (`prompt_*`), the cost ledger and its report queries
(`log_ai_cost`, `costs_*`, `ai_costs_has_rows`), the MCP daily budget (`mcp_ai_reserve`) and the
AI register (`ai_register_*`). Split out of `core/db.py` in stage 3 of the SQLAlchemy Core move
(docs/architecture/sqlalchemy-core.md): the statements are Core, built from `core/schema_model.py`.

Callers write `db_ai.prompt_save(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below
as `db_wire._wire` / `db_locks.<name>` / `db_values.<name>`, a sibling as `db_x.<name>` and their own
names bare, all at CALL time, so a test that patches the owner (`db_ai.ai_register_append`) patches what
every caller uses.
"""

import contextlib
import re
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import db_values
from app.core import db_locks
from app.core import db_wire
from app.core import schema_model as sm
from app.core import sqlcore


# ---- prompts de la IA (overlay del panel sobre el original del código) -----
#
# El original de cada prompt vive en prompts.py:REGISTRY; esta tabla lo pisa.
# Ver el docstring de prompts.py para el porqué del inglés canónico + la
# traducción al español que se edita.

def prompts_all():
    """Todas las filas de override, indexadas por clave."""
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(sm.ai_prompts)).fetchall()
        return {r["key"]: db_wire._wire(r, "ai_prompts") for r in rows}


def prompt_get(clave):
    t = sm.ai_prompts
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(sqlcore.run(cur, sa.select(t).where(t.c.key == clave)).fetchone(),
                     "ai_prompts")


def prompt_save(clave, texto_en, texto_es, editado, usuario=None,
                   historial=True):
    """Crea o pisa el override. Antes de pisar, archiva la versión que había
    (para el "deshacer"), salvo que la fila anterior fuera sólo la traducción
    cacheada del original — esa no es una versión de nadie."""
    t, history = sm.ai_prompts, sm.ai_prompts_history
    with db._connect() as conn, conn.cursor() as cur:
        # the advisory lock stays hand-written SQL (locks are not Core's business here)
        db_locks._advisory_xact_lock(cur, "ai_prompt:", db_values._text_value(clave))
        previous = sqlcore.run(cur, sa.select(t.c.text_en, t.c.text_es, t.c.edited)
                               .where(t.c.key == clave)).fetchone()
        if historial:
            prev = sqlcore.run(cur, sa.select(t).where(
                t.c.key == clave, t.c.edited.is_(True))).fetchone()
            if prev:
                sqlcore.run(cur, sa.insert(history).values(
                    key=clave, text_en=prev["text_en"], text_es=prev["text_es"],
                    updated=prev["updated"], updated_by=prev["updated_by"]))
        upsert = _pg.insert(t).values(
            key=clave, text_en=texto_en, text_es=texto_es, edited=bool(editado),
            updated=sa.func.now(), updated_by=usuario)
        saved = db_wire._wire(sqlcore.run(cur, upsert.on_conflict_do_update(
            index_elements=[t.c.key],
            set_={"text_en": upsert.excluded.text_en, "text_es": upsert.excluded.text_es,
                  "edited": upsert.excluded.edited, "updated": sa.func.now(),
                  "updated_by": upsert.excluded.updated_by},
        ).returning(*t.c)).fetchone(), "ai_prompts")
        if editado:
            from app.core import ai_register
            ai_register_append(ai_register.prompt_events(
                clave, (previous.get('text_es') or previous['text_en']) if previous and previous['edited'] else None,
                texto_es or texto_en, usuario), connection=conn)
        return saved


def prompt_delete(clave, usuario=None, restored_text=None):
    """Restaurar el original: se va el override (y con él la traducción
    cacheada, que se rehace sola la próxima vez que se abra el editor)."""
    t = sm.ai_prompts
    with db._connect() as conn, conn.cursor() as cur:
        db_locks._advisory_xact_lock(cur, "ai_prompt:", db_values._text_value(clave))
        previous = sqlcore.run(cur, sa.select(t.c.text_en, t.c.text_es, t.c.edited)
                               .where(t.c.key == clave)).fetchone()
        sqlcore.run(cur, sa.delete(t).where(t.c.key == clave))
        if previous and previous['edited']:
            from app.core import ai_register
            ai_register_append(ai_register.prompt_events(
                clave, previous['text_en'], restored_text, usuario), connection=conn)


def prompt_history(clave, limit=20):
    t = sm.ai_prompts_history
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(sqlcore.run(cur, sa.select(t).where(t.c.key == clave)
                                     .order_by(t.c.id.desc()).limit(int(limit))).fetchall(),
                         "ai_prompts_history")


# ---- costos de IA (costos_ia.py) --------------------------------------------
#
# Escritura de analytics: mismo contrato que log_search — DB caída => no-op
# silencioso, NUNCA tumba el request que gastó el token. Las lecturas del
# reporte (costos_*) SÍ dejan propagar DbUnavailable: costos_ia.handle la
# traduce a 503 con su propio _db_guard, igual que reports.py.

def log_ai_cost(operacion, costo_usd=0.0, ip=None, usuario_dni=None,
                 modelo=None, reconocido=None):
    """Registra el costo de UNA llamada de IA. Nunca lanza (analytics)."""
    try:
        c = float(costo_usd or 0.0)
    except (TypeError, ValueError):
        c = 0.0
    rec = None if reconocido is None else bool(reconocido)
    try:
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, sa.insert(sm.ai_costs).values(
                operation=str(operacion or "")[:40], cost_usd=c, ip=(ip or None),
                user_dni=(str(usuario_dni)[:20] if usuario_dni else None),
                model=(str(modelo)[:80] if modelo else None), recognized=rec))
    except Exception:  # noqa: BLE001  (analytics: nunca debe tumbar el request)
        pass


def blank_old_ai_cost_ips(days=180, limit=2000):
    """Retention of the only personal datum of the cost ledger: the IP of a call
    older than `days` goes NULL (the cost, operation and model stay: the reports
    need the money, not the address). Same window and batch contract as
    `db_analytics.purge_old_searches`."""
    t = sm.ai_costs
    with db._connect() as conn, conn.cursor() as cur:
        stale = sa.select(t.c.id).where(
            t.c.ts < db_values._days_ago(int(days)), t.c.ip.is_not(None)).limit(int(limit))
        return sqlcore.run(cur, sa.update(t).where(t.c.id.in_(stale)).values(ip=None)).rowcount


def mcp_ai_reserve(limite, dia=None):
    """Reserva una lectura MCP en el día ART; devuelve el uso nuevo o None.

    El WHERE del upsert hace que comparar + incrementar sea una sola operación,
    incluso con varios workers. A diferencia del logging de costos, falla
    cerrado si Postgres no está disponible: no se gasta IA sin presupuesto.
    """
    limite = max(0, int(limite))
    if limite < 1:
        # The upsert's WHERE guards only the UPDATE branch: on a day without a row the INSERT
        # takes the first use whatever the limit says. A limit of 0 means "nothing may be spent".
        return None
    if dia is None:
        dia = datetime.now(db_values._ART).date()
    t = sm.mcp_ai_budget
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, _pg.insert(t).values(
            day=dia, uses=1, updated=sa.func.now(),
        ).on_conflict_do_update(
            index_elements=[t.c.day],
            set_={"uses": t.c.uses + 1, "updated": sa.func.now()},
            where=t.c.uses < limite,
        ).returning(t.c.uses)).fetchone()
        return row["uses"] if row else None


def costs_summary(dias=30):
    """Totales del período + gasto por IP (mediana y promedio). La IP es el
    proxy de 'usuario': casi toda la IA se gasta ANTES del registro (OCR del DNI,
    análisis de la orden), así que no hay usuario_dni todavía."""
    t = sm.ai_costs
    with db._connect() as conn, conn.cursor() as cur:
        r = sqlcore.run(cur, sa.select(
            sa.func.count().label("n"),
            sa.func.coalesce(sa.func.sum(t.c.cost_usd), sa.literal_column("0")).label("total"),
            sa.func.count().filter(t.c.recognized).label("reconocidas"),
            sa.func.count().filter(t.c.recognized.is_(False)).label("no_reconocidas"),
            sa.func.count(sa.distinct(t.c.ip)).filter(t.c.ip.is_not(None)).label("ips"),
        ).where(t.c.ts > db_values._days_ago(dias))).fetchone()
        per_ip = sa.select(sa.func.sum(t.c.cost_usd).label("c")).where(
            t.c.ts > db_values._days_ago(dias), t.c.ip.is_not(None)).group_by(t.c.ip).subquery("t")
        m = sqlcore.run(cur, sa.select(
            sa.func.percentile_cont(sa.literal_column("0.5")).within_group(per_ip.c.c)
            .label("mediana"),
            sa.func.coalesce(sa.func.avg(per_ip.c.c), sa.literal_column("0")).label("promedio"),
        )).fetchone()
        return {
            "operaciones": r["n"] or 0,
            "total_usd": float(r["total"] or 0),
            "reconocidas": r["reconocidas"] or 0,
            "no_reconocidas": r["no_reconocidas"] or 0,
            "ips": r["ips"] or 0,
            "mediana_por_ip": float(m["mediana"]) if m["mediana"] is not None else None,
            "promedio_por_ip": float(m["promedio"] or 0),
        }


def costs_burn_monthly():
    """Proyección mensual basada sólo en días calendario completos.

    El día de la primera observación se descarta porque suele empezar a mitad
    de jornada; hoy también se descarta porque todavía está abierto. Los días
    completos intermedios sin gasto sí cuentan en el denominador: son parte
    real del ritmo operativo, no datos ausentes.
    """
    c = sm.ai_costs.alias("c")
    limites = sa.select(
        sa.func.min(db_values._local_date(sm.ai_costs.c.ts)).label("primer_dia"),
        db_values._local_date(sa.func.now()).label("hoy"),
    ).select_from(sm.ai_costs).cte("limites")
    rango = sa.select(
        (limites.c.primer_dia + sa.literal_column("1")).label("desde"),
        (limites.c.hoy - sa.literal_column("1")).label("hasta"),
    ).where(limites.c.primer_dia.is_not(None)).cte("rango")
    r_ = rango.alias("r")
    with db._connect() as conn, conn.cursor() as cur:
        r = sqlcore.run(cur, sa.select(
            sa.case((r_.c.hasta >= r_.c.desde,
                     r_.c.hasta - r_.c.desde + sa.literal_column("1")),
                    else_=sa.literal_column("0")).label("dias"),
            sa.func.coalesce(sa.func.sum(c.c.cost_usd), sa.literal_column("0")).label("total"),
            r_.c.desde, r_.c.hasta,
        ).select_from(r_.outerjoin(
            c, db_values._local_date(c.c.ts).between(r_.c.desde, r_.c.hasta),
        )).group_by(r_.c.desde, r_.c.hasta)).fetchone()
        if not r or not (r["dias"] or 0):
            return {
                "dias_completos": 0,
                "total_usd_dias_completos": 0.0,
                "run_rate_mensual_usd": None,
                "desde": None,
                "hasta": None,
            }
        dias = int(r["dias"])
        total = float(r["total"] or 0)
        return {
            "dias_completos": dias,
            "total_usd_dias_completos": total,
            "run_rate_mensual_usd": total / dias * 30,
            "desde": r["desde"].isoformat(),
            "hasta": r["hasta"].isoformat(),
        }


def costs_by_operation(dias=30):
    """[{operacion, cantidad, costo_usd, reconocidas, no_reconocidas}] del
    período, ordenado por costo desc."""
    t = sm.ai_costs
    n = sa.func.count().label("n")
    total = sa.func.coalesce(sa.func.sum(t.c.cost_usd), sa.literal_column("0")).label("total")
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            t.c.operation, n, total,
            sa.func.count().filter(t.c.recognized).label("reconocidas"),
            sa.func.count().filter(t.c.recognized.is_(False)).label("no_reconocidas"),
        ).where(t.c.ts > db_values._days_ago(dias)).group_by(t.c.operation)
            .order_by(total.desc(), n.desc())).fetchall()
        return [{"operacion": r["operation"], "cantidad": r["n"],
                 "costo_usd": float(r["total"] or 0),
                 "reconocidas": r["reconocidas"] or 0,
                 "no_reconocidas": r["no_reconocidas"] or 0} for r in rows]


def costs_daily(dias=30):
    """[{fecha, costo_usd, operaciones}] por día del período, agrupado en hora
    de Buenos Aires fija (mismo criterio que reports_volume)."""
    t = sm.ai_costs
    dia = db_values._local_date(t.c.ts).label("dia")
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            dia,
            sa.func.coalesce(sa.func.sum(t.c.cost_usd), sa.literal_column("0")).label("total"),
            sa.func.count().label("n"),
        ).where(t.c.ts > db_values._days_ago(dias)).group_by(dia).order_by(dia)).fetchall()
        return [{"fecha": r["dia"].isoformat(), "costo_usd": float(r["total"] or 0),
                 "operaciones": r["n"]} for r in rows]


def costs_top_ips(dias=30, limit=15):
    """[{ip, operaciones, costo_usd, no_reconocidas}] top por costo del período.
    costos_ia.py marca cuáles son 'sospechosas' (muchas llamadas, casi todo
    irreconocible) — acá sólo se agregan los números."""
    t = sm.ai_costs
    n = sa.func.count().label("n")
    total = sa.func.coalesce(sa.func.sum(t.c.cost_usd), sa.literal_column("0")).label("total")
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            t.c.ip, n, total,
            sa.func.count().filter(t.c.recognized.is_(False)).label("no_reconocidas"),
        ).where(t.c.ts > db_values._days_ago(dias), t.c.ip.is_not(None)).group_by(t.c.ip)
            .order_by(total.desc(), n.desc()).limit(limit)).fetchall()
        return [{"ip": r["ip"], "operaciones": r["n"], "costo_usd": float(r["total"] or 0),
                 "no_reconocidas": r["no_reconocidas"] or 0} for r in rows]


def ai_costs_has_rows():
    """True si `costos_ia` tiene AL MENOS una fila alguna vez (no acotado a
    ningún período) — reports.py lo usa para decidir si la sección 'Ahorro
    estimado' tiene algo real de qué hablar o si, sobre una base recién
    creada sin una sola llamada de IA todavía, debe degradar a
    {"disponible": false} en vez de mostrar un ROI de puros ceros."""
    with db._connect() as conn, conn.cursor() as cur:
        any_row = sa.select(sa.literal_column("1")).select_from(sm.ai_costs).exists()
        return bool(sqlcore.run(cur, sa.select(any_row.label("existe"))).fetchone()["existe"])


# ---- AI register: no patient or prompt content belongs in this evidence ----
def ai_register_append(events, connection=None):
    if not events:
        return
    with (contextlib.nullcontext(connection) if connection is not None else db._connect()) as conn, conn.cursor() as cur:
        for event in events:
            sqlcore.run(cur, sa.insert(sm.ai_register_changes).values(
                function_id=event['function_id'], kind=event['kind'], actor=event['actor'],
                substantial=event['substantial'], details=event['details'],
                status='pending' if event['substantial'] else 'recorded'))


def ai_register_list_changes():
    t = sm.ai_register_changes
    with db._connect() as conn, conn.cursor() as cur:
        return [dict(row) for row in sqlcore.run(cur, sa.select(t).order_by(t.c.id.desc())).fetchall()]


def ai_register_notify(change_id, reference, notified_on, actor):
    # This is the only attestation writer; appends cannot set these fields.
    actor = str(actor or '').strip()
    if not actor:
        raise ValueError('invalid_actor')
    # Reference is an administrative expediente/receipt identifier, not prose.
    import datetime
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./_-]{1,119}', str(reference or '')):
        raise ValueError('invalid_reference')
    try:
        date = datetime.date.fromisoformat(str(notified_on))
    except (ValueError, TypeError):
        raise ValueError('invalid_date') from None
    from app.core import tz
    if date > datetime.datetime.now(tz.tzinfo()).date():
        raise ValueError('future_date')
    t = sm.ai_register_changes
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).where(
            t.c.id == change_id, t.c.substantial.is_(True), t.c.status == 'pending',
        ).values(status='notified', notification_reference=reference, notified_on=date,
                 notified_by=actor, notified_at=sa.func.now()).returning(t.c.id)
        ).fetchone() is not None
