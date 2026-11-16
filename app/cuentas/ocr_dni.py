"""ocr_dni.py — leer los datos del DNI de una FOTO (frente y dorso) con IA.

En el registro (`/cuenta`) el paciente puede sacarle una foto al DNI en vez de
tipear todo. El cliente (account-form.js) intenta primero leer el código de
barras PDF417 del frente EN EL NAVEGADOR (zxing-wasm, gratis, sin red); si no lo
logra, o para el DORSO (donde vive el domicilio, que NO está en el código),
sube la foto acá y un modelo multimodal lee los campos impresos.

Por qué el dorso siempre pasa por IA: el PDF417 del DNI argentino sólo lleva
DNI, nombre, apellido, sexo y fecha de nacimiento. El DOMICILIO está impreso
únicamente en el dorso — no hay código que leer, así que la única vía es OCR.

  POST /api/ocr-dni  {imageDataUrl, lado}   lado ∈ "frente" | "dorso" | "foto"
    frente → {"ok": true, "patient": {dni, apellido, nombres, sexo, nacimiento},
              "bbox": {x, y, w, h}}   (bbox del retrato — puede faltar)
    dorso  → {"ok": true, "domicilio": {calle, numero, piso, localidad,
                                        provincia, cp, texto}}
    foto   → {"ok": true, "bbox": {x, y, w, h}}   sólo el recuadro del retrato
             (fracciones 0..1 de la imagen). Lo usa account-form.js cuando el
             código de barras YA leyó los datos en el navegador y sólo falta
             ubicar la cara para recortar la foto de perfil — el recorte se
             hace client-side con canvas, acá nunca se toca la imagen.
    no se pudo leer → {"ok": false}   (el form sigue a mano, nunca bloquea)

Reusa el cliente OpenRouter de ai_referral (mismo Gemini Flash, misma key). Modo
FAKE con TURNOS_FAKE_AI=1 (sin red, para tests/CI). Rate limit propio por IP
(bucket separado de abuse_guard.py: el WiFi del hospital comparte IP entre muchos
pacientes) + Turnstile en producción, igual que /api/analyze-order.

Sólo stdlib + ai_referral. Nunca tumba el arranque del server.
"""

import re
import json
import base64

from app.ai import ai_referral
from app.cuentas.native_result import Result
from app.core import rate_window
from app.core.ids import digits as _digits

MAX_BYTES = 8 * 1024 * 1024  # el cliente ya baja la foto a <=2000px; techo de sanidad
TIMEOUT = 40


# ---- prompts ----------------------------------------------------------------

_PROMPT_FRENTE = """Sos un lector de documentos de identidad argentinos (DNI).
Te paso la foto del FRENTE de un DNI. Devolvé SOLO un JSON con lo que puedas
leer de los campos impresos (NO inventes; si un campo no se lee, dejalo null):

{
  "es_dni": true|false,          // ¿es realmente el frente de un DNI argentino?
  "dni": "30123456",             // sólo dígitos, sin puntos (7 u 8 dígitos)
  "apellido": "PEREZ",           // en mayúsculas, como figura
  "nombres": "JUAN CARLOS",      // en mayúsculas
  "sexo": "M"|"F"|"X",           // la letra del campo Sexo
  "fecha_nacimiento": "1980-05-09", // YYYY-MM-DD (de "Fecha de nacimiento")
  "foto": {"x": 0.05, "y": 0.30, "w": 0.24, "h": 0.52},
  "legible": true|false
}

"foto" = el recuadro del RETRATO GRANDE de la persona (la foto principal, NO la
miniatura fantasma repetida), como fracciones 0..1 de la IMAGEN completa que te
pasé (x,y = esquina superior izquierda; w,h = ancho y alto). Ajustalo a la cara
con un poco de aire. Si no ves un retrato claro, "foto": null.

Ojo: en el DNI conviven "Fecha de nacimiento" y "Fecha de emisión" — devolvé
SIEMPRE la de nacimiento (la más antigua). El "Número de trámite" (11 dígitos)
NO es el DNI."""

_PROMPT_FOTO = """Te paso la foto del FRENTE de un DNI argentino. Devolvé SOLO
un JSON con el recuadro del RETRATO GRANDE de la persona (la foto principal del
documento, NO la miniatura fantasma repetida), como fracciones 0..1 de la
IMAGEN completa (x,y = esquina superior izquierda; w,h = ancho y alto),
ajustado a la cara con un poco de aire:

{
  "es_dni": true|false,
  "foto": {"x": 0.05, "y": 0.30, "w": 0.24, "h": 0.52}
}

Si no es un DNI o no ves un retrato claro, "foto": null."""

_PROMPT_DORSO = """Sos un lector de documentos de identidad argentinos (DNI).
Te paso la foto del DORSO de un DNI. El dorso trae el DOMICILIO. Devolvé SOLO
un JSON con el domicilio impreso (NO inventes; campo que no se lee → null):

{
  "es_dorso": true|false,        // ¿es el dorso de un DNI (trae domicilio)?
  "domicilio": {
    "calle": "AV SIEMPRE VIVA",
    "numero": "742",
    "piso": "3 B",               // piso/depto si figura, si no null
    "localidad": "PINAMAR",
    "provincia": "BUENOS AIRES",
    "cp": "7167"                 // código postal si figura
  },
  "texto": "AV SIEMPRE VIVA 742 3 B, PINAMAR, BUENOS AIRES (7167)",
  "legible": true|false
}"""


# ---- FAKE (sin red) ---------------------------------------------------------

_FAKE_BBOX = {"x": 0.06, "y": 0.28, "w": 0.24, "h": 0.5}


def _fake_frente():
    return {"ok": True, "patient": {
        "dni": "30123456", "apellido": "PEREZ", "nombres": "JUAN CARLOS",
        "sexo": "M", "nacimiento": "1980-05-09"}, "bbox": dict(_FAKE_BBOX)}


def _fake_foto():
    return {"ok": True, "bbox": dict(_FAKE_BBOX)}


def _fake_dorso():
    return {"ok": True, "domicilio": {
        "calle": "AV DEL LIBERTADOR", "numero": "742", "piso": None,
        "localidad": "PINAMAR", "provincia": "BUENOS AIRES", "cp": "7167",
        "texto": "AV DEL LIBERTADOR 742, PINAMAR, BUENOS AIRES (7167)"}}


# ---- normalización de lo leído ---------------------------------------------

def _up(s, n=120):
    return re.sub(r"\s+", " ", str(s or "").strip()).upper()[:n] or None


def _iso(s):
    """Normaliza a YYYY-MM-DD lo que devuelva el modelo (tolera dd/mm/yyyy)."""
    s = str(s or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return s
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


def _patient_from(leido):
    if not isinstance(leido, dict) or leido.get("es_dni") is False:
        return None
    dni = _digits(leido.get("dni"))
    sexo = str(leido.get("sexo") or "").strip().upper()
    patient = {
        "dni": dni if 7 <= len(dni) <= 8 else "",
        "apellido": _up(leido.get("apellido")) or "",
        "nombres": _up(leido.get("nombres")) or "",
        "sexo": sexo if sexo in ("M", "F", "X") else None,
        "nacimiento": _iso(leido.get("fecha_nacimiento")),
    }
    if not (patient["dni"] or patient["apellido"] or patient["nombres"]):
        return None  # nada útil salió: tratamos como ilegible
    return patient


def _bbox_from(leido):
    """El recuadro del retrato que devolvió el modelo, validado a fracciones
    0..1 sanas (recuadro degenerado o fuera de la imagen → None; nunca se
    confía en que el modelo respetó el formato)."""
    if not isinstance(leido, dict) or leido.get("es_dni") is False:
        return None
    b = leido.get("foto")
    if not isinstance(b, dict):
        return None
    try:
        x, y = float(b.get("x")), float(b.get("y"))
        w, h = float(b.get("w")), float(b.get("h"))
    except (TypeError, ValueError):
        return None
    if not (0 <= x < 1 and 0 <= y < 1):
        return None
    if not (0.02 <= w <= 1 and 0.02 <= h <= 1):
        return None
    # recorta lo que se pase del borde (el modelo a veces redondea de más)
    w = min(w, 1 - x)
    h = min(h, 1 - y)
    if w < 0.02 or h < 0.02:
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def _domicilio_from(leido):
    if not isinstance(leido, dict) or leido.get("es_dorso") is False:
        return None
    d = leido.get("domicilio")
    d = d if isinstance(d, dict) else {}
    dom = {
        "calle": _up(d.get("calle")),
        "numero": _up(_digits(d.get("numero")) or d.get("numero"), 12),
        "piso": _up(d.get("piso"), 20),
        "localidad": _up(d.get("localidad"), 80),
        "provincia": _up(d.get("provincia"), 80),
        "cp": _up(_digits(d.get("cp")), 12),
        "texto": _up(leido.get("texto"), 200),
    }
    if not any(dom.get(k) for k in ("calle", "localidad", "texto")):
        return None
    return dom


# ---- llamada al modelo ------------------------------------------------------

def _leer(data_uri, mime, prompt):
    """Una pasada al modelo con la imagen. Devuelve (dict_leido | None,
    costo_usd, modelo, error_de_servicio). El costo es el usage.cost de
    OpenRouter (0.0 si no viene). Reporta el resultado a ai_health (baja el
    estado si OpenRouter falló por servicio, lo sube si respondió)."""
    if not ai_referral._any_api_key():
        return None, 0.0, None, False
    content = ai_referral._build_content(prompt, data_uri, mime, "dni")
    model = ai_referral._model()
    try:
        data = ai_referral._call_openrouter(content, model, None, timeout=TIMEOUT, temperature=0.1)
    except Exception as e:  # noqa: BLE001 — red/modelo caído: el form sigue a mano
        svc = ai_referral._report_ai(e)
        fb = ai_referral._fallback_model()
        if not (fb and fb != model):
            return None, 0.0, None, svc
        model = fb
        try:
            data = ai_referral._call_openrouter(content, fb, None, timeout=TIMEOUT, temperature=0.1)
        except Exception as e2:  # noqa: BLE001
            svc = svc or ai_referral._report_ai(e2)
            return None, 0.0, None, svc
    ai_referral._report_ai(None)
    cost = 0.0
    try:
        c = data.get("usage", {}).get("cost")
        if c is not None:
            cost = float(c)
    except (AttributeError, TypeError, ValueError):
        cost = 0.0
    try:
        txt = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, cost, model, False
    leido = ai_referral.extract_json(txt) if hasattr(ai_referral, "extract_json") else _loose_json(txt)
    return leido, cost, model, False


def _loose_json(txt):
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", str(txt or ""), re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
        return None


def leer_frente(data_uri, mime):
    if ai_referral._env("TURNOS_FAKE_AI") == "1":
        return _fake_frente()
    leido, cost, model, svc = _leer(data_uri, mime, _PROMPT_FRENTE)
    patient = _patient_from(leido)
    out = {"ok": True, "patient": patient} if patient else {"ok": False}
    if patient:
        bbox = _bbox_from(leido)
        if bbox:
            out["bbox"] = bbox
    out["_costo"], out["_modelo"], out["_svc"] = cost, model, svc  # internos: handle() los saca
    return out


def _leer_parte(data_uri, mime, fake, prompt, campo, extraer):
    """One part of the card read on its own: `fake()` under TURNOS_FAKE_AI, else the model reads `prompt` and
    `extraer` takes `campo` out of the answer; `{"ok": False}` when it finds nothing. The cost, model and
    service ride along as internals."""
    if ai_referral._env("TURNOS_FAKE_AI") == "1":
        return fake()
    leido, cost, model, svc = _leer(data_uri, mime, prompt)
    valor = extraer(leido)
    out = {"ok": True, campo: valor} if valor else {"ok": False}
    out["_costo"], out["_modelo"], out["_svc"] = cost, model, svc
    return out


def leer_foto(data_uri, mime):
    """Sólo el recuadro del retrato (para el recorte de la foto de perfil).
    Se usa cuando el barcode ya leyó los datos y no hace falta el OCR completo."""
    return _leer_parte(data_uri, mime, _fake_foto, _PROMPT_FOTO, "bbox", _bbox_from)


def leer_dorso(data_uri, mime):
    return _leer_parte(data_uri, mime, _fake_dorso, _PROMPT_DORSO, "domicilio", _domicilio_from)


# ---- rate limit propio (bucket separado de abuse_guard.py) -----------------------

_HITS = {}
_HOUR = 3600


def _per_hour():
    try:
        v = ai_referral._env("TURNOS_OCR_PER_HOUR")
        return int(v) if v else 40
    except ValueError:
        return 40


def _permitido(ip):
    return rate_window.allow(_HITS, ip, _per_hour(), _HOUR)


# ---- ruta -------------------------------------------------------------------

def _parse_data_url(url):
    """('image/jpeg', 'data:image/jpeg;base64,...') → (data_uri, mime) o (None, None)."""
    m = re.match(r"^data:([\w/+.\-]+);base64,(.+)$", str(url or ""), re.S)
    if not m:
        return None, None
    mime, b64 = m.group(1), m.group(2)
    if not mime.startswith("image/"):
        return None, None
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:  # noqa: BLE001
        return None, None
    if not raw or len(raw) > MAX_BYTES:
        return None, None
    return url, mime


def _process_core(headers, client_host, body):
    """Process one OCR request from explicit request values and parsed JSON."""
    lado = str(body.get("lado") or "frente").strip().lower()
    if lado not in ("frente", "dorso", "foto"):
        lado = "frente"

    data_uri, mime = _parse_data_url(body.get("imageDataUrl"))
    if not data_uri:
        return Result.json({"error": "imagen inválida"}, status=400)

    # IA caída (tope diario agotado / OpenRouter caído): no gastamos un token
    # que va a fallar. Respondemos ia_no_disponible y el form pide todo a mano
    # (el cliente además esconde la tarjeta de foto vía /api/ai-status).
    from app.ai import ai_health
    if not ai_health.available():
        return Result.json({"ok": False, "motivo": "ia_no_disponible"})

    # Turnstile (mismo gate del sitio) + rate limit propio + fair-use. Sin
    # credenciales de IA el endpoint responde ok:false y el form pide todo a mano.
    from app.ai import abuse_guard
    from app.ai import ai_costs
    ip = abuse_guard.client_ip(headers, client_host)
    tok = body.get("turnstile_token")
    from app.auth import turnstile
    if turnstile.activo_from(headers, client_host) and not abuse_guard.turnstile_ok(
        headers, client_host, tok
    ):
        return Result.json({"ok": False, "error": "captcha"}, status=403)
    # Fair-use: una IP que ya viene subiendo pura basura (fotos que no son un
    # DNI) no gasta otro token — se corta ANTES de llamar al modelo.
    if ai_costs.blocked(ip):
        return Result.json(
            {"error": "abuso",
             "mensaje": "Demasiadas fotos que no pudimos leer. Cargá los datos a mano."},
            status=429)
    if not _permitido(ip):
        return Result.json({"error": "demasiadas consultas, esperá un rato"}, status=429)

    out = (leer_frente(data_uri, mime) if lado == "frente"
           else leer_dorso(data_uri, mime) if lado == "dorso"
           else leer_foto(data_uri, mime))
    costo = out.pop("_costo", 0.0)
    modelo = out.pop("_modelo", None)
    svc = out.pop("_svc", False)
    # Sólo registramos costo si de verdad hubo una llamada al modelo (no cuando
    # falló por servicio antes de consumir tokens).
    if not svc:
        ai_costs.record("ocr_" + lado, cost_usd=costo, ip=ip,
                            model=modelo, recognized=bool(out.get("ok")))
    # Si el fallo fue de SERVICIO (no una foto ilegible), decílo claro: el
    # cliente muestra "no disponible ahora", no "probá con más luz".
    if not out.get("ok") and svc:
        out["motivo"] = "ia_no_disponible"
    return Result.json(out)
