#!/usr/bin/env python3
"""smoke_prod.py — smoke post-deploy contra el sitio DESPLEGADO.

POR QUÉ (semana del 2026-07-14/18): tres clases de regresión sólo se ven en
prod, nunca en tests locales —
  1. seguridad que vive en la config del server (el fallback estático servía
     /server.py y /.env; el IDOR ?dni= respondía PHI sin sesión),
  2. assets viejos por el cache de 4 h de Cloudflare (el bump ?v= olvidado),
  3. el deploy que "no se ve" (webhook ok pero contenedor viejo).

Uso:  python3 scripts/smoke_prod.py [<base-url>]
      SMOKE_BASIC_AUTH='user:pass' python3 scripts/smoke_prod.py <base-url>
      python3 scripts/smoke_prod.py <base-url> user:pass   ( argv pisa el env )
      SMOKE_BASIC_AUTH_ITEM='<item del vault de contraseñas>' [SMOKE_OP_CLI=<op|wrapper>]
        [SMOKE_OP_VAULT=<caja>] python3 scripts/smoke_prod.py [<base-url>]

El basic-auth perimetral (site_auth.py) es opcional. Si el despliegue lo
activa, la credencial viene del segundo argumento, de SMOKE_BASIC_AUTH o del
item del vault de contraseñas SMOKE_BASIC_AUTH_ITEM — nunca de este archivo (auditoría
2026-09-27, hallazgo 2: el default hardcodeado era la puerta frontal de prod
en un repo con campaña de open-source) — y viaja a curl por stdin, no por
argv.

Chequea: matriz de seguridad (403/401), páginas clave 200, y que cada asset
versionado que sirve prod COINCIDA byte-con-byte en tamaño razonable con el
repo local (detecta el contenedor viejo y el ?v= olvidado a la vez: se pide el
?v= que referencia el HTML del repo). Exit 0 = verde.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import smoke_layout  # noqa: E402

DEFAULT_BASE = "https://pinamar.humanrounds.org"


# NOTE (2026-08-08): scripts/turnstile_ip_refresh.py is gone. It maintained
# The product owner's home IPs in the admin-login Turnstile exemption list (ip_exempt.py),
# and that non-loopback exemption was retired as spoofable (no CDN in front,
# so CF-Connecting-IP/X-Forwarded-For are client-suppliable). Scripted logins
# now send the `X-Turnstile-Test-Bypass` header with
# TURNOS_TURNSTILE_TEST_BYPASS_SECRET read from the environment — see
# scripts/seed_patient_account.py for the working pattern.

# path -> status esperado
SECURITY = {
    # Source, tests and documents are not on the static allowlist (2026-11-12,
    # tests/test_static_allowlist.py): 404 whether or not the file exists. A dot
    # segment and the data volume stay 403.
    "/asgi_main.py": 404,
    "/.env": 403,
    "/app/core/db.py": 404,
    "/data/ordenes/": 403,
    "/docs/": 404,
    "/api/requests?dni=12345678": 401,   # IDOR: sin sesión jamás lista PHI
}
PAGES_200 = ["/", "/account", "/doctors",
             "/upload-order", "/about.html", "/static/favicon.svg"]


def _curl(args, auth):
    """curl con la credencial por stdin (`-K -`), nunca en argv: argv se ve
    en `ps` de cualquier usuario de la máquina."""
    if not auth:
        return subprocess.run(["curl", *args], capture_output=True)
    quoted = auth.replace("\\", "\\\\").replace('"', '\\"')
    return subprocess.run(["curl", "-K", "-", *args], capture_output=True,
                          input=('user = "%s"\n' % quoted).encode("utf-8"))


def credencial_del_vault(item, cli=None, vault=None):
    """'user:pass' del item del vault de contraseñas `item` (campos USERNAME/PASSWORD),
    leído con el CLI `cli` (`op` o un wrapper compatible). "" si no se pudo:
    sin credencial el smoke corre igual y la matriz dirá 401 donde haga falta."""
    argv = [cli or "op", "item", "get", item, "--format", "json", "--reveal"]
    if vault:
        argv += ["--vault", vault]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        campos = json.loads(r.stdout).get("fields", []) if r.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    por_uso = {c.get("purpose"): c.get("value") or "" for c in campos}
    user, pwd = por_uso.get("USERNAME", ""), por_uso.get("PASSWORD", "")
    return "%s:%s" % (user, pwd) if user and pwd else ""


def credencial():
    """argv[2] > SMOKE_BASIC_AUTH > el item del vault de contraseñas SMOKE_BASIC_AUTH_ITEM
    (CLI en SMOKE_OP_CLI, default `op`; caja en SMOKE_OP_VAULT)."""
    if len(sys.argv) > 2:
        return sys.argv[2]
    if os.environ.get("SMOKE_BASIC_AUTH"):
        return os.environ["SMOKE_BASIC_AUTH"]
    item = os.environ.get("SMOKE_BASIC_AUTH_ITEM", "")
    if not item:
        return ""
    return credencial_del_vault(item, os.environ.get("SMOKE_OP_CLI"),
                                os.environ.get("SMOKE_OP_VAULT"))


def fetch(base, path, auth):
    # curl y no urllib: el WAF de Cloudflare devuelve 403 a python-urllib
    # (mismo bloqueo visto contra la API de Coolify, error 1010) — curl pasa.
    r = _curl(["-s", "-m", "25", "-w", "\n%{http_code}", base + path], auth)
    out = r.stdout
    if not out or b"\n" not in out:
        return 0, b""
    body, code = out.rsplit(b"\n", 1)
    try:
        return int(code), body
    except ValueError:
        return 0, body


def fetch_con_headers(base, path, auth):
    """Como fetch(), pero además devuelve los headers de respuesta (dict,
    claves en minúscula) — sólo lo necesita el chequeo de assets
    fingerprinted de abajo, que tiene que ver el `Cache-Control` real, no
    sólo el body."""
    r = _curl(["-s", "-D", "-", "-m", "25", base + path], auth)
    out = r.stdout
    if b"\r\n\r\n" not in out:
        return 0, {}, b""
    head, body = out.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1", "ignore").split("\r\n")
    code = 0
    try:
        code = int(lines[0].split()[1])
    except (IndexError, ValueError):
        pass
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return code, headers, body


def consistencia(base, auth, n=10):
    """Un solo request NO prueba que el deploy salió (2026-07-27).

    Coolify dejó un contenedor viejo corriendo al lado del nuevo y Traefik
    repartió entre los dos: prod servía el build viejo y el nuevo en requests
    alternados, así que un curl suelto acertaba la mitad de las veces. Pedimos
    la home n veces con cache-buster y exigimos que TODAS coincidan.
    """
    vistos = {}
    for i in range(n):
        code, body = fetch(base, f"/?cb=smoke{i}", auth)
        if code != 200:
            return 1, f"[FALLA] consistencia: /?cb=smoke{i} -> {code}"
        vistos.setdefault(hashlib.sha256(body).hexdigest()[:12], []).append(i)
    if len(vistos) == 1:
        return 0, f"[OK] consistencia: {n}/{n} requests sirven el mismo build"
    reparto = ", ".join(f"{h}×{len(v)}" for h, v in vistos.items())
    return 1, ("[FALLA] consistencia: prod sirve %d builds distintos (%s) — "
               "contenedor huérfano al lado del nuevo, forzar redeploy "
               "(no diagnostiques datos de prod hasta que dé verde)"
               % (len(vistos), reparto))


def main():
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else DEFAULT_BASE
    auth = credencial()
    if auth and ":" not in auth:
        print("ERROR: la credencial del basic-auth perimetral va como "
              "'user:pass'.", file=sys.stderr)
        return 2
    fallas = 0

    falla, linea = consistencia(base, auth)
    fallas += falla
    print(linea)

    for path, want in SECURITY.items():
        got, _ = fetch(base, path, auth)
        ok = got == want
        fallas += 0 if ok else 1
        print(f"[{'OK' if ok else 'FALLA'}] {path} -> {got} (esperado {want})")

    for path in PAGES_200:
        got, _ = fetch(base, path, auth)
        ok = got == 200
        fallas += 0 if ok else 1
        print(f"[{'OK' if ok else 'FALLA'}] {path} -> {got}")

    # assets frescos: el ?v= que referencia la HOME de prod debe servir un JS
    # cuyo contenido coincide con el checkout local (contenedor viejo => difiere)
    _, home = fetch(base, "/", auth)
    refs = sorted(set(re.findall(r'/((?:[\w-]+)\.js\?v=\d+)', home.decode("utf-8", "ignore"))))
    for ref in refs:
        name = ref.split("?")[0]
        got, remote = fetch(base, "/" + ref, auth)
        try:
            with open(name, "rb") as f:
                local = f.read()
        except OSError:
            print(f"[SKIP] {ref} (no existe local)")
            continue
        ok = got == 200 and remote == local
        fallas += 0 if ok else 1
        detalle = "" if ok else f" (HTTP {got}, remoto {len(remote)}B vs local {len(local)}B — contenedor viejo o ?v= sin bumpear)"
        print(f"[{'OK' if ok else 'FALLA'}] asset {ref}{detalle}")

    falla, lineas = fingerprint_checks(
        home, lambda ref: fetch_con_headers(base, ref, auth))
    fallas += falla
    for linea in lineas:
        print(linea)

    # The appointment cell of the public lists, measured in a browser.
    falla, lineas = smoke_layout.listing_layout(base, smoke_layout.PAGES_PROD, auth)
    fallas += falla
    for linea in lineas:
        print(linea)

    print("—", "VERDE" if not fallas else f"{fallas} FALLA(S)")
    return 1 if fallas else 0


def fingerprint_checks(home, fetch_headers):
    """El build servido coincide con el checkout local, asset por asset.

    `home` es el cuerpo (bytes) de GET /; `fetch_headers(ref)` devuelve
    (status, headers en minúscula, body). Lo comparten smoke_prod.py y
    smoke_demo.py: los dos sitios salen del mismo `main`. Devuelve
    (cantidad de fallas, líneas para imprimir)."""
    fallas = 0
    lineas = []
    # Assets fingerprinted (Tier 4 slice 6, static_fingerprint.py): la home
    # de prod tiene que referenciar el MISMO hash que el checkout local
    # calcula para ese archivo (contenedor viejo => hash distinto, mismo
    # síntoma que el ?v= sin bumpear de arriba) y la respuesta tiene que
    # venir `public, max-age=31536000, immutable` — si algo la sirve sin
    # eso, un cambio futuro al archivo nunca invalidaría la copia cacheada.
    from app.core import static_fingerprint
    # Tier 5 slice 2: el scraper reconoce también un `.mjs` facade y la
    # forma con subdirectorio + sufijo compuesto que usará el vendor JS de
    # slice 3 (`/static/vendor/preact.<hash8>.module.js`) — nada referencia
    # ninguna de las dos todavía desde el HTML servido, así que este bucle
    # sigue vacío para esos casos hasta que una slice futura las cablee; el
    # patrón ya está listo para no volver a tocarlo ese día.
    fp_refs = sorted(set(re.findall(
        r'/static/(?:[\w-]+/)?[\w-]+\.[0-9a-f]{8}\.(?:module\.)?(?:js|css|mjs)',
        home.decode("utf-8", "ignore"))))
    # Module-owned assets need not appear on the home shell. Verify their
    # registered immutable URLs explicitly so a deployment cannot leave an
    # intake/admin screen pointing at a stale raw path.
    # Every registered platform asset too, not only what the home links
    # (2026-11-03): the panel's own CSS and screens (admin-direct.css,
    # admin-content.mjs…) are linked only behind the login, so a deploy that
    # changed nothing but them passed this check while the old build was
    # still served. At the local hash, the old container answers 404.
    fp_refs = sorted(set(fp_refs) | {
        static_fingerprint.module_url(name)
        for name in static_fingerprint._module_assets()
    } | {
        static_fingerprint.url(name)
        for name in static_fingerprint.MIGRATED
    })
    for ref in fp_refs:
        nombre = static_fingerprint._resolve(ref)
        if nombre is None:
            fallas += 1
            lineas.append(f"[FALLA] asset {ref} (hash no coincide con el checkout local — "
                          f"contenedor viejo, o el archivo cambió sin que este deploy lo sirva)")
            continue
        got, headers, remote = fetch_headers(ref)
        with open(static_fingerprint._physical_path(nombre), "rb") as f:
            local = f.read()
        ok = (got == 200 and remote == local
              and headers.get("cache-control") == "public, max-age=31536000, immutable")
        fallas += 0 if ok else 1
        detalle = "" if ok else (f" (HTTP {got}, cache-control={headers.get('cache-control')!r}, "
                                  f"remoto {len(remote)}B vs local {len(local)}B)")
        lineas.append(f"[{'OK' if ok else 'FALLA'}] asset fingerprinted {ref} -> {nombre}{detalle}")

    # Slice 6, batch 3 (the client manifest): the home page emits
    # window.SANA_STATIC, the name-to-fingerprinted-URL map for assets that a
    # static JS file loads at runtime (static_fingerprint.CLIENT_LOADED), once
    # and with the same URLs calculated by the local checkout. The loop above
    # already fetches and compares each URL byte-for-byte (its regex also sees
    # them inside the JSON). This pins the map itself as present, complete, and
    # at the current hash; without it, the runtime loaders fail closed.
    manifest_matches = re.findall(r"window\.SANA_STATIC=(\{.*?\});</script>",
                           home.decode("utf-8", "ignore"))
    expected_manifest = static_fingerprint.manifest()
    try:
        observed_manifest = json.loads(manifest_matches[0]) if len(manifest_matches) == 1 else None
    except ValueError:
        observed_manifest = None
    manifest_ok = observed_manifest == expected_manifest
    fallas += 0 if manifest_ok else 1
    detail = "" if manifest_ok else (
        f" ({len(manifest_matches)} manifest(s) on home; expected 1 with "
        f"{sorted(expected_manifest)} at the local hash — observed {observed_manifest!r})")
    lineas.append(f"[{'OK' if manifest_ok else 'FAIL'}] window.SANA_STATIC on home{detail}")
    return fallas, lineas


if __name__ == "__main__":
    sys.exit(main())
