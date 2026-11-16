#!/usr/bin/env python3
"""browser_smoke.py — smoke de JS real en Chrome headless contra :7700 (o prod).

POR QUÉ (2026-07-18): el rename EN dejó `wireOsCombo()` colgado en
account-form — un ReferenceError que tiraba DENTRO de wire() ANTES de
cablear el submit. Resultado: el form de registro se veía perfecto, pero
"Crear cuenta" hacía GET nativo con la password en la URL. Ningún test de
unidad lo ve (node --check sólo valida sintaxis, no referencias): hace falta
EJECUTAR el JS en un navegador y escuchar Runtime.exceptionThrown.

Uso:
    python3 scripts/browser_smoke.py                      # contra http://127.0.0.1:7700
    python3 scripts/browser_smoke.py https://demo:demo@example.test

Requiere Google Chrome instalado y (para el default) el server de dev en :7700.
Deps: pip install websocket-client (ya está en la máquina de desarrollo). Exit 0 = todo verde.

Qué chequea por página: CERO excepciones no capturadas durante la carga + que
el mount clave existe (o sea, el JS corrió de verdad, no sólo parseó).
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9341  # puerto CDP propio del smoke, no pisa otros

# (path, expresión que debe dar true cuando el JS de la página terminó de montar)
PAGES = [
    ("/account?mode=register",
     "!!document.getElementById('sc-dni') && typeof window.SanaAccount === 'undefined'"),
    ("/service/clinica-medica-pinamar/book",
     "!!document.querySelector('#svc-form-host .sec')"),
    ("/",
     "!!document.getElementById('stznav') && document.getElementById('stznav').children.length > 0"),
    ("/my-requests",
     "!!document.getElementById('stznav')"),
]


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7700"
    auth_hdr = None
    if "@" in base.split("//", 1)[-1].split("/")[0]:
        # https://user:pass@host — sacar las creds de la URL (rompen fetch())
        proto, rest = base.split("//", 1)
        creds, host = rest.split("@", 1)
        base = f"{proto}//{host}"
        auth_hdr = "Basic " + base64.b64encode(creds.encode()).decode()

    if not os.path.exists(CHROME):
        print("SKIP: Chrome no encontrado")
        return 0

    profile = tempfile.mkdtemp(prefix="smoke-chrome-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}",
         "--remote-allow-origins=*", f"--user-data-dir={profile}",
         "--window-size=1280,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import websocket
        for _ in range(20):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        page = next(t for t in tabs if t.get("type") == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30, max_size=None)
        seq = [0]
        exceptions = []

        def cmd(method, params=None, collect_secs=0):
            seq[0] += 1
            ws.send(json.dumps({"id": seq[0], "method": method, "params": params or {}}))
            end = time.time() + collect_secs
            result = None
            while True:
                try:
                    ws.settimeout(max(0.5, end - time.time()) if collect_secs else 15)
                    msg = json.loads(ws.recv())
                except Exception:  # noqa: BLE001
                    if collect_secs and time.time() >= end:
                        return result
                    raise
                if msg.get("method") == "Runtime.exceptionThrown":
                    d = msg["params"]["exceptionDetails"]
                    exceptions.append((d.get("exception", {}) or {}).get("description")
                                      or d.get("text") or "excepción sin detalle")
                if msg.get("id") == seq[0]:
                    result = msg
                    if not collect_secs:
                        return msg
                if collect_secs and time.time() >= end:
                    return result

        cmd("Runtime.enable")
        cmd("Page.enable")
        cmd("Network.enable")
        if auth_hdr:
            cmd("Network.setExtraHTTPHeaders", {"headers": {"Authorization": auth_hdr}})

        fallas = 0
        for path, expr in PAGES:
            exceptions.clear()
            cmd("Page.navigate", {"url": base + path}, collect_secs=6)
            r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            ok_mount = bool(r["result"]["result"].get("value"))
            # ruido benigno (extensiones, favicon) no cuenta; sólo errores de NUESTRO js
            propias = [e for e in exceptions if "extension" not in e.lower()]
            status = "OK" if (ok_mount and not propias) else "FALLA"
            if status == "FALLA":
                fallas += 1
            print(f"[{status}] {path}  mount={ok_mount} excepciones={len(propias)}")
            for e in propias[:3]:
                print(f"        {e.splitlines()[0][:160]}")
        return 1 if fallas else 0
    finally:
        proc.kill()
        subprocess.run(["rm", "-rf", profile], check=False)


if __name__ == "__main__":
    sys.exit(main())
