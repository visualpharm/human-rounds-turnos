"""Public health-data page shared by the legacy and native transports.

``/datos`` is platform-core: it remains available when the appointment
module is disabled.  The source cartilla is an optional sibling-project
snapshot, so a missing or unreadable file degrades to empty charts exactly as
the legacy server did.
"""

import json
import os
import threading
from collections import Counter

from app.core import site_identity, static_fingerprint, theme
from app.core import paths
from app.patient_web import availability_directory, seo


_PROJECT_ROOT = paths.PROJECT_ROOT
_CARTILLA_PATH = os.path.join(
    _PROJECT_ROOT, "..", "salud-pinamar", "cartilla-2026-05-14.json"
)

_cartilla_cache = None
_cartilla_lock = threading.Lock()


def _esc(value):
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _public_shell(title, desc, canonical_path, section, body, extra_css=""):
    return ("""<!DOCTYPE html>
<html lang="es" data-theme=\"""" + theme.current() + """\">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
""" + theme.favicon() + """
""" + seo.head(title, desc, path=canonical_path) + """
""" + theme.fonts() + """
<link rel="stylesheet" href=\"""" + static_fingerprint.url("human-rounds.css") + """\">
<style>
  /* tokens compartidos → /human-rounds.css */
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 var(--font);}
  .wrap{max-width:1000px;margin:0 auto;padding:26px 24px 80px;}
  .pin-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;}
  .pin-card{display:flex;flex-direction:column;gap:5px;background:var(--panel);border:1px solid var(--border);border-radius:13px;
    padding:17px 18px;text-decoration:none;color:inherit;transition:border-color .14s,box-shadow .14s,transform .1s;}
  .pin-card:hover{border-color:var(--hover-bd);box-shadow:0 10px 26px -16px rgba(40,30,20,.26);}
  .pin-card:active{transform:translateY(1px);}
  .pin-rate{font:600 13px/1 var(--font-mono);color:var(--warn);white-space:nowrap;}
  .pin-meta{font-size:13px;color:var(--text-3);}
  .footlink{color:var(--text);text-decoration:none;font-size:16px;}
  .footlink:hover{color:var(--hover-ink);}
  @media(max-width:560px){.wrap{padding:22px 16px 60px;}}
""" + extra_css + """
</style>
</head>
<body>
<div class="wrap">
  <header id="stznav" data-section=\"""" + _esc(section) + """\"></header>
  <script type='module' src='""" + static_fingerprint.url("platform/nav.mjs") + """'></script>
""" + body + """
  <footer style="border-top:1px solid var(--border);padding-top:22px;margin-top:42px;display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between;">
    <div style="display:flex;align-items:center;gap:16px;">
      <a class="footlink" href="/privacidad.html">Privacidad</a>
      <a class="footlink" href="/terminos.html">Términos y condiciones</a>
      <a class="footlink" href="/datos">Datos de salud</a>
    </div>
    <span style="font-size:16px;color:var(--foot-ink-2,var(--text));">Proyecto independiente, gratuito y sin fines de lucro · """ + _esc(site_identity.get("city")) + """</span>
  </footer>
</div>
</body>
</html>""").encode("utf-8")


def _load_cartilla():
    global _cartilla_cache
    with _cartilla_lock:
        if _cartilla_cache is not None:
            return _cartilla_cache
        try:
            with open(_CARTILLA_PATH, encoding="utf-8") as stream:
                data = json.load(stream)
            _cartilla_cache = data.get("cartilla") or []
        except (OSError, json.JSONDecodeError):
            _cartilla_cache = []
        return _cartilla_cache


def _bar_chart(rows, max_val, accent="var(--accent)"):
    """Return the horizontal bar-chart markup for ``(label, count)`` rows."""
    items = []
    for label, count in rows:
        pct = round(count / max_val * 100) if max_val else 0
        items.append(
            f'<div style="display:grid;grid-template-columns:1fr auto;align-items:center;gap:8px;margin-bottom:9px;">'
            f'<div style="position:relative;height:26px;background:var(--bg-2);border-radius:6px;overflow:hidden;">'
            f'<div style="position:absolute;inset:0 auto 0 0;width:{pct}%;background:{accent};border-radius:6px;'
            f'transition:width .3s;"></div>'
            f'<span style="position:absolute;left:10px;top:50%;transform:translateY(-50%);font-size:13px;'
            f'font-weight:500;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
            f'max-width:calc(100% - 14px);line-height:1;">{_esc(label)}</span>'
            f'</div>'
            f'<span style="font:600 13px/26px \'Ubuntu Mono\',monospace;color:var(--text-2);'
            f'white-space:nowrap;min-width:28px;text-align:right;">{count}</span>'
            f'</div>'
        )
    return "".join(items)


def render_data_page():
    loc = site_identity.get("city")
    cartilla = _load_cartilla()

    esp_count = Counter(entry.get("esp", "") for entry in cartilla if entry.get("esp"))
    top_esp = esp_count.most_common(15)
    max_esp = top_esp[0][1] if top_esp else 1
    chart1 = _bar_chart(top_esp, max_esp)

    obra_count = Counter()
    for entry in cartilla:
        for coverage in entry.get("obsoc") or []:
            name = (coverage.get("nombre") or "").strip()
            if name and name not in ("Particulares", "--Todas--"):
                obra_count[name] += 1
    top_obras = obra_count.most_common(12)
    max_obras = top_obras[0][1] if top_obras else 1
    chart2 = _bar_chart(top_obras, max_obras, "var(--ok,#3ecf8e)")

    tree = availability_directory.especialidades_tree()
    groups = tree.get("groups") or []
    turno_rows = [(group["name"], group["count"]) for group in groups if group.get("count", 0) > 0]
    turno_rows.sort(key=lambda item: -item[1])
    turno_rows = turno_rows[:15]
    max_turno = turno_rows[0][1] if turno_rows else 1
    con_online = tree.get("con_online", 0)
    chart3 = _bar_chart(turno_rows, max_turno, "var(--warn,#f59e0b)")

    datos_css = """
  .datos-section{margin-bottom:44px;}
  .datos-section h2{font-size:18px;font-weight:700;letter-spacing:-.02em;margin:0 0 5px;}
  .datos-section p.sub{font-size:13.5px;color:var(--text-2);margin:0 0 18px;line-height:1.4;}
  .datos-card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:20px 22px;}
  .stat-pill{display:inline-block;background:var(--bg-2);border-radius:8px;padding:4px 11px;
    font:600 13px/1.5 var(--font-mono);color:var(--text-2);margin-right:8px;margin-bottom:6px;}
  @media(max-width:767px){
    .wrap{padding-left:0!important;padding-right:0!important;}
    .datos-card{border-left:none;border-right:none;border-radius:0;padding:18px 16px;}
    .wrap>h1,.wrap>p,.datos-section>h2,.datos-section>p.sub,body>.wrap>footer{padding-left:16px;padding-right:16px;}
  }
"""

    body = f"""
<h1 style="margin:0 0 7px;font-size:27px;font-weight:700;letter-spacing:-.03em;">Datos de la salud en {_esc(loc)}</h1>
<p style="margin:0 0 30px;font-size:15px;color:var(--text-2);line-height:1.5;max-width:620px;text-wrap:pretty;">
Estadísticas del sistema de salud de {_esc(loc)} y la zona, para pacientes y médicos.</p>

<div class="datos-section">
  <h2>Médicos por especialidad</h2>
  <p class="sub">442 profesionales registrados en la cartilla de obras sociales de {_esc(loc)} · Top 15 especialidades.</p>
  <div class="datos-card">{chart1}</div>
</div>

<div class="datos-section">
  <h2>Cobertura por obra social</h2>
  <p class="sub">Cuántos médicos de la cartilla aceptan cada cobertura · Top 12 obras sociales y prepagas.</p>
  <div class="datos-card">{chart2}</div>
</div>

<div class="datos-section">
  <h2>Médicos con turno online por especialidad</h2>
  <p class="sub">{con_online} médicos de {_esc(loc)} con turnos online disponibles en {theme.brand_name()} · Top 15 grupos.</p>
  <div class="datos-card">{chart3}</div>
</div>

"""
    return _public_shell(
        f"Datos de salud en {loc} — estadísticas médicas | {theme.brand_name()}",
        f"Estadísticas del sistema de salud de {loc}: especialidades, obras sociales y médicos con turno online.",
        "/datos", "datos", body, extra_css=datos_css,
    )
