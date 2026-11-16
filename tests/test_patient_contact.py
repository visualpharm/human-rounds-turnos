"""core/patient_contact.contacto: for the turn the REQUEST's phone is the main
one, the account's phone is never replaced, and a different account number is
shown next to it with whose it is (the product owner's hub, 2026-10-23) — pure, no database."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import patient_contact  # noqa: E402

CUENTA = {"42000001": {"nombre": "ANA", "apellido": "PEREZ", "contacto": "+5492254100001"}}


class TestContacto(unittest.TestCase):
    def test_pide_con_otro_numero_el_principal_es_el_de_la_solicitud(self):
        c = patient_contact.contacto("42000001", None, "+5492254199999", CUENTA)
        self.assertEqual((c["contacto"], c["contacto_de"]), ("+5492254199999", "solicitud"))
        self.assertEqual((c["contacto_cuenta"], c["contacto_cuenta_de"]), ("+5492254100001", "paciente"))

    def test_mismo_numero_con_otra_escritura_es_uno_solo_dicho_de_la_cuenta(self):
        for escrito in ("2254 10-0001", "02254 15 100001", "+54 9 2254 10-0001"):
            c = patient_contact.contacto("42000001", None, escrito, CUENTA)
            self.assertEqual((c["contacto"], c["contacto_de"]), ("+5492254100001", "paciente"), escrito)
            self.assertIsNone(c["contacto_cuenta"], escrito)

    def test_solo_la_solicitud(self):
        c = patient_contact.contacto("42000009", None, "2254100009", CUENTA)
        self.assertEqual((c["contacto"], c["contacto_de"], c["contacto_cuenta"]), ("2254100009", "solicitud", None))

    def test_solo_la_cuenta_cuando_la_solicitud_no_trae_telefono(self):
        c = patient_contact.contacto("42000001", None, "", CUENTA)
        self.assertEqual((c["contacto"], c["contacto_de"]), ("+5492254100001", "paciente"))

    def test_quien_saco_el_turno_es_el_responsable_y_se_nombra(self):
        c = patient_contact.contacto("42000002", "42000001", "+5492254199999", CUENTA)
        self.assertEqual((c["contacto"], c["contacto_de"]), ("+5492254199999", "solicitud"))
        self.assertEqual((c["contacto_cuenta"], c["contacto_cuenta_de"]), ("+5492254100001", "responsable"))
        self.assertEqual(c["contacto_responsable"], "Ana Perez")

    def test_mail_en_cuenta_y_telefono_en_solicitud_son_dos_contactos(self):
        cuenta = {"42000003": {"contacto": "ana@ejemplo.com"}}
        c = patient_contact.contacto("42000003", None, "2254100003", cuenta)
        self.assertEqual((c["contacto"], c["contacto_cuenta"]), ("2254100003", "ana@ejemplo.com"))
        igual = patient_contact.contacto("42000003", None, "ANA@ejemplo.com", cuenta)
        self.assertIsNone(igual["contacto_cuenta"])

    def test_sin_nada_no_hay_contacto(self):
        c = patient_contact.contacto("42000009", None, "", {})
        self.assertEqual(c, {"contacto": None, "contacto_de": None, "contacto_responsable": None,
                             "contacto_cuenta": None, "contacto_cuenta_de": None})


if __name__ == "__main__":
    unittest.main()
