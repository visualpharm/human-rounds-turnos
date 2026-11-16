"""printable.py — receta y orden imprimibles de una consulta (HTML, no PDF).

Rutas (fase 5; el token opaco ES la autorización, mismo modelo de confianza
que /appointment/offer/<token>):
  GET /r/<receta_token>   receta cuadruplicada (grilla 2x2 con líneas de
                          corte: Paciente/Farmacia/Obra social/Archivo)
  GET /o/<orden_token>    orden de estudios (Original/Duplicado apilados)

Renderiza el contenido YA compuesto y guardado en la fila `consultations`
(receta_json/orden_json) — acá nunca se llama a la IA ni al catálogo;
imprimir es instantáneo y no puede fallar por el modelo. Un token de receta
NUNCA sirve /o/ y viceversa, aunque los dos vivan en la misma fila
(`_consulta_para` chequea que el campo correcto matcheó).

`@page{size:A4;margin:0}` + `@media print` esconden la barra de "Imprimir"
— el layout de las copias (grilla 2x2 / dos apiladas con líneas de corte
punteadas) está portado de <workspace>/sana/app/server/pdf/render.mjs
(pdfkit) a HTML+CSS puro, sin dependencias nuevas."""

import re
from datetime import datetime, timedelta, timezone

from app.core import clock
from app.core import db
from app.core import db_clinical
from app.patient_web import site_base

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_COPIAS_RECETA = ["Paciente", "Farmacia", "Obra social", "Archivo"]
_COPIAS_ORDEN = ["Original", "Duplicado"]


def _consulta_para(token, kind):
    if not token or not _TOKEN_RE.match(token):
        return None
    try:
        row = db_clinical.consultation_by_token(token)
    except db.DbUnavailable:
        return None
    if not row:
        return None
    campo = "receta_token" if kind == "receta" else "orden_token"
    if str(row.get(campo) or "") != token:
        return None
    if _vencido(row):
        return None
    return row


# A link is a bearer credential to a patient's prescription: it stops opening
# after this many days (security review 2026-11, L-1). The consultation itself
# stays in the clinical record; staff reissue a fresh document from it.
LINK_DAYS = 365


def _vencido(row):
    created = row.get("created")
    if not isinstance(created, datetime):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return clock.utcnow() - created > timedelta(days=LINK_DAYS)


def payload_de(row, kind):
    """El receta_json/orden_json guardado, con `anulada: True` si la consulta
    fue reemplazada (`superseded_at`) — el imprimible lo marca ANULADA."""
    payload = row.get(f"{kind}_json")
    payload = dict(payload) if isinstance(payload, dict) else {}
    if row.get("superseded_at"):
        payload["anulada"] = True
    return payload


def reemplazo_de(row, kind):
    """Para una consulta reemplazada: {"url", "fecha"} del documento del mismo
    tipo que hoy la reemplaza (`/r/<token>` o `/o/<token>`), o None si no
    está reemplazada o el reemplazo no trae ese documento. Sólo lectura."""
    if not row or not row.get("superseded_at"):
        return None
    try:
        nueva = db_clinical.consultation_replacement_of(row)
    except db.DbUnavailable:
        return None
    token = (nueva or {}).get(f"{kind}_token")
    if not token:
        return None
    payload = nueva.get(f"{kind}_json")
    fecha = payload.get("fecha") if isinstance(payload, dict) else None
    return {"url": ("/r/" if kind == "receta" else "/o/") + str(token),
            "fecha": str(fecha or "")}


# ---- layout compartido -----------------------------------------------------

def _espacio(payload):
    try:
        v = float(payload.get("espacio_superior_cm") or 0)
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, min(10.0, v))


def _base_css():
    return """
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:#eef1f4;}
body{font-family:Arial,Helvetica,sans-serif;color:#0b2231;}
.print-bar{position:sticky;top:0;background:#0b2231;color:#fff;padding:10px 16px;
  display:flex;justify-content:flex-end;gap:10px;z-index:9;}
.print-bar button{font:600 13px Arial,sans-serif;padding:8px 16px;border:0;border-radius:6px;
  background:#fff;color:#0b2231;cursor:pointer;}
.hoja-wrap{display:flex;justify-content:center;padding:18px 0 40px;}
.hoja{width:210mm;min-height:297mm;background:#fff;box-shadow:0 4px 24px rgba(0,0,0,.15);}
.hoja-receta{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;}
.hoja-receta .copia:nth-child(1){border-right:1px dashed #c2ccd6;border-bottom:1px dashed #c2ccd6;}
.hoja-receta .copia:nth-child(2){border-bottom:1px dashed #c2ccd6;}
.hoja-receta .copia:nth-child(3){border-right:1px dashed #c2ccd6;}
.hoja-orden{display:flex;flex-direction:column;}
.hoja-orden .copia{flex:1;}
.hoja-orden .copia:first-child{border-bottom:1px dashed #c2ccd6;}
.copia{padding:10mm;display:flex;flex-direction:column;position:relative;}
.copia-label{font:700 13px/1 Arial;letter-spacing:.06em;text-transform:uppercase;color:#9aa6b2;text-align:right;}
.titulo-inst{font:700 13px/1.3 Arial;margin:2px 0 0;}
.titulo-doc{display:flex;justify-content:space-between;align-items:baseline;font:700 13px Arial;margin:1px 0 0;}
.fecha{font:400 13px Arial;color:#55606c;}
.divisor{border:0;border-top:1px solid #d7dee6;margin:6px 0;}
.medico{font:700 13px/1.4 Arial;margin:0;}
.medico small{font-weight:400;color:#55606c;}
.paciente-nombre{font:700 13px/1.4 Arial;margin:8px 0 0;}
.paciente-meta{font:13px/1.4 Arial;color:#55606c;margin:1px 0 0;}
.rp{font:700 13px Arial;margin:12px 0 3px;}
.lineas{margin:0;padding:0 0 0 16px;font:13px/1.6 Arial;}
.lineas--bullet{list-style:disc;}
.lineas li{margin:0 0 3px;}
.vacio{color:#9aa6b2;font-style:italic;font:13px Arial;margin:2px 0;}
.dx-titulo{font:700 13px Arial;margin:10px 0 3px;}
.notas{font:13px/1.4 Arial;color:#55606c;font-style:italic;margin:10px 0 0;}
.firma{margin-top:auto;padding-top:18px;}
.firma-linea{border-top:1px solid #0b2231;width:60%;margin-left:auto;}
.firma-texto{font:13px Arial;color:#55606c;text-align:center;width:60%;margin-left:auto;padding-top:3px;}
.pie{font:13px Arial;color:#9aa6b2;text-align:center;margin-top:8px;}
@media print{
  .print-bar{display:none;}
  body{background:#fff;}
  .hoja-wrap{padding:0;}
  .hoja{box-shadow:none;}
}
"""


def _print_bar():
    return '<div class="print-bar"><button type="button" onclick="window.print()">Imprimir</button></div>'


def _page(title, hoja_class, copias_html):
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{site_base._esc(title)}</title>
<style>
@page {{ size: A4; margin: 0; }}
{_base_css()}
</style>
</head>
<body>
{_print_bar()}
<div class="hoja-wrap"><div class="hoja {hoja_class}">{copias_html}</div></div>
</body>
</html>"""


def _masthead(payload, doc_label, label):
    if payload.get("anulada") is True:
        doc_label = "ANULADA — REEMPLAZADA · " + doc_label
    elif payload.get("borrador") is True:
        doc_label = "BORRADOR — REVISAR · " + doc_label
    medico = payload.get("medico") or {}
    paciente = payload.get("paciente") or {}
    detalle = "  ·  ".join(x for x in [
        str(medico.get("especialidad") or ""),
        (f"Mat. {medico.get('matricula')}" if medico.get("matricula") else ""),
    ] if x)
    ident = []
    if paciente.get("dni"):
        ident.append(f"DNI {site_base._esc(paciente.get('dni'))}")
    medico_html = site_base._esc(medico.get("nombre") or "")
    if detalle:
        medico_html += f"<small> — {site_base._esc(detalle)}</small>"
    ident_html = f'<p class="paciente-meta">{" · ".join(ident)}</p>' if ident else ""
    return f"""
      <div class="copia-label">{site_base._esc(label)}</div>
      <div class="titulo-inst">{site_base._esc(payload.get("titulo") or "")}</div>
      <div class="titulo-doc"><span>{site_base._esc(doc_label)}</span><span class="fecha">Fecha: {site_base._esc(payload.get("fecha") or "")}</span></div>
      <hr class="divisor">
      <p class="medico">{medico_html}</p>
      <p class="paciente-nombre">{site_base._esc(paciente.get("nombre") or "")}</p>
      {ident_html}
"""


def _firma_pie(payload):
    return f"""
      <div class="firma"><div class="firma-linea"></div><div class="firma-texto">Firma y sello</div></div>
      <div class="pie">{site_base._esc(payload.get("pie") or "")}</div>
"""


# ---- receta -----------------------------------------------------------------

def _receta_copia(payload, label):
    lineas = payload.get("lineas") or []
    if lineas and isinstance(lineas, list):
        items = "".join(f"<li>{site_base._esc(l)}</li>" for l in lineas)
        cuerpo = f'<p class="rp">Rp/</p><ol class="lineas">{items}</ol>'
    else:
        cuerpo = '<p class="rp">Rp/</p><p class="vacio">(sin medicación)</p>'
    notas = payload.get("notas")
    notas_html = f'<p class="notas">Indicaciones: {site_base._esc(notas)}</p>' if notas else ""
    esp = _espacio(payload)
    return f"""<div class="copia" style="padding-top:calc(10mm + {esp}cm)">
      {_masthead(payload, "RECETA", label)}
      {cuerpo}
      {notas_html}
      {_firma_pie(payload)}
    </div>"""


def _receta_html(payload):
    copias = "".join(_receta_copia(payload, label) for label in _COPIAS_RECETA)
    return _page("Receta", "hoja-receta", copias)


# ---- orden --------------------------------------------------------------------

def _orden_copia(payload, label):
    estudios = payload.get("estudios") or []
    if estudios and isinstance(estudios, list):
        items = "".join(f"<li>{site_base._esc(e)}</li>" for e in estudios)
        cuerpo = f'<p class="rp">Solicito:</p><ul class="lineas lineas--bullet">{items}</ul>'
    else:
        cuerpo = '<p class="rp">Solicito:</p><p class="vacio">(sin estudios)</p>'
    diagnosticos = payload.get("diagnosticos") or []
    dx_html = ""
    if diagnosticos and isinstance(diagnosticos, list):
        items = "".join(f"<li>{site_base._esc(d)}</li>" for d in diagnosticos)
        dx_html = f'<p class="dx-titulo">Diagnóstico presuntivo</p><ul class="lineas lineas--bullet">{items}</ul>'
    notas = payload.get("notas")
    notas_html = f'<p class="notas">Indicaciones: {site_base._esc(notas)}</p>' if notas else ""
    esp = _espacio(payload)
    return f"""<div class="copia" style="padding-top:calc(10mm + {esp}cm)">
      {_masthead(payload, "ORDEN DE ESTUDIOS", label)}
      {cuerpo}
      {dx_html}
      {notas_html}
      {_firma_pie(payload)}
    </div>"""


def _orden_html(payload):
    copias = "".join(_orden_copia(payload, label) for label in _COPIAS_ORDEN)
    return _page("Orden de estudios", "hoja-orden", copias)


def _not_found_html():
    return """<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>Documento no encontrado</title>
<style>body{font-family:Arial,Helvetica,sans-serif;padding:40px;color:#0b2231;}</style></head>
<body><h1>Documento no encontrado</h1>
<p>Este link no corresponde a una receta u orden vigente.</p></body>
</html>"""
