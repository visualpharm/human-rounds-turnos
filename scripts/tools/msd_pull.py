#!/usr/bin/env python3
"""
msd_pull.py — Pull doctors + next available appointments from Mi Salud Digital
(Provincia de Buenos Aires) for Pinamar's centros. Zero AI tokens: pure HTTP +
AES. Self-refreshes the session token from the logged-in Chrome cookie each run,
so you can re-run it any time without touching an LLM.

  python3 scripts/tools/msd_pull.py               # Pinamar, table + JSON to data/msd_turnos.json
  python3 scripts/tools/msd_pull.py --partido 27  # La Plata (has open online diaries)
  python3 scripts/tools/msd_pull.py --json        # JSON only, to stdout

Requires: pycryptodome. Reads Chrome's cookie DB to recover the `k` JWT; if that
fails (Chrome closed / cookie rotated), falls back to /tmp/msd_jwt.txt.
Docs: docs/solutions/misalud-digital-pba-turnos-api.md
"""
import argparse, base64, json, os, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.request, urllib.error
from datetime import datetime
from hashlib import pbkdf2_hmac
from Crypto.Cipher import AES

BASE = "https://misaluddigital.ms.gba.gov.ar"
# SPA-hardcoded envelope (module 8411): AES-192-CBC, PKCS7, base64.
ENV_KEY = b"T313med1cin4fortestingpl"          # 24 bytes -> AES-192
ENV_IV = b"0123456789abcdef"
JWT_FALLBACK = "/tmp/msd_jwt.txt"


class AuthExpired(Exception):
    """Token rejected (401/403). Re-login in Chrome; keep the last good dump."""
CHROME_COOKIES = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome/Default/Cookies")


def _unpad(b):
    return b[: -b[-1]] if b and 1 <= b[-1] <= 16 else b


def env_decrypt(b64text):
    raw = base64.b64decode(b64text)
    return _unpad(AES.new(ENV_KEY, AES.MODE_CBC, ENV_IV).decrypt(raw)).decode("utf-8")


# ---- token: refresh from live Chrome cookie, else cached file ----------------
def token_from_chrome():
    if not os.path.exists(CHROME_COOKIES):
        return None
    try:
        pw = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
            stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        return None
    key = pbkdf2_hmac("sha1", pw, b"saltysalt", 1003, 16)
    tmp = os.path.join(tempfile.gettempdir(), "msd_cookies_live.db")
    shutil.copy2(CHROME_COOKIES, tmp)          # Chrome keeps the DB locked; copy it
    try:
        con = sqlite3.connect(tmp)
        rows = con.execute(
            "SELECT encrypted_value FROM cookies WHERE name='k' "
            "AND host_key LIKE '%misaluddigital%'").fetchall()
        con.close()
    finally:
        os.remove(tmp)
    for (enc,) in rows:
        if not enc or enc[:3] != b"v10":
            continue
        dec = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(enc[3:])
        dec = _unpad(dec)
        # Newer Chrome prepends a 32-byte SHA256 domain hash to the plaintext.
        for cand in (dec, dec[32:]):
            s = cand.decode("utf-8", "ignore")
            if s.startswith("eyJ"):            # looks like a JWT
                return s
    return None


def load_token():
    t = token_from_chrome()
    src = "chrome"
    if not t and os.path.exists(JWT_FALLBACK):
        with open(JWT_FALLBACK) as f:
            t = f.read().strip()
        src = "cache"
    if not t:
        sys.exit("No token: open Chrome logged into misaluddigital, or seed " + JWT_FALLBACK)
    # sanity: not expired
    try:
        p = t.split(".")[1]; p += "=" * (-len(p) % 4)
        exp = json.loads(base64.urlsafe_b64decode(p)).get("exp", 0)
        if exp and exp < time.time():
            sys.stderr.write(f"[warn] token expired {datetime.fromtimestamp(exp)}; re-login in Chrome\n")
    except Exception:
        pass
    # keep a fresh copy for next run's fallback
    if src == "chrome":
        try:
            with open(JWT_FALLBACK, "w") as f:
                f.write(t)
        except OSError:
            pass
    sys.stderr.write(f"[token] via {src}\n")
    return t


# ---- API ---------------------------------------------------------------------
def api_get(path, token):
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": "Bearer " + token,
        "Accept": "text/plain, */*",
        "User-Agent": "Mozilla/5.0 (sana-turnos msd_pull)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8").strip()
    except urllib.error.HTTPError as e:
        if e.code in (404, 204):
            e.close()  # swallowed without reading: close the body it holds
            return None                        # SPA treats NOT_FOUND as []
        if e.code in (401, 403):
            e.close()  # replaced by AuthExpired: nobody else can close it
            raise AuthExpired()
        raise
    if not body:
        return None
    try:
        obj = json.loads(env_decrypt(body))
    except Exception:
        obj = json.loads(body)                 # some errors come back plain
    # API wraps payloads: {httpStatusCode, message:"SUCCESS", data:[...]}
    if isinstance(obj, dict) and "data" in obj and "httpStatusCode" in obj:
        return obj["data"]
    return obj


def centros(partido, token):
    d = api_get(f"/api/reserva-turno/obtener/instituciones/partido/{partido}", token)
    return d or []


def especialidades(inst_id, token):
    d = api_get(f"/api/reserva-turno/obtener/especialidad/institucion/{inst_id}", token)
    return d or []


def slots(esp_id, partido, token, node=1, size=200):
    """All slots for an especialidad across the partido (paginated).

    NB: the endpoint keys on especialidad.id, NOT especialidad.codigo — codigo
    collides across especialidades (Cardiología.codigo 131 == Pediatría.id 131),
    which silently returned the wrong especialidad's slots. And it returns the
    whole partido, so callers must filter by institucion.id per centro.
    """
    out, page = [], 0
    while True:
        d = api_get(
            f"/api/reserva-turno/obtener/turno-disponible/{node}/partido/{partido}"
            f"/especialidad/{esp_id}?page={page}&size={size}", token)
        content = (d.get("content") if isinstance(d, dict) else d) if d else None
        if not content:
            break
        out.extend(content)
        if not isinstance(d, dict) or d.get("last") or len(out) >= (d.get("totalElements") or 0):
            break
        page += 1
    return out


def prof_name(p):
    if not isinstance(p, dict):
        return str(p or "").strip()
    for k in ("nombreCompleto", "apellidoNombre"):
        if p.get(k):
            return str(p[k]).strip()
    return " ".join(str(p.get(x, "")).strip() for x in ("apellido", "nombre")).strip()


# ---- main --------------------------------------------------------------------
def pull(partido):
    token = load_token()
    out = {"partido": partido, "generado": datetime.now().isoformat(timespec="seconds"),
           "centros": []}
    slot_cache = {}  # esp.id -> partido-wide slots (fetched once, shared across centros)
    for c in centros(partido, token):
        cinfo = {"id": c.get("id"), "nombre": c.get("nombre"),
                 "online": c.get("online"), "direccion": c.get("direccion"),
                 "especialidades": []}
        for e in especialidades(c["id"], token):
            esp = e.get("especialidad", {}) or {}
            eid = esp.get("id")
            if eid not in slot_cache:
                slot_cache[eid] = slots(eid, partido, token) if eid is not None else []
            # the endpoint returns the whole partido — keep only this centro's slots
            sl = [s for s in slot_cache[eid]
                  if (s.get("institucion") or {}).get("id") == c.get("id")]
            # group next appointments per professional
            docs = {}
            for s in sl:
                who = prof_name(s.get("profesional")) or "(sin asignar)"
                docs.setdefault(who, []).append({
                    "dia": s.get("day"), "hora": s.get("hour"),
                    "diaryId": s.get("diaryId"),
                    "ordenRequerida": s.get("referralOrderRequired"),
                })
            for v in docs.values():
                v.sort(key=lambda x: (x["dia"] or "", x["hora"] or ""))
            cinfo["especialidades"].append({
                "nombre": esp.get("nombre"), "id": eid, "codigo": esp.get("codigo"),
                "turnos_totales": len(sl),
                "medicos": [{"medico": k, "proximo": v[0] if v else None,
                             "turnos": v} for k, v in sorted(docs.items())],
            })
        out["centros"].append(cinfo)
    return out


def print_table(out):
    P = lambda *a: print(*a)
    P(f"\nPartido {out['partido']} — {out['generado']}")
    for c in out["centros"]:
        online = "online" if c["online"] else "sin agenda online"
        P(f"\n■ {c['nombre']}  ({online})  ·  {c.get('direccion') or ''}")
        if not c["especialidades"]:
            P("    (sin especialidades)")
        for e in c["especialidades"]:
            n = e["turnos_totales"]
            tag = f"{n} turnos" if n else "sin turnos online (solicitud c/orden)"
            P(f"  · {e['nombre']}  [{tag}]")
            for m in e["medicos"]:
                px = m["proximo"]
                when = f"{px['dia']} {px['hora']}" if px else "—"
                P(f"      {m['medico']:<32} próximo: {when}  ({len(m['turnos'])} disp.)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partido", type=int, default=60, help="60=Pinamar (default), 27=La Plata")
    ap.add_argument("--json", action="store_true", help="JSON to stdout, no table")
    ap.add_argument("--out", default=None, help="write JSON here (default data/msd_turnos.json)")
    a = ap.parse_args()
    try:
        out = pull(a.partido)
    except AuthExpired:
        sys.stderr.write("[auth] token expired/rejected — log into misaluddigital in "
                         "Chrome to refresh. Keeping the last good dump.\n")
        sys.exit(3)
    dest = a.out or os.path.join(os.path.dirname(__file__), "..", "..", "data", "msd_turnos.json")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_table(out)
        n_docs = sum(len(e["medicos"]) for c in out["centros"] for e in c["especialidades"])
        n_slots = sum(e["turnos_totales"] for c in out["centros"] for e in c["especialidades"])
        print(f"\n{len(out['centros'])} centros · {n_docs} médicos · {n_slots} turnos → {os.path.abspath(dest)}")


if __name__ == "__main__":
    main()
