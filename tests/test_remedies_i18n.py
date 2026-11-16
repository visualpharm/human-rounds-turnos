"""i18n de remedies.py — el paciente recibe la solución en SU idioma.

Aísla remedy() del catálogo/agenda reales (eso ya lo cubre
test_remedies.py:TestRemedio contra la base de verdad): acá lo que importa es
que cada una de las 18 salidas arme el texto correcto en cada idioma y que el
link de acción llegue intacto. Español se verifica byte a byte contra el
texto de siempre (nada cambió para una instalación de un solo idioma);
inglés, contra la traducción real de modules/turnos/locales/en/remedy.json
(Tier 2 batch D: movido junto con remedies.py).
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.turnos import remedies  # noqa: E402


def _svc(id_, nombre, centro="hospital-pinamar"):
    return {"id": id_, "nombre": nombre, "centro": centro, "modo": "solicitud_con_orden"}


class _RemedyI18nBase(unittest.TestCase):
    """`_localized` queda como identidad (no depende de la caché de
    traducción de pack content, un problema aparte de éste) y `_base`/
    `slots_for` quedan fijos, para que sólo esté bajo prueba el armado del
    texto — no la integración con catálogo/agenda/traductor de paquete."""

    def setUp(self):
        for target, kwargs in (
            ("_localized", {"side_effect": lambda s, lang: s}),
            ("_base", {"return_value": "https://x.ar"}),
        ):
            p = mock.patch.object(remedies, target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def _con_un_turno(self):
        """`slots_for` con un único horario fijo, ya traducido por `_when` —
        así las `acciones` de cada rama tienen un link y un texto para revisar."""
        def _fake(servicio, limite=3, lang=None):
            if not servicio:
                return []
            return [{"dia": "2026-08-04", "hora": "09:00",
                      "cuando": remedies._when("2026-08-04", "09:00", lang),
                      "medico": "Dra. Ruiz"}]
        p = mock.patch.object(remedies, "slots_for", side_effect=_fake)
        p.start()
        self.addCleanup(p.stop)


class TestReupload(_RemedyI18nBase):
    """NOT_A_REFERRAL / UNREADABLE / WRONG_PERSON / DNI_MISMATCH — el turno ya
    está pedido, sólo falta volver a subir la orden."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(remedies, "_service_by_id",
                               return_value=_svc("cardiologia-hospital", "Cardiología"))
        p.start()
        self.addCleanup(p.stop)
        self.row = {"servicio_id": "cardiologia-hospital"}

    def _check(self, motivo, titulo_es, titulo_en, detalle_es, detalle_en):
        es = remedies.remedy(self.row, motivo, lang="es")
        en = remedies.remedy(self.row, motivo, lang="en")
        self.assertEqual(es["titulo"], titulo_es)
        self.assertEqual(es["detalle"], detalle_es)
        self.assertEqual(en["titulo"], titulo_en)
        self.assertEqual(en["detalle"], detalle_en)
        for rem in (es, en):
            self.assertEqual(len(rem["acciones"]), 1)
            self.assertEqual(rem["acciones"][0]["url"],
                              "https://x.ar/upload-order?service=cardiologia-hospital")
        self.assertEqual(es["acciones"][0]["texto"], "Subir la orden")
        self.assertEqual(en["acciones"][0]["texto"], "Upload the referral")

    def test_no_es_orden(self):
        self._check(
            "El documento subido no es una orden médica.",
            "Subí la orden médica y listo",
            "Upload the referral and you're set",
            "Tu pedido de Cardiología ya está armado: sólo falta la orden. "
            "Sacale una foto donde se lea todo (incluida la fecha) y subila acá "
            "— no tenés que empezar de nuevo.",
            "Your Cardiología request is already set up: all that's missing is "
            "the referral. Take a photo where everything is readable "
            "(including the date) and upload it here — you don't have to "
            "start over.")

    def test_ilegible(self):
        self._check(
            "La orden no se lee con claridad.",
            "Subí una foto más nítida de la orden y listo",
            "Upload a clearer photo of the referral and you're set",
            "Tu pedido de Cardiología ya está armado: sólo falta la orden. "
            "Sacale una foto donde se lea todo (incluida la fecha) y subila acá "
            "— no tenés que empezar de nuevo.",
            "Your Cardiología request is already set up: all that's missing is "
            "the referral. Take a photo where everything is readable "
            "(including the date) and upload it here — you don't have to "
            "start over.")

    def test_otra_persona(self):
        self._check(
            "La orden está a nombre de otra persona.",
            "Subí la orden que está a tu nombre y listo",
            "Upload the referral that's in your name and you're set",
            "Tu pedido de Cardiología ya está armado: sólo falta la orden. "
            "Sacale una foto donde se lea todo (incluida la fecha) y subila acá "
            "— no tenés que empezar de nuevo.",
            "Your Cardiología request is already set up: all that's missing is "
            "the referral. Take a photo where everything is readable "
            "(including the date) and upload it here — you don't have to "
            "start over.")

    def test_dni_distinto(self):
        self._check(
            "El DNI de la orden no coincide con el de la cuenta.",
            "Subí la orden que está a tu nombre y listo",
            "Upload the referral that's in your name and you're set",
            "Tu pedido de Cardiología ya está armado: sólo falta la orden. "
            "Sacale una foto donde se lea todo (incluida la fecha) y subila acá "
            "— no tenés que empezar de nuevo.",
            "Your Cardiología request is already set up: all that's missing is "
            "the referral. Take a photo where everything is readable "
            "(including the date) and upload it here — you don't have to "
            "start over.")


class TestOrdenNueva(_RemedyI18nBase):
    """EXPIRED / NO_SIGNATURE / NO_DATE / WRONG_JURISDICTION — la salida es
    una consulta con Medicina general, que no exige orden."""

    def setUp(self):
        super().setUp()
        self._con_un_turno()
        clinico = _svc("medicina-general-pinamar", "Medicina general", centro="caps-pinamar")
        p = mock.patch.object(remedies, "find_service", return_value=clinico)
        p.start()
        self.addCleanup(p.stop)
        self.row = {"servicio_id": "cardiologia-hospital", "centro_slug": "hospital-pinamar"}

    def _check(self, motivo, detalle_es, detalle_en):
        es = remedies.remedy(self.row, motivo, lang="es")
        en = remedies.remedy(self.row, motivo, lang="en")
        self.assertEqual(es["titulo"], "Sacá un turno con Medicina general")
        self.assertEqual(en["titulo"], "Book a General practice appointment")
        self.assertEqual(es["detalle"], detalle_es)
        self.assertEqual(en["detalle"], detalle_en)
        self.assertEqual(es["acciones"][0]["texto"], "Martes 4 de agosto, 09:00")
        self.assertEqual(en["acciones"][0]["texto"], "Tuesday, August 4, 09:00")
        for rem in (es, en):
            self.assertEqual(rem["acciones"][0]["url"],
                              "https://x.ar/service/medicina-general-pinamar/book"
                              "?slot=2026-08-04T09:00")

    def test_vencida(self):
        self._check(
            "La orden está vencida.",
            "Para que te den una orden nueva necesitás una consulta con Medicina "
            "general. No hace falta orden para sacar ese turno.",
            "To get a new referral you need a General practice appointment. No "
            "referral is needed to book that one.")

    def test_sin_firma(self):
        self._check(
            "Falta la firma o la matrícula del médico.",
            "Para que la orden sirva le falta la firma o la matrícula del "
            "médico. Si podés, pedile que te la complete; si no, sacá un turno "
            "con Medicina general y te hacen una nueva.",
            "The referral is missing the doctor's signature or license number. "
            "If you can, ask them to add it; if not, book a General practice "
            "appointment and they'll write you a new one.")

    def test_sin_fecha(self):
        self._check(
            "La orden no tiene fecha.",
            "La orden no tiene fecha, así que no podemos saber si sigue "
            "vigente. Si podés, pedile al médico que le ponga la fecha; si no, "
            "sacá un turno con Medicina general y te hacen una nueva.",
            "The referral has no date, so we can't tell if it's still valid. "
            "If you can, ask the doctor to add the date; if not, book a "
            "General practice appointment and they'll write you a new one.")

    def test_otra_jurisdiccion(self):
        self._check(
            "La orden fue emitida en otra jurisdicción y no corresponde a este centro.",
            "La orden que subiste fue emitida en otra jurisdicción y no "
            "corresponde a este centro. Sacá un turno con Medicina general: no "
            "hace falta orden, y ahí te evalúan y te dan una orden válida para "
            "esta red si corresponde.",
            "The referral you uploaded was issued in another jurisdiction and "
            "doesn't apply to this center. Book a General practice appointment: "
            "no referral needed, and they'll assess you there and issue a "
            "valid referral for this network if needed.")


class TestOtroServicio(_RemedyI18nBase):
    """WRONG_SERVICE — la orden pide otra especialidad; se ofrece esa."""

    def setUp(self):
        super().setUp()
        self._con_un_turno()
        p1 = mock.patch.object(remedies, "suggested_service",
                                return_value={"id": "cardiologia-hospital"})
        p1.start()
        self.addCleanup(p1.stop)
        p2 = mock.patch.object(remedies, "_service_by_id",
                                return_value=_svc("cardiologia-hospital", "Cardiología"))
        p2.start()
        self.addCleanup(p2.stop)
        self.row = {"servicio_id": "psiquiatria-pinamar", "centro_slug": "caps-pinamar"}
        self.motivo = "La orden no corresponde al servicio solicitado."

    def test_sin_centro_repetido(self):
        with mock.patch.object(remedies, "_center_name", return_value=""):
            es = remedies.remedy(self.row, self.motivo, lang="es")
            en = remedies.remedy(self.row, self.motivo, lang="en")
        self.assertEqual(es["titulo"], "Tu orden es para Cardiología")
        self.assertEqual(en["titulo"], "Your referral is for Cardiología")
        self.assertEqual(es["detalle"],
                          "Sacá el turno en Cardiología y usá la misma orden: "
                          "sirve tal cual está.")
        self.assertEqual(en["detalle"],
                          "Book the appointment at Cardiología and use the same "
                          "referral: it works as is.")
        for rem in (es, en):
            self.assertEqual(rem["acciones"][0]["url"],
                              "https://x.ar/service/cardiologia-hospital/book"
                              "?slot=2026-08-04T09:00")

    def test_con_centro_repetido(self):
        with mock.patch.object(remedies, "_center_name", return_value="Hospital de Pinamar"):
            es = remedies.remedy(self.row, self.motivo, lang="es")
            en = remedies.remedy(self.row, self.motivo, lang="en")
        self.assertEqual(es["detalle"],
                          "Sacá el turno en Cardiología (Hospital de Pinamar) y "
                          "usá la misma orden: sirve tal cual está.")
        self.assertEqual(en["detalle"],
                          "Book the appointment at Cardiología (Hospital de "
                          "Pinamar) and use the same referral: it works as is.")


class TestOtroCentro(_RemedyI18nBase):
    """NO_SLOTS / NOT_PERFORMED_HERE — el mismo servicio en otro centro."""

    def setUp(self):
        super().setUp()
        self._con_un_turno()
        p1 = mock.patch.object(remedies, "_service_by_id",
                                return_value=_svc("psiquiatria-pinamar", "Psiquiatría",
                                                   centro="caps-pinamar"))
        p1.start()
        self.addCleanup(p1.stop)
        p2 = mock.patch.object(
            remedies, "_mismo_servicio_otro_centro",
            return_value=_svc("psiquiatria-diabetpin", "Psiquiatría", centro="diabetpin"))
        p2.start()
        self.addCleanup(p2.stop)
        self.row = {"servicio_id": "psiquiatria-pinamar"}

    def test_no_hay_turnos_centro_nombrado(self):
        with mock.patch.object(remedies, "_center_name", return_value="CAPS DiabetPin"):
            es = remedies.remedy(self.row, "No hay turnos disponibles para este servicio por ahora.",
                                  lang="es")
            en = remedies.remedy(self.row, "No hay turnos disponibles para este servicio por ahora.",
                                  lang="en")
        self.assertEqual(es["titulo"], "Hay turnos en CAPS DiabetPin")
        self.assertEqual(en["titulo"], "There are openings at CAPS DiabetPin")
        self.assertEqual(es["detalle"],
                          "Psiquiatría también se atiende en CAPS DiabetPin. Los "
                          "centros del partido están a pocos minutos entre sí.")
        self.assertEqual(en["detalle"],
                          "Psiquiatría is also available at CAPS DiabetPin. The "
                          "district's centers are just a few minutes apart.")
        for rem in (es, en):
            self.assertEqual(rem["acciones"][0]["url"],
                              "https://x.ar/service/psiquiatria-diabetpin/book"
                              "?slot=2026-08-04T09:00")

    def test_no_se_realiza_centro_generico(self):
        with mock.patch.object(remedies, "_center_name", return_value=""):
            es = remedies.remedy(self.row, "El servicio solicitado no se realiza en este centro.",
                                  lang="es")
            en = remedies.remedy(self.row, "El servicio solicitado no se realiza en este centro.",
                                  lang="en")
        self.assertEqual(es["titulo"], "Hay turnos en otro centro")
        self.assertEqual(en["titulo"], "There are openings at another center")
        self.assertEqual(es["detalle"],
                          "Psiquiatría también se atiende en otro centro. Los "
                          "centros del partido están a pocos minutos entre sí.")
        self.assertEqual(en["detalle"],
                          "Psiquiatría is also available at another center. The "
                          "district's centers are just a few minutes apart.")


class TestEmailWhatsapp(unittest.TestCase):
    """`email_lines`/`whatsapp_text` sólo traducen SU chrome — el resto ya
    viene resuelto en `rem`."""

    def _rem(self):
        return {"titulo": "Book a General practice appointment",
                "detalle": "No referral is needed.",
                "turnos": [{"cuando": "Tuesday, August 4, 09:00"}],
                "acciones": [{"texto": "Tuesday, August 4, 09:00",
                              "url": "https://x.ar/service/mg/book?slot=2026-08-04T09:00"}]}

    def test_email_en(self):
        txt = "\n".join(remedies.email_lines(self._rem(), lang="en"))
        self.assertIn("Open times (the link books that time for you):", txt)
        self.assertIn("https://x.ar/service/mg/book?slot=2026-08-04T09:00", txt)

    def test_email_es_por_defecto(self):
        # Sin lang, la instalación de un solo idioma sigue en español de siempre.
        txt = "\n".join(remedies.email_lines(self._rem()))
        self.assertIn("Horarios libres (el link te deja el turno elegido):", txt)

    def test_whatsapp_en_con_nombre_y_servicio(self):
        txt = remedies.whatsapp_text(self._rem(), "Ana", "Cardiología", lang="en")
        self.assertTrue(txt.startswith("Hi, Ana."))
        self.assertIn("We couldn't approve your Cardiología request.", txt)
        self.assertIn("https://x.ar/service/mg/book?slot=2026-08-04T09:00", txt)

    def test_whatsapp_en_sin_nombre_ni_servicio(self):
        txt = remedies.whatsapp_text(self._rem(), "", "", lang="en")
        self.assertTrue(txt.startswith("Hi."))
        self.assertIn("We couldn't approve your request.", txt)

    def test_whatsapp_es_por_defecto(self):
        txt = remedies.whatsapp_text(self._rem(), "Ana", "Cardiología")
        self.assertTrue(txt.startswith("Hola, Ana."))
        self.assertIn("No pudimos aprobar tu pedido de Cardiología.", txt)


if __name__ == "__main__":
    unittest.main()
