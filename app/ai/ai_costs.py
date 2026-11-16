"""ai_costs.py — AI call cost, fair-use, and the admin report.

Every call to a multimodal model costs a few milicents of OpenRouter:
  - referral analysis (~$0.003-0.005)                   -> "analizar_orden"
  - DNI OCR, front and back (~$0.001 each)             -> "ocr_frente"/"ocr_dorso"
All of these run BEFORE registration and without a session, so they're the
natural target for a script hammering them in a loop to burn the OpenRouter
quota.

This module owns:

  record(operation, cost_usd=, ip=, user_dni=, model=, recognized=)
      writes the row in `costos_ia` (db_ai.log_ai_cost) AND feeds the in-memory
      abuse window. Called AFTER the AI call. Never raises.

  blocked(ip) -> bool
      True if that IP has been burning tokens on garbage (many calls in the
      last hour, almost all unrecognizable). The endpoint checks it BEFORE
      spending another token and cuts with a 429. Best-effort in memory (like
      abuse_guard.py): a restart clears the window; the lasting signal is
      backed by the DB log (the report's "suspicious" entries come from the
      table, not from memory).

  GET /api/admin/costs[?days=7|30|90]   (admin role, native router)
      summary + cost per operation + daily spend + top IPs + suspicious.

Envs (all with a sane default; new name first, falls back to the old one if
missing — see _envnew below):
  TURNOS_AI_ABUSE_OFF=1 (ex TURNOS_IA_ABUSO_OFF)       turns off the fair-use gate (not the logging).
  TURNOS_AI_ABUSE_MIN (ex TURNOS_IA_ABUSO_MIN)         minimum calls/hour before even evaluating (8).
  TURNOS_AI_ABUSE_FAILS (ex TURNOS_IA_ABUSO_FALLOS)    minimum unrecognizable/hour to block (6).

stdlib + db + config only. Import-safe: never takes down server
boot.
"""

import time

from app.core import db_ai
from app.core import config
from app.core import rate_window

_PERIODS = (7, 30, 90)
_DEFAULT_DAYS = 30

# Readable labels for the report. The key is what's stored in the
# `operacion` column; the value is how an admin reads it (microcopy: the
# name a hospital staffer would use, not the internal one).
OP_LABELS = {
    # The AI reads an order once (N=1); «3 lecturas» was the old ensemble.
    "analizar_orden": "Análisis de la orden",
    "ocr_frente": "Foto del DNI — frente",
    "ocr_dorso": "Foto del DNI — dorso",
    "analizar_entrevista": "Análisis de la captura de síntomas",
    "redactar_consulta": "Redacción de receta u orden",
    "resumen_paciente": "Resumen del paciente",
    "laboratorio_paciente": "Lectura de análisis de laboratorio",
}
# An operation without a label never reaches the report as its key (critical
# review 08.11, E8: the bar read «resumen_paciente»).
OP_LABEL_OTHER = "Otra operación de IA"

# Fair-use: a call counts as "recognized" only if the model actually got
# something useful out of it (a legible DNI, an address, a referral it could
# read). A wrong or illegible document does NOT count as recognized —
# uploading 8 photos that are neither a DNI nor a referral in one hour is
# exactly the abuse pattern to cut off. The threshold is high on purpose: a
# real patient who fumbles once or twice never touches it.
_DEFAULT_MIN = 8       # calls/hour before even evaluating
_DEFAULT_FAILS = 6     # unrecognizable/hour to block
_MAX_OK_RATE = 0.25    # if more than 25% went well, it's not abuse

_HOUR = 3600


def _envnew(new_key, old_key):
    """Reads `new_key`; if unset (neither in the process env nor .env),
    falls back to `old_key` (pre-2026-07-17 name) — so Coolify/credential vault
    still configured with the old env keep working without a redeploy."""
    return config.get(new_key, default="", legacy=old_key)


def _int_env(new_key, old_key, default):
    try:
        v = _envnew(new_key, old_key)
        return int(v) if v else default
    except (TypeError, ValueError):
        return default


def _abuse_off():
    return _envnew("TURNOS_AI_ABUSE_OFF", "TURNOS_IA_ABUSO_OFF") == "1"


def _min_calls():
    return max(1, _int_env("TURNOS_AI_ABUSE_MIN", "TURNOS_IA_ABUSO_MIN", _DEFAULT_MIN))


def _min_fails():
    return max(1, _int_env("TURNOS_AI_ABUSE_FAILS", "TURNOS_IA_ABUSO_FALLOS", _DEFAULT_FAILS))


# ---- in-memory abuse window (per IP, last hour) ----------------------

_WINDOW = {}  # ip -> [(epoch, recognized_bool), ...]  (only calls with a verdict)
_SWEEP_EVERY = 400
_calls = 0


def _sweep(now):
    rate_window.sweep(_WINDOW, now, _HOUR, stamp=lambda entry: entry[0])


def _stats(ip, now):
    xs = [(t, ok) for t, ok in _WINDOW.get(ip, []) if now - t < _HOUR]
    if xs:
        _WINDOW[ip] = xs
    total = len(xs)
    fails = sum(1 for _, ok in xs if ok is False)
    recognized = sum(1 for _, ok in xs if ok is True)
    return total, fails, recognized


def _is_abusive(total, fails, recognized):
    if total < _min_calls() or fails < _min_fails():
        return False
    ok_rate = (recognized / total) if total else 0.0
    return ok_rate < _MAX_OK_RATE


def blocked(ip):
    """True if `ip` has been abusing (check BEFORE spending a token). Doesn't
    log anything — only looks at the window. The gate turns off with
    TURNOS_AI_ABUSE_OFF=1."""
    if _abuse_off() or not ip:
        return False
    total, fails, recognized = _stats(ip, time.time())
    return _is_abusive(total, fails, recognized)


# ---- recording (DB + window) ------------------------------------------------

def record(operation, cost_usd=0.0, ip=None, user_dni=None,
           model=None, recognized=None, window_key=None):
    """Records an AI call: persistent row in `costos_ia` + in-memory abuse
    window. Called AFTER the model call. Never raises — an analytics
    failure must never break the patient flow.

    `window_key` (default: `ip`) is what the fair-use window counts under —
    the web passes `ip|browser-id` so a hospital's shared IP does not pool
    one person's junk uploads against everybody else. The row in
    `costos_ia` keeps the plain `ip`."""
    window_ip = window_key or ip
    global _calls
    try:
        db_ai.log_ai_cost(operation, costo_usd=cost_usd, ip=ip,
                        usuario_dni=user_dni, modelo=model, reconocido=recognized)
    except Exception:  # noqa: BLE001
        pass
    # The window only counts calls with a verdict (recognized True/False): a
    # row with recognized=None is neither an abuse signal nor a success one.
    if window_ip and recognized is not None:
        try:
            now = time.time()
            _calls += 1
            if _calls >= _SWEEP_EVERY:
                _calls = 0
                _sweep(now)
            xs = [(t, ok) for t, ok in _WINDOW.get(window_ip, []) if now - t < _HOUR]
            xs.append((now, bool(recognized)))
            _WINDOW[window_ip] = xs
        except Exception:  # noqa: BLE001
            pass


def _normalize_days(raw):
    """Pure: `raw` (whatever came in the `?days=` query value, or None) ->
    one of `_PERIODS`, defaulting to `_DEFAULT_DAYS` on anything else
    (missing, non-numeric, or out of the allowed set). Shared by both
    transports so a `?days=` edge case only has ONE resolution to get
    right."""
    try:
        d = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_DAYS
    return d if d in _PERIODS else _DEFAULT_DAYS


def _flag_suspicious(t):
    """t = a costos_top_ips row. Same rule as _is_abusive but over the whole
    period's total (not the 1h window): many calls, almost all
    unrecognizable. `recognized` is derived (total - unrecognized), a floor
    because there can be rows with recognized=None, but that only makes the
    flag more conservative."""
    total = t.get("operaciones") or 0
    fails = t.get("no_reconocidas") or 0
    recognized = max(0, total - fails)
    return _is_abusive(total, fails, recognized)


def _report_data(dias):
    """Pure: builds the whole `/api/admin/costs` payload for a validated
    `dias` (already normalized to one of `_PERIODS`). No handler, no auth —
    the native router calls this function directly."""
    resumen = db_ai.costs_summary(dias)
    burn = db_ai.costs_burn_monthly()
    por_op = db_ai.costs_by_operation(dias)
    diario = db_ai.costs_daily(dias)
    top = db_ai.costs_top_ips(dias)

    for r in por_op:
        r["label"] = OP_LABELS.get(r["operacion"], OP_LABEL_OTHER)
        con_veredicto = r["reconocidas"] + r["no_reconocidas"]
        r["tasa_reconocimiento"] = (round(100.0 * r["reconocidas"] / con_veredicto, 1)
                                    if con_veredicto else None)
        r["costo_prom"] = round(r["costo_usd"] / r["cantidad"], 6) if r["cantidad"] else 0.0

    for t in top:
        t["sospechoso"] = _flag_suspicious(t)

    n = resumen["operaciones"]
    total = resumen["total_usd"]
    reco = resumen["reconocidas"]
    noreco = resumen["no_reconocidas"]
    con_veredicto = reco + noreco
    mediana = resumen["mediana_por_ip"]

    return {
        "dias": dias,
        "resumen": {
            "total_usd": round(total, 6),
            "operaciones": n,
            "costo_prom_op": round(total / n, 6) if n else 0.0,
            "reconocidas": reco,
            "no_reconocidas": noreco,
            "tasa_reconocimiento": round(100.0 * reco / con_veredicto, 1) if con_veredicto else None,
            "run_rate_mensual_usd": (round(burn["run_rate_mensual_usd"], 4)
                                      if burn["run_rate_mensual_usd"] is not None else None),
            "dias_completos": burn["dias_completos"],
            "burn_desde": burn["desde"],
            "burn_hasta": burn["hasta"],
            "ips": resumen["ips"],
            "costo_mediano_por_ip": round(mediana, 6) if mediana is not None else None,
            "por_1000_pacientes_usd": round(resumen["promedio_por_ip"] * 1000, 4),
        },
        "por_operacion": por_op,
        "diario": diario,
        "top_ips": top,
        "sospechosos": [t for t in top if t["sospechoso"]],
        "abuso_activo": not _abuse_off(),
    }
