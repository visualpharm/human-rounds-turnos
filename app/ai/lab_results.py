"""lab_results.py — «Resultados de laboratorio» en la cuenta del paciente
(rediseño 2026-08-06, docs/architecture/laboratorio-paciente.md).

El resumen del médico (chart_summary.py) ya sabe leer la historia clínica
digitalizada y clasificar sus entradas; acá se le da al PACIENTE una vista
propia de la misma fuente, filtrada a lo que le importa a él: sus estudios de
laboratorio. No inventa una segunda taxonomía — reusa
`chart_summary._historia_clinica` (los mismos tres conectores: local_history,
alepo, marqui).

La lectura por informe (tipo de estudio, resumen cauteloso, sensibilidad,
extracción de determinaciones catalogadas y no catalogadas) es trabajo de
`lab_analysis.py` — un pase de IA por lote, cacheado, con un piso de
sensibilidad no editable. Este módulo sólo arma la vista para el paciente a
partir de lo que ese motor ya resolvió: TODO informe de laboratorio se
muestra (catalogado o no); uno marcado sensible se redacta del lado del
servidor, nunca con CSS — sus valores, tipo y resumen nunca cruzan el cable.

Identidad SIEMPRE de la sesión (accounts.current_user) — nunca de un DNI en
la query string, para que un paciente no pueda pedir la historia de otro.

  GET /api/account/lab-results
"""
from __future__ import annotations

import sys

from app.ai import lab_analysis
from app.ai import lab_language
from app.core import i18n
from app.core import log_pii
from app.patient_web import site_availability, site_catalog


def _log(msg):
    print(f"[lab_results] {msg}", file=sys.stderr, flush=True)

_ROUTE = "/api/account/lab-results"

# Mismos topes que la orden de derivación (modules/turnos/booking_requests.py):
# ~25 MB y la MISMA allowlist de formatos — un resultado de laboratorio llega
# por la misma vía (foto del celular o PDF del laboratorio) y no hay razón
# para que la cuenta acepte algo distinto que el wizard.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_MIMES_OK = ("image/png", "image/jpeg", "image/jpg", "image/webp",
             "image/heic", "image/gif", "application/pdf")


def _digits(v):
    return "".join(c for c in str(v or "") if c.isdigit())


def subir_resultado(user, archivo_bytes, mime=None, filename=None,
                    paciente_dni=None):
    """El paciente sube un resultado propio. (status, body).

    La identidad sale SIEMPRE de la sesión: `paciente_dni` sólo puede ser una
    persona a cargo ya vinculada a esa cuenta (mismo criterio que la
    solicitud de turno), nunca un DNI cualquiera.

    El archivo viaja tal cual al proveedor de IA para transcribirlo — la
    misma excepción deliberada de `pii.py` que ya tiene la imagen de la orden
    de derivación (docs/architecture/legal.md)."""
    from app.core import db_clinical
    from app.core import db_slots

    solicitante = _digits(user.get("dni"))
    if not solicitante:
        return 401, {"error": "auth_requerida"}

    dni = solicitante
    pedido = _digits(paciente_dni)
    if pedido and pedido != solicitante:
        if not db_slots.get_dependent(solicitante, pedido):
            return 403, {"error": "paciente_no_vinculado"}
        dni = pedido

    if not archivo_bytes:
        return 400, {"error": "falta_archivo"}
    if len(archivo_bytes) > MAX_UPLOAD_BYTES:
        return 413, {"error": "archivo_muy_pesado"}
    if (mime or "").lower() not in _MIMES_OK:
        return 415, {"error": "formato_no_soportado"}

    from app.ai import lab_upload
    lectura = lab_upload.leer_resultado(archivo_bytes, mime, filename)
    fecha = lectura.get("fecha") or _hoy()
    upload_id = db_clinical.patient_lab_upload_insert(
        dni, solicitante, filename=filename, mime=mime, archivo=archivo_bytes,
        texto=lectura.get("texto") or None, fecha=fecha,
        sin_lectura=bool(lectura.get("sin_lectura")))
    return 200, {"ok": True, "id": upload_id,
                 "sin_lectura": bool(lectura.get("sin_lectura"))}


def entrada_de_subida(fila):
    """La entrada normalizada de una fila de `patient_lab_uploads` — la misma
    que el resumen y esta vista hashean (`lab_analysis.report_hash`)."""
    from app.connectors.patient_uploads import patient_uploads
    return patient_uploads.entry_for(fila)


def borrar_resultado(user, upload_id):
    """Borra un resultado subido — sólo quien lo subió. Se vacían el archivo,
    su transcripción y su nombre (`db_clinical.patient_lab_upload_delete`) y, con ellos,
    la lectura de IA cacheada de ese informe: su resumen y sus valores son el
    mismo dato clínico que el archivo (revisión de esquema №11)."""
    from app.core import db_clinical

    solicitante = _digits(user.get("dni"))
    if not solicitante:
        return 401, {"error": "auth_requerida"}
    fila = db_clinical.patient_lab_upload_get(upload_id, solicitante)
    if not fila or not db_clinical.patient_lab_upload_delete(upload_id, solicitante):
        return 404, {"error": "no_encontrado"}
    try:
        rhash = lab_analysis.report_hash(entrada_de_subida(fila))
        db_clinical.lab_analysis_delete(fila["dni"], rhash)
        lab_language.olvidar(rhash)   # y sus traducciones
    except Exception as e:  # noqa: BLE001 — el archivo ya se borró; el resto se reintenta con la caché vencida
        _log(f"no se pudo borrar la lectura cacheada del resultado {fila['id']}: {log_pii.exc(e)}")
    return 200, {"ok": True}


def _hoy():
    """La fecha del informe cuando el papel no la dice: el día en que se
    subió, en la zona de la instalación."""
    from app.core import tz as _tz
    from datetime import datetime
    return datetime.now(_tz.tzinfo()).date().isoformat()


def _clinico_href():
    """Link to 'a clinician' for the footer note under an out-of-range
    value — reuses the SAME lookup patient_web/site_*.py already does for the
    upload-order footer (there it's center-scoped; here there's no specific
    center, just the general-medicine cluster), so both surfaces point at
    one real booking route instead of two independent guesses. `/doctors`
    (the full directory) is the fallback for a pack with no clinical service
    configured at all — never a dead '#'."""
    gen = next((x for x in site_catalog._services() if site_availability._is_clinical(x)), None)
    if gen and gen.get("cluster"):
        return f'/d/{gen["cluster"]}'
    return "/doctors"


def informes_de(dni, *, sync=False):
    """Los informes de laboratorio del DNI, más nuevo primero, más la serie
    graficable y cuántos informes todavía esperan su lectura de IA.

    Devuelve (informes, fuentes_estado, series, analisis_pendiente). Un
    informe sensible sale como {"fecha", "sensible": true} y NADA más — la
    redacción es server-side, nunca CSS (docs/architecture/
    laboratorio-paciente.md, «Robustness evaluation» ítem 3).

    `sync=False` (default del GET): no espera al modelo — fallback YA + pase
    de IA en fondo. `sync=True` bloquea hasta el tope de lote (tests)."""
    dni = "".join(c for c in str(dni or "") if c.isdigit())
    if not dni:
        return [], {}, [], 0

    resueltos, pendiente, estados = lab_analysis.analizar(dni, sync=sync)
    lang = i18n.active()

    informes = []
    for entrada, fila in resueltos:
        propio = entrada.get("sistema") == "patient_uploads"
        if fila.get("sensible"):
            informes.append({"fecha": entrada.get("fecha"), "sensible": True,
                             "tercero": bool(entrada.get("tercero"))})
            continue
        # La lectura se guarda en castellano; el idioma de quien mira se
        # resuelve acá, al mostrar (ai/lab_language.py).
        tipo, resumen = lab_language.textos(entrada, fila, lang)
        informes.append({
            # Lo subió el propio paciente (conector patient_uploads): la
            # cuenta lo dice, y el resumen del médico lo lee sin verificar.
            "tercero": bool(entrada.get("tercero")),
            "upload_id": entrada.get("id") if propio else None,
            "fecha": entrada.get("fecha"),
            "tipo_estudio": tipo,
            "resumen": resumen,
            "tono": fila.get("tono"),
            "servicio": str(entrada.get("servicio") or "Laboratorio").strip(),
            "items": lab_language.items_en(lab_analysis.merge_valores(entrada, fila), lang),
        })
    informes.sort(key=lambda i: str(i.get("fecha") or ""), reverse=True)

    series = lab_language.series_en(lab_analysis.series_de(resueltos), lang)
    return informes, estados, series, pendiente
