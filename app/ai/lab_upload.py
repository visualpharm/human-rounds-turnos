"""lab_upload.py — transcripción del resultado de laboratorio que sube el
PROPIO paciente desde su cuenta (docs/architecture/laboratorio-paciente.md,
«Resultados aportados por el paciente»).

Una sola lectura barata, exactamente el mismo camino de modelo que usa la
orden de derivación (`ai_referral.read_studies`): normalizar la imagen (o
convertir la primera página del PDF), armar el contenido multimodal y pedirle
al modelo UN JSON. Acá el pedido es más chico todavía: transcribir, no
interpretar — la lectura clínica (tipo de estudio, resumen, sensibilidad,
determinaciones) ya la hace `lab_analysis.py` sobre el texto, igual que con
cualquier otra entrada de la historia.

Sin IA (TURNOS_FAKE_AI=1, sin clave, o el modelo falla): texto vacío y
`sin_lectura=True`, para que la cuenta diga que el archivo se va a leer más
tarde en vez de mentir con un informe vacío.

El archivo del paciente viaja TAL CUAL al proveedor de IA, igual que la
imagen de la orden de derivación — es la excepción deliberada de `pii.py`
documentada en docs/architecture/legal.md.
"""
from __future__ import annotations

import base64
import os
import re
import tempfile
from datetime import date

from app.ai import ai_referral

_PROMPT = (
    "Transcribí este resultado de laboratorio. Copiá TODAS las "
    "determinaciones con su valor, su unidad y su rango de referencia, tal "
    "como figuran en el papel, y la fecha del informe. No interpretes, no "
    "resumas y no agregues nada que no esté escrito. Respondé sólo con JSON: "
    '{"texto": "<la transcripción completa>", "fecha": "AAAA-MM-DD o null"}'
)

_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sin_lectura(modelo=None):
    return {"texto": "", "fecha": None, "modelo": modelo, "sin_lectura": True}


def _fecha_ok(valor):
    """El día del informe si es un día real (AAAA-MM-DD); si no, None — y la
    subida usa entonces el día en que se hizo (`lab_results._hoy`)."""
    valor = str(valor or "").strip()[:10]
    if not _FECHA_RE.match(valor):
        return None
    try:
        date.fromisoformat(valor)
    except ValueError:
        return None
    return valor


def leer_resultado(archivo_bytes, mime=None, filename=None):
    """{"texto", "fecha", "modelo", "sin_lectura"} de un informe subido.

    Nunca levanta: cualquier problema es "todavía no lo leímos", nunca un
    error en la cara del paciente que acaba de subir su análisis."""
    if not archivo_bytes:
        return _sin_lectura()
    if ai_referral._env("TURNOS_FAKE_AI") == "1" or not ai_referral._any_api_key():
        return _sin_lectura("fake" if ai_referral._env("TURNOS_FAKE_AI") == "1"
                            else "sin_clave_api")

    mime = mime or "image/jpeg"
    ext = ai_referral._ext_for(archivo_bytes, mime, filename)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}")
        with os.fdopen(fd, "wb") as f:
            f.write(archivo_bytes)
        path = tmp_path
        if "pdf" in mime.lower():
            png = ai_referral._pdf_to_png(path)
            if png:
                path, mime = png, "image/png"
        raw, mime, _meta = ai_referral._image_for_model(path, mime)
    except (OSError, ValueError):
        return _sin_lectura()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    content = ai_referral._build_content(
        _PROMPT, data_uri, mime, filename or f"laboratorio.{ext}")

    model = ai_referral._model()
    leido, _costo, _svc = ai_referral._read_once(content, model, None, 0.1)
    if not leido:
        fb = ai_referral._fallback_model()
        if fb and fb != model:
            leido, _costo, _svc = ai_referral._read_once(content, fb, None, 0.1)
            model = fb
    if not isinstance(leido, dict):
        return _sin_lectura(model)

    texto = " ".join(str(leido.get("texto") or "").split())
    if not texto:
        return _sin_lectura(model)
    return {"texto": texto, "fecha": _fecha_ok(leido.get("fecha")),
            "modelo": model, "sin_lectura": False}
