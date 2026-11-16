"""test_chat_language.py — el idioma de una conversación de chat.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_chat_language.py"

Cubre las fugas que encontró la simulación multilingüe del 2026-09-11 sobre la
demo (transcripciones en ~/.claude/artifacts/human-rounds/chat-lang-sim-2026-09-11):
un primer mensaje en portugués contestado entero en rioplatense, nombres de
sección en castellano crudo adentro de una respuesta en inglés o ruso, y un
chip «Ingresar» ofrecido a alguien que ya tenía la sesión abierta.

No toca la base ni la red: todo acá es determinístico.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import i18n                      # noqa: E402
from app.core import text_fold                 # noqa: E402
from app.modules.chat import chat_agent        # noqa: E402
from app.modules.chat import chat_api          # noqa: E402
from app.modules.chat import chat_language     # noqa: E402
from app.modules.chat import chat_tools        # noqa: E402


# Una frase real de paciente por idioma instalado: lo primero que alguien
# escribe es «necesito un turno» o «me duele acá», no un párrafo.
FRASES = {
    "es": "Hola, necesito un turno de medicina general",
    "en": "Hi, I need a general medicine appointment",
    "pt": "O que preciso levar na consulta?",
    "it": "Ho bisogno di un appuntamento dal medico",
    "fr": "J'ai besoin d'un rendez-vous avec un médecin",
    "de": "Ich brauche einen Termin beim Arzt",
    "ru": "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435, \u043c\u043d\u0435 \u043d\u0443\u0436\u043d\u0430 \u0437\u0430\u043f\u0438\u0441\u044c \u043a \u0432\u0440\u0430\u0447\u0443 \u043e\u0431\u0449\u0435\u0439 \u043f\u0440\u0430\u043a\u0442\u0438\u043a\u0438",
    "uk": "\u0414\u043e\u0431\u0440\u043e\u0433\u043e \u0434\u043d\u044f, \u043c\u0435\u043d\u0456 \u043f\u043e\u0442\u0440\u0456\u0431\u0435\u043d \u0437\u0430\u043f\u0438\u0441 \u0434\u043e \u0441\u0456\u043c\u0435\u0439\u043d\u043e\u0433\u043e \u043b\u0456\u043a\u0430\u0440\u044f",
    "ja": "診察に何を持って行けばいいですか",
    "zh": "我需要预约全科医生",
}


class Deteccion(unittest.TestCase):
    def test_una_frase_por_idioma_instalado(self):
        for code, frase in FRASES.items():
            self.assertEqual(chat_language.detect(frase), code, frase)

    def test_cubre_todos_los_idiomas_del_sitio(self):
        self.assertEqual(set(FRASES), set(i18n.LANGUAGE_NAMES))

    def test_portugues_no_se_lee_como_castellano(self):
        # La fuga 1 de la simulación: "O que preciso levar" puntuaba castellano
        # porque "que" estaba en la lista castellana y nada más competía.
        self.assertEqual(chat_language.detect("O que preciso levar"), "pt")

    def test_ucraniano_se_distingue_del_ruso_por_sus_letras(self):
        self.assertEqual(chat_language.detect("\u0431\u0456\u043b\u044c \u0443 \u0433\u0440\u0443\u0434\u044f\u0445"), "uk")
        self.assertEqual(chat_language.detect("\u0431\u043e\u043b\u044c \u0432 \u0433\u0440\u0443\u0434\u0438"), "ru")

    def test_mensaje_sin_senal_no_inventa_un_idioma(self):
        self.assertEqual(chat_language.detect(""), "")
        self.assertEqual(chat_language.detect("12345 :)"), "")

    def test_la_regla_nombra_el_idioma(self):
        regla = chat_language.rule(FRASES["pt"])
        self.assertIn("required response language is Português [pt]", regla)
        self.assertEqual(chat_language.evident(FRASES["uk"]), "\u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430")


class RuteoYEmergenciaEnTodoIdioma(unittest.TestCase):
    def test_el_piso_de_emergencia_reconoce_dolor_de_pecho(self):
        for frase in ("me duele el pecho", "I have chest pain",
                      "dor no peito", "dolore al petto",
                      "douleur dans la poitrine", "starke Brustschmerzen",
                      "\u0431\u043e\u043b\u044c \u0432 \u0433\u0440\u0443\u0434\u0438", "\u0431\u0456\u043b\u044c \u0443 \u0433\u0440\u0443\u0434\u044f\u0445"):
            self.assertTrue(chat_agent._busca(chat_agent._EMERGENCY_RE, frase),
                            frase)

    def test_una_emergencia_nunca_pide_horarios(self):
        self.assertFalse(
            chat_agent._requires_slot_grounding("dor no peito, quero marcar uma consulta"))

    def test_pedir_turno_se_reconoce_en_cada_idioma(self):
        for frase in ("quiero un turno", "I want to book an appointment",
                      "preciso marcar uma consulta",
                      "vorrei prenotare un appuntamento",
                      "je voudrais prendre un rendez-vous",
                      "ich brauche einen Termin",
                      "\u043c\u043d\u0435 \u043d\u0443\u0436\u043d\u0430 \u0437\u0430\u043f\u0438\u0441\u044c", "\u043c\u0435\u043d\u0456 \u043f\u043e\u0442\u0440\u0456\u0431\u0435\u043d \u0437\u0430\u043f\u0438\u0441"):
            self.assertTrue(chat_agent._requires_slot_grounding(frase), frase)

    def test_a_que_medico_sigue_siendo_ruteo_y_no_directorio(self):
        # El agregado portugués es plural a propósito: el singular castellano
        # tiene que seguir cayendo en el router de especialidad.
        self.assertTrue(chat_agent._requires_catalog_routing(
            "¿A qué médico tengo que ir?"))
        self.assertFalse(chat_agent._asks_who_attends(
            "¿A qué médico tengo que ir?"))


class NuevoChatResetaElIdioma(unittest.TestCase):
    """«Nuevo chat» era un corte para la pantalla y para el contexto del
    modelo, pero no para el idioma: la conversación anterior seguía mandando."""

    def setUp(self):
        self.marca = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        self.llamadas = []

    def _first_user_message(self, canal, clave, since=None):
        self.llamadas.append((canal, clave, since))
        if since is None:
            return "Hola, necesito un turno"
        return "O que preciso levar na consulta?"

    def test_el_marcador_corre_el_primer_mensaje_de_la_conversacion(self):
        with patch.object(chat_agent.chat_store, "chat_new_session_marker_at",
                          side_effect=lambda canal, clave: self.marca), \
             patch.object(chat_agent.chat_store, "chat_first_user_message",
                          side_effect=self._first_user_message):
            primero = chat_agent._first_user_message("web", "dni:30999888:chat:a1")
        self.assertEqual(primero, "O que preciso levar na consulta?")
        self.assertEqual(chat_language.detect(primero), "pt")
        self.assertEqual(self.llamadas[-1][2], self.marca)

    def test_sin_marcador_nada_cambia(self):
        with patch.object(chat_agent.chat_store, "chat_new_session_marker_at",
                          side_effect=lambda canal, clave: None), \
             patch.object(chat_agent.chat_store, "chat_first_user_message",
                          side_effect=self._first_user_message):
            primero = chat_agent._first_user_message("web", "dni:30999888")
        self.assertEqual(primero, "Hola, necesito un turno")

    def test_el_marcador_se_busca_en_la_clave_de_la_cuenta(self):
        # marcar_sesion_nueva lo deja en "dni:<dni>", no en la sub-conversación
        # de la pestaña.
        self.assertEqual(chat_agent._clave_base("dni:30999888:chat:a1"),
                         "dni:30999888")
        self.assertEqual(chat_agent._clave_base("ip:1.2.3.4"), "ip:1.2.3.4")


class EnlacesConNombreLocalizado(unittest.TestCase):
    def test_una_respuesta_en_ingles_enlaza_my_appointments(self):
        lang, token = i18n.bind_lang("en")
        try:
            enlaces = chat_tools.enlaces_para(
                "what is the status of my requests?",
                "You can see everything in **My appointments**.", [], lang="en")
        finally:
            i18n.unbind(token)
        self.assertIn("/my-requests", [e["href"] for e in enlaces])
        etiqueta = next(e["label"] for e in enlaces if e["href"] == "/my-requests")
        self.assertEqual(etiqueta, "My appointments")

    def test_el_nombre_anterior_castellano_sigue_enlazando(self):
        enlaces = chat_tools.enlaces_para(
            "¿en qué estado están?",
            "Podés verlo en **Mis solicitudes**.", [], lang="es")
        self.assertIn("/my-requests", [e["href"] for e in enlaces])

    def test_una_seccion_no_se_enlaza_dos_veces(self):
        enlaces = chat_tools.enlaces_para(
            "mis solicitudes",
            "Mirá **Mis turnos** y **My appointments**.", [], lang="en")
        hrefs = [e["href"] for e in enlaces]
        self.assertEqual(hrefs.count("/my-requests"), 1)


class EnlacesEnIdiomasNoLatinos(unittest.TestCase):
    """Los chips se deduplican por rótulo. `_norm` pasa por ASCII y deja vacío
    todo rótulo en cirílico, japonés o chino: dos secciones distintas valían lo
    mismo ("") y la segunda se perdía, así que una respuesta en ru/uk/ja/zh que
    nombraba "Mis solicitudes" y "Centros de salud" llevaba un solo chip. En la
    demo están los diez idiomas encendidos."""

    NO_LATINOS = ("ru", "uk", "ja", "zh")

    def _par_que_choca(self, lang):
        """Dos secciones de destino distinto cuyos rótulos en `lang` son
        distintos pero valen lo mismo para `_norm`. Sin un par así el test no
        probaría nada, así que su ausencia es un fallo."""
        rotulos = [(etiqueta, href)
                   for etiqueta, href in chat_tools._secciones_localizadas(lang)
                   if not etiqueta.isascii()]
        for i, (a, href_a) in enumerate(rotulos):
            for b, href_b in rotulos[i + 1:]:
                if href_a != href_b and a != b and text_fold.ascii_words(a) == text_fold.ascii_words(b):
                    return (a, href_a), (b, href_b)
        self.fail("%s: ningún par de secciones choca bajo _norm" % lang)

    def test_dos_secciones_nombradas_en_cada_idioma_no_latino_llevan_cada_una_su_chip(self):
        for lang in self.NO_LATINOS:
            with self.subTest(lang=lang):
                primera, segunda = self._par_que_choca(lang)
                enlaces = chat_tools.enlaces_para(
                    "?", "**%s** / **%s**" % (primera[0], segunda[0]), [], lang=lang)
                self.assertEqual(
                    {(e["label"], e["href"]) for e in enlaces},
                    {primera, segunda})

    def test_el_acumulador_no_confunde_rotulos_no_latinos_distintos(self):
        for lang in self.NO_LATINOS:
            with self.subTest(lang=lang):
                (a, href_a), (b, href_b) = self._par_que_choca(lang)
                enlaces, add = chat_tools._acumulador_de_enlaces()
                self.assertTrue(add(a, href_a))
                self.assertTrue(add(b, href_b))
                self.assertFalse(add(a, "/otro-destino"))
                self.assertEqual([e["href"] for e in enlaces], [href_a, href_b])

    def test_el_mismo_rotulo_latino_sigue_siendo_uno_con_acento_y_mayusculas(self):
        enlaces, add = chat_tools._acumulador_de_enlaces()
        self.assertTrue(add("Mis turnos", "/my-requests"))
        self.assertFalse(add("MIS TURNOS", "/otro"))
        self.assertFalse(add("Mís turnos", "/otro"))
        self.assertEqual(len(enlaces), 1)


class ChipDeLoginConSesionAbierta(unittest.TestCase):
    def test_se_descarta_la_opcion_de_ingresar(self):
        self.assertEqual(
            chat_api._sin_opcion_de_login(["Ingresar", "Mis solicitudes"]),
            ["Mis solicitudes"])

    def test_tambien_cuando_el_modelo_la_envuelve(self):
        self.assertEqual(
            chat_api._sin_opcion_de_login(["Ir para Ingresar", "Ver Laboratorio"]),
            ["Ver Laboratorio"])

    def test_en_ingles_tambien(self):
        self.assertEqual(chat_api._sin_opcion_de_login(["Sign in"]), [])


class EtiquetasDeEsperaLocalizadas(unittest.TestCase):
    def test_no_son_castellano_para_todos(self):
        self.assertEqual(chat_api.estado_etiquetas("en")[0],
                         "Reading your question…")
        self.assertEqual(chat_api.estado_etiquetas("es")[0],
                         "Leyendo tu consulta…")


if __name__ == "__main__":
    unittest.main()
