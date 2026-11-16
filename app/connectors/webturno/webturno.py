#!/usr/bin/env python3
"""
webturno.py — cliente del sistema de turnos Webturno (webturno.com.ar) para Pinamar.

Webturno tiene una UX horrible: hay que "registrarse" clínica por clínica aunque
la identidad y la contraseña son siempre las mismas (DNI + últimos 3 dígitos del DNI).
Este cliente esconde ese desastre: una sola identidad, todas las clínicas y todos los
médicos en un solo lugar, ordenados por el turno más cercano.

La API real es app_w_v70_2.php — GET, sin sesión (stateless): el DNI y la contraseña
viajan en cada request. Endpoints mapeados por ingeniería inversa:

  pf=dame_tipo      p0=DNI p1=PASS p2=CLIN          -> login / verificación de credenciales
  pf=dame_clinpag   p0=DNI                          -> clínicas donde el paciente está registrado
  pf=dame_clinmed   p2=CLIN tus=P med=0             -> médicos de una clínica
  pf=dame_turn op=l med=IDME p2=CLIN tus=P p0=DNI   -> turnos LIBRES de ese médico
  pf=dame_turn op=l med=  p2=CLIN tus=P p0=DNI      -> mis turnos pendientes en esa clínica
  pf=dame_turn op=a med=0 p2=CLIN tus=P p0=DNI      -> todos mis turnos agendados
  pf=dame_datclin   p2=CLIN                         -> datos de la clínica
  pf=dame_turn op=i ...                             -> RESERVAR (inferido, ver book())

Uso:
  python webturno.py login
  python webturno.py clinics
  python webturno.py doctors <clinic_token>
  python webturno.py slots <clinic_token> <idme>
  python webturno.py appointments
  python webturno.py db                 # construye data/availability.json (lo que usa la UI)

Moved here from repo-root webturno.py in the Phase 6b connector extraction
(2026-07-30) — judgment call (per this slice's brief: "check how webturno
actually works first, it may be a router not a source, judge and document"):
this is a REAL, self-contained slot-source API client — login, doctors,
slots, book and cancel. Its adapter is now operator-selectable for services
that carry WebTurno's `clinic_token` and `idme`; MSD remains the installation
default. server.py's
WebTurno client (the module-scope import `_OWNER_ENV = webturno._load_env()`
plus the routes that called `Webturno()`/`build_availability()`/
`refresh_doctors()` for the calendar sync) was deleted outright in
retirement slice 3 (2026-08-16 — tests/test_server_webturno_retirement.py),
so nothing in server.py or the admin panel consumes this connector anymore.
The connector registry's patient slot-source seam and the offline CLI
(`python3 -m connectors.webturno.webturno db`, `login`, `passwords`) are its
two supported entry points. The PATIENT-facing
multi-clinic directory this client used to power (`/clinicas`,
`/directorio`, `/clinica/*`) was explicitly de-linked back to `/` by
patient_web/site_*.py (routing-y-modulos.md, "do not revive it for the hospital").
Selecting the adapter does not revive those retired routes. See
connectors/webturno/connector.json for the full accounting.

The repo-root `webturno.py` compat shim was retired in Tier 2 batch L
(2026-08-07): import `connectors.webturno.webturno`, and the CLI runs as
`python3 -m connectors.webturno.webturno`. Two
__file__-relative paths fixed for the move (see `_ROOT` below): `main()`'s
`data/availability.json` output path resolves the REPO root, not this file's
own connectors/webturno/ directory —
`load_passwords()`'s `passwords.json` lookup was left resolving alongside
THIS file (connectors/webturno/passwords.json) since that setting file has
always lived next to the script itself, never at the repo root (it isn't
tracked in git either way — an operator-provided local file).
"""

import os
import re
import sys
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from app import connectors as _connectors_pkg
from app.core import config
from app.core import paths
from app.core import log_pii

# Repo root, NOT this file's own directory — see renaper.py's identical fix.
_ROOT = paths.PROJECT_ROOT

BASE = "https://webturno.com.ar/app_w_v70_2.php"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Clínica de entrada para el login (cualquiera donde el paciente esté registrado sirve).
# Pinamed no aparece en dame_clinpag pero es una clínica registrada, así que la sembramos.
PINAMED_PUBLIC_CLINIC_ID = "WkJ6OWpBSWNyUUFXR0dpQUNDR2daUT09"

# ---- directorio público de WebTurno (sin login) -----------------------
# La landing de webturno.com.ar arma su buscador con app_pag33.php; de ahí salen TODAS las
# clínicas (no solo donde el paciente está registrado), cada una con su localidad y dirección.
# El token de booking (p2=CLIN) se resuelve por clínica: logine.php?c=<idc> redirige a /login/<token>.
# WebTurno NO valida registro por clínica para leer médicos/turnos, así que un solo DNI alcanza.
DIR_BASE = "https://webturno.com.ar"
# Localidades de la zona de Pinamar que entran al directorio. WebTurno tiene clínicas de
# TODO el país (Mendoza, San Rafael, Bariloche, CABA, Chubut, Santa Fe, etc.) — esas NO van.
# Solo la zona: 1/11 = Pinamar, 18 = Mar de Ostende, 24 = Cariló, 22 = Pinamar-Villa Gesell-Cariló
# (Valeria del Mar cae bajo Pinamar, idlo 1), 7 = Villa Gesell. Madariaga (idlo 6) queda
# afuera por pedido del responsable del producto.
LOCALIDADES_PINAMAR = {"1", "7", "11", "18", "22", "24"}


def _dir_get(pf):
    req = urllib.request.Request(f"{DIR_BASE}/app_pag33.php?pf={pf}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _booking_token(idc):
    """idc del directorio público -> token base64 de la API de booking.
    logine.php?c=<idc> redirige a /login/<token>; ese token es el p2=CLIN."""
    req = urllib.request.Request(f"{DIR_BASE}/logine.php?c={idc}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r"/login/([A-Za-z0-9+/=]+)", html)
    return m.group(1) if m else None


def directory_clinics(localidades=None, verbose=True):
    """Clínicas del directorio público de WebTurno. Devuelve [{token, nombre, idc, direccion}].
    No requiere login: usa app_pag33.php (público) + logine.php para el token de booking.

    localidades=None (default): TODAS las clínicas del directorio (todas las localidades de la
    zona — Pinamar, Ostende, Valeria, Cariló, Madariaga, Villa Gesell, etc.; sin podar). Pasar
    un set de idlo (p.ej. LOCALIDADES_PINAMAR) para restringir solo a esas localidades."""
    try:
        data = _dir_get("dame_clin_inic")
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"directorio público no disponible: {log_pii.exc(e)}", file=sys.stderr)
        return []
    out = []
    for c in data.get("clinicas", []):
        locs = {x for x in (c.get("localidades") or "").split("-") if x}
        if localidades and not (locs & set(localidades)):
            continue
        idc = c.get("idc")
        try:
            tok = _booking_token(idc)
        except Exception:  # noqa: BLE001
            tok = None
        if not tok:
            if verbose:
                print(f"  sin token de booking: {c.get('title')} (idc={idc})", file=sys.stderr)
            continue
        out.append({"token": tok, "nombre": (c.get("title") or "").strip(),
                    "idc": idc, "direccion": (c.get("direccion") or "").strip()})
    return out


def _load_env():
    """WebTurno credentials with canonical config precedence.

    Only the two credentials this connector itself consumes — exactly the
    manifest's ``config_keys``. The keys the retired server.py client used
    to read through here (``OWNER_DNI``, ``SACOTURNO_REMOTE_URL``) went with
    it (retirement slice 3, 2026-08-16).

    This intentionally follows ``core.config`` (DB override, process
    environment, then ``TURNOS_ENV_FILE``) instead of a second env parser.
    """
    env = {}
    for k in (
        "WEBTURNO_DNI",
        "WEBTURNO_PASS",
    ):
        value = config.get(k)
        if value:
            env[k] = value
    return env


class WebturnoError(Exception):
    pass


class Webturno:
    def __init__(self, dni=None, password=None, timeout=20):
        env = _load_env()
        self.dni = str(dni or env.get("WEBTURNO_DNI") or "").strip()
        # Contraseña por defecto de Webturno = últimos 3 dígitos del DNI.
        self.password = str(
            password or env.get("WEBTURNO_PASS") or (self.dni[-3:] if self.dni else "")
        ).strip()
        self.timeout = timeout
        if not self.dni:
            raise WebturnoError("Falta WEBTURNO_DNI (en la configuración runtime o argumento).")

    # ---- transporte ----------------------------------------------------
    def _get(self, pf, **params):
        params = {k: v for k, v in params.items() if v is not None}
        qs = urllib.parse.urlencode({"pf": pf, **params})
        url = f"{BASE}?{qs}"
        # Reintentos: la egress del contenedor a webturno.com.ar tira timeouts transitorios;
        # sin esto, un solo timeout abortaba el build completo (clínicas nuevas no aparecían).
        raw = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read().decode("utf-8", "replace").strip()
                break
            except Exception as e:  # noqa: BLE001 — timeout/red: reintentar antes de rendirse
                if attempt == 2:
                    raise WebturnoError(f"red falló en pf={pf}: {e}")
                time.sleep(0.6 * (attempt + 1))
        # El backend devuelve JSON con espacios raros y alguna coma doble (",,").
        cleaned = raw.replace(",,", ",")
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            if "Error en nombre de consulta" in raw:
                raise WebturnoError(f"Consulta desconocida: pf={pf}")
            raise WebturnoError(f"Respuesta no-JSON de pf={pf}: {raw[:120]}")

    # ---- endpoints -----------------------------------------------------
    def login(self, clinic=PINAMED_PUBLIC_CLINIC_ID):
        """Verifica credenciales contra una clínica. Devuelve el dict de usuario."""
        d = self._get("dame_tipo", p0=self.dni, p1=self.password, p2=clinic)
        if d.get("status") != "success":
            raise WebturnoError(f"Login falló: {d}")
        return d

    def my_clinics(self):
        """Clínicas donde el paciente está registrado (incluye Pinamed sembrada)."""
        d = self._get("dame_clinpag", p0=self.dni)
        clinics = [{"token": c["idc"], "nombre": c["nombre"]} for c in d.get("clinicas", [])]
        if not any(c["token"] == PINAMED_PUBLIC_CLINIC_ID for c in clinics):
            clinics.insert(0, {"token": PINAMED_PUBLIC_CLINIC_ID, "nombre": "Pinamed"})
        return clinics

    def doctors(self, clinic):
        d = self._get("dame_clinmed", p2=clinic, tus="P", med=0)
        return d.get("especialistas", [])

    def slots(self, clinic, idme):
        """Turnos libres de un médico. Lista de {fecha, fectur, idtur}."""
        d = self._get("dame_turn", op="l", med=idme, p2=clinic, tus="P", p0=self.dni)
        return d.get("turnos", [])

    def my_appointments(self, clinic):
        d = self._get("dame_turn", op="a", med=0, p2=clinic, tus="P", p0=self.dni)
        return d.get("turnos", [])

    def upcoming_appointments(self):
        """Turnos próximos/agendados del paciente (op=a es patient-scoped: una sola llamada
        devuelve todos). Devuelve lista normalizada ordenada por fecha."""
        d = self._get("dame_turn", op="a", med=0,
                      p2=PINAMED_PUBLIC_CLINIC_ID, tus="P", p0=self.dni)
        seen, out = set(), []
        for t in d.get("turnos", []):
            idtur = t.get("idtur")
            if idtur in seen:
                continue
            seen.add(idtur)
            out.append({
                "fecha": t.get("fecha"),
                "fectur": t.get("fectur"),
                "idtur": idtur,
                "medico": (t.get("medico") or "").strip(),
                "especialidad": (t.get("especialidad") or "").strip(),
                "clinica": (t.get("nomclin") or "").strip(" ()").strip(),
                "esttur": t.get("esttur"),
            })
        out.sort(key=lambda x: x.get("fecha") or "")
        return out

    def book(self, clinic, idtur, idme=None):
        """
        RESERVAR (tomar) un turno. Endpoint real verificado en vivo (book+cancel):
          pf=mod_tur op=T usuario=DNI tus=P p0=DNI p2=CLIN idt=IDTUR
        Devuelve {status, mensaje, color}. Crea un turno médico real, pero es
        reversible con cancel_appointment(). idme es opcional (no lo pide el backend).
        """
        return self._get(
            "mod_tur", op="T", usuario=self.dni, tus="P", p0=self.dni, p2=clinic, idt=idtur
        )

    def cancel_appointment(self, clinic, idtur, med=0):
        """
        CANCELAR un turno agendado. Endpoint real verificado en vivo:
          pf=mod_tur op=C usuario=DNI tus=P p0=DNI p2=CLIN idt=IDTUR
        Devuelve {status, mensaje, color}. Éxito = status:success y el idtur
        desaparece de upcoming_appointments().
        """
        return self._get(
            "mod_tur", op="C", usuario=self.dni, tus="P", p0=self.dni, p2=clinic, idt=idtur
        )

    def auth_ok(self, clinic, password):
        """True si (DNI, password) autentica en esa clínica (dame_tipo -> success)."""
        d = self._get("dame_tipo", p0=self.dni, p1=password, p2=clinic)
        return d.get("status") == "success"

    def change_password(self, clinic, new_password):
        """
        Cambia la contraseña del paciente en una clínica.
        Endpoint real (ingeniería inversa de la SPA, pantalla cambiar-clave):
          pf=modif_pass op=p p0=DNI p1=NUEVA p2=CLINICA usuario=DNI
        Nota: el backend NO pide la contraseña anterior, sólo setea la nueva.
        Reversible: volver a llamar con la contraseña previa.
        """
        d = self._get(
            "modif_pass", op="p", p0=self.dni, p1=new_password, p2=clinic, usuario=self.dni
        )
        return d


# ---- helpers de "próximo turno" ---------------------------------------
def _apply_slots(row, slots):
    """Vuelca la respuesta de slots() sobre una fila, derivando online del resultado."""
    slots = list(slots or [])
    if slots:
        slots.sort(key=lambda s: s.get("fecha", ""))
        row["proximo_turno"] = slots[0].get("fecha")
        row["proximo_turno_fmt"] = slots[0].get("fectur")
        row["turnos_libres"] = len(slots)
        row["slots"] = [
            {"fecha": s.get("fecha"), "fectur": s.get("fectur"), "idtur": s.get("idtur")}
            for s in slots[:8]
        ]
    else:
        row["proximo_turno"] = None
        row["proximo_turno_fmt"] = None
        row["turnos_libres"] = 0
        row["slots"] = []
    row["online"] = row["turnos_libres"] > 0
    row["refreshed"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return row


def build_availability(wt, max_workers=6, verbose=True, include_directory=True):
    """
    Recorre las clínicas y todos sus médicos, trae el próximo turno libre de cada uno
    y arma una base unificada. Devuelve el dict de salida.

    include_directory=True (default): además de las clínicas donde el dueño está
    registrado, suma las clínicas del directorio público de WebTurno que están en la zona
    de Pinamar (LOCALIDADES_PINAMAR) — NO las de otras provincias (Mendoza, etc.). Como
    WebTurno no valida registro por clínica para leer, el mismo DNI alcanza — sin cuentas.
    """
    wt.login()
    clinics = wt.my_clinics()
    if include_directory:
        have = {c["token"] for c in clinics}
        for c in directory_clinics(LOCALIDADES_PINAMAR, verbose=verbose):
            if c["token"] not in have:
                clinics.append({"token": c["token"], "nombre": c["nombre"]})
                have.add(c["token"])
    if verbose:
        print(f"Clínicas a recorrer: {len(clinics)} -> {[c['nombre'] for c in clinics]}", file=sys.stderr)

    rows = []
    for c in clinics:
        try:
            docs = wt.doctors(c["token"])
        except Exception as e:  # noqa: BLE001 — un timeout/clínica caída no debe abortar todo el build
            if verbose:
                print(f"  [{c['nombre']}] sin médicos: {log_pii.exc(e)}", file=sys.stderr)
            continue
        if verbose:
            print(f"  [{c['nombre']}] {len(docs)} médicos", file=sys.stderr)

        # una fila por médico; el estado online se deriva de la API (no de heurística de texto)
        clinic_rows = []
        for m in docs:
            esp = ", ".join(e.get("nom", "") for e in m.get("especialidades", []))
            os_list = [o.get("nom", "") for o in m.get("obrasSociales", [])]
            row = {
                "clinica": c["nombre"],
                "clinic_token": c["token"],
                "idme": m.get("idme"),
                "nombre": m.get("nombre", "").strip(),
                "especialidad": esp,
                "horario": (m.get("horario") or "").strip(),
                "mensaje": (m.get("mensaje") or "").strip(),
                "pie": (m.get("pie") or "").strip(),
                "obras_sociales": os_list,
                "online": False,
                "proximo_turno": None,
                "proximo_turno_fmt": None,
                "turnos_libres": 0,
                "slots": [],
                "refreshed": None,
            }
            rows.append(row)
            clinic_rows.append(row)

        # traer slots de TODOS los médicos en paralelo; online = turnos_libres > 0
        def fetch(row):
            try:
                slots = wt.slots(row["clinic_token"], row["idme"])
            except Exception:  # noqa: BLE001 — timeout/red: ese médico queda sin slots, no rompe el build
                slots = []
            return _apply_slots(row, slots)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(fetch, r) for r in clinic_rows]
            done = 0
            for _ in as_completed(futs):
                done += 1
                if verbose and done % 10 == 0:
                    print(f"    ...{done}/{len(clinic_rows)} agendas leídas", file=sys.stderr)

    con_turno = sum(1 for r in rows if r["proximo_turno"])
    out = {
        "generado": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "paciente": wt.dni,
        "clinicas": [c["nombre"] for c in clinics],
        "total_medicos": len(rows),
        "con_turno_online": con_turno,
        "medicos": rows,
    }
    try:
        out["mis_turnos"] = wt.upcoming_appointments()
    except Exception:  # noqa: BLE001 — no perder todo el build por un timeout al final
        out["mis_turnos"] = []
    if verbose:
        print(f"Listo: {len(rows)} médicos, {con_turno} con turno online.", file=sys.stderr)
    return out


def refresh_doctors(wt, db, pairs, max_workers=6):
    """Re-trae los slots de cada par {clinic_token, idme} y actualiza la fila que
    coincide en db['medicos'] in place (proximo_turno, proximo_turno_fmt,
    turnos_libres, slots[:8], online, refreshed=now). Devuelve la lista de filas
    actualizadas. Las consultas se hacen concurrentemente."""
    rows_by_key = {(r.get("clinic_token"), r.get("idme")): r for r in db.get("medicos", [])}

    targets = []
    for p in pairs or []:
        key = (p.get("clinic_token"), p.get("idme"))
        row = rows_by_key.get(key)
        if row is not None and row not in targets:
            targets.append(row)

    def fetch(row):
        try:
            slots = wt.slots(row["clinic_token"], row["idme"])
        except WebturnoError:
            slots = []
        return _apply_slots(row, slots)

    if targets:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(as_completed([ex.submit(fetch, r) for r in targets]))
    return targets


# ---- gestión de contraseñas (setting) ---------------------------------
def load_passwords():
    """
    Lee la 'setting' de contraseñas desde passwords.json (junto a este archivo):
      { "candidates": ["123", "MI_CLAVE_UNIFICADA"],   # se prueban EN ESTE ORDEN
        "desired": "MI_CLAVE_UNIFICADA" }              # a la que se quiere unificar todo
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "passwords.json")
    if not os.path.exists(path):
        raise WebturnoError("Falta passwords.json (candidates + desired).")
    with open(path) as f:
        cfg = json.load(f)
    cfg.setdefault("candidates", [])
    if not cfg.get("desired"):
        raise WebturnoError("passwords.json sin 'desired'.")
    # la deseada siempre debe estar entre las candidatas a probar
    if cfg["desired"] not in cfg["candidates"]:
        cfg["candidates"].append(cfg["desired"])
    return cfg


def _safe_auth(wt, tok, pw):
    try:
        return wt.auth_ok(tok, pw)
    except WebturnoError:
        return False


def sync_passwords(wt, apply=False, verbose=True):
    """
    Para cada clínica del paciente prueba las contraseñas candidatas EN ORDEN y
    registra cuál autentica. Si apply=True, cambia las que no coinciden con la
    deseada y verifica. Devuelve {desired, clinicas:[{clinica, actual, cambiada, ok, nota?}]}.
    """
    cfg = load_passwords()
    candidates, desired = cfg["candidates"], cfg["desired"]
    out = []
    for c in wt.my_clinics():
        name, tok = c["nombre"], c["token"]
        found = next((pw for pw in candidates if _safe_auth(wt, tok, pw)), None)
        row = {"clinica": name, "actual": found, "cambiada": False, "ok": found == desired}
        if found is None:
            row["nota"] = "ninguna candidata autenticó"
        elif apply and found != desired:
            wt.change_password(tok, desired)
            row["ok"] = wt.auth_ok(tok, desired)
            row["cambiada"] = True
            row["actual"] = desired if row["ok"] else found
            if not row["ok"]:
                row["nota"] = "el cambio no verificó"
        if verbose:
            mark = "✓" if row["ok"] else ("·" if found else "✗")
            shown = "—" if found is None else ("(deseada)" if row["actual"] == desired else row["actual"])
            extra = " [cambiada]" if row["cambiada"] else ""
            print(f"  {mark} {name:<26} {shown}{extra}", file=sys.stderr)
        out.append(row)
    return {"desired": desired, "clinicas": out}


# ---- CLI ---------------------------------------------------------------
def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    wt = Webturno()

    if cmd == "login":
        print(json.dumps(wt.login(), ensure_ascii=False, indent=2))
    elif cmd == "clinics":
        print(json.dumps(wt.my_clinics(), ensure_ascii=False, indent=2))
    elif cmd == "doctors":
        print(json.dumps(wt.doctors(argv[1]), ensure_ascii=False, indent=2))
    elif cmd == "slots":
        print(json.dumps(wt.slots(argv[1], argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "appointments":
        out = []
        for c in wt.my_clinics():
            for t in wt.my_appointments(c["token"]):
                out.append(t)
        # dedup por idtur
        seen, uniq = set(), []
        for t in out:
            if t.get("idtur") not in seen:
                seen.add(t.get("idtur"))
                uniq.append(t)
        print(json.dumps(uniq, ensure_ascii=False, indent=2))
    elif cmd == "db":
        out = build_availability(wt)
        path = os.path.join(_ROOT, "data", "availability.json")
        with open(path, "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(path)
    elif cmd == "passwords":
        # passwords            -> sólo detecta qué contraseña usa cada clínica
        # passwords --apply    -> además cambia todas a la deseada y verifica
        apply = "--apply" in argv[1:]
        print(("APLICANDO cambios a la contraseña deseada" if apply
               else "DETECCIÓN (sin cambios). Usá --apply para unificar.") + ":", file=sys.stderr)
        res = sync_passwords(wt, apply=apply)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"comando desconocido: {cmd}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
