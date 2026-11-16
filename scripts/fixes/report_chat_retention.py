"""What a chat retention period would delete — read-only dry run.

The hospital decides how long the chat is kept (problems.md №12). The mechanism
(`TURNOS_CHAT_RETENTION_DAYS`, optionally `TURNOS_CHAT_SUMMARY_RETENTION_DAYS`)
ships OFF. This prints how much is stored today and, for each candidate period,
how many messages, conversations, summaries and compactions that period would
delete — counts only, never a message. The pre-consultation interview
transcript is clinical record and is always excluded.

    python3 scripts/fixes/report_chat_retention.py [--days 90 --days 180 ...]
                                                   [--summary-days N] [--json]

Without `--days` it previews 90, 180, 365 and 730 days. Each period applies to
the messages and, unless `--summary-days` is given, to the summaries and
compactions too (the default of the real setting). The connection is opened
read-only and does not go through `db._connect()`, so it can never migrate the
schema. The effective configured plan is printed first.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db  # noqa: E402
from app.core import db_chat  # noqa: E402

_DEFAULT_DAYS = (90, 180, 365, 730)


def report(conn, plazos, summary_days="same"):
    """{configurado, dias_configurados, dias_resumenes_configurados, total, configurado_ahora,
    plazos: {dias: vista previa}} — sólo conteos, sobre la conexión de sólo lectura `conn`."""
    from app.ops import housekeeping
    mensajes, resumenes = housekeeping.chat_retention_days(), housekeeping.chat_summary_retention_days()
    return {
        "configurado": mensajes is not None or resumenes is not None,
        "dias_configurados": mensajes,
        "dias_resumenes_configurados": resumenes,
        "total": db_chat.chat_totals(connection=conn),
        "configurado_ahora": db_chat.chat_retention_preview(mensajes, resumenes, connection=conn),
        "plazos": {str(d): db_chat.chat_retention_preview(d, summary_days, connection=conn) for d in plazos},
    }


def _parse(argv):
    plazos, as_json, summary = [], False, "same"
    it = iter(argv)
    for arg in it:
        if arg == "--json":
            as_json = True
        elif arg == "--days":
            plazos.append(int(next(it)))
        elif arg == "--summary-days":
            summary = int(next(it))
    return (plazos or list(_DEFAULT_DAYS)), summary, as_json


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    plazos, summary, as_json = _parse(argv)
    import psycopg
    from psycopg.rows import dict_row
    url = db._db_url()
    if not url:
        print("DATABASE_URL no configurada", file=sys.stderr)
        return 2
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.read_only = True
        r = report(conn, plazos, summary)
    if as_json:
        print(json.dumps(r, default=str, ensure_ascii=False, indent=2))
        return 0
    t = r["total"]
    print("retención del chat: " + (
        f"ACTIVA, mensajes {r['dias_configurados'] or 'sin plazo'} días, "
        f"resúmenes {r['dias_resumenes_configurados'] or 'sin plazo'} días"
        if r["configurado"] else "apagada (no se borra nada por fecha)"))
    if r["configurado"]:
        v = r["configurado_ahora"]
        print(f"  con ese plan la próxima pasada borraría: {v['mensajes']} mensajes, "
              f"{v['resumenes']} resúmenes, {v['compactaciones']} compactaciones")
    print(f"hoy hay {t['mensajes']} mensajes, {t['resumenes']} resúmenes, {t['compactaciones']} compactaciones")
    for canal, v in t["por_canal"].items():
        print(f"  {canal}: {v['n']} mensajes, el más viejo del {str(v['mas_viejo'])[:10]}")
    if t["resumenes"]:
        print(f"  resúmenes: el más viejo cubre hasta {str(t['resumenes_mas_viejo'])[:10]}")
    for dias, v in r["plazos"].items():
        canales = ", ".join(f"{c} {n}" for c, n in sorted(v["por_canal"].items())) or "—"
        print(f"con {dias} días se borrarían: {v['mensajes']} mensajes en {v['conversaciones']} "
              f"conversaciones ({canales}), {v['resumenes']} resúmenes, {v['compactaciones']} compactaciones"
              f"  [la entrevista previa queda: {v['entrevista_exenta']} mensajes vencidos exentos]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
