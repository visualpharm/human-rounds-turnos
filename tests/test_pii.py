"""Nada que identifique a una persona sale hacia un proveedor de IA externo.

Los tres caminos que mandan texto (chat, entrevista previa y resumen de
historia clínica) pasan por `pii.Scrubber`. Lo que se prueba acá es lo que la
política de privacidad le promete al paciente:

  - el nombre, el DNI, el teléfono, el mail y el domicilio salen como
    etiquetas;
  - la respuesta del modelo vuelve con los datos reales (seudónimo reversible,
    no borrado);
  - un valor clínico o una fecha NO se confunden con un documento;
  - la imagen de la orden viaja sin tocar (es lo único que sale tal cual, y
    está dicho en la política).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import pii  # noqa: E402


PERSONA = {
    "nombre": "Ana", "apellido": "Molina", "dni": "30111222",
    "contacto": "+54 9 2254 55-6677", "email": "ana.molina@mail.com",
    "domicilio": {"texto": "Del Tuyú 1234, Pinamar"},
}


def _sembrado():
    s = pii.Scrubber()
    s.learn_person(PERSONA)
    return s


class TestIdentidadConocida(unittest.TestCase):

    def test_nombre_completo_dni_telefono_mail_y_domicilio_no_salen(self):
        s = _sembrado()
        texto = ("Soy Ana Molina, DNI 30111222, tel +54 9 2254 55-6677, "
                 "mail ana.molina@mail.com, vivo en Del Tuyú 1234, Pinamar.")
        salida = s.scrub(texto)
        for dato in ("Ana Molina", "30111222", "2254", "ana.molina@mail.com",
                     "Del Tuyú 1234"):
            self.assertNotIn(dato, salida, f"{dato} salió sin enmascarar")
        self.assertIn("[nombre 1]", salida)
        self.assertIn("[documento 1]", salida)

    def test_la_respuesta_del_modelo_vuelve_con_los_datos_reales(self):
        s = _sembrado()
        texto = "Soy Ana Molina y mi DNI es 30111222."
        salida = s.scrub(texto)
        self.assertEqual(s.restore(salida), texto)

    def test_el_mismo_valor_siempre_recibe_la_misma_etiqueta(self):
        s = _sembrado()
        salida = s.scrub("Ana Molina pidió turno. Ana Molina lo canceló.")
        self.assertEqual(salida.count("[nombre 1]"), 2)

    def test_un_apellido_en_minuscula_no_se_confunde_con_palabra_comun(self):
        # "Molina" es un apellido y también un sustantivo común. Sólo se
        # reemplaza cuando aparece como nombre propio.
        s = _sembrado()
        self.assertEqual(s.scrub("el molino y el barrio molina siguen igual"),
                         "el molino y el barrio molina siguen igual")
        self.assertIn("[nombre", s.scrub("La Dra. Molina atiende los martes."))

    def test_el_nombre_en_mayusculas_del_mostrador_tambien_se_tapa(self):
        s = _sembrado()
        self.assertNotIn("MOLINA", s.scrub("MOLINA figura en la agenda."))


class TestBarridoGenerico(unittest.TestCase):
    """Lo que la persona tipea a mano y nosotros no teníamos guardado."""

    def test_dni_cuil_telefono_y_mail_ajenos_se_detectan_solos(self):
        s = pii.Scrubber()
        salida = s.scrub("Mi hija tiene DNI 55666777, CUIL 27-55666777-4, "
                         "tel 2254 998877 y mail otra@mail.com")
        for dato in ("55666777", "27-55666777-4", "998877", "otra@mail.com"):
            self.assertNotIn(dato, salida)

    def test_una_fecha_no_es_un_documento(self):
        s = pii.Scrubber()
        for fecha in ("12/05/2026", "12-05-2026", "12.05.2026"):
            self.assertIn(fecha, s.scrub(f"El turno es el {fecha} a las 10."))

    def test_un_valor_de_laboratorio_no_es_un_documento(self):
        s = pii.Scrubber()
        texto = "Plaquetas 1.250.000, TA 120/80, hemoglobina 13.5, peso 72."
        self.assertEqual(s.scrub(texto), texto)

    def test_el_numero_de_emergencias_no_se_tapa(self):
        s = pii.Scrubber()
        self.assertIn("107", s.scrub("Ante una urgencia llamá al 107."))


class TestMensajes(unittest.TestCase):

    def test_el_system_conserva_sus_numeros_pero_pierde_el_nombre(self):
        # El system lleva instrucciones nuestras (donde un número puede SER la
        # instrucción) y además el resumen de charlas anteriores de esta misma
        # persona, donde el nombre sí tiene que salir.
        s = _sembrado()
        salida = s.scrub_messages([
            {"role": "system", "content": "Máximo 3 turnos. Ana Molina ya consultó."},
        ])
        self.assertIn("Máximo 3 turnos.", salida[0]["content"])
        self.assertNotIn("Ana Molina", salida[0]["content"])

    def test_la_imagen_adjunta_viaja_sin_tocar(self):
        # La foto de la orden es lo único que sale tal cual, y está dicho en
        # la política de privacidad.
        s = _sembrado()
        imagen = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}
        salida = s.scrub_messages([{"role": "user", "content": [
            {"type": "text", "text": "Soy Ana Molina"}, imagen]}])
        partes = salida[0]["content"]
        self.assertNotIn("Ana Molina", partes[0]["text"])
        self.assertEqual(partes[1], imagen)

    def test_los_argumentos_de_una_tool_se_tapan_al_salir_y_vuelven_al_ejecutar(self):
        s = _sembrado()
        tc = {"id": "1", "function": {"name": "buscar",
                                      "arguments": '{"q": "Ana Molina"}'}}
        afuera = s.scrub_messages([{"role": "assistant", "content": None,
                                    "tool_calls": [tc]}])
        salidos = afuera[0]["tool_calls"][0]["function"]["arguments"]
        self.assertNotIn("Ana Molina", salidos)
        vuelto = s.restore_tool_call(afuera[0]["tool_calls"][0])
        self.assertEqual(vuelto["function"]["arguments"], tc["function"]["arguments"])

    def test_json_anidado_de_una_tool_se_limpia_entero(self):
        s = _sembrado()
        salida = s.scrub_json({"turnos": [{"paciente": "Ana Molina",
                                           "dni": "30111222"}]})
        self.assertNotIn("Ana Molina", str(salida))
        self.assertNotIn("30111222", str(salida))
        self.assertEqual(s.restore_json(salida),
                         {"turnos": [{"paciente": "Ana Molina", "dni": "30111222"}]})


class TestDegradacion(unittest.TestCase):

    def test_sin_base_de_datos_sigue_funcionando_con_lo_que_sabe(self):
        # for_dni() consulta la base para aprender el nombre; sin base tiene
        # que devolver igual un scrubber usable con el DNI solo.
        s = pii.for_dni("30111222")
        self.assertNotIn("30111222", s.scrub("DNI 30111222"))

    def test_texto_vacio_o_none_no_rompe(self):
        s = _sembrado()
        self.assertEqual(s.scrub(""), "")
        self.assertIsNone(s.scrub(None))
        self.assertEqual(s.scrub_messages([]), [])


if __name__ == "__main__":
    unittest.main()
