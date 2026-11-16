"""Voice is AI too: no unguarded Fal egress when inference is disabled."""
import unittest
from unittest.mock import patch

from app.modules.chat import voice
from app.core import ai_register, config


class TestVoiceGovernance(unittest.TestCase):
    def test_global_kill_switch_stops_upload_and_queue_transport(self):
        with patch.object(config, 'get', side_effect=lambda key, default=None:
                          '1' if key == 'TURNOS_AI_DISABLED' else default), \
             patch('urllib.request.urlopen') as network:
            for call in (
                lambda: voice._fal_upload_audio(b'synthetic audio', 'audio/webm', 'synthetic-key'),
                lambda: voice._json_request('https://queue.fal.run/synthetic', 'synthetic-key'),
            ):
                with self.assertRaises(ValueError):
                    call()
        network.assert_not_called()

    def test_fake_mode_never_sends_audio_to_a_provider(self):
        import base64
        with patch.object(config, 'get', side_effect=lambda key, default=None:
                          '1' if key == 'TURNOS_FAKE_AI' else default), \
             patch.object(voice, '_fal_transcribe') as model:
            status, body = voice.transcribe_result({'audio': {
                'mime':'audio/webm','data_b64':base64.b64encode(b'synthetic audio').decode()}})
        model.assert_not_called()
        self.assertEqual(status, 200)

