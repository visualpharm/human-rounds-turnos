"""Voice input for the public chat and its admin-owned model setting.

Routes:
  POST /api/chat/transcribe   {audio:{mime,data_b64}} -> {texto}
  GET  /api/chat/voice        -> public capability status
  GET  /api/admin/chat-voice  -> model/configuration status
  POST /api/admin/chat-voice  {model} -> saved model

The browser never receives FAL_KEY. Audio is bounded, transcribed through
fal.ai's queue API, and not written to disk. The selected model is stored in
the existing catalogo config overlay; TURNOS_VOICE_MODEL supplies the default.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from app.core import ai_policy, config, i18n
from app.core import rate_window

DEFAULT_MODEL = ai_policy.DEFAULT_VOICE_MODEL
MAX_AUDIO_BYTES = 6 * 1024 * 1024
MAX_TEXT_CHARS = 4000
MIN_SPEECH_SECONDS = 0.25
_MODEL_RE = re.compile(r"^fal-ai/[A-Za-z0-9._/-]{1,90}$")
_MIME_OK = {
    "audio/webm", "audio/mp4", "audio/mpeg", "audio/mp3",
    "audio/mpga", "audio/m4a", "audio/x-m4a", "audio/wav",
}
_HITS = {}
_HOUR = 3600


class NoSpeechError(ValueError):
    pass


def _env(key, default=""):
    """Use the canonical DB > environment > effective env-file precedence."""
    return config.get(key, default)


def _valid_model(value):
    value = str(value or "").strip()
    return value if _MODEL_RE.fullmatch(value) and value == DEFAULT_MODEL else ""


def current_model():
    return ai_policy.voice_model()


def _fake():
    return config.get('TURNOS_FAKE_AI') == '1'


def save_model(value):
    model = _valid_model(value)
    if not model:
        raise ValueError("modelo inválido")
    from app.core import db_catalog
    db_catalog.catalog_upsert("config", "chat_voice", {"model": model})
    return model


def _allowed(ip):
    return rate_window.allow(_HITS, ip, 20, _HOUR, prune_above=None)


def _json_request(url, key, method="GET", payload=None, timeout=20):
    if not ai_policy.voice_enabled() or _fake():
        raise ValueError('AI voice inference is disabled.')
    data = None
    headers = {"Authorization": f"Key {key}"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fal_upload_audio(audio_bytes, mime, key):
    """Upload input audio to Fal's CDN with a short retention window.

    Wizper rejects audio data URLs, so its documented file workflow is:
    initiate an upload, PUT the bytes, then send the returned URL to the model.
    """
    if not ai_policy.voice_enabled() or _fake():
        raise ValueError('AI voice inference is disabled.')
    ext = {
        "audio/webm": "webm",
        "audio/mp4": "m4a",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/mpga": "mp3",
        "audio/m4a": "m4a",
        "audio/x-m4a": "m4a",
        "audio/wav": "wav",
    }.get(mime, "audio")
    payload = json.dumps({
        "file_name": f"chat-voice.{ext}",
        "content_type": mime,
    }).encode("utf-8")
    initiate = urllib.request.Request(
        ai_policy.VOICE_UPLOAD_URL,
        data=payload,
        headers={
            "Authorization": f"Key {key}",
            "Content-Type": "application/json",
            "X-Fal-Object-Lifecycle-Preference":
                '{"expiration_duration_seconds":3600}',
        },
        method="POST",
    )
    with urllib.request.urlopen(initiate, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    upload_url = str(result.get("upload_url") or "").strip()
    file_url = str(result.get("file_url") or "").strip()
    if not upload_url or not file_url:
        raise RuntimeError("fal no devolvió una URL de carga")
    upload = urllib.request.Request(
        upload_url, data=audio_bytes,
        headers={"Content-Type": mime}, method="PUT")
    with urllib.request.urlopen(upload, timeout=30):
        pass
    return file_url


def _fal_transcribe(audio_bytes, mime, model, key):
    audio_url = _fal_upload_audio(audio_bytes, mime, key)
    endpoint = ai_policy.VOICE_QUEUE_URL + quote(model, safe="/")
    payload = {
        "audio_url": audio_url,
        "task": "transcribe",
    }
    if model == DEFAULT_MODEL:
        # This public composer is Spanish. Do not let a short, noisy Argentine
        # dictation depend on Whisper's language classifier: a wrong language
        # guess can turn valid speech into fluent-looking nonsense. `transcribe`
        # preserves the spoken words; `es` supplies the known language.
        payload.update({
            "version": "3",
            "chunk_level": "segment",
            "language": "es",
        })
    else:
        payload.update({"language": "es", "merge_chunks": True})
    queued = _json_request(
        endpoint, key, method="POST",
        payload=payload)
    request_id = str(queued.get("request_id") or "").strip()
    if not request_id:
        raise RuntimeError("fal no devolvió un identificador")
    status_url = str(queued.get("status_url") or
                     f"{endpoint}/requests/{quote(request_id)}/status")
    result_url = str(queued.get("response_url") or
                     f"{endpoint}/requests/{quote(request_id)}")

    deadline = time.time() + 45
    while time.time() < deadline:
        status = _json_request(status_url, key, timeout=12)
        state = str(status.get("status") or "").upper()
        if state == "COMPLETED":
            result = _json_request(result_url, key, timeout=20)
            return str(result.get("text") or "").strip()
        if state in {"FAILED", "CANCELED", "CANCELLED"}:
            raise RuntimeError("fal no pudo transcribir el audio")
        time.sleep(0.6)
    raise TimeoutError("la transcripción tardó demasiado")


def _run_av(command, args, timeout=35):
    executable = shutil.which(command)
    if not executable:
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = os.path.join(prefix, command)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                executable = candidate
                break
    return subprocess.run(
        [executable or command, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def _probe_duration(path):
    result = _run_av(
        "ffprobe",
        ["-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
    )
    if result.returncode == 0:
        try:
            duration = float(result.stdout.strip())
            if duration > 0:
                return duration
        except ValueError:
            pass

    # Chrome's streamed WebM sometimes omits the container duration. Its final
    # packet timestamp still gives us a reliable duration without decoding it.
    result = _run_av(
        "ffprobe",
        ["-v", "error", "-select_streams", "a:0",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
    )
    if result.returncode == 0:
        rows = [row.strip() for row in result.stdout.splitlines() if row.strip()]
        try:
            duration = float(rows[-1]) if rows else 0
            if duration > 0:
                return duration
        except ValueError:
            pass

    # Last resort from Lira: fully decode streamed WebM and read ffmpeg's final
    # timestamp. Some mobile recordings omit both container and packet timing.
    result = _run_av("ffmpeg", ["-i", path, "-f", "null", "-"])
    timestamps = re.findall(
        r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if timestamps:
        hours, minutes, seconds = timestamps[-1]
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if duration > 0:
            return duration
    raise RuntimeError("no se pudo medir el audio")


def _prepare_audio(audio_bytes, mime):
    """Port Lira's VAD: remove silence and re-encode speech as compact Opus.

    The temporary files live only for this request. If ffmpeg is unavailable or
    cannot understand a browser-specific container, keep dictation working by
    sending the original bytes instead of failing the request.
    """
    extension = {
        "audio/webm": "webm",
        "audio/mp4": "m4a",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/mpga": "mp3",
        "audio/m4a": "m4a",
        "audio/x-m4a": "m4a",
        "audio/wav": "wav",
    }.get(mime, "audio")
    try:
        with tempfile.TemporaryDirectory(prefix="sana-voz-") as folder:
            source = os.path.join(folder, f"entrada.{extension}")
            filtered = os.path.join(folder, "voz.webm")
            with open(source, "wb") as file:
                file.write(audio_bytes)
            _probe_duration(source)
            result = _run_av(
                "ffmpeg",
                [
                    "-y", "-i", source,
                    "-af",
                    # Lira uses 0.3 s for long recordings. Chat dictation is
                    # often one sentence, so 0.05 s avoids clipping its first
                    # word while still rejecting a silent microphone.
                    "silenceremove=start_periods=1:start_duration=0.05:"
                    "start_threshold=-45dB:stop_periods=-1:"
                    "stop_duration=1.5:stop_threshold=-45dB:detection=rms",
                    "-c:a", "libopus", "-b:a", "24k", filtered,
                ],
            )
            if result.returncode != 0 or not os.path.exists(filtered):
                return audio_bytes, mime
            if os.path.getsize(filtered) == 0:
                raise NoSpeechError("audio sin voz")
            try:
                filtered_duration = _probe_duration(filtered)
            except RuntimeError:
                if os.path.getsize(filtered) < 2000:
                    raise NoSpeechError("audio sin voz")
                return audio_bytes, mime
            if filtered_duration < MIN_SPEECH_SECONDS:
                raise NoSpeechError("audio sin voz")
            with open(filtered, "rb") as file:
                return file.read(), "audio/webm"
    except NoSpeechError:
        raise
    except (FileNotFoundError, OSError, RuntimeError,
            subprocess.SubprocessError):
        return audio_bytes, mime


def _audio(body):
    item = body.get("audio") if isinstance(body.get("audio"), dict) else body
    mime = str(item.get("mime") or "").split(";", 1)[0].strip().lower()
    raw = str(item.get("data_b64") or "")
    if "," in raw[:100] and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    if mime not in _MIME_OK:
        raise ValueError("formato de audio no compatible")
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("audio inválido") from exc
    if not data:
        raise ValueError("audio vacío")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("el audio es demasiado largo")
    return data, mime


def transcribe_result(body):
    """Pure tail of POST /api/chat/transcribe given an ALREADY-decoded
    request dict and an ALREADY-passed per-IP rate check (the caller's job
    — see the native router; calling `_allowed()` twice per
    request would double-count hits against the sliding window). Returns
    (status, payload)."""
    try:
        audio_bytes, mime = _audio(body)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception:  # noqa: BLE001
        return 400, {"error": "audio inválido"}

    if not ai_policy.voice_enabled():
        return 503, {'error': 'voz_desactivada', 'mensaje': i18n.t_or(
            'ai.voice.disabled', 'El dictado por voz está desactivado. Podés escribir el mensaje.', i18n.active())}
    if _fake():
        return 200, {'texto': i18n.t_or('ai.voice.fake', 'Dictado simulado.', i18n.active())}
    try:
        audio_bytes, mime = _prepare_audio(audio_bytes, mime)
    except NoSpeechError:
        return 422, {
            "error": "sin_voz",
            "mensaje": "No escuchamos una frase clara. Probá de nuevo o escribila.",
        }

    key = _env("FAL_KEY")
    if not key:
        return 503, {
            "error": "voz_no_configurada",
            "mensaje": "El dictado por voz todavía no está disponible.",
        }
    try:
        text = _fal_transcribe(
            audio_bytes, mime, current_model(), key)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return 502, {
            "error": "transcripcion",
            "mensaje": "No pudimos transcribir el audio. Podés escribir el mensaje.",
        }
    if not text:
        return 422, {
            "error": "sin_voz",
            "mensaje": "No escuchamos una frase clara. Probá de nuevo o escribila.",
        }
    return 200, {"texto": text[:MAX_TEXT_CHARS]}


def admin_status_payload():
    return {
        "model": current_model(),
        # Dictation works here: a Fal key, or the simulated AI (demo, e2e)
        # that answers «Dictado simulado» — the same rule as the patient's
        # microphone (public_status_payload).
        "fal_configurado": _fake() or bool(_env("FAL_KEY")),
        "default_model": DEFAULT_MODEL,
    }


def public_status_payload():
    return {"disponible": ai_policy.voice_enabled() and (_fake() or bool(_env("FAL_KEY")))}
