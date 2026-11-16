"""booking_referrals.py — the referral file («orden de derivación»): analysis before registration, the
pending-token store, serving the file to staff and the Confirmá preview, and the retention sweep.

Split out of booking_requests.py (TASK-60, 2026-11-06). The shared mutable paths (`REFERRALS_DIR`) and
limits (`MAX_REFERRAL_BYTES`) stay in booking_requests.py and are read there at call time (`bkr.<name>`),
so a test that points them elsewhere still reaches this module.
"""

import os
import re
import sys
import json
import time
import base64
import secrets
import threading
import mimetypes
from datetime import datetime

from app.modules.turnos import booking_requests_store as store
from app.ai import ai_referral
from app.ai import abuse_guard
from app.core import i18n
from app.core import log_pii
from app.modules.turnos import booking_requests as bkr


_EXT_BY_MIME = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/webp": "webp", "image/heic": "heic", "image/gif": "gif",
    "application/pdf": "pdf",
}


def _log(msg):
    print(f"[retencion-ordenes] {msg}", file=sys.stderr, flush=True)


# ---- pending referrals (analyzed before registration) -----------------------

def _pending_dir():
    return os.path.join(bkr.REFERRALS_DIR, "pending")


_REFERRAL_EXTS = {"png", "jpg", "jpeg", "webp", "heic", "gif", "pdf"}


def _sniff_referral(data):
    """REAL extension via magic bytes, or None. The client's mime/filename
    isn't trusted: a file with <script> declared as an image would be saved
    with an .html extension and the static fallback would serve it as
    executable from our own origin (stored XSS)."""
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1", b"ftyphevc"):
        return "heic"
    return None


_SERVED_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
                "pdf": "application/pdf", "gif": "image/gif", "heic": "image/heic"}


def referral_served_type(data):
    """Content-Type a stored referral is SERVED with: what its bytes say, never
    the mime the patient declared. A declared text/html or image/svg+xml
    would run script on the panel origin (stored XSS, review 2026-11-04);
    bytes that match no allowlisted format go out as an attachment."""
    return _SERVED_MIME.get(_sniff_referral(data) or "", "application/octet-stream")


def _ext_for(orden_bytes, orden_mime, orden_filename):
    # 1) magic bytes rule.
    ext = _sniff_referral(orden_bytes)
    if ext:
        return ext
    # 2) no bytes (moving a pending one) or unrecognized: fall back to the
    #    declared mime/extension, but ALWAYS within the allowlist — never
    #    html/svg/js/xml.
    ext = _EXT_BY_MIME.get((orden_mime or "").lower())
    if not ext and orden_filename and "." in orden_filename:
        ext = orden_filename.rsplit(".", 1)[1].lower()[:5]
    return ext if ext in _REFERRAL_EXTS else "bin"


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _load_pending(token):
    """Saved analysis metadata (server-side, tamper-proof)."""
    if not token or not _TOKEN_RE.match(token):
        return None
    jpath = os.path.join(_pending_dir(), f"{token}.json")
    if not os.path.exists(jpath):
        return None
    try:
        with open(jpath, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _discard_pending_meta(token):
    if not token or not _TOKEN_RE.match(token):
        return
    try:
        os.remove(os.path.join(_pending_dir(), f"{token}.json"))
    except OSError:
        pass


def _cleanup_pending(max_age_h=48):
    """Deletes pending files older than max_age_h (best-effort)."""
    pdir = _pending_dir()
    if not os.path.isdir(pdir):
        return
    cutoff = time.time() - max_age_h * 3600
    try:
        for name in os.listdir(pdir):
            fp = os.path.join(pdir, name)
            try:
                if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
            except OSError:
                pass
    except OSError:
        pass


# ---- retención de archivos de orden (auditoría 2026-08-06) ----------------
#
# `data/ordenes/` guardaba el archivo PARA SIEMPRE, uno por solicitud
# (promedio 118 KB, hasta 25 MB cada uno). Este barrido borra sólo el
# ARCHIVO -- nunca la fila de `booking_requests`, nunca el historial clínico
# -- de solicitudes que llegaron a un estado terminal (aprobada con el turno
# ya pasado, rechazada, cancelada) hace más de TURNOS_REFERRAL_RETENTION_DAYS
# (default 180). Una pendiente, o una aprobada con turno futuro, jamás es
# candidata -- eso lo decide `referrals_to_purge` (ver su docstring).

_REFERRAL_RETENTION_DAYS_DEFAULT = 180


_referral_sweep_stop = threading.Event()


def _referral_retention_days():
    try:
        dias = int(bkr._env("TURNOS_REFERRAL_RETENTION_DAYS") or _REFERRAL_RETENTION_DAYS_DEFAULT)
    except (TypeError, ValueError):
        return _REFERRAL_RETENTION_DAYS_DEFAULT
    return dias


def _referral_data_path(path):
    """`path` of a referral file relative to the data volume
    (`ordenes/<id>.<ext>`) — the key `backup_journal` and `purged_files` use.
    A pointer that lies outside the current `data/ordenes/` (a row written
    when the volume was mounted elsewhere) is journaled by its file name:
    the file is `<id>.<ext>` either way, and it must never block the sweep."""
    rel = os.path.relpath(path, bkr.REFERRALS_DIR).replace(os.sep, "/")
    if rel.startswith("../") or rel == "..":
        rel = os.path.basename(path)
    return "ordenes/" + rel


def reapply_purged_files():
    """Removes, from `data/ordenes/`, every file the retention already
    journaled as purged that is back on disk (an old volume restored, a copy
    pasted back). Returns how many it removed; never raises. Journaled paths
    outside `ordenes/` are not this sweep's business."""
    try:
        paths = store.purged_file_paths()
    except Exception as e:  # noqa: BLE001 — sin la memoria no se borra nada
        _log(f"no se pudo leer el registro de archivos borrados: {log_pii.exc(e)}")
        return 0
    quitados = 0
    for rel in sorted(paths):
        if not rel.startswith("ordenes/") or "/pending/" in rel:
            continue
        fp = os.path.join(bkr.REFERRALS_DIR, rel[len("ordenes/"):])
        try:
            if os.path.isfile(fp):
                os.remove(fp)
                quitados += 1
        except OSError as e:
            _log(f"no se pudo quitar un archivo borrado que volvió: {log_pii.exc(e)}")
    if quitados:
        _log(f"{quitados} archivo(s) de orden ya purgado(s) que habían vuelto: quitados")
    return quitados


def sweep_referrals(retention_days=None, limit=200):
    """Runs one retention pass (pure/testable, injectable `retention_days` —
    same shape as reminders.tick()). `retention_days<=0` disables it
    outright (an explicit opt-out, not silently 0-days-means-delete-today).
    Best-effort per file: a bad path never stops the rest of the sweep, and
    a missing file on disk still clears the DB pointer (nothing left to
    retry). Returns how many files were purged, never raises."""
    days = _referral_retention_days() if retention_days is None else int(retention_days)
    if days <= 0:
        return 0
    reapply_purged_files()
    try:
        candidatos = store.referrals_to_purge(days, limit)
    except Exception as e:  # noqa: BLE001 — el servidor nunca cae por esto
        _log(f"no se pudieron leer candidatos: {log_pii.exc(e)}")
        return 0
    borrados = 0
    for row in candidatos:
        sid, path = row.get("id"), row.get("orden_path")
        try:
            if path:
                # The intent goes on record BEFORE the file goes (schema review
                # №11): the B2 sweep deletes the remote copies of what is
                # journaled, and nothing journaled is ever uploaded or
                # restored again. No record, no deletion — retried next pass.
                store.purged_file_record(_referral_data_path(path), "retention")
            if path and os.path.exists(path):
                os.remove(path)
            store.clear_referral_path(sid)
            borrados += 1
        except OSError as e:
            _log(f"no se pudo borrar el archivo de la solicitud {sid}: {log_pii.exc(e)}")
        except Exception as e:  # noqa: BLE001 — una fila rota no frena las demás
            _log(f"fallo al purgar la solicitud {sid}: {log_pii.exc(e)}")
    if borrados:
        _log(f"{borrados} archivo(s) de orden purgado(s) (retención {days}d)")
    return borrados


def _referral_sweep_loop(interval_h):
    while not _referral_sweep_stop.is_set():
        try:
            sweep_referrals()
        except Exception as e:  # noqa: BLE001 — el hilo nunca muere
            _log(f"barrido falló: {log_pii.exc(e)}")
        _referral_sweep_stop.wait(interval_h * 3600)


def _referral_sweep_interval_h():
    """THE one resolution of TURNOS_REFERRAL_SWEEP_INTERVAL_H — the thread
    loop and the queue registration (worker.py) both derive their cadence
    from this, never a second parse (same contract as
    reminders._interval_min())."""
    try:
        return int(bkr._env("TURNOS_REFERRAL_SWEEP_INTERVAL_H") or 24)
    except (TypeError, ValueError):
        return 24


def start_referral_sweep():
    """Launches the daemon thread, unless TURNOS_REFERRAL_SWEEP_OFF=1 or the
    retention window is disabled. Never raises (same start() contract as
    reminders.py/waitlist.py).

    Tier 4 worker-topology (periodic-daemon batch 2): TURNOS_QUEUE_WORKER=1
    means a SEPARATE worker process (worker.py)
    owns this sweep via modules/turnos/referrals_queue.py — this web
    process starts nothing for it. Same mutual-exclusion-by-construction
    shape as `waitlist.start()`/`reminders.start()`, proven by
    tests/test_queue_worker.py::TestDelegationMatrix."""
    if bkr._env("TURNOS_REFERRAL_SWEEP_OFF") == "1":
        _log("desactivado (TURNOS_REFERRAL_SWEEP_OFF=1)")
        return
    if _referral_retention_days() <= 0:
        _log("retención desactivada (TURNOS_REFERRAL_RETENTION_DAYS<=0)")
        return
    from app.core import queue_infra
    modo_worker = queue_infra.worker_mode()
    if modo_worker:
        _log(f"delegado al worker externo (TURNOS_QUEUE_WORKER={modo_worker}) — este proceso no barre")
        return
    interval_h = _referral_sweep_interval_h()
    try:
        th = threading.Thread(target=_referral_sweep_loop, args=(interval_h,),
                              name="retencion-ordenes", daemon=True)
        th.start()
        _log(f"iniciado (cada {interval_h}h, retención {_referral_retention_days()}d)")
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo iniciar: {log_pii.exc(e)}")


def _decode_referral(orden):
    """orden = {filename, mime, data_b64} -> (bytes, mime, filename) or raises
    ValueError with an error message if the base64 fails to decode."""
    orden_mime = (orden.get("mime") or "").strip() or None
    orden_filename = (orden.get("filename") or "").strip() or None
    data_b64 = orden.get("data_b64") or ""
    if data_b64.strip().startswith("data:") and "," in data_b64:
        data_b64 = data_b64.split(",", 1)[1]
    try:
        orden_bytes = base64.b64decode(data_b64, validate=False)
    except Exception:  # noqa: BLE001
        raise ValueError(i18n.t_or(
            "booking.error.referral_decode_failed",
            "la orden no se pudo decodificar", i18n.active()))
    if not orden_mime:
        orden_mime = (mimetypes.guess_type(orden_filename or "")[0]
                      if orden_filename else None) or "application/octet-stream"
    return orden_bytes, orden_mime, orden_filename


def analyze_referral_bytes(orden_bytes, orden_mime, orden_filename, servicio_id="",
                   especialidad="", ip=None, ident=None):
    """The REUSABLE core of "analyze a referral": save the file server-side,
    run the AI (or degrade to 'pendiente_ia' if it's down), match
    preparation, and save the metadata under a token — exactly what
    `_analyze_referral` (POST /api/analyze-order) used to do for the web
    wizard. Extracted (2026-07-23) so the MCP connector and the chat engine
    (chat_tools.analizar_orden_para) reuse the SAME pipeline instead of
    tripling it — the only difference between surfaces is the gates BEFORE
    getting here (Turnstile/abuse_guard.py for the web, its own
    Bearer/rate-limit for MCP, ai_cooldown across all three).

    `ip` is only for ai_costs.record (can be missing — MCP/chat don't
    always have a real IP worth logging); `ident` is the per-browser key the
    fair-use window counts under (`abuse_guard.identity`), default `ip`.

    Returns (token, ai_json, ia_disponible)."""
    especialidad = especialidad or (bkr._service_name(servicio_id) if servicio_id else "")
    _cleanup_pending()  # opportunistic: deletes pendings older than 48h
    pdir = _pending_dir()
    os.makedirs(pdir, exist_ok=True)

    token = secrets.token_urlsafe(16)
    fpath = os.path.join(pdir, f"{token}.{_ext_for(orden_bytes, orden_mime, orden_filename)}")
    with open(fpath, "wb") as f:
        f.write(orden_bytes)

    context = {"servicio_id": servicio_id, "especialidad": especialidad,
               "filename": orden_filename}

    # Exact duplicate uploads are common when a patient retries after a slow
    # connection.  Reuse only the expensive model read: each upload still has
    # its own pending file/token, and the later account-bound reconciliation is
    # performed against that request's identity.  The opaque content key also
    # contains the service and prompt/model contract, so it cannot cross those
    # boundaries.  Cache failures are deliberately invisible to the patient.
    try:
        from app.ai import orden_analysis_cache
        analysis_cache_key = orden_analysis_cache.key_for(
            orden_bytes, orden_mime, servicio_id, ai_referral.analysis_cache_contract())
        ai_json = orden_analysis_cache.get(analysis_cache_key)
    except Exception:  # noqa: BLE001
        analysis_cache_key, ai_json = None, None

    if ai_json is not None:
        # The cached result has no request ownership.  Recalculate all
        # service/context comparisons in code rather than carrying them over.
        ai_json = ai_referral.reconcile_with_request(ai_json, context)
        ia_disponible = True
    else:
        ia_disponible = None

    # AI down (daily cap exhausted / OpenRouter down): we do NOT analyze. The
    # referral is already saved (fpath); the token stays 'pendiente_ia' and,
    # when the request is created, it enters the queue like that — ai_health
    # reprocesses it once the AI is back.
    from app.ai import ai_health
    if ai_json is None and not ai_health.available():
        ai_json = ai_referral.pending_ai_3pass()
        meta = {"ai": ai_json, "orden_path": fpath, "orden_mime": orden_mime,
                "orden_filename": orden_filename, "servicio_id": servicio_id,
                "especialidad": especialidad, "created": datetime.now().isoformat()}
        try:
            with open(os.path.join(pdir, f"{token}.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
        except OSError:
            pass
        return token, ai_json, False

    if ai_json is None:
        ai_json = ai_referral.analyze_referral_3pass(fpath, orden_mime, context)
        try:
            orden_analysis_cache.put(analysis_cache_key, ai_json)
        except Exception:  # noqa: BLE001
            pass

    # Cost + fair-use: logs what this read spent. "recognized" = the model
    # read an actual referral (parece_correcta/dudosa); a wrong, illegible,
    # or non-analyzable document does NOT count as recognized — so a burst
    # of junk raises the abuse signal (ai_costs.blocked).
    if ia_disponible is None:
        from app.ai import ai_costs
        ai_costs.record(
            "analizar_orden",
            cost_usd=(ai_json.get("costo_usd") if isinstance(ai_json, dict) else 0.0) or 0.0,
            ip=ip, window_key=ident,
            model=(ai_json.get("modelo") if isinstance(ai_json, dict) else None),
            recognized=(ai_json.get("verdict") in ("parece_correcta", "dudosa")
                        if isinstance(ai_json, dict) else False),
        )

    # R4 (how to prepare): matching IN CODE, never from the network — the
    # specialty the model read from the referral (if it read one), the
    # practices/studies it listed (ai_json.campos.practicas), and the page
    # service's specialty, all matched against
    # webdata/estudios-preparacion.json. Lazy import: avoids the
    # booking_requests<->preparation cycle. Never blocks submission.
    try:
        from app.modules.turnos import preparation
        campos = ai_json.get("campos") or {}
        esp_leida = (campos.get("especialidad") or {}).get("valor")
        practicas = (campos.get("practicas") or {}).get("valor") or []
        # ONLY what the referral says (patient-flow-design rule 8: the
        # document wins); servicio_id is a FALLBACK if the referral matched
        # nothing.
        textos = [esp_leida] + list(practicas)
        matched, no_rec = preparation.match_studies(textos, servicio_id)
        ai_json["preparacion"] = matched
        ai_json["preparacion_no_reconocidos"] = [t for t in no_rec if t != esp_leida]
    except Exception:  # noqa: BLE001
        ai_json["preparacion"] = []
        ai_json["preparacion_no_reconocidos"] = []

    meta = {"ai": ai_json, "orden_path": fpath, "orden_mime": orden_mime,
            "orden_filename": orden_filename, "servicio_id": servicio_id,
            "especialidad": especialidad, "created": datetime.now().isoformat()}
    try:
        with open(os.path.join(pdir, f"{token}.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except OSError:
        pass

    return token, ai_json, True


def _analyze_referral_core(headers, client_host, ip, read_body):
    """Pure(ish) domain function behind POST /api/analyze-order: typed `ip`
    (already resolved by the native router from headers and client host) and
    `read_body` (a zero-arg callable that parses the already-buffered ASGI
    body) in, `(status, payload)` out.
    `read_body` is a callable, not the already-parsed dict, so the body
    parse keeps happening at the EXACT point the original code reached it —
    AFTER the ai_costs/abuse_guard rate checks below, never before (an IP
    already blocked/rate-limited must get that 429 even with a malformed
    body, same as today). Turnstile receives those same explicit header/host
    values. No request object or handler-shaped adapter crosses into this
    domain function.

    No session (runs before registration) => same anti-abuse as
    /api/preparacion (abuse_guard.py): shared rate limit + Turnstile — never
    requires a session, only cuts off automated abuse. On top of that, since
    2026-07-23, a PER-IP ai_cooldown layer (the product owner: "protect image analysis
    with an escalating per-identity cooldown, on top of the existing coarse
    limits" — here the only identity available is the IP, it's anonymous
    surface)."""
    # Fair-use: an IP that's already been burning tokens on documents that
    # aren't referrals gets cut off BEFORE spending another analysis (lazy
    # import — ai_costs is only needed on this route).
    from app.ai import ai_costs, ai_health
    # The per-person limits are about AI spend and about a person feeding the
    # reader junk, so they count PER BROWSER (`ip|id`: a hospital's patients
    # share one IP) and only while the reader can run. With no key, with
    # TURNOS_AI_DISABLED=1 or with the provider down the referral is only
    # saved ("pendiente_ia"): nothing is spent, so nobody is told to wait —
    # that was salud's "Hiciste varias consultas seguidas" with no key at all.
    # The ceiling per IP still bounds the disk and any script.
    ident = abuse_guard.identity(ip, headers)
    ai_on = ai_health.available()
    if ai_on and ai_costs.blocked(ident):
        return 429, {
            "error": "abuso",
            "mensaje": i18n.t_or(
                "booking.error.upload_abuse",
                "Subiste varios documentos que no pudimos reconocer como una orden. "
                "Esperá un rato o cargá el turno a mano.", i18n.active()),
        }
    if (ai_on and not abuse_guard.allowed(ident)) or not abuse_guard.ceiling_ok(ip):
        return 429, {
            "error": "limite",
            "mensaje": i18n.t_or(
                "booking.error.rate_limited",
                "Hiciste varias consultas seguidas. Esperá unos minutos y volvé a subirla. "
                "Si es urgente, sacá el turno desde Servicios y Centros o en el centro de salud.",
                i18n.active()),
        }

    try:
        body = read_body()
    except Exception as e:  # noqa: BLE001
        return 400, {"error": bkr._json_invalido(e)}

    if not abuse_guard.turnstile_ok(
        headers, client_host, body.get("turnstile_token") or ""
    ):
        return 403, {"error": "turnstile"}

    servicio_id = (body.get("servicio_id") or "").strip()
    especialidad = (body.get("especialidad") or "").strip() or bkr._service_name(servicio_id)

    orden = body.get("orden") or {}
    if not orden.get("data_b64"):
        return 400, {"error": i18n.t_or(
            "booking.error.missing_referral", "falta la orden", i18n.active())}
    try:
        orden_bytes, orden_mime, orden_filename = _decode_referral(orden)
    except ValueError as e:
        return 400, {"error": str(e)}
    if len(orden_bytes) > bkr.MAX_REFERRAL_BYTES:
        return 413, {"error": i18n.t_or(
            "booking.error.file_too_large", "el archivo es muy pesado", i18n.active())}

    from app.ai import ai_cooldown
    if ai_on:
        ok, espera = ai_cooldown.attempt(ident)
        if not ok:
            return 429, {
                "error": "enfriamiento", "retry_after_seconds": espera,
                "mensaje": ai_cooldown.message(espera),
            }

    try:
        token, ai_json, ia_disponible = analyze_referral_bytes(
            orden_bytes, orden_mime, orden_filename, servicio_id, especialidad,
            ip=ip, ident=ident)
    except OSError:
        return 500, {"error": i18n.t_or(
            "booking.error.referral_save_failed", "no se pudo guardar la orden",
            i18n.active())}

    out = {"ok": True, "token": token, "ai": ai_json}
    if not ia_disponible:
        out["ia_disponible"] = False
    return 200, out


# ---- GET /api/orden-preview/<token> ---------------------------------------
#
# Confirmá needs a real thumbnail, not a filename, even when the image bytes
# never made it into `wiz` — the "Subí tu orden" handoff and the SSO
# round-trip only carry the analyze-order token forward (service.js:512-528,
# the base64 is megabytes, never stashed in sessionStorage). Same capability
# model as `orden_token` in `create_request()`: possession of the token IS the
# authorization (secrets.token_urlsafe(16), never accepted from anywhere but
# this browser's own sessionStorage/URL) — no session/admin gate needed.
# PDFs are refused here on purpose: Confirmá renders those as a document
# chip (service.js ordenSummary), never an <img src>.

# Raster-only: image/svg+xml would execute inline script on the app origin.
_PREVIEW_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/heic"}


def _orden_preview_core(token):
    """Pure domain function behind GET /api/orden-preview/<token>: typed
    `token` in, the SAME `(status, is_json, body, content_type,
    extra_headers)` tuple `_referral_stream_response()` returns, out."""
    meta = _load_pending(token)
    mime = (meta or {}).get("orden_mime") or ""
    if not meta or mime not in _PREVIEW_MIMES:
        return 404, True, {"error": "orden no encontrada"}, None, {}
    return _referral_stream_response(
        {"orden_path": meta.get("orden_path"), "orden_mime": mime}, sandbox=True)


# ---- GET /api/admin/order/<id> -----------------------------------------------

def _referral_stream_response(row, sandbox=False):
    """Pure domain function behind streaming a referral file (already
    resolved by the caller — this doesn't re-check auth/ownership): typed
    `row`/`sandbox` in, `(status, is_json, body, content_type, extra_headers)`
    out — no handler/wfile touched. The native booking and doctor-appointments
    routers build their responses from this single source of truth."""
    if not row or not row.get("orden_path") or not os.path.exists(row["orden_path"]):
        return (404, True,
                {"error": i18n.t_or("admin.srv.referral.no_encontrada", "orden no encontrada", i18n.active())},
                None, {})
    try:
        with open(row["orden_path"], "rb") as f:
            data = f.read()
    except OSError:
        return (500, True,
                {"error": i18n.t_or("admin.srv.referral.error_lectura", "no se pudo leer la orden", i18n.active())},
                None, {})
    mime = referral_served_type(data)
    headers = {"X-Content-Type-Options": "nosniff"}
    if mime == "application/octet-stream":
        headers["Content-Disposition"] = 'attachment; filename="orden.bin"'
    elif sandbox:
        # Even a mis-classified file must not run script on the app origin.
        headers["Content-Disposition"] = "inline"
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return 200, False, data, mime, headers
