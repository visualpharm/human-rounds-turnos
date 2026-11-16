#!/usr/bin/env python3
"""smoke_demo.py — smoke post-deploy contra el demo DESPLEGADO
(demo.humanrounds.org).

Hermano de smoke_prod.py pero con su propia lista: la matriz de seguridad y
la lista de páginas de smoke_prod.py son de Pinamar (rutas de esa
cartilla) — no aplican acá. Lo que sí es específico del demo:
  1. /api/demo confiesa que es ficticio (`{"demo": true}`),
  2. el catálogo servido es Sampleton, NUNCA el de Pinamar,
  3. /admin/demo-access da de alta la sesión sintética sin contraseña,
  4. esa sesión puede LEER pero cualquier mutación es 403 demo_solo_lectura,
  5. el chat público usa el modelo real y la cartilla ficticia, no el fixture,
  6. el mismo chequeo de "consistencia de build" que smoke_prod.py (Coolify
     dejando un contenedor viejo al lado del nuevo) y la misma verificación
     de build: cada asset fingerprinted (home + módulos) y window.SANA_STATIC
     coinciden con el checkout local,
  7. frescura post-push: el último deploy del demo en Coolify es origin/main
     (el webhook saltea EN SILENCIO al demo si su manual_webhook_secret_gitlab
     no verifica el token — pasó 2026-08-01 y 2026-08-27, entonces vía GitHub).

Uso:  python3 scripts/smoke_demo.py                       # default demo.humanrounds.org
      python3 scripts/smoke_demo.py <base-url>

Exit 0 = verde.
"""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import smoke_layout, smoke_prod

DEFAULT_BASE = "https://demo.humanrounds.org"


def fetch(base, path, method="GET", cookiejar=None, body=None):
    # curl, no urllib: el WAF de Cloudflare bloquea python-urllib (mismo
    # motivo que smoke_prod.py).
    cmd = ["curl", "-s", "-m", "25", "-w", "\n%{http_code}", "-X", method]
    if cookiejar:
        cmd += ["-c", cookiejar, "-b", cookiejar]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    cmd.append(base + path)
    r = subprocess.run(cmd, capture_output=True)
    out = r.stdout
    if not out or b"\n" not in out:
        return 0, b""
    body_out, code = out.rsplit(b"\n", 1)
    try:
        return int(code), body_out
    except ValueError:
        return 0, body_out


def fetch_fingerprinted_asset(base, path):
    """Fetch a build-derived immutable asset outside the stable route census.

    Returns (status, lower-case headers, body): the build check compares the
    bytes and the immutable cache policy, not only the status."""
    return smoke_prod.fetch_con_headers(base, path, "")


def build_match(base):
    """El demo sirve el MISMO build que el checkout local (2026-10-22).

    Un 200 en cada asset de módulo no alcanza: smoke_prod.py compara los
    bytes de cada asset fingerprinted de la home y de los módulos, la política
    immutable y window.SANA_STATIC contra lo que calcula este checkout, y el
    demo sale del mismo `main`. Mismo chequeo, con o sin token de Coolify."""
    code, home = fetch(base, "/")
    if code != 200:
        return 1, [f"[FALLA] build: / -> {code}"]
    return smoke_prod.fingerprint_checks(
        home, lambda path: fetch_fingerprinted_asset(base, path))


def consistencia(base, n=12):
    """Mismo chequeo que smoke_prod.py: n requests con cache-buster distinto,
    TODOS tienen que servir el mismo build (un contenedor huérfano al lado
    del nuevo reparte entre los dos)."""
    vistos = {}
    for i in range(n):
        code, cuerpo = fetch(base, f"/?cb=smokedemo{i}")
        if code != 200:
            return 1, f"[FALLA] consistencia: /?cb=smokedemo{i} -> {code}"
        vistos.setdefault(hashlib.sha256(cuerpo).hexdigest()[:12], []).append(i)
    if len(vistos) == 1:
        return 0, f"[OK] consistencia: {n}/{n} requests sirven el mismo build"
    reparto = ", ".join(f"{h}×{len(v)}" for h, v in vistos.items())
    return 1, ("[FALLA] consistencia: el demo sirve %d builds distintos (%s) — "
               "contenedor huérfano al lado del nuevo, forzar redeploy"
               % (len(vistos), reparto))


# The demo's Coolify application id is installation data, not code: it comes
# from SMOKE_DEMO_APP_UUID or, on the reference machine, from the untracked-in-
# the-public-cut `.claude/deploy-ids.json` ({"demo_app_uuid": "..."}). Without
# it the freshness check is skipped, never failed.
DEMO_APP_UUID_ENV = "SMOKE_DEMO_APP_UUID"
DEPLOY_IDS_FILE = os.path.join(ROOT, ".claude", "deploy-ids.json")


def _demo_app_uuid():
    value = os.environ.get(DEMO_APP_UUID_ENV, "").strip()
    if value:
        return value
    try:
        with open(DEPLOY_IDS_FILE, encoding="utf-8") as fh:
            value = str(json.load(fh).get("demo_app_uuid") or "").strip()
    except (OSError, ValueError, AttributeError):
        return None
    return value or None

# Cleartext http on a public IP would expose the token: the API is reached
# only through the SSH forward opened by ~/.claude/bin/coolify-tunnel.
COOLIFY_TUNNEL = os.path.expanduser("~/.claude/bin/coolify-tunnel")
# The token is injected by whoever runs the smoke (on the reference machine,
# the agents-hub vault broker binding the "api token" field of
# "Human Rounds — Coolify (São Paulo)"). This script never starts a vault CLI.
COOLIFY_TOKEN_ENV = "COOLIFY_API_TOKEN"


def _coolify_token():
    return os.environ.get(COOLIFY_TOKEN_ENV, "").strip() or None


def frescura(base):
    """El último deploy del demo en Coolify tiene que ser origin/main.

    El endpoint /webhooks/source/gitlab/events/manual abanica el push sobre
    las DOS apps del mismo repo+rama, pero solo despliega las que verifican
    el token con su propio manual_webhook_secret_gitlab. Si al demo se le
    resetea ese secret (2026-08-01 y 2026-08-27, entonces vía GitHub), el mismo push lo saltea EN
    SILENCIO y el demo queda sirviendo el build viejo mientras prod avanza.
    Este chequeo hace ruidosa esa deriva. Sin token de Coolify (otra
    instalación, otra máquina) se saltea, no falla."""
    if base != DEFAULT_BASE:
        return 0, "[SKIP] frescura: base custom, no aplica el chequeo Coolify"
    app_uuid = _demo_app_uuid()
    if not app_uuid:
        return 0, ("[SKIP] frescura: sin id de la app demo en Coolify "
                   f"({DEMO_APP_UUID_ENV} o .claude/deploy-ids.json), chequeo salteado")
    try:
        t = subprocess.run([COOLIFY_TUNNEL], capture_output=True, text=True, timeout=20)
        api = t.stdout.strip() if t.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        api = ""
    if not api.startswith(("http://127.0.0.1:", "https://")):
        return 0, "[SKIP] frescura: sin túnel a Coolify, chequeo salteado"
    token = _coolify_token()
    if not token:
        return 0, "[SKIP] frescura: sin token Coolify (COOLIFY_API_TOKEN), chequeo salteado"
    # el token va por stdin (--config -), nunca en argv
    r = subprocess.run(
        ["curl", "-s", "-m", "15", "--config", "-",
         f"{api}/api/v1/deployments/applications/{app_uuid}?take=1"],
        input=f'header = "Authorization: Bearer {token}"\n',
        capture_output=True, text=True)
    try:
        data = json.loads(r.stdout)
        if isinstance(data, dict):
            data = data.get("deployments") or [data]
        dep = data[0]
        commit, status = dep["commit"], dep.get("status", "?")
    except Exception:  # noqa: BLE001
        return 0, "[SKIP] frescura: Coolify no contesta, chequeo salteado"
    local = subprocess.run(["git", "-C", ROOT, "rev-parse", "origin/main"],
                           capture_output=True, text=True).stdout.strip()
    if not local:
        return 0, "[SKIP] frescura: sin origin/main local, chequeo salteado"
    if commit == local:
        return 0, f"[OK] frescura: demo desplegado en origin/main ({commit[:8]}, {status})"
    return 1, (f"[FALLA] frescura: demo en {commit[:8]} ({status}) pero origin/main es "
               f"{local[:8]} — el push NO llegó al demo. Primer sospechoso: "
               "manual_webhook_secret_gitlab del app demo (humanrounds-pinamar/docs/"
               "deploy-coolify.md); catch-up: POST /api/v1/deploy?uuid="
               f"{app_uuid}&force=true")


def main():
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else DEFAULT_BASE
    fallas = 0

    falla, linea = frescura(base)
    fallas += falla
    print(linea)

    falla, linea = consistencia(base)
    fallas += falla
    print(linea)

    # Module assets are not necessarily linked by the demo home page. Their
    # registry URLs remain part of the deployed static contract even when the
    # module is disabled, so build_match probes each current immutable target
    # directly, byte for byte against this checkout.
    falla, lineas = build_match(base)
    fallas += falla
    for linea in lineas:
        print(linea)

    # 1) /api/demo confiesa que es ficticio
    code, cuerpo = fetch(base, "/api/demo")
    try:
        payload = json.loads(cuerpo)
    except Exception:  # noqa: BLE001
        payload = {}
    ok = code == 200 and payload.get("demo") is True
    fallas += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] /api/demo -> {code} {payload}")

    # 2) el catálogo es Sampleton, nunca el de Pinamar
    code, cuerpo = fetch(base, "/centers")
    texto = cuerpo.decode("utf-8", "ignore")
    ok = code == 200 and "Sampleton" in texto and "Pinamar" not in texto
    fallas += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] catálogo (/centers) es Sampleton -> {code}")

    # 3) el demo prueba el asistente real. La consulta es administrativa y no
    # contiene datos personales; además verifica que el modelo use la cartilla.
    code, cuerpo = fetch(base, "/api/chat", method="POST", body={
        "mensaje": "I have an itchy skin condition. Which doctor should I go to?",
        # A fixed conversation eventually reaches the intentional daily
        # per-conversation cap and makes a healthy deployment look broken.
        # The «smoke_demo_» prefix is db_chat.SYNTHETIC_SESSION_PREFIX: the admin
        # chats list leaves these conversations out.
        "session_id": "smoke_demo_real_chat_" + secrets.token_hex(6),
    })
    try:
        chat = json.loads(cuerpo)
    except Exception:  # noqa: BLE001
        chat = {}
    answer = str(chat.get("respuesta") or "")
    ok = (code == 200 and "recibido (fake)" not in answer.lower()
          and "no está disponible" not in answer.lower()
          and "dermat" in answer.lower())
    fallas += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] chat real y orientado por cartilla -> {code}")

    # 4) /admin/demo-access da de alta la sesión sintética sin contraseña
    with tempfile.NamedTemporaryFile(prefix="smoke-demo-cookies-") as cj:
        cookiejar = cj.name
        code, _ = fetch(base, "/admin/demo-access", cookiejar=cookiejar)
        ok = code in (200, 302)
        fallas += 0 if ok else 1
        print(f"[{'OK' if ok else 'FALLA'}] /admin/demo-access -> {code}")

        code, cuerpo = fetch(base, "/api/admin/whoami", cookiejar=cookiejar)
        try:
            who = json.loads(cuerpo)
        except Exception:  # noqa: BLE001
            who = {}
        ok = code == 200 and who.get("usuario") == "demo" and who.get("rol") == "admin"
        fallas += 0 if ok else 1
        print(f"[{'OK' if ok else 'FALLA'}] whoami -> {code} {who}")

        # 4) lee sin problema, pero cualquier mutación es 403 demo_solo_lectura
        # limit=1: every request a list shows leaves a row in the append-only clinical
        # access log (once per process and day), and each deploy restarts the process;
        # one row is all «reading works» needs.
        code, cuerpo = fetch(base, "/api/admin/requests?limit=1", cookiejar=cookiejar)
        ok = code == 200
        fallas += 0 if ok else 1
        print(f"[{'OK' if ok else 'FALLA'}] GET /api/admin/requests (lectura) -> {code}")

        code, cuerpo = fetch(base, "/api/admin/requests/999999999/approve",
                              method="POST", cookiejar=cookiejar, body={"turno_dt": ""})
        try:
            err = json.loads(cuerpo).get("error")
        except Exception:  # noqa: BLE001
            err = None
        ok = code == 403 and err == "demo_solo_lectura"
        fallas += 0 if ok else 1
        print(f"[{'OK' if ok else 'FALLA'}] POST admin (mutación) -> {code} {err} "
              "(esperado 403 demo_solo_lectura)")

    # 5) the appointment cell of the public lists, measured in a browser
    falla, lineas = smoke_layout.listing_layout(base, smoke_layout.PAGES_DEMO)
    fallas += falla
    for linea in lineas:
        print(linea)

    print("—", "VERDE" if not fallas else f"{fallas} FALLA(S)")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
