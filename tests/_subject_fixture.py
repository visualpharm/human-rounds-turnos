"""_subject_fixture.py — a synthetic person with a row in EVERY place the
personal-data registry knows (and a second person whose rows must never move).

Shared by test_subject_export.py, test_subject_erase.py and
test_subject_requests.py. Not named test_*.py on purpose (discovery skips it).

`seed_person(...)` returns the markers it planted — strings that appear in the
person's data and nowhere else — so a test can search a whole export / the
whole database for them.
"""
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Json

_FILLER = {"date": datetime(2000, 1, 1).date(), "jsonb": {}, "integer": 1,
           "bigint": 1, "boolean": False, "bytea": b""}


_TABLE_SHAPE = {}   # (url, table) -> (NOT NULL columns without a default, has an `id` column)


class Fixture:
    """Raw-SQL seeding against one test database (`url`).

    One connection serves every statement of the fixture (a person is ~70 inserts: a connection
    and two catalog queries for each of them was most of the time of the files that seed one), and
    the shape of a table is read from the catalog once per database. `case` is the TestCase:
    the connection and the temporary directory go away with its test."""

    def __init__(self, url, case):
        self.url = url
        self.tmp = tempfile.mkdtemp(prefix="subject-fixture-")
        self._shared = None
        case.addCleanup(self.close)

    def close(self):
        if self._shared is not None:
            self._shared.close()
            self._shared = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def conn(self):
        """A new connection for the caller to close (`with fx.conn() as c`)."""
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.url, autocommit=True, connect_timeout=5, row_factory=dict_row)

    def _cursor(self):
        if self._shared is None or self._shared.closed:
            self._shared = self.conn()
        return self._shared.cursor()

    def _shape(self, table):
        key = (self.url, table)
        if key not in _TABLE_SHAPE:
            with self._cursor() as cur:
                cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                                WHERE table_schema='public' AND table_name=%s
                                  AND is_nullable='NO' AND column_default IS NULL""", (table,))
                required = [(r["column_name"], r["data_type"]) for r in cur.fetchall()]
                cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                            "AND table_name=%s AND column_name='id'", (table,))
                _TABLE_SHAPE[key] = (required, cur.fetchone() is not None)
        return _TABLE_SHAPE[key]

    def required(self, table):
        return list(self._shape(table)[0])

    def insert(self, table, **values):
        """Insert one row (NOT NULL columns without a default get a filler) and
        return its `id` when the table has one."""
        row = dict(values)
        required, has_id = self._shape(table)
        for n, (col, typ) in enumerate(required):
            if col in row:
                continue
            if typ.startswith("timestamp"):
                row[col] = datetime.now(timezone.utc) + timedelta(days=1)
            elif typ in _FILLER:
                row[col] = _FILLER[typ]
            else:
                row[col] = f"{table}-{col}-{n}-{os.urandom(3).hex()}"
        cols = list(row)
        vals = [Json(v) if isinstance(v, (dict, list)) else v for v in row.values()]
        sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(cols), ", ".join(["%s"] * len(cols)))
        with self._cursor() as cur:
            cur.execute(sql + (" RETURNING id" if has_id else ""), vals)
            return cur.fetchone()["id"] if has_id else None

    def drop_account(self, dni):
        """Delete the account row of `dni` the way the database allows (Alembic
        0032): what hangs from the account (sessions, reset tokens, the adult
        of a dependent link, uploads it made) goes first."""
        with self._cursor() as cur:
            for table, column in (("sessions", "dni"), ("reset_tokens", "dni"),
                                  ("dependents", "adult_dni"), ("patient_lab_uploads", "uploaded_by_dni")):
                cur.execute("DELETE FROM " + table + " WHERE " + column + " = %s", (dni,))
            cur.execute("DELETE FROM users WHERE dni = %s", (dni,))

    def execute(self, sql, params=None):
        with self._cursor() as cur:
            cur.execute(sql, params)

    def scalar(self, sql, params=None):
        with self._cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return None if row is None else list(row.values())[0]

    def table_counts(self):
        with self._cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1")
            names = [r["table_name"] for r in cur.fetchall()]
            counts = {}
            for t in names:
                cur.execute(f"SELECT count(*) AS n FROM {t}")
                counts[t] = cur.fetchone()["n"]
            return counts

    def reset(self):
        with self._cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_type='BASE TABLE' "
                        "AND table_name NOT IN ('alembic_version','clinical_access_log') "
                        "AND table_name NOT LIKE 'procrastinate%%'")
            names = [r["table_name"] for r in cur.fetchall()]
            cur.execute("TRUNCATE %s RESTART IDENTITY CASCADE" % ", ".join(names))

    # ---- the person --------------------------------------------------------

    def seed_person(self, dni, tag, phone="2255123456", email=None, with_files=True,
                    birth="1985-04-03", guardian_dni=None):
        """One person, every place. `tag` makes the free text unique to them."""
        email = email or f"{tag.lower()}@example.org"
        m = {"dni": dni, "tag": tag, "phone": phone, "email": email,
             "name": f"Nombre{tag}", "surname": f"Apellido{tag}",
             "secrets": {}, "ids": {}}
        sec = m["secrets"]
        sec["pass_hash"] = f"hash-{tag}-pass"
        sec["feed_token"] = f"feed-{tag}-tok"
        sec["feed_token_hint"] = f"feedhint-{tag}"
        sec["mcp_token"] = f"mcp-{tag}-tok"
        sec["mcp_token_hint"] = f"mcphint-{tag}"
        self.insert("users", dni=dni, first_name=m["name"], last_name=m["surname"],
                    birth_date=birth, sex="F", contact="+54 9 " + phone[:4] + " " + phone[4:],
                    email=email, pass_hash=sec["pass_hash"], feed_token=sec["feed_token"],
                    feed_token_hint=sec["feed_token_hint"], mcp_token=sec["mcp_token"], mcp_token_hint=sec["mcp_token_hint"], address={"calle": f"Calle {tag}"},
                    coverage={"os": f"Obra {tag}"}, notifications={"mail": True},
                    renaper={"raw": f"claims-{tag}"}, portal_user_id=f"portal-{tag}")
        # account
        dep = "9" + dni[1:]
        self.insert("dependents", adult_dni=dni, dni=dep, first_name=f"Hijo{tag}",
                    last_name=m["surname"], birth_date="2015-05-05", sex="M", relationship="hijo")
        if guardian_dni:
            self.insert("dependents", adult_dni=guardian_dni, dni=dni, first_name=m["name"],
                        last_name=m["surname"], birth_date=birth, sex="F", relationship="hijo")
        sec["session"] = f"sess-{tag}"
        self.insert("sessions", token=sec["session"], dni=dni)
        sec["reset"] = f"reset-{tag}"
        self.insert("reset_tokens", token=sec["reset"], dni=dni)
        sec["otp"] = f"otp-{tag}-code"
        self.insert("otp_codes", contact=phone, purpose="login", code_hash=sec["otp"])
        self.insert("otp_verified", contact=phone, purpose="login")
        # requests
        referral = None
        if with_files:
            referral = os.path.join(self.tmp, f"orden-{tag}.pdf")
            with open(referral, "wb") as fh:
                fh.write(b"%PDF referral " + tag.encode())
        sec["confirm"] = f"confirm-{tag}"
        sec["idem"] = f"idem-{tag}"
        rid = self.insert("booking_requests", dni=dni, requester_dni=dni, first_name=m["name"],
                          last_name=m["surname"], birth_date=birth, sex="F", contact=email,
                          service_id="lab", center_slug="c", status="aprobada",
                          appointment_at=datetime.now(timezone.utc) - timedelta(days=30),
                          referral_path=referral, referral_mime="application/pdf",
                          ai_json={"paciente": m["name"]}, patient_note=f"nota {tag}",
                          rejection_reason="motivo",
                          hsi_patient_id=f"hsi-{tag}", confirm_token=sec["confirm"],
                          idem_key=sec["idem"])
        m["ids"]["request"] = rid
        # a request the person made FOR their dependent (another person's data)
        m["ids"]["dependent_request"] = self.insert(
            "booking_requests", dni=dep, requester_dni=dni, first_name=f"Hijo{tag}",
            last_name=m["surname"], service_id="lab", center_slug="c", status="pendiente")
        self.insert("attendance_events", request_id=rid, dni=dni, attendance_to="presente",
                    operator="mesa")
        self.insert("appointment_events", request_id=rid, kind="approved", actor="mesa:x")
        wid = self.insert("waitlist", request_id=rid, dni=dni, email=email, service_id="lab",
                          center_slug="c")
        sec["offer"] = f"offer-{tag}"
        self.insert("appointment_offers", waitlist_id=wid, token=sec["offer"], service_id="lab",
                    center_slug="c", status="ofrecida")
        sec["hold"] = f"hold-{tag}"
        self.insert("slot_holds", service_id="lab", day="2026-11-02", time=f"10:{int(dni[-2:]) % 60:02d}",
                    token=sec["hold"], request_id=rid)
        # clinical
        sec["interview"] = f"itok-{tag}"
        iid = self.insert("interviews", dni=dni, request_id=rid, entered_name=m["name"],
                          entered_surname=m["surname"], session_key=f"interview:{tag}",
                          patient_token=sec["interview"], summary=f"resumen {tag}",
                          red_flags=[], brief={}, differential=[], suggested_exams=[])
        m["ids"]["interview"] = iid
        self.insert("chat_messages", channel="interview", key=f"interview:{tag}", role="user",
                    content={"text": f"transcript {tag}"})
        sec["receta"] = f"tok-receta-{tag}"
        sec["orden"] = f"tok-orden-{tag}"
        self.insert("consultations", interview_id=iid, doctor_id="d1", diagnoses=[], exams=[],
                    meds=[{"nombre": f"med {tag}"}], notes=f"notes {tag}",
                    receta_token=sec["receta"], orden_token=sec["orden"])
        self.insert("patient_health_profile", dni=dni, datos={"alergias": f"alergia {tag}"})
        self.insert("patient_summaries", dni=dni, doctor_id="d1", resumen={"t": f"sum {tag}"})
        self.insert("clinical_history_records", dni=dni, source_system="alepo",
                    record_hash=f"h-{tag}", record={"x": f"hist {tag}"})
        self.insert("lab_report_analysis", dni=dni, report_hash=f"r-{tag}", prompt_hash="p",
                    resumen=f"lab {tag}", valores=[])
        m["lab_bytes"] = f"LABFILE-{tag}".encode()
        m["ids"]["lab"] = self.insert("patient_lab_uploads", dni=dni, uploaded_by_dni=dni,
                                      filename=f"lab-{tag}.pdf", mime="application/pdf",
                                      archivo=m["lab_bytes"], texto=f"texto {tag}")
        # HSI mirrors
        self.insert("hsi_patients", hsi_patient_id=f"hsi-{tag}", dni=dni,
                    payload={"nombre": m["name"]})
        self.insert("hsi_appointments", hsi_appointment_id=f"ha-{tag}", hsi_patient_id=f"hsi-{tag}",
                    payload={"x": 1})
        self.insert("hsi_checkpoints", entity_type="patient", entity_id=f"hsi-{tag}",
                    remote={"documento": dni})
        self.insert("hsi_inbox", source_key=f"sk-{tag}", entity_type="patient", entity_id="1",
                    payload={"documento": dni, "nombre": m["name"]})
        self.insert("hsi_sync_conflicts", entity_type="patient", entity_id="1", direction="inbound",
                    reason="x", local_payload={"documento": dni}, remote_payload={},
                    resolved_at=datetime.now(timezone.utc))
        self.insert("hsi_outbox", entity_type="appointment", entity_id=str(rid), operation="create",
                    payload={"dni": dni, "nombre": m["name"]}, idempotency_key=f"idem-ob-{tag}",
                    state="confirmed", hsi_result={"patient": m["name"]},
                    last_error=f"HSI said no to {m['name']}")
        # conversations
        self.insert("chat_messages", channel="web", key=f"dni:{dni}", role="user",
                    content={"text": f"web {tag}"})
        self.insert("chat_messages", channel="web", key=f"dni:{dni}:chat:abc", role="user",
                    content={"text": f"web2 {tag}"})
        self.insert("chat_messages", channel="whatsapp", key=f"tel:54{phone}", role="user",
                    content={"text": f"wa {tag}"})
        self.insert("chat_summaries", patient_key=f"dni:{dni}", channel="web", summary=f"mem {tag}",
                    message_count=1)
        self.insert("chat_compactions", patient_key=f"dni:{dni}", summary=f"comp {tag}",
                    message_count=1)
        sec["handoff"] = f"code-{tag}"
        self.insert("chat_handoffs", code=sec["handoff"], web_key=f"dni:{dni}", used_phone=phone)
        # telemetry
        self.insert("ai_costs", operation="chat", ip=f"10.0.0.{len(tag)}", user_dni=dni)
        # the desk's check of the number: the person's own account asked for it
        self.insert("dni_claims", dni_claimed=dni, claimant_key=dni, status="unverified")
        self.insert("searches", q=f"busco {dni}", results=0, source="web")
        # the person's own requests to us
        from app.core import access_log
        ledger = self.insert("data_subject_requests", kind="access",
                             subject_hash=access_log.dni_hash(dni), received_on="2026-10-01",
                             due_on="2026-10-11", registered_by="mesa")
        self.insert("data_subject_request_events", request_id=ledger, actor="mesa",
                    event="received")
        # backups bookkeeping names the profile photo by DNI
        self.insert("backup_journal", path=f"cuenta-fotos/{dni}.jpg", sha256="s", size=1,
                    mtime_ns=1, remote_name=f"cuenta-fotos/{dni}.jpg", origin="local")
        return m

    def photo(self, photos_dir, dni, data=b"\xff\xd8\xff photo"):
        os.makedirs(photos_dir, exist_ok=True)
        path = os.path.join(photos_dir, f"{dni}.jpg")
        with open(path, "wb") as fh:
            fh.write(data)
        return path
