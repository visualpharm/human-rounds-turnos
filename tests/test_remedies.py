"""Cuando algo sale mal, el paciente recibe el REMEDIO (remedies.py).

Lo que se fija acá es el contrato que le importa al paciente: que el aviso
traiga el paso siguiente con un link que resuelve en un click, y que ese link
apunte al MISMO servicio al que el operador va a derivar (los dos salen de
esta misma función — si divergen, el mail promete una cosa y la cola hace otra).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.turnos import remedies  # noqa: E402
from _packtest import reference_pack_case  # noqa: E402


def _orden(especialidad, coincide=False, practicas=None):
    return {
        "campos": {
            "especialidad": {"valor": especialidad, "coincide": coincide},
            "practicas": {"valor": practicas or []},
        }
    }


class TestCausas(unittest.TestCase):
    def test_cada_motivo_estandar_tiene_causa(self):
        # Los motivos son una lista cerrada y curada (admin.html:MOTIVOS_*):
        # si uno deja de reconocerse, ese paciente vuelve al mail sin salida.
        motivos = [
            ("La orden está vencida.", remedies.EXPIRED),
            ("El documento subido no es una orden médica.", remedies.NOT_A_REFERRAL),
            ("La orden no se lee con claridad.", remedies.UNREADABLE),
            ("La orden está a nombre de otra persona.", remedies.WRONG_PERSON),
            ("El DNI de la orden no coincide con el de la cuenta.", remedies.DNI_MISMATCH),
            ("La orden no corresponde al servicio solicitado.", remedies.WRONG_SERVICE),
            ("Falta la firma o la matrícula del médico.", remedies.NO_SIGNATURE),
            ("No hay turnos disponibles para este servicio por ahora.", remedies.NO_SLOTS),
            ("El servicio solicitado no se realiza en este centro.", remedies.NOT_PERFORMED_HERE),
            ("La orden fue emitida en otra jurisdicción y no corresponde a este centro.",
             remedies.WRONG_JURISDICTION),
        ]
        for texto, esperado in motivos:
            self.assertEqual(remedies.cause_of(texto), esperado, texto)

    def test_motivo_libre_no_inventa_causa(self):
        # "Otro motivo…" es texto del operador: no hay solución estándar que
        # ofrecer y preferimos el aviso simple a mandarlo a cualquier lado.
        self.assertIsNone(remedies.cause_of("El paciente ya se atendió por guardia"))
        self.assertIsNone(remedies.cause_of(""))


class TestMatcher(unittest.TestCase):
    """Espejo Python de search.js: el operador y la orden nombran al
    profesional, el catálogo nombra a la especialidad."""

    CAT = [
        {"id": "cardiologia-hospital", "nombre": "Cardiología", "centro": "hospital-pinamar",
         "modo": "solicitud_con_orden", "palabras": []},
        {"id": "psiquiatria-pinamar", "nombre": "Psiquiatría", "centro": "caps-pinamar",
         "modo": "solicitud", "palabras": []},
        {"id": "psiquiatria-diabetpin", "nombre": "Psiquiatría", "centro": "diabetpin",
         "modo": "solicitud", "palabras": []},
        {"id": "ergometria-hospital", "nombre": "Ergometría", "centro": "hospital-pinamar",
         "modo": "solicitud_con_orden", "palabras": ["prueba de esfuerzo"]},
        {"id": "medicina-general-pinamar", "nombre": "Medicina general", "centro": "caps-pinamar",
         "modo": "solicitud", "palabras": ["clínico"]},
    ]

    def _buscar(self, q, **kw):
        return remedies.find_service(q, servicios=self.CAT, solo_turnos=False, **kw)

    def test_profesional_encuentra_especialidad(self):
        self.assertEqual(self._buscar("cardiólogo")["id"], "cardiologia-hospital")
        self.assertEqual(self._buscar("psiquiatra")["id"], "psiquiatria-pinamar")

    def test_frase_de_orden_gana_el_estudio_no_la_especialidad(self):
        # El caso de search-match.test.mjs: la frase completa nombra el estudio
        # primero; degradar por la cola lo deja ganar por NOMBRE.
        self.assertEqual(self._buscar("Ergometría de 12 derivaciones")["id"],
                         "ergometria-hospital")

    def test_palabra_clave_encuentra_el_servicio(self):
        self.assertEqual(self._buscar("prueba de esfuerzo")["id"], "ergometria-hospital")

    def test_desempata_por_el_centro_del_paciente(self):
        # De dos Psiquiatrías, la del centro donde el paciente ya pidió.
        self.assertEqual(self._buscar("psiquiatría", centro="diabetpin")["id"],
                         "psiquiatria-diabetpin")
        self.assertEqual(self._buscar("psiquiatría", centro="caps-pinamar")["id"],
                         "psiquiatria-pinamar")

    def test_sin_match_devuelve_none(self):
        self.assertIsNone(self._buscar("resonancia magnética"))

    def test_solo_turnos_descarta_walkin(self):
        cat = [{"id": "rx", "nombre": "Radiografía", "centro": "hospital-pinamar",
                "modo": "walkin_con_orden", "palabras": []}]
        # Sin turno que sacar no hay deep link honesto que mandar.
        self.assertIsNone(remedies.find_service("radiografía", servicios=cat))
        self.assertIsNotNone(
            remedies.find_service("radiografía", servicios=cat, solo_turnos=False))


@reference_pack_case
class TestMismoServicioOtroCentro(unittest.TestCase):
    """`_mismo_servicio_otro_centro` faltaba (NameError) y dejaba muerta la
    rama NO_SLOTS/NOT_PERFORMED_HERE de remedy() — este test contra el
    catálogo real prueba que ahora encuentra el gemelo en otro centro."""

    def test_psiquiatria_pinamar_encuentra_a_diabetpin(self):
        actual = remedies._service_by_id("psiquiatria-pinamar")
        self.assertIsNotNone(actual, "el catálogo real tiene psiquiatria-pinamar")
        otro = remedies._mismo_servicio_otro_centro(actual, "psiquiatria-pinamar")
        self.assertIsNotNone(otro)
        self.assertEqual(otro["id"], "psiquiatria-diabetpin")
        self.assertNotEqual(otro["centro"], actual["centro"])

    def test_nunca_se_devuelve_a_si_mismo(self):
        actual = remedies._service_by_id("psiquiatria-diabetpin")
        otro = remedies._mismo_servicio_otro_centro(actual, "psiquiatria-diabetpin")
        self.assertNotEqual((otro or {}).get("id"), "psiquiatria-diabetpin")

    def test_sin_actual_no_rompe(self):
        self.assertIsNone(remedies._mismo_servicio_otro_centro(None, "x"))
        self.assertIsNone(remedies._mismo_servicio_otro_centro({}, "x"))


class TestServicioSugerido(unittest.TestCase):
    def test_solo_cuando_la_lectura_contradice(self):
        row = {"servicio_id": "psiquiatria-pinamar", "centro_slug": "caps-pinamar",
               "ai_json": _orden("Psiquiatría", coincide=True)}
        self.assertIsNone(remedies.suggested_service(row))

    def test_no_comparable_no_sugiere(self):
        # ai_referral nunca inventa un conflicto: coincide=None = no se pudo leer.
        row = {"servicio_id": "psiquiatria-pinamar",
               "ai_json": _orden("", coincide=None)}
        self.assertIsNone(remedies.suggested_service(row))

    def test_sin_ai_json_no_sugiere(self):
        self.assertIsNone(remedies.suggested_service({"servicio_id": "x", "ai_json": None}))

    def _sugerir(self, sid, esp, practicas=(), centro="hospital-pinamar"):
        row = {"servicio_id": sid, "centro_slug": centro, "ai_json": {"campos": {
            "especialidad": {"valor": esp, "coincide": False},
            "practicas": {"valor": list(practicas)}}}}
        s = remedies.suggested_service(row)
        return s["nombre"] if s else None

    def test_matriz_contra_el_catalogo_real(self):
        """La sugerencia prefillea el combo, o sea que cambia lo que se aprueba:
        acertar importa, pero NO sugerir vale más que sugerir cualquier cosa."""
        # la especialidad de la orden manda cuando la práctica es genérica
        self.assertEqual(self._sugerir("psiquiatria-pinamar", "CARDIOLOGIA",
                                       ["Consulta cardiológica"]), "Cardiología")
        self.assertEqual(self._sugerir("psiquiatria-pinamar", "TRAUMATOLOGIA"),
                         "Traumatología")
        # y la práctica manda cuando nombra un estudio concreto
        self.assertEqual(self._sugerir("cardiologia-hospital", "Cardiología",
                                       ["Ergometría de 12 derivaciones"]), "Ergometría")
        # una CATEGORÍA no es un servicio: antes proponía "Ecografía infantil"
        # por matchear la palabra clave "diagnóstico por imágenes"
        self.assertIsNone(self._sugerir("ecografia-hospital", "Diagnóstico por imágen"))
        # "rm de" no puede vetar por el token "rm" (deRMatología, enfeRMo)
        self.assertEqual(self._sugerir("psiquiatria-pinamar", "DERMATOLOGIA",
                                       ["Control de lunares"]), "Dermatología")


class TestTextos(unittest.TestCase):
    def test_link_de_turno_lleva_el_slot_elegido(self):
        url = remedies.appointment_link("https://x.ar", "cardiologia-hospital", "2026-08-03", "08:30")
        self.assertEqual(url, "https://x.ar/service/cardiologia-hospital/book?slot=2026-08-03T08:30")

    def test_link_sin_slot_es_el_wizard_del_servicio(self):
        self.assertEqual(remedies.appointment_link("https://x.ar", "s1"),
                         "https://x.ar/service/s1/book")

    def test_link_de_orden_precarga_el_servicio(self):
        self.assertEqual(remedies.referral_link("https://x.ar", "s1"),
                         "https://x.ar/upload-order?service=s1")

    def test_cuando_es_fecha_amigable_sin_relativos(self):
        # Un mail se lee más tarde: "Hoy" envejece mal.
        self.assertEqual(remedies._when("2026-08-03", "08:30"),
                         "Lunes 3 de agosto, 08:30")

    def test_lineas_email_incluyen_cada_link(self):
        rem = {"titulo": "Sacá un turno con Medicina general",
               "detalle": "Necesitás una orden nueva.",
               "turnos": [{"cuando": "Lunes 3 de agosto, 08:30"}],
               "acciones": [{"texto": "Lunes 3 de agosto, 08:30",
                             "url": "https://x.ar/service/mg/book?slot=2026-08-03T08:30"}]}
        txt = "\n".join(remedies.email_lines(rem))
        self.assertIn("Sacá un turno con Medicina general", txt)
        self.assertIn("https://x.ar/service/mg/book?slot=2026-08-03T08:30", txt)

    def test_sin_remedio_no_agrega_nada(self):
        self.assertEqual(remedies.email_lines(None), [])
        self.assertIsNone(remedies.whatsapp_text(None, "Ana", "Cardiología"))

    def test_whatsapp_lleva_la_salida_y_el_link(self):
        rem = {"titulo": "Tu orden es para Cardiología", "detalle": "Sacá el turno ahí.",
               "acciones": [{"texto": "Lunes 3 de agosto, 08:30", "url": "https://x.ar/a"}]}
        txt = remedies.whatsapp_text(rem, "Ana", "Psiquiatría")
        self.assertIn("Ana", txt)
        self.assertIn("Tu orden es para Cardiología", txt)
        self.assertIn("https://x.ar/a", txt)


class TestRemedio(unittest.TestCase):
    """remedy() habla con el catálogo real; lo que se fija es la FORMA de la
    salida y que nunca levante (el rechazo ya persistió cuando corre esto)."""

    def test_causa_desconocida_no_da_remedio(self):
        row = {"servicio_id": "cardiologia-hospital"}
        self.assertIsNone(remedies.remedy(row, "cualquier cosa"))

    def test_reupload_apunta_al_servicio_ya_pedido(self):
        row = {"servicio_id": "cardiologia-hospital", "centro_slug": "hospital-pinamar"}
        rem = remedies.remedy(row, "La orden no se lee con claridad.")
        self.assertIsNotNone(rem)
        urls = [a["url"] for a in rem["acciones"]]
        self.assertTrue(any("/upload-order?service=cardiologia-hospital" in u for u in urls))

    def test_vencida_ofrece_medicina_general_con_turnos(self):
        row = {"servicio_id": "cardiologia-hospital", "centro_slug": "hospital-pinamar"}
        rem = remedies.remedy(row, "La orden está vencida.")
        self.assertIsNotNone(rem, "el catálogo real tiene Medicina general")
        self.assertIn("Medicina general", rem["titulo"])
        self.assertTrue(rem["acciones"], "siempre al menos un link")
        for a in rem["acciones"]:
            self.assertIn("/service/", a["url"])
            self.assertNotIn("/my-requests", a["url"])

    def test_otra_jurisdiccion_ofrece_medicina_general(self):
        # No es "conseguí la misma orden de nuevo" (reupload no ayuda acá):
        # el remedio real es la consulta local que puede darle una orden
        # válida para esta red.
        row = {"servicio_id": "cardiologia-hospital", "centro_slug": "hospital-pinamar"}
        rem = remedies.remedy(
            row, "La orden fue emitida en otra jurisdicción y no corresponde a este centro.")
        self.assertIsNotNone(rem, "el catálogo real tiene Medicina general")
        self.assertIn("Medicina general", rem["titulo"])
        self.assertIn("jurisdicción", rem["detalle"])
        self.assertTrue(rem["acciones"], "siempre al menos un link")
        for a in rem["acciones"]:
            self.assertIn("/service/", a["url"])

    def test_nunca_levanta_con_una_fila_rota(self):
        # El rechazo YA persistió cuando corre esto: pase lo que pase, devuelve
        # algo o None, nunca una excepción que se lleve puesto el aviso.
        # (Una fila sin servicio igual resuelve "vencida": el remedio es el
        # clínico, que no depende de lo que el paciente había pedido.)
        remedies.remedy({}, "La orden está vencida.")
        self.assertIsNone(remedies.remedy({"servicio_id": None}, ""))
        self.assertIsNone(remedies.remedy({}, None))


if __name__ == "__main__":
    unittest.main()
