"""test_whatsapp_markdown.py — notifications/otp_send.py's `_markdown_a_whatsapp`.

The product owner (2026-08-06): "you are sending double stars, bolds to WhatsApp, and it
interprets single star as bold. So the bold highlighted text has also some
extra set of stars on the sides." The model writes Markdown; WhatsApp's own
markup uses a single delimiter, so `**x**` renders with literal asterisks
stuck to the bold text. `enviar_texto` is the one outbound choke point for
free WhatsApp text (whatsapp_chat.py calls it for every assistant reply), so
fixing the conversion there covers every WhatsApp text the app sends, not
only the chat.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

from app.notifications.otp_send import _markdown_a_whatsapp, enviar_texto  # noqa: E402
from app.notifications import otp_send  # noqa: E402


class TestMarkdownAWhatsapp(unittest.TestCase):
    def test_bold_doble_asterisco_a_simple(self):
        self.assertEqual(
            _markdown_a_whatsapp("Turno en **Hospital de Pinamar** a las 08:00"),
            "Turno en *Hospital de Pinamar* a las 08:00")

    def test_underline_doble_guion_bajo_a_simple(self):
        self.assertEqual(_markdown_a_whatsapp("__importante__"), "_importante_")

    def test_tachado_doble_virgulilla_a_simple(self):
        self.assertEqual(_markdown_a_whatsapp("~~viejo~~"), "~viejo~")

    def test_encabezados_a_negrita_whatsapp(self):
        self.assertEqual(_markdown_a_whatsapp("# Título"), "*Título*")
        self.assertEqual(_markdown_a_whatsapp("## Subtítulo"), "*Subtítulo*")
        self.assertEqual(_markdown_a_whatsapp("### Menor"), "*Menor*")

    def test_vineta_asterisco_a_guion(self):
        self.assertEqual(
            _markdown_a_whatsapp("* Ítem uno\n* Ítem dos"),
            "- Ítem uno\n- Ítem dos")

    def test_vineta_corre_despues_de_la_negrita_no_rompe_el_bold_inicial(self):
        # Un ítem de lista que empieza con negrita: el "*" de la negrita NO
        # se puede confundir con el "*" de la viñeta.
        self.assertEqual(
            _markdown_a_whatsapp("* **Traumatología**: 08:00 y 12:00"),
            "- *Traumatología*: 08:00 y 12:00")

    def test_link_absoluto_queda_como_label_dos_puntos_url(self):
        self.assertEqual(
            _markdown_a_whatsapp("Mirá [tus turnos](https://human-rounds.org/mis-turnos)"),
            "Mirá tus turnos: https://human-rounds.org/mis-turnos")

    def test_link_relativo_queda_solo_el_label(self):
        # Una ruta de la app (/mis-solicitudes) no sirve fuera del navegador.
        self.assertEqual(
            _markdown_a_whatsapp("Vas a [Mis solicitudes](/mis-solicitudes)"),
            "Vas a Mis solicitudes")

    def test_asterisco_o_guion_bajo_sueltos_en_prosa_quedan_igual(self):
        texto = "5 * 3 = 15, y esto_no_es_bold porque no está duplicado"
        self.assertEqual(_markdown_a_whatsapp(texto), texto)

    def test_idempotente_texto_ya_en_formato_whatsapp(self):
        texto = "*ya en negrita*, _ya en cursiva_, ~ya tachado~\n- ya con guión"
        self.assertEqual(_markdown_a_whatsapp(texto), texto)

    def test_aplicar_dos_veces_da_el_mismo_resultado(self):
        original = "**Hospital de Pinamar**\n* horario\n[Mis turnos](/mis-turnos)"
        una_vez = _markdown_a_whatsapp(original)
        dos_veces = _markdown_a_whatsapp(una_vez)
        self.assertEqual(una_vez, dos_veces)

    def test_enviar_texto_pasa_por_la_conversion_antes_de_mandar(self):
        # enviar_texto es el choke point: verificamos que lo que le llega al
        # transporte YA está convertido, no el Markdown crudo.
        capturado = {}

        def _fake_meta(destino, texto):
            capturado["texto"] = texto
            return True

        original_provider = otp_send.wa_provider
        original_meta = otp_send._enviar_meta
        try:
            otp_send.wa_provider = lambda: "meta"
            otp_send._enviar_meta = _fake_meta
            enviar_texto("5492255111222", "**Hospital de Pinamar** a las 08:00")
        finally:
            otp_send.wa_provider = original_provider
            otp_send._enviar_meta = original_meta
        self.assertEqual(capturado["texto"], "*Hospital de Pinamar* a las 08:00")


class TestInlineListsNormalization(unittest.TestCase):
    """INLINE catalog bullet lists the model crams onto a single line.

    The model often writes a whole service catalog inline, each item a bullet
    asterisk with bold stuck to it:
      "...agosto: * **Cardiología** en el Hospital * **Oftalmología** ..."
    Without the inline bullet+bold step, bold resolves first and leaves a
    bullet asterisk stuck to the already-converted bold ("* *Cardiología*"),
    which WhatsApp renders as a literal star, and the items stay crammed on
    one line. Recovered from 7d924a16.

    Recognition is STRUCTURAL (Codex review): a line is rebuilt as a list only
    with TWO OR MORE bullet items. A single inline bullet is ambiguous -- it
    is arithmetic (`2 * **3**`) or a prose em-dash (`08:00 - *nota*`), not a
    list -- so it is left alone. The negative cases below pin that boundary.
    """

    # --- Positive: genuine multi-item inline catalogs are rebuilt. ---

    def test_varias_vinetas_negrita_inline_en_el_mismo_renglon(self):
        # The real case: the whole catalog, item after item, in one paragraph.
        self.assertEqual(
            _markdown_a_whatsapp(
                "Turnos en agosto: * **Cardiología** en el Hospital "
                "* **Oftalmología** en CAPS"),
            "Turnos en agosto:\n- *Cardiología* en el Hospital\n- *Oftalmología* en CAPS")

    def test_residuo_vineta_negrita_ya_en_formato_whatsapp(self):
        # Same shape but already single-asterisk (`* *X*`), e.g. after a
        # partial upstream conversion — normalized too.
        self.assertEqual(
            _markdown_a_whatsapp("Opciones: * *Cardiología* * *Oftalmología*"),
            "Opciones:\n- *Cardiología*\n- *Oftalmología*")

    def test_items_con_guion_en_el_mismo_renglon_se_parten(self):
        # Items that already use a dash (`- *X*`) but are crammed together:
        # split one per line so WhatsApp renders the bullets.
        self.assertEqual(
            _markdown_a_whatsapp("Hoy: - *Cardiología* - *Oftalmología*"),
            "Hoy:\n- *Cardiología*\n- *Oftalmología*")

    def test_no_deja_asterisco_de_vineta_pegado_a_la_negrita(self):
        # The exact symptom of the bug: no residual inline `* *X*` may remain.
        convertido = _markdown_a_whatsapp(
            "Catálogo: * **Cardiología** * **Oftalmología**")
        self.assertNotIn("* *Cardiología*", convertido)
        self.assertNotIn("* *Oftalmología*", convertido)
        self.assertEqual(convertido, "Catálogo:\n- *Cardiología*\n- *Oftalmología*")

    # --- Negative: a single inline bullet is NOT a list (Codex review). ---

    def test_una_sola_vineta_negrita_inline_no_se_convierte_en_lista(self):
        # One bullet+bold inline is ambiguous; it stays inline (normal bold
        # conversion only) and is never split onto its own dash line. Accepted
        # trade-off for not mis-reading arithmetic/prose as a list.
        out = _markdown_a_whatsapp("Turnos en agosto: * **Cardiología** hoy")
        self.assertNotIn("\n- ", out)
        self.assertIn("*Cardiología*", out)

    def test_aritmetica_no_se_convierte_en_lista(self):
        # `2 * **3** = 6`: the `*` is a multiply operator, `**3**` a bold
        # number — not a bullet list. Only the bold resolves; no dash, no
        # newline.
        self.assertEqual(
            _markdown_a_whatsapp("Resultado: 2 * **3** = 6"),
            "Resultado: 2 * *3* = 6")

    def test_prosa_con_guion_y_enfasis_no_se_parte(self):
        # `08:00 - *sujeto a disponibilidad*`: an em-dash before an
        # emphasized note, not a list. Must stay on one line untouched.
        original = "Horario 08:00 - *sujeto a disponibilidad*"
        self.assertEqual(_markdown_a_whatsapp(original), original)

    def test_guion_unico_en_prosa_no_se_parte(self):
        # A single dash bullet inside prose is also not a list.
        out = _markdown_a_whatsapp("Hoy atendemos - *Cardiología* sobre todo")
        self.assertNotIn("\n- ", out)

    def test_lista_uno_por_renglon_no_se_toca_aqui(self):
        # A proper one-bullet-per-line Markdown list is handled by the
        # line-start bullet step, NOT by the inline repair: each line holds a
        # single item, so the inline guard (2+ per line) leaves it alone and
        # the start-of-line step does the conversion.
        self.assertEqual(
            _markdown_a_whatsapp("* **Cardiología**\n* **Oftalmología**"),
            "- *Cardiología*\n- *Oftalmología*")

    # --- Idempotency: running twice == once, for every shape. ---

    def test_listas_inline_son_idempotentes(self):
        for original in (
            "Turnos en agosto: * **Cardiología** en el Hospital "
            "* **Oftalmología** en CAPS",
            "Opciones: * *Cardiología* * *Oftalmología*",
            "Hoy: - *Cardiología* - *Oftalmología*",
        ):
            with self.subTest(original=original):
                una_vez = _markdown_a_whatsapp(original)
                self.assertEqual(_markdown_a_whatsapp(una_vez), una_vez)

    def test_casos_negativos_son_idempotentes(self):
        # The rejected shapes must also be stable across two passes — a second
        # pass must not somehow turn them into a list.
        for original in (
            "Resultado: 2 * **3** = 6",
            "Horario 08:00 - *sujeto a disponibilidad*",
            "Turnos en agosto: * **Cardiología** hoy",
        ):
            with self.subTest(original=original):
                una_vez = _markdown_a_whatsapp(original)
                self.assertEqual(_markdown_a_whatsapp(una_vez), una_vez)


if __name__ == "__main__":
    unittest.main()
