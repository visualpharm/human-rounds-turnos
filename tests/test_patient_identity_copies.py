"""test_patient_identity_copies.py — problems.md №9, step 1.

The patient's name, birth date and sex are copied into every booking request
(and the name into the interview) at booking time. Correcting the patient
afterwards used to leave the desk queue, the doctor's agenda and the HSI push
with the old value. Now a correction on `users` (the desk's Pacientes screen
or the patient's own profile) rewrites those copies in the patient's PENDING
and FUTURE requests and in the interviews tied to them; past requests are the
history snapshot and are never touched, and a request's phone is its own
(the product owner's hub, 2026-10-23) and is never touched either.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p test_patient_identity_copies.py
"""
import os
import sys
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_identity_copies", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_DNI = "40111222"
_OTRO = "40999888"


def _sql(query, params=()):
    import psycopg
    with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall() if cur.description else None


class TestCopiasDeIdentidad(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _sql("TRUNCATE users, booking_requests, interviews RESTART IDENTITY CASCADE")
        db_accounts.create_user(_DNI, "Ana", "Gomes", "1990-05-05", "F", "2254550001", "clave1234")
        db_accounts.create_user(_OTRO, "Beto", "Pérez", "1980-01-01", "M", "2254550009", "clave1234")

    def _pedido(self, dni=_DNI, estado="pendiente", turno="+3 days", contacto="2254550001"):
        sid = db_booking.create_booking_request(dni, "Ana", "Gomes", "1990-05-05", contacto,
                                        "cardiologia", "centro-a", sexo="F")
        if turno is None:
            _sql("UPDATE booking_requests SET status=%s WHERE id=%s", (estado, sid))
        else:
            _sql("UPDATE booking_requests SET status=%s, appointment_at = now() + %s::interval WHERE id=%s",
                 (estado, turno.lstrip("+"), sid))
        return sid

    def _copia(self, sid):
        (fila,) = _sql("SELECT first_name, last_name, birth_date, sex, contact FROM booking_requests WHERE id=%s",
                       (sid,))
        return fila

    CORREGIDO = ("Ana María", "Gómez", date(1991, 6, 6), "X")

    def _corregir_desde_la_mesa(self):
        db_accounts.user_admin_update(_DNI, {"nombre": "Ana María", "apellido": "Gómez",
                                    "nacimiento": "1991-06-06", "sexo": "X"})

    def test_la_correccion_de_la_mesa_llega_a_las_solicitudes_pendientes_y_futuras(self):
        pendiente = self._pedido()
        aprobada_futura = self._pedido(estado="aprobada", turno="+5 days")
        aprobada_sin_hora = self._pedido(estado="aprobada", turno=None)
        self._corregir_desde_la_mesa()
        for sid in (pendiente, aprobada_futura, aprobada_sin_hora):
            self.assertEqual(self._copia(sid)[:4], self.CORREGIDO, sid)

    def test_lo_pasado_y_lo_cerrado_es_historia_y_no_se_toca(self):
        original = ("Ana", "Gomes", date(1990, 5, 5), "F")
        aprobada_pasada = self._pedido(estado="aprobada", turno="-3 days")
        rechazada = self._pedido(estado="rechazada")
        cancelada = self._pedido(estado="cancelada")
        self._corregir_desde_la_mesa()
        for sid in (aprobada_pasada, rechazada, cancelada):
            self.assertEqual(self._copia(sid)[:4], original, sid)

    def test_el_telefono_de_la_solicitud_es_suyo_y_no_se_toca(self):
        sid = self._pedido(contacto="2254559999")
        self._corregir_desde_la_mesa()
        db_accounts.update_user(_DNI, nombre="Ana María", apellido="Gómez", nacimiento="1991-06-06", sexo="X",
                       contacto="2254558888")
        self.assertEqual(self._copia(sid)[4], "2254559999")

    def test_otro_paciente_no_se_toca(self):
        ajena = self._pedido(dni=_OTRO)
        self._corregir_desde_la_mesa()
        self.assertEqual(self._copia(ajena)[:4], ("Ana", "Gomes", date(1990, 5, 5), "F"))

    def test_una_correccion_parcial_solo_cambia_ese_dato(self):
        sid = self._pedido()
        db_accounts.user_admin_update(_DNI, {"nacimiento": "1991-06-06"})
        self.assertEqual(self._copia(sid)[:4], ("Ana", "Gomes", date(1991, 6, 6), "F"))

    def test_un_dato_vaciado_o_sin_cambios_no_borra_la_copia(self):
        sid = self._pedido()
        db_accounts.user_admin_update(_DNI, {"sexo": None, "apellido": ""})
        self.assertEqual(self._copia(sid)[:4], ("Ana", "Gomes", date(1990, 5, 5), "F"))
        # lo que no existe en la cuenta tampoco se inventa para nadie
        self.assertIsNone(db_accounts.user_admin_update("00000000", {"nombre": "Nadie"}))

    def test_el_perfil_propio_tambien_actualiza_las_copias(self):
        sid = self._pedido()
        db_accounts.update_user(_DNI, nombre="Ana María", apellido="Gómez", nacimiento="1991-06-06", sexo="X",
                       contacto="2254550001", email=None, cobertura=None, domicilio=None, avisos=None)
        self.assertEqual(self._copia(sid)[:4], self.CORREGIDO)

    def test_la_entrevista_de_una_solicitud_futura_o_abierta_toma_el_nombre_nuevo(self):
        futura = self._pedido()
        pasada = self._pedido(estado="aprobada", turno="-3 days")
        db_clinical.interview_create(doctor_id="med-1", entry_mode="post_booking", request_id=futura, dni=_DNI,
                            entered_name="Ana", entered_surname="Gomes", matched=True)
        db_clinical.interview_create(doctor_id="med-1", entry_mode="post_booking", request_id=pasada, dni=_DNI,
                            entered_name="Ana", entered_surname="Gomes", matched=True)
        db_clinical.interview_create(doctor_id="med-1", entry_mode="qr", dni=_DNI,
                            entered_name="Ana", entered_surname="Gomes", matched=False)
        cerrada = db_clinical.interview_create(doctor_id="med-1", entry_mode="qr", dni=_DNI,
                                      entered_name="Ana", entered_surname="Gomes", matched=False)
        db_clinical.interview_set_status(cerrada["id"], "done")
        anonima = db_clinical.interview_create(doctor_id="med-1", entry_mode="qr", dni=_DNI, matched=False)
        self._corregir_desde_la_mesa()
        filas = _sql("SELECT id, entered_name, entered_surname FROM interviews ORDER BY id")
        self.assertEqual([(f[1], f[2]) for f in filas], [
            ("Ana María", "Gómez"),   # de la solicitud futura
            ("Ana", "Gomes"),         # de la solicitud pasada: historia
            ("Ana María", "Gómez"),   # QR abierta sin solicitud
            ("Ana", "Gomes"),         # QR ya cerrada: historia
            (None, None),             # QR anónima: no tenía nombre, no se le inventa uno
        ])
        self.assertEqual(anonima["id"], 5)

    def _sync(self, **datos):
        with db._connect() as conn, conn.cursor() as cur:
            return db_accounts._sync_identity_copies(cur, _DNI, **datos)

    def test_la_sincronizacion_cuenta_solo_lo_que_cambio(self):
        """Pinned on the hand-written SQL before the move to SQLAlchemy Core: the (requests,
        interviews) counts, only rows whose copy really differs, and nothing without data."""
        primera, segunda = self._pedido(), self._pedido(estado="aprobada", turno="+2 days")
        db_clinical.interview_create(doctor_id="med-1", entry_mode="post_booking", request_id=primera, dni=_DNI,
                            entered_name="Ana", entered_surname="Gomes", matched=True)
        _sql("UPDATE booking_requests SET first_name='Ana María' WHERE id=%s", (segunda,))
        self.assertEqual(self._sync(nombre=" Ana María "), (1, 1))
        self.assertEqual(self._sync(nombre="Ana María"), (0, 0))
        self.assertEqual(self._sync(apellido="Gómez", sexo="X"), (2, 1))
        self.assertEqual(self._sync(nacimiento=date(1991, 6, 6)), (2, 0))
        self.assertEqual(self._sync(), (0, 0))
        self.assertEqual(self._sync(nombre="  ", apellido=None), (0, 0))
        with db._connect() as conn, conn.cursor() as cur:
            self.assertEqual(db_accounts._sync_identity_copies(cur, "", nombre="Otra"), (0, 0))
        self.assertEqual([self._copia(s)[:4] for s in (primera, segunda)], [self.CORREGIDO] * 2)

    def test_una_entrada_por_qr_de_mas_de_un_dia_es_historia(self):
        vieja = db_clinical.interview_create(doctor_id="med-1", entry_mode="qr", dni=_DNI,
                                    entered_name="Ana", entered_surname="Gomes", matched=False)
        _sql("UPDATE interviews SET created = now() - interval '2 days' WHERE id=%s", (vieja["id"],))
        self.assertEqual(self._sync(nombre="Ana María", apellido="Gómez"), (0, 0))
        self.assertEqual(_sql("SELECT entered_name, entered_surname FROM interviews"), [("Ana", "Gomes")])


if __name__ == "__main__":
    unittest.main()
