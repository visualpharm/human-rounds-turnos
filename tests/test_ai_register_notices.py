"""Server HTML and WhatsApp first-reply notice rendering; no sends."""
import unittest
from unittest.mock import patch

from app.core import ai_register, config, jinja_env
from app.modules.chat import whatsapp_chat
from app.modules.turnos import preparation


class TestPriorNotices(unittest.TestCase):
    def test_order_reader_discloses_ai_before_upload_host(self):
        with patch.object(config, '_db_overrides', return_value={}), \
             patch('app.ai.ai_health.available', return_value=True):
            html = preparation._upload_order_page({}).decode("utf-8")
        self.assertIn(ai_register.notice('ai.notice.order'), html)
        self.assertLess(html.index(ai_register.notice('ai.notice.order')), html.index('id="prep-host"'))
        # un solo aviso en el HTML del servidor, marcado para que la zona de subida no lo repita
        self.assertEqual(html.count(ai_register.notice('ai.notice.order')), 1)
        self.assertIn('data-ai-notice="order"', html)

    def test_each_staff_editor_renders_its_notice_before_controls(self):
        cases = [('knowledge', 'ai.notice.knowledge', 'sec-knowledge'),
                 ('settings', 'ai.notice.probe', 'sec-settings')]
        for screen, key, control in cases:
            with self.subTest(screen=screen), patch.object(config, '_db_overrides', return_value={}):
                html = jinja_env.render('admin/' + screen + '.html', nav_items=[], active_section=screen,
                                       tipo='servicios', tipo_url='services', initial_queue_html='',
                                       queue_data={'grupos':[], 'total':0},
                                       editor={'id':'services','title_key':'admin.shell.nav.servicios','title_es':'Servicios','doctors':False})
                text = ai_register.notice(key)
                self.assertIn(text, html)
                self.assertLess(html.index(text), html.index('id="' + control, html.index(text)))

    def test_day_queue_notice_rides_with_the_ai_content_not_over_the_screen(self):
        # The product owner, 2026-10-26: the notice sat as a banner over an empty day. The server
        # table of /admin/cola has no AI content; admin-queue.mjs (avisoIAHtml) puts the
        # notice right before the interview / order-reading content when the day has any.
        with patch.object(config, '_db_overrides', return_value={}):
            html = jinja_env.render('admin/cola.html', nav_items=[], active_section='cola',
                                   initial_queue_html='', queue_data={'grupos': [], 'total': 0})
        self.assertNotIn(ai_register.notice('ai.notice.clinical'), html)
        with open('static/platform/admin-queue.mjs', encoding='utf-8') as fh:
            js = fh.read()
        self.assertIn('T("ai.notice.clinical", null,', js)
        self.assertIn('entrevistaHeaderHtml() + entrevistaExplainerHtml() + avisoIAHtml() + entrevistasSinTurnoHtml()', js)

    def test_prompt_notice_rides_with_the_translated_edit_not_under_the_title(self):
        # The product owner, 2026-11-08: the notice sat as a subtitle under «Instrucciones
        # de la IA». admin-prompts.mjs now puts it inside the editor, before
        # the translated text (or the English one when the panel is English).
        with patch.object(config, '_db_overrides', return_value={}):
            html = jinja_env.render('admin/prompts.html', nav_items=[], active_section='prompts')
        self.assertNotIn(ai_register.notice('ai.notice.prompt'), html)
        with open('static/platform/admin-prompts.mjs', encoding='utf-8') as fh:
            js = fh.read()
        self.assertIn('data-ai-notice="prompt">\' + esc(T("ai.notice.prompt", null,', js)
        self.assertIn('\'<div data-panel="trad">\' + aviso + ', js)
        self.assertLess(js.index('+ aviso +'), js.index('id="pr-es-host"'))
        self.assertIn('(soloIngles ? aviso : "")', js)

    def test_requests_notice_rides_with_the_reading_not_over_the_list(self):
        # 2026-11-08 review A3 moved it off the search; 2026-11-09 off the list
        # too (Decreto 742/2026: the mark goes with the AI text itself). Only
        # admin-solicitudes.mjs puts it, under the reading of an open request.
        with patch.object(config, '_db_overrides', return_value={}):
            html = jinja_env.render('admin/solicitudes.html', nav_items=[], active_section='solicitudes',
                                   initial_queue_html='', queue_data={'grupos': [], 'total': 0})
        self.assertNotIn(ai_register.notice('ai.notice.clinical'), html)
        self.assertNotIn('data-ai-notice', html)
        with open('static/root/admin-app.js', encoding='utf-8') as fh:
            self.assertNotIn("querySelector('[data-ai-notice]')", fh.read())
        with open('static/platform/admin-solicitudes.mjs', encoding='utf-8') as fh:
            js = fh.read()
        self.assertIn('+ avisoIAHtml(d)', js)
        self.assertIn('if (!(d.veredicto || d.casoHtml || d.camposHtml)) return "";', js)
        self.assertIn('".sol-det .sol-det-ai{margin:8px 0 0;}"', js)

    def test_translation_notice_captions_save_not_the_screen(self):
        # 2026-11-08 review B5: no server band over the catalog editors. The
        # notice is the caption right above «Guardar» of an editable form
        # (saving translates the text, Decreto 742/2026); in Médicos only on
        # «Información básica»; never in a doctor's own session.
        for doctors in (False, True):
            with self.subTest(doctors=doctors), patch.object(config, '_db_overrides', return_value={}):
                html = jinja_env.render('admin/content.html', nav_items=[], active_section='medicos', rol='admin',
                                       editor={'id': 'medicos', 'title_key': 'admin.shell.nav.medicos',
                                               'title_es': 'Médicos', 'doctors': doctors})
                self.assertNotIn(ai_register.notice('ai.notice.translation'), html)
        with open('static/platform/admin-content.mjs', encoding='utf-8') as fh:
            js = fh.read()
        self.assertIn('html += translationNoticeHtml();\n        html += \'<div class="cnt-actions">\';', js)
        self.assertIn('if (window.CURRENT_ROLE === "medico") return "";', js)
        self.assertIn('if (aviso) aviso.hidden = name !== "info";', js)
        with open('static/root/admin-direct.css', encoding='utf-8') as fh:
            css = fh.read()
        self.assertIn('.cnt-ai-notice{margin:32px 0 0;}', css)
        self.assertIn('.cnt-ai-notice:not([hidden])+.cnt-actions{margin-top:12px;}', css)

    def test_whatsapp_first_reply_notice_even_when_account_is_unknown(self):
        with patch('app.core.db_chat.user_by_phone', return_value=None), \
             patch('app.core.db_chat.user_by_handoff_phone', return_value=None), \
             patch('app.core.db_chat.chat_history', return_value=[]), \
             patch('app.core.db_chat.chat_disclosure_sent', return_value=False), \
             patch('app.core.db_chat.chat_metadata_get', return_value=None), \
             patch('app.core.db_chat.chat_save_message') as store, \
             patch.object(whatsapp_chat, '_procesar_handoff', return_value=(None, None)), \
             patch('app.notifications.otp_send.enviar_texto') as send:
            whatsapp_chat._procesar_mensaje({'from': 'synthetic-sender', 'body': 'Synthetic input'})
        send.assert_called_once()
        self.assertTrue(send.call_args.args[1].startswith(ai_register.notice('ai.notice.chat')))
        self.assertEqual(store.call_args.args[2], {'role':'ai_disclosure','content':'AI disclosure delivered v1'})

    def test_whatsapp_delivers_notice_before_first_chat_or_document_reading(self):
        for media in (None, {'filename': 'synthetic-order.png', 'mime_type': 'image/png'}):
            events = []
            def sent(number, text):
                events.append(('notice', text))
                return True
            def read(*args):
                events.append(('read', None))
                return {}
            def chat(*args, **kwargs):
                events.append(('chat', None))
                return 'Synthetic reply'
            with self.subTest(media=bool(media)), \
                 patch('app.core.db_chat.user_by_phone', return_value={'dni': '99000001'}), \
                 patch('app.core.db_chat.chat_history', return_value=[]), \
             patch('app.core.db_chat.chat_disclosure_sent', return_value=False), \
             patch('app.core.db_chat.chat_metadata_get', return_value=None), \
                 patch('app.core.db_chat.chat_save_message'), \
                 patch.object(whatsapp_chat, '_procesar_handoff', return_value=(None, None)), \
                 patch.object(whatsapp_chat, '_descargar_media', return_value=(b'synthetic', 'image/png')), \
                 patch('app.modules.chat.chat_tools.analizar_orden_para', side_effect=read), \
                 patch('app.modules.chat.chat_tools.narrar_adjunto', return_value='Synthetic order'), \
                 patch('app.notifications.confirmations.respond', return_value=None), \
                 patch('app.modules.chat.chat_agent.chat', side_effect=chat), \
                 patch('app.notifications.otp_send.enviar_texto', side_effect=sent):
                whatsapp_chat._procesar_mensaje({'from': 'synthetic-sender', 'body': 'Synthetic input', 'media': media})
            self.assertEqual(events[0][0], 'notice')
            self.assertIn(ai_register.notice('ai.notice.chat'), events[0][1])
            if media:
                self.assertIn(ai_register.notice('ai.notice.order'), events[0][1])
                self.assertLess(events.index(events[0]), events.index(('read', None)))
            self.assertLess(events.index(events[0]), events.index(('chat', None)))

    def test_failed_whatsapp_disclosure_does_not_process_ai_or_claim_delivery(self):
        with patch('app.core.db_chat.user_by_phone', return_value={'dni': '99000001'}), \
             patch('app.core.db_chat.chat_history', return_value=[]), \
             patch('app.core.db_chat.chat_disclosure_sent', return_value=False), \
             patch('app.core.db_chat.chat_metadata_get', return_value=None), \
             patch('app.core.db_chat.chat_save_message') as store, \
             patch.object(whatsapp_chat, '_procesar_handoff', return_value=(None, None)), \
             patch('app.notifications.confirmations.respond', return_value=None), \
             patch('app.modules.chat.chat_agent.chat') as chat, \
             patch('app.notifications.otp_send.enviar_texto', return_value=False):
            whatsapp_chat._procesar_mensaje({'from': 'synthetic-sender', 'body': 'Synthetic input'})
        chat.assert_not_called()
        store.assert_not_called()
