import base64
import io
import json
import math
import os
import shutil
import struct
import unittest
import wave
from unittest.mock import MagicMock, patch

from app.modules.chat import voice


class TestAudio(unittest.TestCase):
    def setUp(self):
        voice._HITS.clear()
        fake = patch.object(voice, "_fake", return_value=False)
        fake.start()
        self.addCleanup(fake.stop)

    @staticmethod
    def _wav_bytes(speech=True):
        rate = 16000
        samples = []
        for index in range(rate * 3):
            active = speech and rate <= index < rate * 2
            value = int(9000 * math.sin(2 * math.pi * 440 * index / rate)) if active else 0
            samples.append(struct.pack("<h", value))
        output = io.BytesIO()
        with wave.open(output, "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(rate)
            file.writeframes(b"".join(samples))
        return output.getvalue()

    @staticmethod
    def _ffmpeg_disponible():
        return bool(
            shutil.which("ffmpeg") or
            os.path.isfile("/opt/homebrew/bin/ffmpeg")
        )

    def test_audio_valido_se_decodifica_sin_guardarlo(self):
        raw = base64.b64encode(b"audio").decode()
        data, mime = voice._audio({"audio": {
            "mime": "audio/webm;codecs=opus",
            "data_b64": f"data:audio/webm;base64,{raw}",
        }})
        self.assertEqual(data, b"audio")
        self.assertEqual(mime, "audio/webm")

    def test_transcripcion_usa_fal_en_servidor_y_devuelve_texto(self):
        raw = base64.b64encode(b"audio").decode()
        body = {"audio": {"mime": "audio/webm", "data_b64": raw}}
        with patch.object(voice, "_env", return_value="fal-secret"), \
             patch.object(voice, "current_model", return_value="fal-ai/wizper"), \
             patch.object(voice, "_prepare_audio",
                          return_value=(b"filtered", "audio/webm")), \
             patch.object(voice, "_fal_transcribe",
                          return_value="Necesito un turno con cardiología") as call:
            status, payload = voice.transcribe_result(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["texto"],
                         "Necesito un turno con cardiología")
        self.assertEqual(
            call.call_args.args,
            (b"filtered", "audio/webm", "fal-ai/wizper", "fal-secret"),
        )

    def test_audio_sin_voz_no_llega_al_modelo(self):
        raw = base64.b64encode(b"audio").decode()
        body = {"audio": {"mime": "audio/webm", "data_b64": raw}}
        with patch.object(voice, "_prepare_audio",
                          side_effect=voice.NoSpeechError("audio sin voz")), \
             patch.object(voice, "_fal_transcribe") as transcribir:
            status, payload = voice.transcribe_result(body)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"], "sin_voz")
        transcribir.assert_not_called()

    def test_preparacion_falla_abierto_si_ffmpeg_no_esta_disponible(self):
        with patch.object(voice, "_probe_duration",
                          side_effect=FileNotFoundError):
            data, mime = voice._prepare_audio(b"audio", "audio/webm")
        self.assertEqual(data, b"audio")
        self.assertEqual(mime, "audio/webm")

    def test_preparacion_real_recorta_y_comprime_voz_con_silencio(self):
        if not self._ffmpeg_disponible():
            self.skipTest("ffmpeg no disponible")
        raw = self._wav_bytes(speech=True)
        filtered, mime = voice._prepare_audio(raw, "audio/wav")
        self.assertEqual(mime, "audio/webm")
        self.assertLess(len(filtered), len(raw) // 3)

    def test_preparacion_real_rechaza_silencio(self):
        if not self._ffmpeg_disponible():
            self.skipTest("ffmpeg no disponible")
        with self.assertRaises(voice.NoSpeechError):
            voice._prepare_audio(self._wav_bytes(speech=False), "audio/wav")

    def test_duracion_webm_sin_metadata_cae_a_decodificacion_completa(self):
        first = MagicMock(returncode=0, stdout="N/A\n", stderr="")
        second = MagicMock(returncode=0, stdout="", stderr="")
        third = MagicMock(
            returncode=0, stdout="",
            stderr="size=N/A time=00:00:02.75 bitrate=N/A")
        with patch.object(voice, "_run_av",
                          side_effect=[first, second, third]) as run:
            self.assertEqual(voice._probe_duration("/tmp/audio.webm"), 2.75)
        self.assertEqual(run.call_count, 3)

    def test_fal_subir_audio_usa_cdn_y_expiracion_corta(self):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "upload_url": "https://v3b.fal.media/files/upload",
            "file_url": "https://v3b.fal.media/files/audio.wav",
        }).encode()
        response.__enter__.return_value = response
        upload_response = MagicMock()
        upload_response.__enter__.return_value = upload_response
        with patch("urllib.request.urlopen",
                   side_effect=[response, upload_response]) as urlopen:
            out = voice._fal_upload_audio(b"audio", "audio/wav", "fal-secret")
        self.assertEqual(out, "https://v3b.fal.media/files/audio.wav")
        initiate = urlopen.call_args_list[0].args[0]
        upload = urlopen.call_args_list[1].args[0]
        self.assertIn("expiration_duration_seconds",
                      initiate.headers["X-fal-object-lifecycle-preference"])
        self.assertEqual(upload.data, b"audio")

    def test_wizper_usa_la_version_actual_y_fija_espanol(self):
        with patch.object(voice, "_fal_upload_audio",
                          return_value="https://fal.test/audio.webm"), \
             patch.object(voice, "_json_request", side_effect=[
                 {"request_id": "req-1"},
                 {"status": "COMPLETED"},
                 {"text": "Necesito cardiología"},
             ]) as request:
            text = voice._fal_transcribe(
                b"audio", "audio/webm", "fal-ai/wizper", "fal-secret")
        self.assertEqual(text, "Necesito cardiología")
        payload = request.call_args_list[0].kwargs["payload"]
        self.assertEqual(payload["version"], "3")
        self.assertEqual(payload["chunk_level"], "segment")
        self.assertEqual(payload["task"], "transcribe")
        self.assertEqual(payload["language"], "es")

    def test_fal_falla_sin_esperar_el_timeout_completo(self):
        with patch.object(voice, "_fal_upload_audio",
                          return_value="https://fal.test/audio.webm"), \
             patch.object(voice, "_json_request", side_effect=[
                 {"request_id": "req-1"},
                 {"status": "FAILED"},
             ]), \
             patch.object(voice.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError):
                voice._fal_transcribe(
                    b"audio", "audio/webm", "fal-ai/wizper", "fal-secret")
        sleep.assert_not_called()

    def test_sin_fal_key_falla_sin_exponer_configuracion(self):
        raw = base64.b64encode(b"audio").decode()
        body = {"audio": {"mime": "audio/webm", "data_b64": raw}}
        with patch.object(voice, "_env", return_value=""):
            status, payload = voice.transcribe_result(body)
        self.assertEqual(status, 503)
        self.assertNotIn("key", json.dumps(payload).lower())


class TestConfiguracion(unittest.TestCase):
    def setUp(self):
        fake = patch.object(voice, "_fake", return_value=False)
        fake.start()
        self.addCleanup(fake.stop)
    def test_estado_publico_indica_si_el_dictado_esta_disponible(self):
        with patch.object(voice, "_env", return_value="fal-secret"):
            payload = voice.public_status_payload()
        self.assertEqual(payload, {"disponible": True})

        with patch.object(voice, "_env", return_value=""):
            payload = voice.public_status_payload()
        self.assertEqual(payload, {"disponible": False})
        self.assertNotIn("key", json.dumps(payload).lower())

    def test_modelo_restringido_a_fal(self):
        self.assertEqual(voice._valid_model("fal-ai/wizper"), "fal-ai/wizper")
        self.assertEqual(voice._valid_model("https://otro.test/modelo"), "")
        self.assertEqual(voice._valid_model("fal-ai/otro-modelo"), "")

    def test_admin_muestra_estado_no_la_clave(self):
        with patch.object(voice, "_env", return_value="fal-secret"), \
             patch.object(voice, "current_model", return_value="fal-ai/wizper"):
            payload = voice.admin_status_payload()
        self.assertEqual(payload["model"], "fal-ai/wizper")
        self.assertTrue(payload["fal_configurado"])
        self.assertNotIn("fal-secret", json.dumps(payload))

    def test_admin_status_follows_whether_dictation_works(self):
        # The panel's dictation block shows exactly when the patient's
        # microphone works: a Fal key, or the simulated AI (2026-11-08 D8).
        for fake, key, expected in ((False, "", False), (True, "", True), (False, "k", True)):
            with patch.object(voice, "_fake", return_value=fake), \
                 patch.object(voice, "_env", return_value=key), \
                 patch.object(voice, "current_model", return_value="fal-ai/wizper"):
                self.assertIs(voice.admin_status_payload()["fal_configurado"], expected)


if __name__ == "__main__":
    unittest.main()
