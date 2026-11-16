# Voice dictation must reuse Lira's complete audio pipeline

## Problem

The chat received a real dictation but Wizper returned only “Gracias.” Two
differences from Lira made the result unreliable: Sana did not explicitly
control Wizper's language and called an unpinned version. An omitted
`language` field defaults to English, while auto-detection can misclassify a
short or noisy dictation and return fluent-looking nonsense. Sana also sent
the raw MediaRecorder container, including silence.

The original browser blob could not be inspected after the fact. The widget
keeps it only in memory, the server never persists it, and Fal receives an
ephemeral one-hour upload. That privacy boundary is intentional.

The composer also treated browser recording support as voice availability.
When `FAL_KEY` was absent, it let the patient record first and only then
returned `503 voz_no_configurada`.

## Pattern

- Use `fal-ai/wizper` v3 with `task: "transcribe"` and explicit
  `language: "es"` in Pinamar's Spanish public composer. `transcribe`
  preserves the spoken words; the known language prevents a short Argentine
  dictation from depending on Whisper's language classifier.
- Accept only the Wizper model in the admin setting unless another provider gets
  its own request/response adapter.
- Before upload, apply Lira's `silenceremove` filter at `-45 dB`, adapted from
  its 300 ms long-recording onset to 50 ms so one-sentence dictation keeps its
  first word, then encode the remaining speech as 24 kbps Opus/WebM.
- Resolve Homebrew ffmpeg/ffprobe explicitly on macOS: LaunchAgents inherit
  `/usr/bin:/bin:/usr/sbin:/sbin`, not the interactive shell's PATH.
- Reject a filtered clip shorter than 250 ms, but fall back to the original
  recording if ffmpeg itself is missing or cannot decode a browser container.
- Keep preprocessing files inside a request-scoped temporary directory.
- Stop immediately when Fal reports a failed or cancelled queue job.
- Treat the Permissions API as a recovery hint, not an initial visibility
  gate: Chrome can report `denied` when the OS, browser automation, or another
  layer blocked capture even though the patient never explicitly prohibited
  this site. If an actual `getUserMedia` call returns `NotAllowedError`,
  `SecurityError`, or `NotFoundError`, remove the microphone control instead of
  filling the composer with an error. Reveal it again when a permission-change
  event moves away from `denied`.
- Start the microphone hidden. `GET /api/chat/voice` exposes only
  `{disponible: bool}`; reveal the control after that preflight succeeds. A
  failed preflight stays hidden.
- If configuration disappears between preflight and upload, hide the control
  on `voz_no_configurada` without showing a post-dictation configuration error.
- Keep the microphone button as the edit-first path: tapping it again stops and
  fills the composer. If the person taps Send while recording, stop, transcribe
  and send the resulting text in one continuous action.

## Verification

A synthetic three-second clip (one second silence, one second signal, one
second silence) became a 1.71-second Opus file and shrank from 288,078 bytes to
7,084 bytes. Live endpoint probes with extra leading and trailing silence
preserved the Spanish samples. A real browser recording verified that tapping
Send during Spanish dictation produced “Quiero un oftalmólogo.” and sent it
automatically.
A three-second silent clip returned `422 sin_voz` in 0.1 seconds. Backend,
widget, syntax, and divider-contract tests passed. With Fal intentionally
unconfigured, the real 792×915 composer rendered no microphone and no error.
After pinning Spanish, the exact regression phrase “Me caí de la moto. Por eso
me duele el pecho.” survived clean, silence-padded, quiet, and pink-noise
samples verbatim through the filtered live-provider path.
