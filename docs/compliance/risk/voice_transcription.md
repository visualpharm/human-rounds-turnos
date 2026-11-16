# voice_transcription — provisional AI risk

Purpose: dictation for booking chat and pre-consult interview. Self-assessed
level: **alto**, because speech may contain health content, identity and voice
biometric characteristics. Hospital/municipal acceptance: **not supplied**.

Provider/model resolve through core/ai_policy.py from the existing voice config
and chat_voice overlay. Queue and upload destinations are the same constants
used by the actual transport. No provider probe is performed. First deployment
and exact historical substantial change dates are unverified.

## Data and controls

Complete audio is uploaded to Fal storage and sent for transcription. It cannot
be text-scrubbed before recognition. The app does not retain a voice archive;
temporary processing files are removed. Requested provider retention is not
proof of deletion. Later chat text passes through ordinary pii.py handling.

The shared composer shows ai.notice.voice on the first microphone press, which
does not record; the next press records. The microphone
is absent when the server capability is unavailable. TURNOS_AI_DISABLED=1 and
TURNOS_VOICE_ENABLED=0 stop upload and queue transport. Fake mode returns a
synthetic transcript without provider traffic; it does not offer real dictation.

The patient dictates and submits, can review/edit the text, and may explicitly
press Send while recording to request sending after transcription. Transcription
never confirms/rejects/cancels/reassigns appointments or writes an HSI evolution.
Model-setting changes have the same actor/hash/date audit boundary as other AI.

## Assessment to complete (owner: municipal authority + hospital)

- Rights: assess transcription errors and loss of accessibility when disabled.
- Service quality: validate Argentine Spanish/noise/accent errors with synthetic cases.
- Data provenance: speech is user-supplied; confirm consent/processing grounds.
- Bias: evaluate language, disability, speech/accent and microphone quality.
- Labour: determine training, review and escalation responsibility.
- Human oversight: validate the submit-during-recording and review workflow.
- Environment: obtain provider hosting/energy and retention/jurisdiction evidence.

No official Resolution 9/25 classification is asserted; its detailed criteria
were not obtained. [Normative map](../ia-pba-2026.md).
