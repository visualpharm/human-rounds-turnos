"""ai_health.py — AI (OpenRouter) health status and graceful degradation.

All AI on the site goes through OpenRouter (ai_referral.py, ocr_dni.py). If that
service is unavailable — the key's daily spend cap ran out (402/403 "Key
limit exceeded"), OpenRouter is down (5xx), a network timeout — we do NOT
want to:
  - tell the patient "try with more light" (a lie: the photo is fine);
  - offer AI features that won't work (reading the DNI, analyzing the referral).

Instead:
  - we mark the AI as DOWN as soon as a real call fails with a service error
    (not an unreadable image: that's 200 + "couldn't read it");
  - surfaces check `available()` and hide whatever needs AI;
  - the appointment flow KEEPS working without AI: the referral gets
    uploaded, saved, and the request stays "pending analysis";
  - a periodic probe (daemon) retries; when the AI comes back, we mark it UP
    and REPROCESS everything that piled up (referrals that came in while it
    was down).

In-memory state (best-effort, like abuse_guard.py): a restart starts
optimistic (available=True) and the first call that fails brings it back
down; the daemon recovers it. None of this ever blocks the patient flow.

Envs (new name first, falls back to the old one if unset — see _env2):
  TURNOS_AI_FORCE = up|down (ex TURNOS_IA_FORZAR)     force the state (for testing or a manual cutover).
  TURNOS_AI_HEALTH_OFF = 1 (ex TURNOS_IA_SALUD_OFF)   don't start the daemon (manual probe/reprocess).
  TURNOS_AI_PROBE_MIN (ex TURNOS_IA_PROBE_MIN)        how many minutes between probe retries (3).

stdlib + ai_referral only (+ db for the reprocess, lazy import). Import-safe.
"""

import time
import socket
import threading
import urllib.error

from app.ai import ai_referral
from app.ai import ai_client
from app.core import config


def _env2(new_key, old_key):
    """Reads `new_key`; if unset, falls back to `old_key` (pre-2026-07-17
    name) — so Coolify/credential vault entries on the old env keep working without
    a redeploy."""
    return config.get(new_key, default="", legacy=old_key)


_LOCK = threading.Lock()
_STATE = {"disponible": True, "desde": None, "motivo": None}
_HOUR = 3600

# OpenRouter HTTP codes that mean "the service isn't available to us right
# now": provider payment/quota/auth/rate limits and server errors.
# 400/404/413 are NOT included: those are our own request errors, not an
# outage.
_HTTP_DOWN = frozenset({401, 402, 403, 408, 409, 429, 500, 502, 503, 504,
                        *range(300, 400)})


# ---- manual override --------------------------------------------------------

def _forced():
    v = (_env2("TURNOS_AI_FORCE", "TURNOS_IA_FORZAR") or "").strip().lower()
    if v in ("up", "ok", "on", "1", "true"):
        return True
    if v in ("down", "off", "0", "false", "caida"):
        return False
    return None


# ---- state --------------------------------------------------------------

def configured():
    """True if the AI can be called at all: a key (primary or backup) is set
    and `TURNOS_AI_DISABLED` is off — or the deterministic `TURNOS_FAKE_AI`
    stand-in is on, which needs no key. This is the state BEFORE any call:
    the circuit breaker below only learns about a dead provider after the
    first failure, so an installation that never had a key (salud, 2026-10)
    looked "up" and offered the assistant and the reader anyway."""
    if ai_referral._env("TURNOS_FAKE_AI") == "1":
        return True
    return ai_client.available()


def available():
    """True if the AI is operational. O(1) in memory — called on the hot
    path (every /account load hits /api/ai-status). TURNOS_AI_FORCE overrides
    it; no key / TURNOS_AI_DISABLED=1 is DOWN from the first request."""
    f = _forced()
    if f is not None:
        return f
    if not configured():
        return False
    with _LOCK:
        return _STATE["disponible"]


def status():
    f = _forced()
    with _LOCK:
        st = dict(_STATE)
    if f is not None:
        st["disponible"] = f
        st["forzado"] = True
    elif not configured():
        st["disponible"] = False
        st["motivo"] = st.get("motivo") or "sin_clave"
    # Which OpenRouter key is currently in use (primary/backup/none) — the product owner's
    # item 6: the backup-key failover shouldn't be invisible to the panel.
    st["key_activa"] = ai_client.active_key_kind()
    return st


def mark_ok():
    """An AI call succeeded. Cheap: only acts on the down→up transition. The
    daemon does the backlog reprocessing, not here (this runs on the
    request's own thread)."""
    if _forced() is not None:
        return
    with _LOCK:
        if not _STATE["disponible"]:
            _STATE["disponible"] = True
            _STATE["desde"] = time.time()
            _STATE["motivo"] = None
            recovered = True
        else:
            recovered = False
    if recovered:
        print("[ai_health] AI recovered — backlog will be reprocessed.", flush=True)


def mark_down(reason=""):
    """An AI call failed with a service error. Brings the state down."""
    if _forced() is not None:
        return
    with _LOCK:
        if _STATE["disponible"]:
            _STATE["disponible"] = False
            _STATE["desde"] = time.time()
            _STATE["motivo"] = str(reason)[:120]
            print(f"[ai_health] AI marked DOWN: {reason}", flush=True)


# ---- error classification ----------------------------------------------

def is_service_error(exc):
    """True if `exc` (from _call_openrouter) is a service outage (payment,
    quota, auth, provider rate limit, 5xx, timeout, network) and NOT our own
    error (400/404/413) nor a 200 with an unreadable image."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _HTTP_DOWN
    if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return True
    return False


def report(exc):
    """Helper for callers: reports the outcome of a call. `exc` None =
    success; otherwise classifies it and brings the state down if it was a
    service error. Returns True if the error was a service error (the caller
    can degrade its response)."""
    if exc is None:
        mark_ok()
        return False
    if is_service_error(exc):
        mark_down(f"{type(exc).__name__}: {getattr(exc, 'code', '')}")
        return True
    return False


# ---- probe (cheap real call) -------------------------------------------

def probe():
    """Cheap real test: a 1-token completion. 200 → AI up; a service error
    (e.g. daily cap exhausted) → stays down. No API key (primary or backup)
    → False (no AI). Never raises."""
    if not ai_client.available():
        return False
    content = [{"type": "text", "text": "ok"}]
    try:
        ai_referral._call_openrouter(content, ai_referral._model(), None,
                                  timeout=20, temperature=0)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---- backlog reprocessing ---------------------------------------------

def reprocess_pending(limit=25):
    """Analyzes the requests that came in without AI (ai_json.verdict =
    'pendiente_ia'): runs the 3 passes over the saved referral and updates
    ai_json. Best-effort: the DB or a missing referral never blocks it.
    Returns how many it processed."""
    if not available():
        return 0
    import os
    from app.core import db_booking
    try:
        rows = db_booking.booking_requests_pending_ai(limit)
    except Exception:  # noqa: BLE001
        return 0
    done = 0
    for r in rows:
        if not available():
            break  # went down again mid-reprocess: stop
        path = r.get("orden_path")
        if not path or not os.path.exists(path):
            continue
        ctx = {"dni": r.get("dni"), "nombre": r.get("nombre"),
               "apellido": r.get("apellido"), "servicio_id": r.get("servicio_id"),
               "especialidad": r.get("servicio_id")}
        try:
            ai_json = ai_referral.analyze_referral_3pass(path, r.get("orden_mime"), ctx)
        except Exception:  # noqa: BLE001
            continue
        # If it just went down again, analyze_referral_3pass returns
        # pendiente_ia again — don't save it as resolved (stays in the queue).
        if isinstance(ai_json, dict) and ai_json.get("verdict") == "pendiente_ia":
            break
        try:
            db_booking.save_ai(r["id"], ai_json)
            done += 1
        except Exception:  # noqa: BLE001
            continue
    if done:
        print(f"[ai_health] reprocessed {done} backlogged referrals.", flush=True)
    return done


# ---- daemon -----------------------------------------------------------------

def _interval_sec():
    try:
        v = _env2("TURNOS_AI_PROBE_MIN", "TURNOS_IA_PROBE_MIN")
        return max(30, int(float(v)) * 60) if v else 180
    except (TypeError, ValueError):
        return 180


def _loop():
    while True:
        try:
            # If down, retry with a real probe; if it's back, mark OK.
            if not available() and _forced() is None:
                if probe():
                    mark_ok()
            # With AI available, drain any accumulated backlog.
            if available():
                reprocess_pending()
        except Exception:  # noqa: BLE001 — the daemon never dies on a tick
            pass
        time.sleep(_interval_sec())


def start():
    """Starts the daemon (same pattern as reminders.py/waitlist.py).
    TURNOS_AI_HEALTH_OFF=1 turns it off."""
    if _env2("TURNOS_AI_HEALTH_OFF", "TURNOS_IA_SALUD_OFF") == "1":
        return
    threading.Thread(target=_loop, name="ai-health", daemon=True).start()
