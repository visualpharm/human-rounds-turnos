#!/usr/bin/env python3
"""seed_patient_account.py — create-or-update a FULL patient account
(email, WhatsApp/phone contact, DNI, and a realistic clinical history
including lab results) against a running Human Rounds instance, entirely
through its admin HTTP API.

Why HTTP and not a direct DB write (docs/pinamar/deploy-coolify.md): a
Coolify deployment's Postgres is not reachable from outside the deployment —
scripts/import_clinical_history.py (`db_clinical.clinical_history_replace` direct)
only works against a database this machine can open a socket to. This script
uses the SAME admin console the front desk uses (admin_patients.py), so it
works identically against a local dev server and against prod.

Usage (local, default target http://127.0.0.1:7700):
    python3 scripts/seed_patient_account.py \\
        --dni 20304050 --nombre Ilya --apellido Belov \\
        --nacimiento 1990-01-01 --sexo M \\
        --email ilya@example.com --telefono "+5492254000000"

Usage (prod):
    python3 scripts/seed_patient_account.py \\
        --target https://pinamar.humanrounds.org \\
        --dni 20304050 --email owner@example.test --telefono "+5492254000000" \\
        --turnstile-bypass "$TURNOS_TURNSTILE_TEST_BYPASS_SECRET"
(add --http-auth user:pass only if the deployment still sits behind a
Coolify/Cloudflare basic-auth gate — see docs/pinamar/deploy-coolify.md)

Admin credentials, in order: --admin-user/--admin-pass/--admin-totp-code,
then TURNOS_ADMIN_USER/TURNOS_ADMIN_PASS env, then credential vault
(`op item get "Human Rounds — Admin" --vault AI`, TOTP resolved live with
`--otp` so the raw secret never has to leave credential vault). Nothing here is
ever printed — a freshly generated patient password is written straight
into credential vault (best-effort) instead of stdout.

Clinical history: reuses the exact generator alepo/marqui already fall back
to for any demo patient without a curated fixture of their own — the
"plantillas" (adulto_f/adulto_m/menor) in connectors/alepo|marqui/
sample_historias.json (see connectors/legacy_history.py:LegacySystem._template_for). Both
systems' template for the chosen archetype are combined (older paper-era
entries + newer digitized ones — the same two-system story the canonical
demo patient Sofía Herrera tells) and every date is shifted by the same
offset so the most recent entry lands `--dias-desde-ultimo` days before
today, preserving the original spacing between visits. Written with
source_system="local_history" — the per-installation private archive
(see docs/architecture/resumen-paciente.md) — never "alepo"/"marqui",
which stay reserved for the demo and tests.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_ADMIN_ITEM = "Human Rounds — Admin"
_ADMIN_VAULT = "AI"

_ARCHETYPE_BY_SEXO = {"M": "adulto_m", "F": "adulto_f", "X": "adulto_f"}


# ---- transporte (curl: python-urllib se cuelga contra el WAF de prod, ------
# mismo motivo que scripts/smoke_prod.py) --------------------------------

class Http:
    def __init__(self, base, http_auth=None, timeout=25):
        self.base = base.rstrip("/")
        self.http_auth = http_auth
        self.timeout = timeout
        self._jar = tempfile.NamedTemporaryFile(prefix="hr-seed-cookies-", suffix=".txt", delete=False)
        self._jar.close()

    def close(self):
        try:
            os.unlink(self._jar.name)
        except OSError:
            pass

    def request(self, method, path, body=None, extra_headers=None):
        cmd = ["curl", "-s", "-m", str(self.timeout), "-w", "\n%{http_code}",
               "-X", method, "-c", self._jar.name, "-b", self._jar.name]
        if self.http_auth:
            cmd += ["-u", self.http_auth]
        for h in (extra_headers or {}).items():
            cmd += ["-H", f"{h[0]}: {h[1]}"]
        data = None
        if body is not None:
            cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        cmd.append(self.base + path)
        r = subprocess.run(cmd, input=data, capture_output=True, timeout=self.timeout + 5)
        out = r.stdout
        if not out or b"\n" not in out:
            return 0, {}
        raw_body, code = out.rsplit(b"\n", 1)
        try:
            status = int(code)
        except ValueError:
            status = 0
        try:
            parsed = json.loads(raw_body) if raw_body.strip() else {}
        except ValueError:
            parsed = {"_raw": raw_body.decode("utf-8", "replace")}
        return status, parsed

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body, extra_headers=None):
        return self.request("POST", path, body, extra_headers)

    def put(self, path, body):
        return self.request("PUT", path, body)


# ---- credenciales de admin ------------------------------------------------

def _op(*args):
    try:
        r = subprocess.run(["op", *args], capture_output=True, timeout=15, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def resolve_admin_credentials(args):
    """(usuario, password, codigo_totp_o_None). Nunca imprime nada de esto."""
    usuario = args.admin_user or os.environ.get("TURNOS_ADMIN_USER")
    password = args.admin_pass or os.environ.get("TURNOS_ADMIN_PASS")
    codigo = args.admin_totp_code or os.environ.get("TURNOS_ADMIN_TOTP_CODE")
    if usuario and password:
        return usuario, password, codigo
    usuario = usuario or _op("item", "get", _ADMIN_ITEM, "--vault", _ADMIN_VAULT, "--fields", "username")
    password = password or _op("item", "get", _ADMIN_ITEM, "--vault", _ADMIN_VAULT, "--fields", "password", "--reveal")
    if not codigo:
        codigo = _op("item", "get", _ADMIN_ITEM, "--vault", _ADMIN_VAULT, "--otp")
    if not usuario or not password:
        raise SystemExit(
            "No pude resolver las credenciales de admin: pasá --admin-user/--admin-pass, "
            "seteá TURNOS_ADMIN_USER/TURNOS_ADMIN_PASS, o tené acceso a credential vault "
            f'("{_ADMIN_ITEM}", vault {_ADMIN_VAULT}).')
    return usuario, password, (codigo or None)


def login(http, args):
    usuario, password, codigo = resolve_admin_credentials(args)
    headers = {}
    bypass = args.turnstile_bypass or os.environ.get("TURNOS_TURNSTILE_TEST_BYPASS_SECRET")
    if bypass:
        headers["X-Turnstile-Test-Bypass"] = bypass
    body = {"usuario": usuario, "password": password}
    if codigo:
        body["codigo"] = codigo
    status, out = http.post("/api/admin/login", body, headers)
    if status == 401 and out.get("need_totp") and not codigo:
        raise SystemExit(
            "El admin tiene 2FA (TOTP) activado y no se pudo resolver un código vivo — "
            "pasá --admin-totp-code o revisá que credential vault tenga el campo 'one-time password'.")
    if status != 200 or not out.get("ok"):
        raise SystemExit(f"Login de admin falló ({status}): {out.get('error') or out}")
    return out


# ---- generador de historia clínica (reusa las plantillas alepo/marqui) ----

def _plantilla(sistema, archetype):
    path = os.path.join(ROOT, "app", "connectors", sistema, "sample_historias.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("plantillas", {}).get(archetype) or [])


def build_clinical_history(sexo, archetype=None, dias_desde_ultimo=10):
    """Historia realista para un DNI nuevo: la plantilla adulto_f/adulto_m
    (misma que legacy_history.py:LegacySystem._template_for sirve a cualquier paciente demo sin
    ficha propia) de AMBOS sistemas — paja vieja (marqui) + digitalizado
    reciente (alepo) — con las fechas desplazadas para que la entrada más
    nueva quede a `dias_desde_ultimo` días de hoy, preservando el espaciado
    relativo original entre visitas."""
    archetype = archetype or _ARCHETYPE_BY_SEXO.get((sexo or "F").upper(), "adulto_f")
    records = _plantilla("marqui", archetype) + _plantilla("alepo", archetype)
    records = [dict(r) for r in records if isinstance(r, dict) and r.get("fecha")]
    if not records:
        return []
    fechas = sorted(date.fromisoformat(r["fecha"][:10]) for r in records)
    delta_dias = (date.today() - timedelta(days=dias_desde_ultimo) - fechas[-1]).days
    for r in records:
        original = date.fromisoformat(r["fecha"][:10])
        r["fecha"] = (original + timedelta(days=delta_dias)).isoformat()
        r.pop("sistema", None)  # el source_system del POST decide la procedencia
    records.sort(key=lambda r: r["fecha"])
    return records


# ---- alta/edición de cuenta -------------------------------------------------

def upsert_account(http, args):
    status, out = http.get(f"/api/admin/patients/{args.dni}")
    if status == 200:
        campos = {}
        if args.email:
            campos["email"] = args.email
        if args.telefono:
            campos["contacto"] = args.telefono
        if args.nombre:
            campos["nombre"] = args.nombre
        if args.apellido:
            campos["apellido"] = args.apellido
        if args.nacimiento:
            campos["nacimiento"] = args.nacimiento
        if args.sexo:
            campos["sexo"] = args.sexo
        status, out = http.put(f"/api/admin/patients/{args.dni}", campos)
        if status != 200 or not out.get("ok"):
            raise SystemExit(f"No pude actualizar la cuenta {args.dni} ({status}): {out.get('error') or out}")
        return "actualizada", out["item"], None
    if status != 404:
        raise SystemExit(f"No pude consultar la cuenta {args.dni} ({status}): {out.get('error') or out}")
    if not args.nombre or not args.apellido or not args.nacimiento:
        raise SystemExit("Cuenta nueva: hacen falta --nombre, --apellido y --nacimiento.")
    body = {
        "dni": args.dni, "nombre": args.nombre, "apellido": args.apellido,
        "nacimiento": args.nacimiento, "sexo": args.sexo or "X",
        "email": args.email, "contacto": args.telefono,
    }
    if args.password:
        body["password"] = args.password
    status, out = http.post("/api/admin/patients", body)
    if status != 200 or not out.get("ok"):
        raise SystemExit(f"No pude crear la cuenta {args.dni} ({status}): {out.get('error') or out}")
    return "creada", out["item"], out.get("password_temporal")


def _store_generated_password(dni, password_temporal, target):
    """Regla del harness: una credencial nueva va A credential vault al crearse,
    nunca a stdout. Best-effort — si `op` no está a mano acá, se avisa sin
    imprimir la contraseña (queda en la respuesta ÚNICA que ya dio la API;
    quien corrió el script la vio una vez en su propia terminal, no en este
    log)."""
    title = f"Human Rounds — Patient {dni}"
    rc = subprocess.run(
        ["op", "item", "create", "--category=login", f"--title={title}",
         f"--vault={_ADMIN_VAULT}", f"username={dni}", f"password={password_temporal}",
         f"url={target}", f"notesPlain=Cuenta de paciente creada por seed_patient_account.py"],
        capture_output=True, text=True)
    return rc.returncode == 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", default="http://127.0.0.1:7700")
    p.add_argument("--http-auth", help="user:pass si el deployment está detrás de basic auth")
    p.add_argument("--dni", required=True)
    p.add_argument("--nombre")
    p.add_argument("--apellido")
    p.add_argument("--nacimiento", help="AAAA-MM-DD")
    p.add_argument("--sexo", choices=["M", "F", "X"])
    p.add_argument("--email")
    p.add_argument("--telefono", help="WhatsApp/contacto del paciente")
    p.add_argument("--password", help="si se omite en una cuenta nueva, la API genera una temporal")
    p.add_argument("--admin-user")
    p.add_argument("--admin-pass")
    p.add_argument("--admin-totp-code", help="código TOTP de 6 dígitos ya calculado (si el admin tiene 2FA)")
    p.add_argument("--turnstile-bypass", help="TURNOS_TURNSTILE_TEST_BYPASS_SECRET del deployment (prod)")
    p.add_argument("--source-system", default="local_history")
    p.add_argument("--plantilla", choices=["adulto_f", "adulto_m", "menor"],
                    help="por default se infiere de --sexo")
    p.add_argument("--dias-desde-ultimo", type=int, default=10,
                    help="a cuántos días de hoy queda el registro más reciente (default 10)")
    p.add_argument("--sin-historia", action="store_true", help="sólo alta/edición de cuenta, sin cargar historia clínica")
    args = p.parse_args(argv)

    http = Http(args.target, http_auth=args.http_auth)
    try:
        login(http, args)
        estado, item, password_temporal = upsert_account(http, args)
        print(f"cuenta {estado}: dni={item.get('dni')} "
              f"nombre=\"{item.get('nombre')} {item.get('apellido')}\" "
              f"email={'sí' if item.get('email') else 'no'} "
              f"contacto={'sí' if item.get('contacto') else 'no'}")
        if password_temporal:
            if _store_generated_password(args.dni, password_temporal, args.target):
                print(f"contraseña temporal guardada en credential vault (\"Human Rounds — Patient {args.dni}\")")
            else:
                print("aviso: no pude escribir la contraseña temporal en credential vault — "
                      "quedó SOLO en la respuesta de arriba de esta terminal, no la vuelvo a imprimir.")

        if args.sin_historia:
            http.close()
            return 0

        records = build_clinical_history(item.get("sexo") or args.sexo, args.plantilla, args.dias_desde_ultimo)
        if not records:
            print("aviso: no había plantilla de historia clínica para ese sexo/archetype — nada para cargar.")
            http.close()
            return 0
        status, out = http.post(
            f"/api/admin/patients/{args.dni}/clinical-history",
            {"records": records, "source_system": args.source_system})
        if status != 200 or not out.get("ok"):
            raise SystemExit(f"No pude cargar la historia clínica ({status}): {out.get('error') or out}")
        n_labs = sum(1 for r in records if str(r.get("tipo") or "").lower() == "laboratorio")
        print(f"historia clínica cargada: {out.get('count')} registros "
              f"({n_labs} de laboratorio) en source_system={args.source_system}")
        print(f"Resultados de laboratorio del paciente: {args.target}/account#labs "
              "(la cuenta tiene que loguearse con el DNI para verlos)")
    finally:
        http.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
