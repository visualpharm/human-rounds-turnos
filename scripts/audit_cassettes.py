#!/usr/bin/env python3
"""audit_cassettes.py — Tier 3 slice 8, nightly rung (Area 3's "What nightly
DOES check", docs/roadmap/tier3-harness-design.md). A self-contained,
no-network, no-Postgres audit of every committed cassette under
tests/cassettes/<connector>/*.json:

  (a) STALENESS — each cassette's `recorded_at` age, warned past
      `_STALE_DAYS` (90, per the design). Non-fatal: a cassette can be old
      and still correctly shaped, so age alone never fails the run.
  (b) SHAPE — each cassette's response bodies checked against the specific
      field names/types the REAL connector code reads off them (not a full
      schema — just enough to catch "the upstream API silently changed its
      response shape and nobody noticed because the cassette still replays
      fine"). A shape mismatch IS fatal (exit 1) — that is the actionable
      signal a human needs to go re-record.

Never records, never touches the network, never imports a connector module
(the checks below are hand-derived from reading each connector's parsing
code once — see the design doc's own "lightweight structural expectation,
not a full schema" framing). Run manually any time with:

    python3 scripts/audit_cassettes.py

Exit 0 = no shape failures (staleness warnings, if any, are still printed
and still visible in the log — this is what makes a nightly run "loud":
GitHub's default scheduled-workflow failure email fires only on a non-zero
exit, so age alone deliberately does NOT trigger it, per the design's own
"surfaced as a warning" wording for (a) vs "flagged" for (b)).
"""
import datetime
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASSETTES_DIR = os.path.join(ROOT, "tests", "cassettes")

_STALE_DAYS = 90


def _age_days(recorded_at):
    dt = datetime.datetime.fromisoformat(recorded_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - dt).days


# ---- per-connector shape checks --------------------------------------------
# Each function takes the cassette's `interactions` list and returns a list
# of human-readable problem strings (empty = all shapes hold). Path patterns
# and the fields checked were read off each connector's own parsing code —
# see the "source" comment on each check for the exact function.


def _check_hsi(interactions):
    problems = []
    for it in interactions:
        path, method = it["path"], it["method"]
        body = it["response"]["body"]

        def _want(label, cond):
            if not cond:
                problems.append(
                    f"HSI {method} {path}: {label} (vino {type(body).__name__})")

        if path == "/api/patient/minimalsearch":
            # ensure_patient()'s search step — connectors/hsi/hsi_client.py
            _want("se esperaba una lista (resultado de minimalsearch)",
                  isinstance(body, list))
        elif re.fullmatch(r"/api/patient/institution/\d+", path) and method == "POST":
            # ensure_patient()'s create step: "returns the new id" (int)
            _want("se esperaba un id numérico (patientId nuevo)",
                  isinstance(body, int))
        elif path.endswith("/healthcareprofessional"):
            _want("se esperaba una lista de profesionales",
                  isinstance(body, list))
        elif path.endswith("/medicalConsultations/diary"):
            _want("se esperaba una lista de agendas", isinstance(body, list))
        elif re.search(r"/medicalConsultations/diary/\d+$", path):
            # diary_detail() reads appointmentDuration/startDate/endDate —
            # connectors/hsi/hsi_client.py:create_appointment()
            required = {"appointmentDuration", "startDate", "endDate"}
            _want(f"faltan campos {sorted(required - set(body))} que lee diary_detail()"
                  if isinstance(body, dict) else "se esperaba un objeto (detalle de agenda)",
                  isinstance(body, dict) and required <= body.keys())
        elif re.search(r"/appointments/diary/\d+$", path):
            _want("se esperaba una lista de turnos existentes",
                  isinstance(body, list))
        elif path.endswith("/medicalConsultations/appointments") and method == "POST":
            # create_appointment()'s create step returns the new appointment
            # id (bare int) — same shape as ensure_patient()'s create step.
            _want("se esperaba el id numérico del turno creado",
                  isinstance(body, int))
    return problems


_XML_TAGS = ("resultado", "coberturaSocial", "denominacion", "nrodoc")


def _check_puco(interactions):
    # connectors/puco/puco.py's _parse_respuesta() reads <resultado>,
    # <coberturaSocial>, <denominacion>, <nrodoc> tags out of the raw XML
    # text body — WS131 has no JSON envelope.
    problems = []
    for it in interactions:
        if not re.search(r"/sisa/services/rest/puco/\d+$", it["path"]):
            continue
        body = it["response"]["body"]
        if not isinstance(body, str):
            problems.append(f"PUCO {it['path']}: se esperaba texto XML, "
                             f"vino {type(body).__name__}")
            continue
        missing = [t for t in _XML_TAGS if f"<{t}>" not in body]
        if missing:
            problems.append(f"PUCO {it['path']}: faltan tags XML {missing} "
                             "que lee _parse_respuesta()")
    return problems


def _check_renaper(interactions):
    # auth_renaper.py: the token response is read for id_token/access_token
    # (_claims_reales); the userinfo response is read by _extraer_datos()
    # for dni/given_name (or nombre)/family_name (or apellido)/birthdate (or
    # fecha_nacimiento).
    problems = []
    for it in interactions:
        path, body = it["path"], it["response"]["body"]
        if not isinstance(body, dict):
            continue
        if path.rstrip("/").endswith("/token"):
            required = {"id_token", "access_token"}
            missing = required - body.keys()
            if missing:
                problems.append(f"RENAPER {path}: faltan campos {sorted(missing)} "
                                 "que lee auth_renaper._claims_reales()")
        elif path.rstrip("/").endswith("/userinfo"):
            has_dni = "dni" in body
            has_nombre = "given_name" in body or "nombre" in body
            has_apellido = "family_name" in body or "apellido" in body
            has_nacimiento = "birthdate" in body or "fecha_nacimiento" in body
            if not (has_dni and has_nombre and has_apellido and has_nacimiento):
                problems.append(f"RENAPER {path}: faltan claims que lee "
                                 "auth_renaper._extraer_datos() "
                                 "(dni / given_name|nombre / family_name|apellido / "
                                 "birthdate|fecha_nacimiento)")
    return problems


def _check_meta(interactions):
    # otp_send.py's _enviar_meta() reads result["messages"][0]["id"].
    problems = []
    for it in interactions:
        if not it["path"].endswith("/messages") or it["method"] != "POST":
            continue
        body = it["response"]["body"]
        msg_id = None
        if isinstance(body, dict):
            msgs = body.get("messages") or [{}]
            msg_id = (msgs[0] or {}).get("id") if msgs else None
        if not msg_id:
            problems.append(f"Meta {it['path']}: falta messages[0].id que lee "
                             "otp_send._enviar_meta()")
    return problems


def _check_mailjet(interactions):
    # connectors/mailjet/mailjet.py's send() posts Messages[] (From.Email,
    # To[].Email, TextPart) to /v3.1/send and reads Messages[0].Status.
    problems = []
    sends = [it for it in interactions
             if it["method"] == "POST" and it["path"] == "/v3.1/send"]
    if not sends:
        problems.append("Mailjet: falta el POST /v3.1/send que hace mailjet.send()")
    for it in sends:
        msgs = (it.get("request_body") or {}).get("Messages") or [{}]
        msg = msgs[0] or {}
        if not ((msg.get("From") or {}).get("Email") and (msg.get("To") or [{}])[0].get("Email")
                and "TextPart" in msg):
            problems.append("Mailjet /v3.1/send: el pedido no trae From.Email, To[].Email y TextPart")
        body = it["response"]["body"]
        status = None
        if isinstance(body, dict):
            status = ((body.get("Messages") or [{}])[0] or {}).get("Status")
        if it["response"].get("status", 200) < 400 and not status:
            problems.append("Mailjet /v3.1/send: falta Messages[0].Status que lee mailjet.send()")
    return problems


_CHECKS = {
    "hsi": _check_hsi,
    "puco": _check_puco,
    "renaper": _check_renaper,
    "meta": _check_meta,
    "mailjet": _check_mailjet,
}


def _audit_one(path):
    """Returns (connector, scenario, age_days, problems)."""
    with open(path, encoding="utf-8") as f:
        cassette = json.load(f)
    connector = cassette.get("connector") or os.path.basename(os.path.dirname(path))
    scenario = cassette.get("scenario") or os.path.basename(path)
    age = _age_days(cassette["recorded_at"])
    checker = _CHECKS.get(connector)
    problems = checker(cassette.get("interactions") or []) if checker else [
        f"{connector}: no hay checker de forma registrado en audit_cassettes.py — "
        "agregar uno antes de confiar en este cassette"
    ]
    return connector, scenario, age, problems


def main():
    files = sorted(glob.glob(os.path.join(CASSETTES_DIR, "*", "*.json")))
    if not files:
        print("audit_cassettes.py: no hay cassettes bajo tests/cassettes/ — nada que auditar.")
        return 0

    warnings = []
    errors = []
    for path in files:
        rel = os.path.relpath(path, ROOT)
        try:
            connector, scenario, age, problems = _audit_one(path)
        except Exception as exc:  # noqa: BLE001 — un cassette ilegible es un error, no un warning
            errors.append(f"{rel}: no se pudo leer/auditar ({type(exc).__name__}: {exc})")
            continue

        if age > _STALE_DAYS:
            msg = (f"{rel}: grabado hace {age} días (> {_STALE_DAYS}) — "
                   f"{connector}/{scenario} candidato a re-grabar")
            warnings.append(msg)
            print(f"::warning::{msg}")
        else:
            print(f"{rel}: OK, {age}d de antigüedad, forma verificada "
                  f"({connector}/{scenario})")

        for p in problems:
            msg = f"{rel}: {p}"
            errors.append(msg)
            print(f"::error::{msg}")

    print()
    print(f"audit_cassettes.py: {len(files)} cassette(s), "
          f"{len(warnings)} warning(s) de antigüedad, {len(errors)} error(es) de forma.")

    if errors:
        print("audit_cassettes.py: BLOQUEADO — hay cassette(s) con la forma cambiada; "
              "re-grabar con scripts/record_cassette.py y revisar el diff antes de commitear.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
