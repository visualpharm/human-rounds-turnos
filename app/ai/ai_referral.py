"""ai_referral.py — analysis of the medical referral (derivación) with Gemini Flash.

The admin staff (Martita) reviews a queue of appointment requests; each one
brings a photo/PDF of a medical referral. To make the review fast, we
pre-analyze the referral with a multimodal model (Gemini Flash via
OpenRouter, the same slug the sibling app sana/app uses) and give the admin a
judgment ("the fields I could analyze look correct").

The model only PROPOSES what it reads. The comparison against the request
(DNI and specialty) and the final verdict are built IN CODE — the model never
decides whether to approve. Any network/parse failure => verdict
'revision_manual', it never blocks the request.

TURNOS_FAKE_AI=1 => deterministic result, no network (tests / CI).
TURNOS_AI_MODEL   => model override (default: Gemini 3 Flash, 1 reading).
"""

import os
import re
import json
import copy
import hashlib
import base64
import io
import math
import threading
import urllib.error
from collections import Counter
from datetime import datetime

from app.ai import prompts
from app.core import config
from app.core import paths
from app.core import theme
from app.ai import ai_client
from app.core import text_fold
from app.core import tz as _tz
from app.core.text_fold import fold_words as _norm

_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Gemini 3 Flash, a SINGLE reading (the product owner, 2026-07-19: "One reading with Gemini
# Flash 3. That's it." — 3 passes tripled the cost per referral).
from app.core.ai_policy import DEFAULT_MODEL
# BACKUP model for when the main one fails on the key's quota (OpenRouter
# returns 403 "Key limit exceeded (daily limit)" — a USD spend cap per key).
# ":free" models cost $0, so they don't count against that cap and still go
# through. They're best-effort (sometimes rate-limited by the provider), but
# keep the pre-reading alive for the reviewer. Overridable via env.
from app.core.ai_policy import DEFAULT_FALLBACK_MODEL


def _env(key, default=""):
    return config.get(key, default)


def _model():
    from app.core import ai_policy
    return ai_policy.model()


def _fallback_model():
    from app.core import ai_policy
    return ai_policy.fallback_model()

def _any_api_key():
    """Profile-neutral "is inference configured" guard for legacy callers."""
    return ai_client.available()


# ---- normalization / comparison -------------------------------------------

def _digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _fmt_dni(s):
    digits = _digits(s)
    return f"{int(digits):,}".replace(",", ".") if digits else ""


def _specialty_matches(read, requested):
    a, b = _norm(read), _norm(requested)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    # short words ("de", "y") don't count
    inter = {w for w in (ta & tb) if len(w) >= 4}
    return bool(inter)


def _name_matches(read, account):
    """The referral's patient name vs. the account holder, by normalized
    tokens (accent-stripped): tolerates reversed order (last-first) and
    extra middle/second names on one side."""
    return text_fold.same_name_tokens(set(_norm(read).split()), set(_norm(account).split()))


def reconcile_with_request(ai_json, solicitud):
    """Recomputes the comparisons when the analysis ran BEFORE login.

    `/api/analyze-order` doesn't know the account yet, so it saves
    `coincide=None`. Once the token is consumed we have the real account
    holder: we can't keep a green badge if the referral's DNI or name
    contradict it. Doesn't call the model again; it only compares the
    already-read fields in code.
    """
    out = copy.deepcopy(ai_json or {})
    # 'pendiente_ia' = the referral came in without analysis (AI was down).
    # No fields to reconcile; it gets fully reprocessed once the AI is back.
    # Return as-is.
    if out.get("verdict") == "pendiente_ia":
        return out
    campos = out.setdefault("campos", {})
    dni_c = campos.get("dni") if isinstance(campos.get("dni"), dict) else {}
    pac_c = campos.get("paciente") if isinstance(campos.get("paciente"), dict) else {}
    esp_c = campos.get("especialidad") if isinstance(campos.get("especialidad"), dict) else {}
    prac_c = campos.get("practicas") if isinstance(campos.get("practicas"), dict) else {}

    dni_read = _digits(dni_c.get("valor"))
    dni_sol = _digits(solicitud.get("dni"))
    # A legible Argentine DNI has 7-8 digits. Long health-insurer member
    # numbers are not compared as a DNI (invariant of handwritten/insurer
    # referrals).
    dni_match = (dni_read == dni_sol) if len(dni_read) in (7, 8) and dni_sol else None
    dni_c["coincide"] = dni_match

    nombre_sol = " ".join(x for x in (solicitud.get("nombre"), solicitud.get("apellido")) if x).strip()
    pac_val = pac_c.get("valor")
    pac_match = _name_matches(pac_val, nombre_sol) if pac_val and nombre_sol else None
    pac_c["coincide"] = pac_match

    esp_sol = solicitud.get("especialidad") or solicitud.get("servicio_id") or ""
    esp_val = esp_c.get("valor")
    practicas = prac_c.get("valor") if isinstance(prac_c.get("valor"), list) else []
    prac_match = any(_specialty_matches(p, esp_sol) for p in practicas if p)
    esp_match = (_specialty_matches(esp_val, esp_sol) or prac_match) if esp_val and esp_sol else None
    esp_c["coincide"] = esp_match

    campos["dni"], campos["paciente"], campos["especialidad"] = dni_c, pac_c, esp_c
    conflictos = []
    if dni_match is False:
        conflictos.append(f"el DNI de la orden ({_fmt_dni(dni_read)}) no coincide con el de la cuenta ({_fmt_dni(dni_sol)})")
    if pac_match is False:
        conflictos.append(f"la orden está a nombre de {pac_val}, no del titular de la cuenta ({nombre_sol})")
    if esp_match is False:
        conflictos.append(f"la especialidad de la orden ({esp_val}) no coincide con la solicitada ({esp_sol})")

    if conflictos and out.get("verdict") not in ("documento_equivocado", "ilegible"):
        out["verdict"] = "dudosa"
        out["confianza"] = min(float(out.get("confianza") or 0.5), 0.5)
        out["resumen"] = "Revisar: " + "; ".join(conflictos) + "."
    return out


def _doc_score(d):
    """Score for 'looks like the referral', used to pick among several
    documents read off the same photo (real case: two forms — a
    prescription + a study request)."""
    if not isinstance(d, dict):
        return -1
    score = 0
    if d.get("es_orden_medica") is True:
        score += 10
    if d.get("especialidad_solicitada"):
        score += 3
    score += sum(1 for k in ("paciente", "dni", "medico_firmante", "matricula", "fecha") if d.get(k))
    return score


def extract_json(text):
    """Robust JSON extraction: fences, and if the photo brings more than one
    document the model may return a LIST of objects — pick the one that looks
    most like the referral (_doc_score)."""
    if not text:
        return {}
    t = text.strip()
    if "```" in t:
        import re
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.I)
        if m:
            t = m.group(1).strip()
    lb, rb = t.find("["), t.rfind("]")
    candidates = [t]
    if lb != -1 and rb > lb:
        candidates.append(t[lb:rb + 1])
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed:
            best = max(parsed, key=_doc_score)
            return best if isinstance(best, dict) else {}
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return {}


# ---- prompt ----------------------------------------------------------------
#
# `fecha` is injected with TODAY's date (ART) on every call — see `_prompt()`
# below. Reason (demo 2026-07-16, seen twice live): the model reads the
# handwritten YEAR on the referral as 2024/2025 when it actually says 2026 —
# the year digit is the messiest stroke on a handwritten date, so without a
# "today" reference the model has no way to notice it misread it. Without
# this anchor, `admin.html:freshness()` computes the days elapsed since that
# misread date and suggests "The referral is expired." — a wrong rejection of
# a fresh referral.

# The text lives in prompts.py (editable from the panel; see its docstring).
# `{hoy}` gets replaced by prompts.text() — a literal replace, not
# str.format: the prompt has JSON braces and is edited by non-engineers.


def _today_ar():
    """Today's date in America/Argentina/Buenos_Aires, DD/MM/YYYY — the anchor
    the prompt uses to judge which year is plausible in a handwritten date."""
    return datetime.now(_ART).strftime("%d/%m/%Y")


def _jurisdiccion():
    """The jurisdiction this installation's hospital network serves —
    "<province>, <country>" (or just the country if no province is set) —
    the anchor the prompt uses to flag an order issued OUTSIDE it (a
    different province's public system, or a different country). Templated
    per-installation via site_identity, never hardcoded to Pinamar/Argentina:
    a data pack overrides "province"/"country" in its own sitio.json."""
    from app.core import site_identity
    provincia = str(site_identity.get("province") or "").strip()
    pais = str(site_identity.get("country") or "").strip()
    return ", ".join(x for x in (provincia, pais) if x) or pais or "esta instalación"


def _prompt():
    """The prompt with TODAY's date and the installation's jurisdiction
    injected — computed on every call (never an import-time constant: the
    analysis can run days after the process started, and the text may also
    have been edited in the panel in the meantime)."""
    return prompts.text("orden_analisis", hoy=_today_ar(), jurisdiccion=_jurisdiccion())


def analysis_cache_contract():
    """Revision marker for referral-analysis cache entries.

    The editable prompt and selected model both change what the model should
    read.  Including their stable form in the cache key makes those edits take
    effect immediately without having to purge private runtime storage.
    """
    stable_prompt = prompts.text("orden_analisis", hoy="__HOY__",
                                 jurisdiccion=_jurisdiccion())
    return hashlib.sha256(("orden-analysis-v1\0" + _model() + "\0" + stable_prompt)
                          .encode("utf-8")).hexdigest()


# Long side of each converted page, in pixels. `-scale-to` instead of a fixed
# DPI: at `-r 200` a big page (a high-res A4 scan) takes MINUTES and eats the
# timeout — with this the same page comes out in ~2s, because the cost
# depends on the requested size, not the original. Normalization afterwards
# fits it into one or two tiles without touching the original file the admin
# sees.
PX_PAGE = 1536

# Gemini charges images in 768×768 tiles (an image up to 384×384 also costs
# 258 tokens). A legible vertical referral fits comfortably in 768×1536: two
# tiles. The 768 boundary is deliberate — 769px on both axes quadruples the
# tile count without adding useful reading.
_GEMINI_TILE = 768
_MAX_IMAGE_TILES = 2
_WHITE_THRESHOLD = 248


def _tile_count(width, height):
    """Visual tile count as documented by Gemini."""
    if width <= 384 and height <= 384:
        return 1
    return max(1, math.ceil(width / _GEMINI_TILE) *
               math.ceil(height / _GEMINI_TILE))


def _crop_blank_margins(image):
    """Crops only near-white margins, with a conservative pad.

    A photo on a dark table is left alone: the background isn't white so the
    bbox covers the whole image. On a scan it does remove the empty paper
    around the text. Never tries to detect/deskew the document.
    """
    from PIL import ImageOps

    gray = ImageOps.grayscale(image)
    mask = gray.point(lambda p: 255 if p < _WHITE_THRESHOLD else 0, mode="1")
    bbox = mask.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    pad_x = max(16, round(image.width * 0.03))
    pad_y = max(16, round(image.height * 0.03))
    box = (max(0, left - pad_x), max(0, top - pad_y),
           min(image.width, right + pad_x), min(image.height, bottom + pad_y))
    new_area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    old_area = max(1, image.width * image.height)
    # Less than 3% savings doesn't justify re-encoding a photo.
    return image.crop(box) if new_area <= old_area * 0.97 else image


def _image_for_model(path, mime):
    """(bytes, mime, metadata) normalized for Gemini, best-effort.

    Fixes EXIF, crops white margins and fits the image to a max of two 768px
    tiles. If Pillow can't open it, keeps the original: an optimization must
    never make a referral fail to arrive.
    """
    with open(path, "rb") as f:
        original = f.read()
    original_mime = mime or "image/jpeg"
    if "pdf" in original_mime.lower():
        return original, original_mime, None
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(original)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
        before = (image.width, image.height)
        image = _crop_blank_margins(image)
        cropped = (image.width, image.height)

        # Portrait: 768×1536; landscape: 1536×768. This keeps a complete
        # prescription at native Gemini tile resolution with at most 2 tiles.
        box = ((_GEMINI_TILE, _GEMINI_TILE * _MAX_IMAGE_TILES)
               if image.height >= image.width
               else (_GEMINI_TILE * _MAX_IMAGE_TILES, _GEMINI_TILE))
        scale = min(1.0, box[0] / image.width, box[1] / image.height)
        if scale < 1.0:
            size = (max(1, round(image.width * scale)),
                    max(1, round(image.height * scale)))
            image = image.resize(size, Image.Resampling.LANCZOS)

        after = (image.width, image.height)
        changed = before != after or cropped != before
        if not changed:
            return original, original_mime, {
                "before": before, "after": after,
                "tiles_before": _tile_count(*before),
                "tiles_after": _tile_count(*after),
            }

        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            white.alpha_composite(rgba)
            image = white.convert("RGB")
        else:
            image = image.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=90, subsampling=0,
                   optimize=True, progressive=True)
        return out.getvalue(), "image/jpeg", {
            "before": before, "after": after,
            "tiles_before": _tile_count(*before),
            "tiles_after": _tile_count(*after),
        }
    except Exception:  # noqa: BLE001 — best-effort optimization
        return original, original_mime, None


def _pdf_to_png(pdf_path):
    """First page of the PDF to PNG, best-effort: pdftoppm (poppler, in the
    Docker image) or sips (macOS). Returns the PNG path or None (→ falls back
    to the file-part path and, if that fails, revision_manual — never
    blocks)."""
    import shutil
    import subprocess
    out_base = pdf_path + ".page1"
    out_png = out_base + ".png"
    if os.path.exists(out_png):
        return out_png
    try:
        if shutil.which("pdftoppm"):
            subprocess.run(["pdftoppm", "-png", "-scale-to", str(PX_PAGE),
                            "-f", "1", "-l", "1", "-singlefile", pdf_path, out_base],
                           check=True, capture_output=True, timeout=30)
        elif shutil.which("sips"):
            subprocess.run(["sips", "-s", "format", "png", pdf_path, "--out", out_png],
                           check=True, capture_output=True, timeout=30)
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    return out_png if os.path.exists(out_png) else None


# How many pages of a PDF we look at. Uploading the referral scanned on BOTH
# SIDES (front + back with the stamp and signature) is the most common thing
# people do, and before we only read page 1: the license number and
# signature were lost and the referral came out "dudosa" for no reason
# (the product owner, 2026-07-20). Deliberately low cap: these are images billed per
# token, and a real referral doesn't have 10 pages.
MAX_PDF_PAGES = 3


def _pdf_pages(pdf_path, max_pages=MAX_PDF_PAGES):
    """Up to `max_pages` pages of the PDF as PNG, in order. Returns [] if
    there's no converter (→ falls back to the file-part path). Only
    pdftoppm can do this; `sips` only converts the first page, so we fall
    back to one there."""
    import shutil
    import subprocess
    if not shutil.which("pdftoppm"):
        uno = _pdf_to_png(pdf_path)
        return [uno] if uno else []
    # Own prefix + "-N.png" glob: pdftoppm numbers pages as "<base>-1.png".
    # Careful shortening it — with base ".pg" the pattern ".pg*.png" also
    # matches the ".page1.png" that _pdf_to_png leaves behind, and a
    # duplicate page (or one from another run) would sneak into the list.
    out_base = pdf_path + ".hoja"
    try:
        # JPEG, not PNG: a 2000px scan comes out ~1.7MB as PNG and ~300KB as
        # JPEG. With two or three pages the PNG POST weighs several MB and
        # cuts itself off (BrokenPipe, seen here) — plus it costs more
        # tokens. For reading a referral, JPEG 85 is indistinguishable.
        subprocess.run(["pdftoppm", "-jpeg", "-jpegopt", "quality=85",
                        "-scale-to", str(PX_PAGE),
                        "-f", "1", "-l", str(max_pages), pdf_path, out_base],
                       check=True, capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001
        uno = _pdf_to_png(pdf_path)
        return [uno] if uno else []
    import glob
    import re
    pgs = glob.glob(out_base + "-*.jpg")
    # NUMERIC order by page: alphabetically "-10" would sort before "-2" and
    # the referral's front page would stop being the first image the model
    # sees.
    def _n(path):
        m = re.search(r"-(\d+)\.jpg$", path)
        return int(m.group(1)) if m else 0
    return sorted(pgs, key=_n)


def _build_content(prompt, data_uri, mime, filename, extra_uris=None):
    """`extra_uris`: ADDITIONAL pages/sides of the SAME referral (the back
    with the stamp and signature, typically). They go as more images in the
    same message: for the model it's a single document — the prompt tells it
    to combine them into one JSON."""
    is_pdf = "pdf" in (mime or "").lower()
    if is_pdf:
        return [
            {"type": "text", "text": prompt},
            {"type": "file", "file": {"filename": filename or "orden.pdf",
                                      "file_data": data_uri}},
        ]
    partes = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_uri}},
    ]
    for u in (extra_uris or []):
        partes.append({"type": "image_url", "image_url": {"url": u}})
    return partes


# Same non-blocking-acquire pattern as chat_agent.py's _MODEL_SLOTS: every
# referral-reading caller (analyze_referral, analyze_referral_3pass,
# read_studies, ai_health.probe) funnels through _call_openrouter, so one
# semaphore here caps them all — a burst of uploads can't queue up dozens of
# concurrent OpenRouter calls.
_MODEL_SLOTS = threading.BoundedSemaphore(6)


def _call_openrouter(content, model, api_key=None, timeout=45, temperature=0.1):
    """`api_key` stays a parameter for call-site compatibility (ai_health.probe
    and ocr_dni.py both pass their own resolved key here) but is no longer
    what actually goes on the wire: ai_client.call() resolves the real key
    itself — primary vs. the sticky backup — so every caller gets the same
    402/403 failover without having to know about it."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
        # The expected JSON fits comfortably; a document with a prompt
        # injection can't make the model generate an endless response.
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    # Self-throttling, not a service outage: this RuntimeError never reaches
    # ai_health.report (it isn't an HTTPError/timeout/URLError), so a busy
    # burst degrades this one call to manual review without opening the
    # shared breaker for every other AI surface.
    if not _MODEL_SLOTS.acquire(blocking=False):
        raise RuntimeError("demasiados análisis de orden en curso")
    try:
        return ai_client.call(
            body,
            timeout=timeout,
            headers_extra={"X-Title": theme.brand_name() + " - análisis de orden médica"},
        )
    finally:
        _MODEL_SLOTS.release()


# ---- final result (built in code) ------------------------------------

def _fake_result(solicitud):
    """Deterministic result, no network (TURNOS_FAKE_AI / tests)."""
    dni = str(solicitud.get("dni") or "")
    esp = solicitud.get("especialidad") or solicitud.get("servicio_id") or ""
    paciente = " ".join(x for x in [solicitud.get("nombre"), solicitud.get("apellido")] if x)
    campos = {
        "dni": {"valor": dni, "coincide": True},
        "especialidad": {"valor": esp, "coincide": True},
        "paciente": {"valor": paciente or None},
        "medico": {"valor": "Dr. Ejemplo"},
        "matricula": {"valor": "MP 12345"},
        "fecha": {"valor": None},
    }
    return {
        "verdict": "parece_correcta",
        "resumen": f"Los campos analizables parecen correctos: DNI coincide, especialidad {esp or 'sin especificar'}.",
        "campos": campos,
        "confianza": 0.9,
        "modelo": "fake",
    }


def _verdict_from(leido, solicitud, model):
    """Compares what the model read against the request and builds the final
    ai_json."""
    dni_leido = _digits(leido.get("dni"))
    dni_sol = _digits(solicitud.get("dni"))
    esp_leida = leido.get("especialidad_solicitada")
    esp_sol = solicitud.get("especialidad") or solicitud.get("servicio_id") or ""
    legible = leido.get("legible")

    dni_coincide = bool(dni_leido) and bool(dni_sol) and dni_leido == dni_sol
    esp_coincide = _specialty_matches(esp_leida, esp_sol)

    got_something = any(leido.get(k) for k in
                        ("paciente", "dni", "especialidad_solicitada", "medico_firmante"))

    if legible is False or not got_something:
        verdict, conf = "ilegible", 0.2
        resumen = "La orden no se pudo leer con claridad — revisala a mano."
    elif dni_coincide and esp_coincide:
        verdict, conf = "parece_correcta", 0.9
        partes = ["DNI coincide"]
        if esp_leida:
            partes.append(f"especialidad {esp_leida}")
        if leido.get("medico_firmante"):
            firma = leido["medico_firmante"]
            if leido.get("matricula"):
                firma += f" {leido['matricula']}"
            partes.append(f"firma {firma}")
        resumen = "Los campos analizables parecen correctos: " + ", ".join(partes) + "."
    else:
        verdict, conf = "dudosa", 0.5
        motivos = []
        if not dni_coincide:
            motivos.append(f"el DNI de la orden ({_fmt_dni(dni_leido) if dni_leido else 'ilegible'}) no coincide con el de la solicitud ({_fmt_dni(dni_sol)})")
        if not esp_coincide:
            motivos.append(f"la especialidad leída ({esp_leida or 'ilegible'}) no coincide con la solicitada ({esp_sol})")
        resumen = "Revisar: " + "; ".join(motivos) + "."

    campos = {
        "dni": {"valor": leido.get("dni"), "coincide": dni_coincide},
        "especialidad": {"valor": esp_leida, "coincide": esp_coincide},
        "paciente": {"valor": leido.get("paciente")},
        "medico": {"valor": leido.get("medico_firmante")},
        "matricula": {"valor": leido.get("matricula")},
        "fecha": {"valor": leido.get("fecha")},
    }
    return {"verdict": verdict, "resumen": resumen, "campos": campos,
            "confianza": conf, "modelo": model}


def _manual(resumen, model="revision_manual"):
    return {"verdict": "revision_manual", "resumen": resumen, "campos": {},
            "confianza": 0.0, "modelo": model}


def analyze_referral(orden_path, orden_mime, solicitud):
    """Analyzes the referral file and returns the ai_json for the admin.

    solicitud: dict with at least dni, nombre, apellido, servicio_id and
    (optional) 'especialidad' (requested service name, for comparison).
    Never raises: on any problem it returns verdict 'revision_manual'.
    """
    if _env("TURNOS_FAKE_AI") == "1":
        return _fake_result(solicitud)

    if not orden_path or not os.path.exists(orden_path):
        return _manual("No se adjuntó una orden para analizar.")

    if not _any_api_key():
        return _manual("Sin configuración de IA: la orden no se analizó automáticamente.")

    try:
        with open(orden_path, "rb") as f:
            raw = f.read()
    except OSError:
        return _manual("No se pudo leer el archivo de la orden.")

    mime = orden_mime or "image/jpeg"
    data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    filename = os.path.basename(orden_path)
    model = _model()
    content = _build_content(_prompt(), data_uri, mime, filename)

    try:
        data = _call_openrouter(content, model, None)
    except urllib.error.HTTPError as e:
        # OpenRouter may reject the PDF content part -> manual review.
        if "pdf" in mime.lower():
            e.close()  # swallowed without reading: close the body it holds
            return _manual("PDF no analizable automáticamente", model)
        e.close()
        return _manual(f"El análisis automático falló (HTTP {e.code}).", model)
    except Exception:  # noqa: BLE001  (timeout, network, etc.)
        return _manual("El análisis automático no estuvo disponible.", model)

    content_txt = ""
    try:
        content_txt = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return _manual("El modelo no devolvió una respuesta utilizable.", model)

    leido = extract_json(content_txt)
    if not leido:
        return _manual("No se pudo interpretar la lectura de la orden.", model)

    result = _verdict_from(leido, solicitud, model)
    # real OpenRouter cost if present (for traceability, optional).
    try:
        cost = data.get("usage", {}).get("cost")
        if cost is not None:
            result["costo_usd"] = cost
    except AttributeError:
        pass
    return result


# ============================================================================
# SINGLE reading (the product owner, 2026-07-19 — it used to be 3 passes at different
# temperatures; a single Gemini 3 Flash reading is enough and costs a third).
# The verdict and comparisons are still built IN CODE in _verdict_3pass, which
# tolerates N readings — the name *_3pass stays for call-site compatibility,
# but N is 1. This is the analysis the new wizard and the admin consume; the
# single-pass legacy above stays for backward compat.
# ============================================================================

_TEMP = 0.2


def _read_once(content, model, api_key=None, temperature=0.1):
    """One model reading -> (dict_read | None, cost_usd, was_service_error).
    Reports the outcome to ai_health (brings the state down if OpenRouter
    failed with a service error, brings it up if it responded). Never
    raises."""
    try:
        data = _call_openrouter(content, model, None, temperature=temperature)
    except Exception as e:  # noqa: BLE001  (timeout, network, HTTP, PDF rejected)
        svc = _report_ai(e)
        return None, 0.0, svc
    _report_ai(None)
    try:
        txt = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return None, 0.0, False
    cost = 0.0
    try:
        c = data.get("usage", {}).get("cost")
        if c is not None:
            cost = float(c)
    except (AttributeError, TypeError, ValueError):
        cost = 0.0
    return (extract_json(txt) or None), cost, False


def _report_ai(exc):
    """Tells ai_health how the call went (lazy import: ai_health imports
    ai_referral, so this avoids the cycle). Returns True if `exc` was a
    service error (the AI is down)."""
    try:
        from app.ai import ai_health
        return ai_health.report(exc)
    except Exception:  # noqa: BLE001
        return False


def _reconcile(leidos, key, norm=None):
    """Majority non-null value of `key` among the readings + how many agree.
    `norm` normalizes for comparison (dni->digits, especialidad->_norm)."""
    present = [ld.get(key) for ld in leidos if ld.get(key) not in (None, "")]
    if not present:
        return None, 0
    keyed = [(v, (norm(v) if norm else v)) for v in present]
    best_key, agree = Counter(k for _, k in keyed).most_common(1)[0]
    chosen = next(v for v, k in keyed if k == best_key)
    return chosen, agree


def _reconcile_bool(leidos, key, default=True):
    vals = [ld.get(key) for ld in leidos if isinstance(ld.get(key), bool)]
    if not vals:
        return default, 0
    best, agree = Counter(vals).most_common(1)[0]
    return best, agree


def _reconcile_list(leidos, key):
    """Practices: ordered union of everything read (dedup by normalized
    text), ordered by how many readings it appeared in. `agree` = items that
    showed up in ≥ half the passes (the highest-consensus ones)."""
    counts = Counter()
    display = {}
    for ld in leidos:
        seen = set()
        for it in (ld.get(key) or []):
            txt = str(it or "").strip()
            k = _norm(txt)
            if not k or k in seen:
                continue
            seen.add(k)
            counts[k] += 1
            display.setdefault(k, txt)
    if not counts:
        return [], 0
    orden = [display[k] for k, _ in counts.most_common()]
    thresh = max(1, (len(leidos) + 1) // 2)
    agree = sum(1 for _, c in counts.items() if c >= thresh)
    return orden, agree


def _manual_3pass(resumen, model="revision_manual", costo=0.0):
    return {"verdict": "revision_manual", "resumen": resumen, "campos": {},
            "documento": {"es_orden": True, "tipo": None,
                          "jurisdiccion_valida": True, "jurisdiccion_detectada": None},
            "pases": 0, "confianza": 0.0, "modelo": model,
            "costo_usd": round(costo, 6)}


# The AI is unavailable (daily cap exhausted / OpenRouter down). The referral
# was already saved; it stays "pendiente_ia" for ai_health to reprocess when
# it's back. It's NOT a rejection nor "manual review": it's "not analyzed
# yet".
_PENDING_AI_SUMMARY = ("El análisis automático no está disponible en este momento. "
                         "La orden quedó guardada y el hospital la revisa.")


def pending_ai_3pass():
    return {"verdict": "pendiente_ia", "resumen": _PENDING_AI_SUMMARY, "campos": {},
            "documento": {"es_orden": True, "tipo": None,
                          "jurisdiccion_valida": True, "jurisdiccion_detectada": None},
            "pases": 0, "confianza": 0.0, "modelo": "pendiente_ia", "costo_usd": 0.0,
            "preparacion": [], "preparacion_no_reconocidos": []}


# The demo's synthetic orders (TASK-52 / TASK-58, 26.10): with no AI key the
# demo read every order as «no service» or as the same ECG + echocardiogram.
# Each file of the corpus says in golden.json what it asks for; the offline
# reader answers by the file's name, with or without a service chosen.
_DEMO_CORPUS = os.path.join(paths.PROJECT_ROOT,
                            "packs", "example-town", "ordenes-demo", "golden.json")


def _demo_corpus_case(filename):
    name = re.split(r"[\\/]", str(filename or ""))[-1].strip()
    if not name:
        return None
    try:
        with open(_DEMO_CORPUS, encoding="utf-8") as fh:
            cases = json.load(fh).get("cases") or []
    except (OSError, ValueError):
        return None
    return next((c for c in cases if c.get("path") == name and c.get("reading")), None)


def _fake_result_3pass(context):
    """Deterministic, no network. With a filename containing 'factura' it
    returns the documento_equivocado case (to test the "wrong document" flow
    offline); with a filename containing 'extranjer' (extranjero/extranjera)
    it returns the wrong-jurisdiction case (to test that flow offline too —
    a real referral, just issued outside the jurisdiction this installation
    serves)."""
    context = context or {}
    dni = str(context.get("dni") or "")
    esp = context.get("especialidad") or context.get("servicio_id") or ""
    paciente = " ".join(x for x in [context.get("nombre"), context.get("apellido")] if x)
    filename = str(context.get("filename") or "")

    if "factura" in filename.lower():
        tipo = "una factura de peluquería canina"
        campos = {
            "dni": {"valor": None, "coincide": False, "acuerdo": 1.0},
            "especialidad": {"valor": None, "coincide": False, "acuerdo": 1.0},
            "paciente": {"valor": None, "acuerdo": 1.0},
            "medico": {"valor": None, "acuerdo": 1.0},
            "matricula": {"valor": None, "acuerdo": 1.0},
            "fecha": {"valor": None, "acuerdo": 1.0},
        }
        return {
            "verdict": "documento_equivocado",
            "resumen": f"El documento no parece una orden médica: parece {tipo}.",
            "campos": campos,
            "documento": {"es_orden": False, "tipo": tipo,
                          "jurisdiccion_valida": True, "jurisdiccion_detectada": None},
            "pases": 1, "confianza": 0.85, "modelo": "fake", "costo_usd": 0.0,
        }

    if "extranjer" in filename.lower():
        detectada = "Chile"
        campos = {
            "dni": {"valor": dni or None, "coincide": (True if dni else None), "acuerdo": 1.0},
            "especialidad": {"valor": esp or None, "coincide": (True if esp else None), "acuerdo": 1.0},
            "diagnostico": {"valor": "Control cardiológico" if esp else None, "acuerdo": 1.0},
            "practicas": {"valor": (["Electrocardiograma"] if esp else []), "acuerdo": 1.0},
            "paciente": {"valor": paciente or None, "coincide": (True if paciente else None), "acuerdo": 1.0},
            "medico": {"valor": "Dr. Ejemplo Extranjero", "acuerdo": 1.0},
            "matricula": {"valor": "RUT 12.345.678-9", "acuerdo": 1.0},
            "fecha": {"valor": None, "acuerdo": 1.0},
        }
        return {
            "verdict": "dudosa",
            "resumen": f"La orden parece emitida en otra jurisdicción ({detectada}), no en la de este centro.",
            "campos": campos,
            "documento": {"es_orden": True, "tipo": None,
                          "jurisdiccion_valida": False, "jurisdiccion_detectada": detectada},
            "pases": 1, "confianza": 0.85, "modelo": "fake", "costo_usd": 0.0,
        }

    # With no service chosen (/upload-order) the file says what it asks for;
    # inside a service's booking the stub answers by that service — it used
    # to say ECG + echocardiogram for every service, three preparation blocks
    # on an ultrasound (TASK-58).
    demo = None if esp else _demo_corpus_case(filename)
    if demo:
        leido = demo["reading"]
        esp_leida = leido.get("especialidad") or None
        campos = {
            "dni": {"valor": dni or None, "coincide": (True if dni else None), "acuerdo": 1.0},
            "especialidad": {"valor": esp_leida,
                             "coincide": (_norm(esp_leida or "") == _norm(esp) if esp and esp_leida else None),
                             "acuerdo": 1.0},
            "diagnostico": {"valor": leido.get("diagnostico") or None, "acuerdo": 1.0},
            "practicas": {"valor": list(leido.get("practicas") or []), "acuerdo": 1.0},
            "paciente": {"valor": paciente or None, "coincide": (True if paciente else None), "acuerdo": 1.0},
            "medico": {"valor": "Dr. Ejemplo", "acuerdo": 1.0},
            "matricula": {"valor": "MP 12345", "acuerdo": 1.0},
            "fecha": {"valor": None, "acuerdo": 1.0},
        }
        # Only WHAT the order asks comes from the corpus; the offline verdict
        # stays the stub's usual one (the golden verdicts are review anchors
        # for hosted models, not what this stub promises — README).
        return {
            "verdict": "parece_correcta",
            "resumen": f"Orden de {esp_leida or 'estudios'}: {', '.join(leido.get('practicas') or [])}.",
            "campos": campos,
            "documento": {"es_orden": True, "tipo": None,
                          "jurisdiccion_valida": True, "jurisdiccion_detectada": None},
            "pases": 1, "confianza": 0.9, "modelo": "fake", "costo_usd": 0.0,
        }

    campos = {
        "dni": {"valor": dni or None, "coincide": (True if dni else None), "acuerdo": 1.0},
        "especialidad": {"valor": esp or None, "coincide": (True if esp else None), "acuerdo": 1.0},
        "diagnostico": {"valor": None, "acuerdo": 1.0},
        "practicas": {"valor": ([esp] if esp else []), "acuerdo": 1.0},
        "paciente": {"valor": paciente or None, "coincide": (True if paciente else None), "acuerdo": 1.0},
        "medico": {"valor": "Dr. Ejemplo", "acuerdo": 1.0},
        "matricula": {"valor": "MP 12345", "acuerdo": 1.0},
        "fecha": {"valor": None, "acuerdo": 1.0},
        "prepaga": {"valor": "OSDE" if esp else None, "acuerdo": 1.0},
        "afiliado": {"valor": "123456789" if esp else None, "acuerdo": 1.0},
    }
    return {
        "verdict": "parece_correcta",
        "resumen": f"Los campos analizables parecen correctos: DNI coincide, especialidad {esp or 'sin especificar'}.",
        "campos": campos,
        "documento": {"es_orden": True, "tipo": None,
                      "jurisdiccion_valida": True, "jurisdiccion_detectada": None},
        "pases": 1, "confianza": 0.9, "modelo": "fake", "costo_usd": 0.0,
    }


def _reconcile_readings(leidos):
    """Every field of the readings reconciled in code: ({field: value}, {field: number of agreeing readings}).
    `practicas` is the merged list; the booleans and the document type carry no agreement."""
    val, ag = {}, {}
    for key, source, norm in (
            ("dni", "dni", _digits), ("esp", "especialidad_solicitada", _norm), ("pac", "paciente", _norm),
            ("med", "medico_firmante", _norm), ("mat", "matricula", _digits), ("fec", "fecha", _norm),
            ("diag", "diagnostico", _norm)):
        val[key], ag[key] = _reconcile(leidos, source, norm)
    val["practicas"], ag["practicas"] = _reconcile_list(leidos, "practicas")
    val["prepaga"], ag["prepaga"] = _reconcile(leidos, "obra_social_o_prepaga", _norm)
    val["afiliado"], ag["afiliado"] = _reconcile(leidos, "nro_afiliado", _digits)
    val["es_orden"], ag["es_orden"] = _reconcile_bool(leidos, "es_orden_medica", default=True)
    val["tipo"], _tipo_ag = _reconcile(leidos, "tipo_documento", _norm)
    val["legible"], _leg_ag = _reconcile_bool(leidos, "legible", default=True)
    # Defaults to True (like es_orden_medica above): an order the model
    # couldn't place anywhere in particular is NOT the same as one it placed
    # outside the jurisdiction — only a positive false, read off the
    # letterhead/licence, should ever flag it.
    val["jurisdiccion_valida"], _jur_ag = _reconcile_bool(leidos, "jurisdiccion_valida", default=True)
    val["jurisdiccion"], _jurval_ag = _reconcile(leidos, "jurisdiccion_detectada", _norm)
    return val, ag


def _compare_with_request(val, solicitud):
    """What the reading says against the account's request: the DNI, the holder's name and the specialty, each
    `coincide` True/False or None when it is not comparable, plus whether it lets the order through (`*_ok`)."""
    dni_sol = _digits(solicitud.get("dni"))
    esp_sol = solicitud.get("especialidad") or solicitud.get("servicio_id") or ""
    dni_read = _digits(val["dni"])
    # The DNI is only compared if the referral CARRIES a legible DNI: many
    # handwritten referrals don't write it (or carry the insurer's member
    # number, which is NOT a DNI) — that's not a conflict, identity is
    # checked by the patient's name. It only blocks a DNI that was read and
    # DIFFERS from the account's.
    compare_dni = bool(dni_sol) and bool(dni_read)
    dni_coincide = (dni_read == dni_sol) if compare_dni else None
    # Identity by name: the referral must be in the name of the account
    # holder making the request (the admin header shows the account; this
    # flags whether the referral belongs to someone else).
    nombre_sol = " ".join(x for x in [solicitud.get("nombre"), solicitud.get("apellido")] if x).strip()
    pac_comparable = bool(nombre_sol) and bool(val["pac"])
    pac_coincide = _name_matches(val["pac"], nombre_sol) if pac_comparable else None
    # The TARGET specialty is only compared if we read it AND the patient
    # picked a service. If the referral doesn't state it (esp_val empty),
    # it's NOT a conflict: it stays "not comparable" (coincide=None), never
    # "doesn't match". A doctor of any specialty can refer to another, so
    # only a read target that differs from the requested one flags a
    # conflict.
    esp_comparable = bool(esp_sol) and bool(val["esp"])
    # If any requested PRACTICE is the requested service, the referral
    # applies even if the deduced specialty is different: a referral that
    # requests "Ergometría" works for the Ergometría service even if the AI
    # deduces "Cardiología".
    prac_match = any(_specialty_matches(p, esp_sol) for p in (val["practicas"] or []) if p)
    esp_coincide = (_specialty_matches(val["esp"], esp_sol) or prac_match) if esp_comparable else None
    return {
        "dni_sol": dni_sol, "dni_read": dni_read, "dni_coincide": dni_coincide,
        "dni_ok": dni_coincide is not False,
        "nombre_sol": nombre_sol, "pac_coincide": pac_coincide, "pac_ok": pac_coincide is not False,
        "esp_sol": esp_sol, "esp_coincide": esp_coincide,
        "esp_ok": esp_coincide is not False,   # True or None (unknown) don't block
    }


def _missing_fields(val):
    """COMPLETENESS. An order with nobody signing it and no issue date is not
    "correct" — it is incomplete. Those fields are what makes a referral a
    document: without them nobody can say WHO ordered the study or WHEN,
    and the queue's own vigencia check (admin.html:freshness) has nothing
    to measure, so an undated order used to sail through as "not expired".
    Reported as 'dudosa', never a forced rejection: a stamp-only order with
    no legible licence is still an order and some centres will take it —
    the human at the desk decides, same as every other doubtful case."""
    faltantes = []
    if not val["med"] and not val["mat"]:
        faltantes.append("firma_matricula")
    if not val["fec"]:
        faltantes.append("fecha")
    return faltantes


def _verdict_and_summary(val, ag, cmp, faltantes, n):
    """(verdict, confidence, summary sentence) — the first branch that applies wins."""
    es_orden = val["es_orden"]
    got_something = any([val["dni"], val["esp"], val["pac"], val["med"]])
    coinc = ag["dni"] if val["dni"] else ag["es_orden"]
    # With a single reading there's no "agreement" worth reporting — the
    # phrase only makes sense if several passes were reconciled.
    frase_ac = f" {coinc}/{n} lecturas coinciden." if n > 1 else ""
    dni_ok, esp_ok, pac_ok = cmp["dni_ok"], cmp["esp_ok"], cmp["pac_ok"]

    if es_orden is False:
        verdict, conf = "documento_equivocado", 0.85
        if val["tipo"]:
            resumen = f"El documento no parece una orden médica: parece {val['tipo']}.{frase_ac}"
        else:
            resumen = f"El documento no parece una orden médica.{frase_ac}"
    elif val["legible"] is False or not got_something:
        verdict, conf = "ilegible", 0.2
        resumen = "La orden no se pudo leer con claridad — revisala a mano."
    elif val["jurisdiccion_valida"] is False:
        # Orthogonal to the DNI/name/specialty checks below (same shape as
        # freshness/vencida in admin.html): the order IS a real referral
        # (es_orden stayed true), it just wasn't issued inside the
        # jurisdiction this installation serves — a matching DNI or name
        # doesn't change that. 'dudosa' (not a forced rejection) because some
        # installations may still choose to see the patient regardless of
        # where the order came from — a human decides, same as any other
        # doubtful case.
        verdict, conf = "dudosa", 0.85
        origen = f" ({val['jurisdiccion']})" if val["jurisdiccion"] else ""
        resumen = f"La orden parece emitida en otra jurisdicción{origen}, no en la de este centro.{frase_ac}"
    elif dni_ok and esp_ok and pac_ok and faltantes:
        # Identity and specialty check out, but the order is missing what
        # makes it one. Worded as the desk would say it, not as a field dump.
        verdict, conf = "dudosa", 0.6
        motivos = []
        if "firma_matricula" in faltantes:
            motivos.append("no se lee la firma ni la matrícula del médico")
        if "fecha" in faltantes:
            motivos.append("la orden no tiene fecha de emisión")
        resumen = "Revisar: " + "; ".join(motivos) + f".{frase_ac}"
    elif dni_ok and esp_ok and pac_ok:
        verdict, conf = "parece_correcta", 0.9
        partes = []
        if cmp["dni_coincide"]:
            partes.append("DNI coincide")
        elif cmp["pac_coincide"]:
            partes.append("el nombre del paciente coincide")
        if val["esp"]:
            partes.append(f"especialidad {val['esp']}")
        if val["med"]:
            firma = val["med"] + (f" {val['mat']}" if val["mat"] else "")
            partes.append(f"firma {firma}")
        resumen = "Los campos analizables parecen correctos: " + ", ".join(partes) + f".{frase_ac}"
    else:
        verdict, conf = "dudosa", 0.5
        motivos = []
        if cmp["dni_coincide"] is False:
            motivos.append(f"el DNI de la orden ({_fmt_dni(cmp['dni_read'])}) no coincide con el de la cuenta ({_fmt_dni(cmp['dni_sol'])})")
        if cmp["pac_coincide"] is False:
            motivos.append(f"la orden está a nombre de {val['pac']}, no del titular de la cuenta ({cmp['nombre_sol']})")
        if cmp["esp_coincide"] is False:
            motivos.append(f"la especialidad de la orden ({val['esp']}) no coincide con la solicitada ({cmp['esp_sol']})")
        if not motivos:
            motivos.append("no se pudieron confirmar todos los datos de la orden")
        resumen = "Revisar: " + "; ".join(motivos) + f".{frase_ac}"
    return verdict, conf, resumen


def _verdict_3pass(leidos, solicitud, model, costo):
    """Reconciles the readings and builds the final ai_json (built IN
    CODE)."""
    n = len(leidos)

    def ac(agree):
        return round(agree / n, 3) if n else 0.0

    val, ag = _reconcile_readings(leidos)
    cmp = _compare_with_request(val, solicitud)
    faltantes = _missing_fields(val)
    verdict, conf, resumen = _verdict_and_summary(val, ag, cmp, faltantes, n)
    es_orden, jurisdiccion_valida = val["es_orden"], val["jurisdiccion_valida"]

    campos = {
        "dni": {"valor": val["dni"], "coincide": cmp["dni_coincide"], "acuerdo": ac(ag["dni"])},
        "especialidad": {"valor": val["esp"], "coincide": cmp["esp_coincide"], "acuerdo": ac(ag["esp"])},
        "diagnostico": {"valor": val["diag"], "acuerdo": ac(ag["diag"])},
        "practicas": {"valor": val["practicas"], "acuerdo": ac(ag["practicas"])},
        "paciente": {"valor": val["pac"], "coincide": cmp["pac_coincide"], "acuerdo": ac(ag["pac"])},
        "medico": {"valor": val["med"], "acuerdo": ac(ag["med"])},
        "matricula": {"valor": val["mat"], "acuerdo": ac(ag["mat"])},
        "fecha": {"valor": val["fec"], "acuerdo": ac(ag["fec"])},
        "prepaga": {"valor": val["prepaga"], "acuerdo": ac(ag["prepaga"])},
        "afiliado": {"valor": val["afiliado"], "acuerdo": ac(ag["afiliado"])},
    }
    return {
        "verdict": verdict, "resumen": resumen, "campos": campos,
        "documento": {"es_orden": bool(es_orden),
                      "tipo": (val["tipo"] if es_orden is False else None),
                      "jurisdiccion_valida": bool(jurisdiccion_valida),
                      "jurisdiccion_detectada": (val["jurisdiccion"] if jurisdiccion_valida is False else None),
                      # Only meaningful on a document that IS a legible order:
                      # listing "no signature" on a dog-grooming invoice or an
                      # unreadable photo is noise, the panel already says why.
                      "campos_faltantes": (faltantes if (es_orden is not False and verdict != "ilegible") else [])},
        "pases": n, "confianza": conf, "modelo": model,
        "costo_usd": round(costo, 6),
    }


def analyze_referral_3pass(orden_path, orden_mime, solicitud):
    """Analyzes the referral with 3 model readings and reconciles in code.

    solicitud/context: dict with (optional) dni, especialidad/servicio_id,
    nombre/apellido and filename (for FAKE mode). Never raises: if all 3
    passes fail it returns verdict 'revision_manual'.
    """
    solicitud = solicitud or {}
    # Check for missing order BEFORE returning fake result: with no order
    # attached, never show a fabricated AI reading.
    if not orden_path or not os.path.exists(orden_path):
        return _manual_3pass("No se adjuntó una orden para analizar.")

    if _env("TURNOS_FAKE_AI") == "1":
        return _fake_result_3pass(solicitud)

    if not _any_api_key():
        return _manual_3pass("Sin configuración de IA: la orden no se analizó automáticamente.")

    mime = orden_mime or "image/jpeg"
    # PDFs: OpenRouter charges the file-parser with a minimum balance (402
    # "requires at least $0.50 in balance for files") — we convert the pages
    # to PNG and use the image path instead, which has no such requirement.
    # ALL of them go (up to the cap): a two-page PDF is usually the front and
    # back of the same referral, and keeping only the first one lost the
    # stamp and signature.
    extras = []
    if "pdf" in mime.lower():
        pgs = _pdf_pages(orden_path)
        if pgs:
            # The converter returns JPEG; the single-page fallback, PNG.
            orden_path = pgs[0]
            mime = "image/png" if orden_path.lower().endswith(".png") else "image/jpeg"
            extras = pgs[1:]

    try:
        raw, mime, _image_meta = _image_for_model(orden_path, mime)
    except OSError:
        return _manual_3pass("No se pudo leer el archivo de la orden.")

    data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    extra_uris = []
    for pg in extras:
        try:
            pg_mime = "image/png" if pg.lower().endswith(".png") else "image/jpeg"
            pg_raw, pg_mime, _pg_meta = _image_for_model(pg, pg_mime)
            extra_uris.append(f"data:{pg_mime};base64,"
                              + base64.b64encode(pg_raw).decode("ascii"))
        except OSError:  # a page that couldn't be read doesn't abort the analysis
            pass
    filename = os.path.basename(orden_path)
    model = _model()
    content = _build_content(_prompt(), data_uri, mime, filename, extra_uris)

    leidos, costo, hubo_svc = [], 0.0, False
    try:
        leido, cost, svc = _read_once(content, model, None, _TEMP)
        costo += cost or 0.0
        hubo_svc = svc
        if leido:
            leidos.append(leido)
    except Exception:  # noqa: BLE001
        pass

    if not leidos:
        # The main model returned nothing (typically 403 "Key limit
        # exceeded" = the key's spend cap). Best-effort backup with a free
        # model: since it costs $0 it dodges that cap. A single pass (the
        # ":free" ones are slow / sometimes rate-limited).
        fb = _fallback_model()
        if fb and fb != model:
            leido, cost, svc = _read_once(content, fb, None, _TEMP)
            costo += cost or 0.0
            hubo_svc = hubo_svc or svc
            if leido:
                return _verdict_3pass([leido], solicitud, fb, costo)
        # If the failure was a SERVICE error (cap/network/5xx) or ai_health
        # already marked it down, the referral stays 'pendiente_ia' — it
        # gets reprocessed once the AI is back, instead of going to "manual
        # review" forever.
        if hubo_svc or not _ai_available():
            return pending_ai_3pass()
        return _manual_3pass("El análisis automático no estuvo disponible.", model, costo)

    return _verdict_3pass(leidos, solicitud, model, costo)


def _ai_available():
    try:
        from app.ai import ai_health
        return ai_health.available()
    except Exception:  # noqa: BLE001
        return True


# ============================================================================
# read_studies — a SINGLE reading (not 3-pass) of WHAT STUDIES a document
# requests. Consumed by preparation.py (POST /api/preparacion): it's
# informative ("what do I need to do to prepare?"), it doesn't approve
# anything, so it doesn't need analyze_referral_3pass's 3-pass reconciliation
# — one cheap reading (~$0.001) is enough. Matching against
# estudios-preparacion.json is pure code (preparation.match_studies), never
# network.
# ============================================================================

# The text lives in prompts.py (editable from the panel).


def _read_studies_failure(model=None):
    return {"es_orden_medica": None, "tipo_documento": None, "legible": None,
            "estudios_solicitados": [], "modelo": model}


def _fake_read_studies(context):
    """Deterministic, no network (TURNOS_FAKE_AI). filename with 'factura' ->
    not a referral; otherwise the filename (without extension) split into
    tokens stands in for 'estudios_solicitados' (uploading
    'glucemia-ecografia.jpg' -> matches glucemia + ecografía) — same trick
    _fake_result_3pass uses to test the wrong-document flow offline."""
    context = context or {}
    filename = str(context.get("filename") or "")
    if "factura" in filename.lower():
        return {"es_orden_medica": False, "tipo_documento": "una factura",
                "legible": True, "estudios_solicitados": [], "modelo": "fake"}
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    tokens = [t for t in re.split(r"[^a-zA-Z]+", base) if t]
    return {"es_orden_medica": True, "tipo_documento": None, "legible": True,
            "estudios_solicitados": tokens, "modelo": "fake"}


def read_studies(orden_path, orden_mime, context=None):
    """Reads which studies/practices the document requests (a single, cheap
    pass).

    context: dict with (optional) 'filename' (for FAKE mode). Never raises:
    on any problem returns es_orden_medica=None (never blocks the caller,
    which treats it as "couldn't read it" — never as a rejection).
    """
    context = context or {}
    if _env("TURNOS_FAKE_AI") == "1":
        return _fake_read_studies(context)

    if not orden_path or not os.path.exists(orden_path):
        return _read_studies_failure()

    if not _any_api_key():
        return _read_studies_failure()

    mime = orden_mime or "image/jpeg"
    if "pdf" in mime.lower():
        png = _pdf_to_png(orden_path)
        if png:
            orden_path, mime = png, "image/png"

    try:
        raw, mime, _image_meta = _image_for_model(orden_path, mime)
    except OSError:
        return _read_studies_failure()

    data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    filename = os.path.basename(orden_path)
    content = _build_content(prompts.text("orden_estudios"), data_uri, mime,
                             filename)

    model = _model()
    leido, _cost, _svc = _read_once(content, model, None, 0.2)
    if not leido:
        # same best-effort backup as analyze_referral_3pass: the main model
        # can be out of quota — a free model keeps this informational tool
        # alive even at lower quality.
        fb = _fallback_model()
        if fb and fb != model:
            leido, _cost, _svc = _read_once(content, fb, None, 0.2)
            model = fb
    if not leido:
        return _read_studies_failure(model)

    estudios = leido.get("estudios_solicitados")
    if not isinstance(estudios, list):
        estudios = [estudios] if estudios else []
    return {
        "es_orden_medica": leido.get("es_orden_medica"),
        "tipo_documento": leido.get("tipo_documento"),
        "legible": leido.get("legible"),
        "estudios_solicitados": [str(x).strip() for x in estudios if str(x or "").strip()],
        "modelo": model,
    }
