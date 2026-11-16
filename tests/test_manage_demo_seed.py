"""test_manage_demo_seed.py — scripts/seed_demo_all.py (manage.py demo seed|reset).

Two things matter here: the hard guard (never touches a database unless
TURNOS_DEMO_MODE is on, even if DATABASE_URL is reachable) and idempotency
(running `seed()` twice must not accumulate duplicate rows — the marker-based
delete-then-recreate has to actually delete before it recreates).
"""

import os
import sys
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_DEMO_SEED_REAL_AI="0")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.core import config  # noqa: E402
from app.core import format  # noqa: E402
from app.ops import demo_installation  # noqa: E402
from app import manage  # noqa: E402
import scripts.seed_demo_all as seed_demo_all  # noqa: E402

import _dbtest  # noqa: E402
import _password_hash  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_demo_seed",
                          seed=lambda: db_catalog.catalog_list("centros"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _password_hash.reuse_hashes()
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _counts():
    """Row counts of every table `seed()` populates, marker-scoped so a
    reused test DB never mixes this module's rows with another's."""
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        cur.execute("SELECT COUNT(*) AS n FROM booking_requests WHERE contact LIKE %s",
                    ("%" + seed_demo_all.MARCA,))
        solicitudes = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE %s",
                    ("%" + seed_demo_all.MARCA,))
        pacientes = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM waitlist WHERE email LIKE %s",
                    ("%" + seed_demo_all.MARCA,))
        espera = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM searches WHERE source=%s",
                    (seed_demo_all.SEARCH_SOURCE,))
        busquedas = cur.fetchone()["n"]
    return {"solicitudes": solicitudes, "pacientes": pacientes,
            "espera": espera, "busquedas": busquedas}


class TestGuardFueraDeDemo(unittest.TestCase):
    """Hard guard: ni `seed()` ni `reset()` tocan la base si TURNOS_DEMO_MODE
    no está prendido — aunque DATABASE_URL apunte a una base alcanzable
    (exactamente el escenario de "apuntar por error a la base real")."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "Postgres no disponible")
        self.enterContext(_env.patched_env(TURNOS_DEMO_MODE="0"))

    def test_seed_no_opera_sin_demo_mode(self):
        antes = _counts()
        seed_demo_all.seed()
        self.assertEqual(_counts(), antes)

    def test_reset_no_opera_sin_demo_mode(self):
        # Sembramos algo primero (con demo mode prendido momentáneamente) para
        # probar que reset() de verdad NO lo toca sin el flag.
        with _env.patched_env({"TURNOS_DEMO_MODE": "1"}, clear=False):
            seed_demo_all.seed()
        antes = _counts()
        self.assertGreater(antes["solicitudes"], 0)
        seed_demo_all.reset()
        self.assertEqual(_counts(), antes)

    def test_command_check_nunca_confunde_una_cuenta_real(self):
        self.assertFalse(demo_installation.enabled())


class TestSeedIdempotente(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "Postgres no disponible")
        self.enterContext(_env.patched_env(TURNOS_DEMO_MODE="1"))

    def test_seed_dos_veces_deja_los_mismos_conteos(self):
        seed_demo_all.seed()
        primero = _counts()
        self.assertGreater(primero["solicitudes"], 0)
        self.assertGreater(primero["pacientes"], 0)
        self.assertGreater(primero["busquedas"], 0)

        seed_demo_all.seed()
        segundo = _counts()
        self.assertEqual(primero, segundo)

    def test_reset_reseeds_con_los_mismos_conteos(self):
        seed_demo_all.seed()
        primero = _counts()

        seed_demo_all.reset()
        segundo = _counts()
        self.assertEqual(primero, segundo)

    def test_forced_reseed_deletes_children_before_parents_under_foreign_keys(self):
        """Alembic 0032: the database refuses to delete a request, an interview,
        a waiting-list entry or an account that still has rows under it. The
        daily refresh (`seed(force=True)`) must clear everything a visitor or
        the app hung on the seeded rows, children first."""
        seed_demo_all.seed(force=True)
        primero = _counts()
        marca = "%" + seed_demo_all.MARCA
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute("SELECT id FROM booking_requests WHERE contact LIKE %s LIMIT 1", (marca,))
            rid = cur.fetchone()["id"]
            cur.execute("INSERT INTO slot_holds (service_id, day, time, token, expires, request_id) "
                        "VALUES ('s', '2030-01-01', '09:00', 'fk-seed', now() + interval '1 day', %s)", (rid,))
            cur.execute("INSERT INTO waitlist (request_id, email) VALUES (%s, 'visitor@example.org')", (rid,))
            cur.execute("SELECT id FROM waitlist ORDER BY id DESC LIMIT 1")
            wid = cur.fetchone()["id"]
            cur.execute("INSERT INTO appointment_offers (waitlist_id, token, service_id, center_slug, slot_at, "
                        "expires_at) VALUES (%s, 'fk-offer', 's', 'c', now() + interval '1 day', now() + interval '1 day')",
                        (wid,))
            cur.execute("SELECT dni FROM users WHERE email LIKE %s LIMIT 1", (marca,))
            dni = cur.fetchone()["dni"]
            cur.execute("INSERT INTO reset_tokens (token, dni, expires) VALUES ('fk-reset', %s, now() + interval '1 day')", (dni,))
            cur.execute("INSERT INTO patient_lab_uploads (dni, uploaded_by_dni) VALUES (%s, %s)", (dni, dni))
        seed_demo_all.seed(force=True)          # must not raise ForeignKeyViolation
        self.assertEqual(primero, _counts())

    def test_cuentas_tienen_telefono_en_telefono_y_email_en_email(self):
        # 25/10: la ficha de una paciente del demo mostraba su email en
        # «Teléfono» — el marcador vivía en `contact`.
        seed_demo_all.seed()
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute("SELECT contact, email FROM users WHERE email LIKE %s",
                        ("%" + seed_demo_all.MARCA,))
            filas = cur.fetchall()
        self.assertGreater(len(filas), 0)
        for fila in filas:
            self.assertRegex(fila["contact"] or "", r"^\+549\d{10}$", fila)
            self.assertEqual(format.tel_e164(fila["contact"]), fila["contact"])

    def test_seed_reemplaza_las_cuentas_de_antes_con_el_email_en_telefono(self):
        # Las cuentas que ya están en el demo se sembraron con el marcador en
        # `contact` también: la siembra diaria las tiene que reemplazar.
        seed_demo_all.seed()
        antes = _counts()["pacientes"]
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute("UPDATE users SET contact=email WHERE email LIKE %s",
                        ("%" + seed_demo_all.MARCA,))
            conn.commit()
        seed_demo_all.seed(force=True)
        self.assertEqual(_counts()["pacientes"], antes)
        self.assertRegex(db_accounts.get_user(seed_demo_all.PACIENTES[0]["dni"])["contacto"], r"^\+549")

    def test_manage_cli_demo_seed_no_lanza(self):
        # Misma llamada que `python3 manage.py demo seed` (cmd_demo_seed no
        # usa `args`, así que None es válido acá).
        self.assertEqual(manage.cmd_demo_seed(None), 0)
        self.assertGreater(_counts()["solicitudes"], 0)

    def test_featured_patient_connects_account_history_and_doctor_summary(self):
        seed_demo_all.seed()
        dni = seed_demo_all.DEMO_PATIENT["dni"]
        self.assertEqual(db_accounts.get_user(dni)["nombre"], "Sofía")
        requests = db_booking.list_by_dni(dni)
        self.assertGreaterEqual(len(requests), 4)
        assigned = {r["servicio_id"]: (r["medico_asignado"], r["medico_asignado_id"])
                    for r in requests}
        self.assertEqual(
            assigned["medicina-general-riverside-clinic"],
            ("Juan González", "gonzalez-juan"),
        )
        self.assertEqual(
            assigned["cardiologia-sampleton-hospital"],
            ("Sergio Ruiz", "ruiz-sergio"),
        )
        self.assertEqual(
            assigned["ecografia-general-anexo"],
            ("Mónica Herrero", "herrero-monica"),
        )
        interviews = db_slots.interviews_by_dni(dni)
        self.assertEqual(len(interviews), 1)
        self.assertEqual(interviews[0]["doctor_id"], "gonzalez-juan")
        self.assertEqual(interviews[0]["status"], "done")
        self.assertNotEqual(interviews[0]["ai_model"], "demo-seed")
        self.assertTrue(interviews[0]["brief"])
        self.assertTrue(interviews[0]["brief"][0].get("point"))
        transcript = db_chat.chat_history(
            "interview", interviews[0]["session_key"], 20)
        self.assertEqual(len(transcript), 8)
        self.assertEqual(len([m for m in transcript if m["role"] == "user"]), 4)
        summary = db_clinical.summary_get(dni, "gonzalez-juan")
        self.assertTrue(summary and summary["resumen"]["titular"])

    def test_reseed_reemplaza_el_transcript_en_vez_de_acumularlo(self):
        seed_demo_all.seed()
        seed_demo_all.seed()
        interview = db_slots.interviews_by_dni(seed_demo_all.DEMO_PATIENT["dni"])[0]
        transcript = db_chat.chat_history("interview", interview["session_key"], 50)
        self.assertEqual(len(transcript), 8)

    def test_seed_habilita_el_selector_de_idioma(self):
        seed_demo_all.seed()
        # 2026-09-11: the demo enables EVERY shipped language, `es` first, and
        # leaves LANG_DEFAULT empty so Accept-Language (or `?lang=` coming from
        # humanrounds.org) decides — an explicit default would pin every
        # visitor to Spanish.
        from app.core import i18n
        esperados = [c for c in i18n.LANGUAGE_NAMES if c in i18n.available_locales()]
        self.assertEqual(config.get("LANGUAGES"), ",".join(esperados))
        self.assertEqual(esperados[0], "es")
        self.assertGreater(len(esperados), 2)
        self.assertEqual(config.get("LANG_DEFAULT") or "", "")
        self.assertEqual(config.get("LANG_SWITCHER_MODE"), "nav")


class TestDiaDeHoyCasiLleno(unittest.TestCase):
    """La demo se muestra CUALQUIER día: el día de hoy tiene que leerse como
    un día de trabajo, no como una agenda vacía (responsable del producto, 2026-08-01)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "Postgres no disponible")
        self.enterContext(_env.patched_env(TURNOS_DEMO_MODE="1"))

    def _turnos_de_hoy(self):
        hoy = seed_demo_all._now().date()
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute(
                """SELECT service_id, appointment_at, assigned_doctor_id, attendance
                     FROM booking_requests
                    WHERE contact LIKE %s AND appointment_at::date = %s
                    ORDER BY appointment_at""",
                ("%" + seed_demo_all.MARCA, hoy),
            )
            return cur.fetchall()

    def _grupos_de_hoy(self):
        """Las mismas agendas que usa el seed: las de los médicos ficticios
        que este catálogo tenga, más los servicios sin médico. Con el paquete
        de Pinamar cargado (los tests no montan packs/example-town) quedan
        sólo estos últimos, y el relleno se verifica igual."""
        grupos = list(seed_demo_all._agenda_de_hoy())
        grupos += seed_demo_all._sin_medico_del_dia(seed_demo_all._now().date())
        return grupos

    def test_hoy_queda_casi_lleno_pero_no_lleno(self):
        seed_demo_all.seed()
        filas = self._turnos_de_hoy()
        self.assertGreaterEqual(len(filas), 6, "un día de demo vacío no muestra nada")

        grupos = self._grupos_de_hoy()
        self.assertTrue(grupos, "el día tiene que tener al menos una agenda")
        libres_totales = 0
        for medico_id, _nombre, svc_id, _centro, horas in grupos:
            # Cada horario que la agenda abre hoy tiene paciente, salvo el
            # hueco de cada _LIBRE_CADA: si el turno no cayera en un horario
            # REAL, el día se vería lleno pero el panel no podría ni ofrecerlo
            # ni liberarlo.
            libres = seed_demo_all.libres_de(horas)
            esperadas = {h for i, h in enumerate(horas) if i not in libres}
            # SIEMPRE a la zona de la instalación: el offset que devuelve
            # Postgres es el del proceso, no el de la instalación (tz.py).
            ocupadas = {f["appointment_at"].astimezone(seed_demo_all.ART).strftime("%H:%M")
                        for f in filas
                        if f["service_id"] == svc_id
                        and f["assigned_doctor_id"] == medico_id}
            self.assertTrue(esperadas <= ocupadas,
                            f"{svc_id}: horarios sin turno {sorted(esperadas - ocupadas)}")
            libres_totales += len(set(horas) - esperadas)
        # …y siempre queda algún hueco para dar en el mostrador.
        self.assertGreater(libres_totales, 0, "sin horarios libres no hay nada que mostrar")

    def test_cada_paciente_de_hoy_tiene_ficha_que_crear_turno_encuentra(self):
        """TASK-54 (26.10, persona Karla): «Crear turno» buscaba «Selim» —
        que estaba en «Pacientes de hoy» de Juan González — y decía «Ningún
        paciente con ese dato». La búsqueda lee las fichas (users) y la demo
        sólo creaba 40 de los ~93 pacientes que reparte por la agenda. Quien
        está en la cola de hoy tiene ficha, y se encuentra por apellido y por
        DNI."""
        seed_demo_all.seed()
        hoy = seed_demo_all._now().date()
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute(
                """SELECT DISTINCT dni, last_name FROM booking_requests
                    WHERE contact LIKE %s AND appointment_at::date = %s""",
                ("%" + seed_demo_all.MARCA, hoy))
            de_hoy = cur.fetchall()
        self.assertTrue(de_hoy)
        for fila in de_hoy:
            por_dni, _ = db_accounts.users_admin_list(fila["dni"])
            self.assertIn(fila["dni"], {u["dni"] for u in por_dni}, fila)
            por_apellido, _ = db_accounts.users_admin_list(fila["last_name"])
            self.assertIn(fila["dni"], {u["dni"] for u in por_apellido}, fila)

    def test_la_orden_de_cada_solicitud_es_de_su_servicio(self):
        """TASK-58 (26.10): el panel mostraba «Especialidad: Neurología ✓
        coincide» en una solicitud de Ginecología — la historia repartía las
        órdenes de ejemplo sin mirar el servicio. Una orden que nombra una
        especialidad va sólo a una solicitud de esa especialidad."""
        seed_demo_all.seed()
        alias = {"cardiology": "cardiologia"}
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute(
                """SELECT service_id, ai_json FROM booking_requests
                    WHERE contact LIKE %s AND ai_json IS NOT NULL""",
                ("%" + seed_demo_all.MARCA,))
            filas = cur.fetchall()
        con_especialidad = 0
        for fila in filas:
            ai = fila["ai_json"] if isinstance(fila["ai_json"], dict) else {}
            esp = ((ai.get("campos") or {}).get("especialidad") or {}).get("valor")
            if not esp:
                continue
            con_especialidad += 1
            clave = seed_demo_all._slug(esp)
            clave = alias.get(clave, clave)
            self.assertTrue(fila["service_id"].startswith(clave), (fila["service_id"], esp))
        self.assertGreater(con_especialidad, 10, "la demo sigue mostrando órdenes leídas")

    def test_reseed_manana_mueve_el_dia(self):
        """demo_refresh.py llama a seed() cada día: el día siguiente tiene que
        salir del seed igual de completo, no quedarse con el de ayer."""
        from app.ops import demo_refresh
        seed_demo_all.seed()
        hoy = seed_demo_all._now().date()
        self.assertTrue(demo_refresh._dia_ya_sembrado(hoy))
        self.assertFalse(demo_refresh._dia_ya_sembrado(hoy + timedelta(days=400)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
