"""ai_cooldown.py — ESCALATING per-identity cooldown for AI reads of an
image/PDF (analyzing a medical referral). Today it applies to: the
`analyze_order` tool on the MCP connector (mcp_api.py, via chat_tools.py),
the same tool inside the chat engine (chat_agent.py, both channels: site
widget and WhatsApp), and as an EXTRA layer on top of the
`POST /api/analyze-order` the web wizard already uses.

Why another layer on top of abuse_guard.py/ai_costs.py: those two cut by
IP — one by a fixed window (10/hour, 30/day), the other by a garbage signal
(many unreadable ones in a row). Neither one stops a real IDENTITY that
simply keeps re-analyzing the SAME referral over and over — each attempt
costs a few milli-cents of OpenRouter, and a person (or an AI assistant
acting on their behalf) can do that with no bad intent. This module adds
that brake, without banning anyone: it scales with use and decays on its own.

Scheme (the product owner, 2026-07-23): each identity gets a BURST (`burst`, default 2)
of analyses with no wait. Past the burst, each following analysis requires a
minimum wait since the PREVIOUS one that DOUBLES: 1 min, 2, 4, 8, 16… capped
at 64 min (envs TURNOS_IMG_BURST / TURNOS_IMG_BASE_SECONDS /
TURNOS_IMG_CAP_MIN). The counter decays on its own: if the identity analyzed
nothing in 24h, the next call starts a fresh burst.

Identity = whoever requests the analysis — the session/Bearer/matched-phone
DNI. On anonymous surfaces (the web wizard, before login) it falls back to
the IP — same criteria as abuse_guard.py, but in a separate window.

API:
  attempt(identity, now=None) -> (ok: bool, retry_after_seconds: int)
      Checks AND, if `ok`, RECORDS the attempt — call it ONCE per real
      analysis, right before spending the token. Empty/None `identity`
      ALWAYS allows (degrade-open: never block for having no one to charge
      the brake to — an identity bug shouldn't take down the flow).
  message(retry_after_seconds) -> Spanish text, ready to show or say
      ("Podés volver a subir una orden en 3 minutos.").

Stdlib only, in memory (best-effort, same pattern as
abuse_guard.py/ai_costs.py): a restart clears the state — it's
anti-waste, not a hard security limit."""

import time
from app.core import config

_DAY = 86400
_SWEEP_EVERY = 500
_calls = 0

# identity -> {"n": calls recorded since the last reset, "last": epoch
# of the last recorded call}
_STATE = {}


_int_env = config.get_int


def _burst():
    return max(0, _int_env("TURNOS_IMG_BURST", 2))


def _base_seconds():
    return max(1, _int_env("TURNOS_IMG_BASE_SECONDS", 60))


def _cap_seconds():
    return max(_base_seconds(), _int_env("TURNOS_IMG_CAP_MIN", 64) * 60)


def _sweep(now):
    muertas = [k for k, v in _STATE.items() if now - v["last"] > _DAY]
    for k in muertas:
        del _STATE[k]


def _wait_for(idx, base, cap):
    """idx = 1, 2, 3… (1st call post-burst = idx 1) -> minimum seconds to wait
    since the previous call, doubling and capped."""
    return min(base * (2 ** (idx - 1)), cap)


def attempt(identity, now=None):
    """True/(False, seconds to wait). Records the attempt when it returns
    True — don't call it "dry" just to check, that desyncs the counter from
    the real analyses."""
    global _calls
    if not identity:
        return True, 0
    now = now if now is not None else time.time()
    _calls += 1
    if _calls >= _SWEEP_EVERY:
        _calls = 0
        _sweep(now)

    burst, base, cap = _burst(), _base_seconds(), _cap_seconds()
    st = _STATE.get(identity)
    if st is None or (now - st["last"]) > _DAY:
        _STATE[identity] = {"n": 1, "last": now}
        return True, 0

    n = st["n"]
    if n < burst:
        st["n"] = n + 1
        st["last"] = now
        return True, 0

    idx = n - burst + 1
    espera = _wait_for(idx, base, cap)
    transcurrido = now - st["last"]
    if transcurrido >= espera:
        st["n"] = n + 1
        st["last"] = now
        return True, 0
    return False, int(round(espera - transcurrido))


def message(retry_after_seconds):
    mins = max(1, round((retry_after_seconds or 0) / 60))
    return f"Podés volver a subir una orden en {mins} minuto{'s' if mins != 1 else ''}."


def _reset_para_tests():
    """Sólo para tests: vacía el estado en memoria."""
    _STATE.clear()
