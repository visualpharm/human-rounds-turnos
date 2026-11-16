#!/bin/bash
# Nightly chat learner (responsable del producto, 2026-07-23): un modelo pesado lee las
# conversaciones del asistente de las últimas 24 h y MEJORA los prompts.
# Corre como LaunchAgent com.sana-turnos.chat-learner (03:37 ART) en la máquina de desarrollo.
# Transcripts: GET /api/chat-transcripts (Bearer TURNOS_CHAT_EXPORT_TOKEN,
# ver charla.py). El único artefacto editable por defecto es el prompt
# (chat_agente.py) + copy del widget; los hallazgos quedan en
# docs/chat-learnings.md. Nunca manda mensajes salientes.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE="${CLAUDE_BIN:-$HOME/.nvm/versions/node/v20.19.2/bin/claude}"
LOG="$HOME/Library/Logs/sana-turnos-chat-learner.log"
cd "$REPO" || exit 1

PROMPT=$(cat <<'EOF'
Nightly chat-learning pass for Human Rounds — Turnos (the booking assistant — read docs/architecture/chat-y-whatsapp.md first).

Goal: make tomorrow's chats better than today's by learning from real conversations.

1. Fetch the last 24h of conversations: GET https://pinamar.humanrounds.org/api/chat-transcripts?hours=24 with header "Authorization: Bearer <TURNOS_CHAT_EXPORT_TOKEN from .env>". If the site's pre-launch Basic Auth blocks it, retry with -u using TURNOS_BASIC_AUTH_USER/PASS from Coolify (memory: reference_coolify_runtime_envs). No conversations → log "sin conversaciones" and stop, zero edits.
2. Study them like a conversation designer: where did the assistant counter-question instead of answering, invent or omit information, misformat, loop, fail a tool call, or lose the person? Where did the person give up?
3. Improve the assistant based on what you found. The default lever is the system prompt in chat_agente.py (SYSTEM_PROMPT and friends). Widget copy in chat-widget.js only for clear copy-level fixes (it is content-hash fingerprinted — nothing to bump if touched). Do NOT restructure code, do NOT touch anything outside the chat surfaces, do NOT change tool behavior — leave a note instead if a code fix seems needed.
4. Append a dated entry to docs/chat-learnings.md: what the chats showed, what you changed and why, what you deliberately left alone.
5. Verify: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py" AND node --test tests/*.test.mjs. Green → commit (Spanish, repo style) and push to main (auto-deploys). Not green → revert your edits, log the failure, push nothing.
6. Never send any outbound message to anyone. If the working tree has unrelated uncommitted changes, leave them untouched and commit only your files.
EOF
)

{
  echo "=== $(date -Iseconds) chat-learner start"
  "$CLAUDE" -p "$PROMPT" --model claude-fable-5 --permission-mode bypassPermissions --max-turns 80
  echo "=== $(date -Iseconds) chat-learner end (exit $?)"
} >>"$LOG" 2>&1
