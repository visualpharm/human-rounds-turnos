"""core/db_analytics.py — the small operational tables: searches, login-panel events, alert claims.

`log_search` / `top_searches` / `purge_old_searches` (patient search analytics), `log_panel_login` /
`stats_panel_login` / `purge_old_login_panel_events` (the /admin login-gate experiment) and
`claim_daily_alert` / `purge_old_alert_claims` (the at-most-once daily alert). Split out of
`core/db.py` in stage 3 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md): the
statements are Core, built from `core/schema_model.py`.

Callers write `db_analytics.log_search(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below
as `db_values.<name>` and a sibling as `db_x.<name>`, all at CALL time, so a test that patches the owner
patches what every caller uses.
"""

from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import clock
from app.core import db
from app.core import db_values
from app.core import schema_model as sm
from app.core import search_collapse
from app.core import sqlcore


def claim_daily_alert(alert_key, dia):
    """Takes the at-most-once claim for a daily alert (`alert_claims`):
    True if THIS caller just won it, False if it was already taken. The
    caller must claim BEFORE emitting (log/email) — a claim whose send
    then fails stays consumed on purpose: zero or one emission per day,
    never two (housekeeping.check_disk). `dia` is the ISO date in the
    INSTALLATION's timezone (tz.py), never the process's."""
    t = sm.alert_claims
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, _pg.insert(t).values(
            alert_key=alert_key, day=dia,
        ).on_conflict_do_nothing(index_elements=[t.c.alert_key, t.c.day])).rowcount == 1


def purge_old_alert_claims(days=180, limit=2000):
    """Daily-alert claims (`alert_claims`) older than `days` — a consumed
    claim only matters ON its own day (it blocks a same-day duplicate);
    after that it is pure audit residue, and without this purge the table
    grows one row per key per low-disk day forever (adversarial review,
    2026-08-08: unbounded growth in the very state the claim feature
    added). LIMIT-bounded like every other housekeeping purge. The cut-off is
    a calendar day in the INSTALLATION's timezone (`clock.today()`, the same
    zone `claim_daily_alert` was given), bound as a value: SQL
    `current_date` is the database session's day, which between 21:00 and
    24:00 ART is already tomorrow's UTC date."""
    t = sm.alert_claims
    ctid = sa.literal_column("ctid")
    cutoff = clock.today() - timedelta(days=int(days))
    with db._connect() as conn, conn.cursor() as cur:
        stale = sa.select(ctid).select_from(t).where(t.c.day < cutoff).limit(int(limit))
        return sqlcore.run(cur, sa.delete(t).where(ctid.in_(stale))).rowcount


def purge_old_searches(days=180, limit=2000):
    """Analítica cruda del buscador (`searches`) sin valor pasados `days`."""
    t = sm.searches
    with db._connect() as conn, conn.cursor() as cur:
        stale = sa.select(t.c.id).where(t.c.ts < db_values._days_ago(int(days))).limit(int(limit))
        return sqlcore.run(cur, sa.delete(t).where(t.c.id.in_(stale))).rowcount


def purge_old_login_panel_events(days=180, limit=2000):
    """Log del experimento del panel de login (`login_panel_events`), mismo
    contrato que `purge_old_searches`."""
    t = sm.login_panel_events
    with db._connect() as conn, conn.cursor() as cur:
        stale = sa.select(t.c.id).where(t.c.ts < db_values._days_ago(int(days))).limit(int(limit))
        return sqlcore.run(cur, sa.delete(t).where(t.c.id.in_(stale))).rowcount


# ---- búsquedas de pacientes (analytics, busquedas.py) -----------------------
#
# Alimenta el futuro reporte admin "Búsquedas". Sin PII, sin IP guardada: sólo
# la query normalizada, cuántos resultados devolvió y de qué página vino. El
# sitio NUNCA se cae por esto — log_search/top_searches absorben cualquier
# falla de la base (mismo contrato de "nunca romper" que el resto de db.py,
# pero acá explícito porque es la ÚNICA escritura no-crítica del módulo).

def _norm_search(q):
    import re
    return re.sub(r"\s+", " ", str(q or "").strip().lower())


def log_search(q, resultados, origen=None, sid=None):
    """Registra una búsqueda de paciente. q se normaliza (lower/trim/espacios
    colapsados) antes de guardar. `sid` es el id al azar que la página inventa
    por carga (ni persona ni IP): sólo sirve para pegar los prefijos de una
    misma sesión de tipeo en el reporte. Nunca lanza: DB caída -> no-op silencioso."""
    q = _norm_search(q)
    if not q:
        return
    try:
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, sa.insert(sm.searches).values(
                q=q, results=int(resultados or 0), source=(origen or None),
                sid=(sid or None)))
    except Exception:  # noqa: BLE001  (analytics: nunca debe tumbar el request)
        pass


def top_searches(dias=30):
    """[(q, veces, con_resultados)] de los últimos `dias`, agrupado por q
    normalizado y ordenado por veces desc — para el reporte admin. Vacío
    (nunca levanta) si la base no está disponible.

    Typed letter by letter, a search lands as «ped», «pedi», «pediat»: within
    ONE session (sid) a row that the NEXT one extends, logged within
    search_collapse.GLUE_SECONDS, is a fragment and does not count (the same
    rule as the stream report, core/search_collapse.py). It is applied in SQL
    (lead over the session), so only the grouped result reaches the process.
    Rows without a sid (older clients) are never glued."""
    try:
        t = sm.searches
        desde = db_values._days_ago(dias)
        order = (t.c.ts, t.c.id)
        nxt_q = sa.func.lead(t.c.q).over(partition_by=t.c.sid, order_by=order)
        nxt_ts = sa.func.lead(t.c.ts).over(partition_by=t.c.sid, order_by=order)
        w = sa.select(t.c.q, t.c.results, t.c.sid, t.c.ts, nxt_q.label("nq"),
                      nxt_ts.label("nts")).where(t.c.ts > desde).subquery("w")
        fragment = sa.and_(
            w.c.sid.isnot(None), w.c.nq.isnot(None),
            sa.func.starts_with(w.c.nq, w.c.q),
            w.c.nts - w.c.ts <= timedelta(seconds=search_collapse.GLUE_SECONDS))
        veces = sa.func.count().label("veces")
        stmt = (sa.select(w.c.q, veces, sa.func.count().filter(w.c.results > 0).label("con_resultados"))
                .where(sa.not_(fragment)).group_by(w.c.q)
                .order_by(veces.desc(), w.c.q))
        with db._connect() as conn, conn.cursor() as cur:
            return [(r["q"], r["veces"], r["con_resultados"])
                    for r in sqlcore.run(cur, stmt).fetchall()]
    except Exception:  # noqa: BLE001  (analytics: nunca debe tumbar el request)
        return []


# ---- panel de login (panel_login.py) ----------------------------------------

def log_panel_login(sid, variante, evento, tab=None, item=None, ms=None):
    """Registra un evento del panel del gate de /admin. Mismo contrato que
    log_search: analytics, DB caída -> no-op silencioso, nunca lanza."""
    try:
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, sa.insert(sm.login_panel_events).values(
                sid=sid, variant=variante, event=evento, tab=tab, item=item, ms=ms))
    except Exception:  # noqa: BLE001  (analytics: nunca debe tumbar el request)
        pass


def stats_panel_login(dias=30):
    """Agregados del login del personal para el reporte admin. A diferencia
    de las escrituras, las LECTURAS dejan propagar DbUnavailable (reports.py
    lo traduce a 503, mismo criterio que el resto de los reportes).

    El experimento del gate (memes vs consejos) se cerró el 19.07; lo que
    queda del beacon es la fricción del login. Una SESIÓN es un sid con un
    intento (login_ok o login_error); la exposición del gate no cuenta.

    Devuelve:
      sesiones            sesiones DISTINTAS con intento de login
      logins              sesiones distintas que entraron (login_ok)
      errores             eventos login_error (reintentos incluidos)
      sesiones_con_error  sesiones distintas que chocaron con un error
      ms_mediana_login    mediana de los ms de login_ok (None si no hay)
    """
    t = sm.login_panel_events
    intento = t.c.event.in_([sa.literal_column("'login_ok'"),
                             sa.literal_column("'login_error'")])
    with db._connect() as conn, conn.cursor() as cur:
        r = sqlcore.run(cur, sa.select(
            sa.func.count(sa.distinct(t.c.sid)).filter(intento).label("sesiones"),
            sa.func.count(sa.distinct(t.c.sid)).filter(
                t.c.event == sa.literal_column("'login_ok'")).label("logins"),
            sa.func.count().filter(
                t.c.event == sa.literal_column("'login_error'")).label("errores"),
            sa.func.count(sa.distinct(t.c.sid)).filter(
                t.c.event == sa.literal_column("'login_error'")).label("sesiones_con_error"),
            sa.func.percentile_cont(sa.literal_column("0.5")).within_group(t.c.ms).filter(
                sa.and_(t.c.event == sa.literal_column("'login_ok'"), t.c.ms.is_not(None))
            ).label("ms_mediana_login"),
        ).where(t.c.ts > db_values._days_ago(dias))).fetchone()
    return {
        "sesiones": r["sesiones"] or 0,
        "logins": r["logins"] or 0,
        "errores": r["errores"] or 0,
        "sesiones_con_error": r["sesiones_con_error"] or 0,
        "ms_mediana_login": float(r["ms_mediana_login"]) if r["ms_mediana_login"] is not None else None,
    }
