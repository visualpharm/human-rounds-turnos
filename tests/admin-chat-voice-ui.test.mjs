// Source guards for the chat-dictation (Fal) block semantics, now owned by
// the native controller static/platform/admin-appearance.mjs. Source shape
// only — the locked display and the block's visibility lifecycle are pinned
// at runtime by tests/admin-appearance-lifecycle.test.mjs.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(
  new URL("../static/platform/admin-appearance.mjs", import.meta.url), "utf8");

test("el dictado muestra su estado, sin la clave del servicio ni formulario que no pueda guardar", () => {
  assert.match(src, /Dictado del chat/);
  assert.match(src, /\/api\/admin\/chat-voice/);
  assert.match(src, /id="cfg-voice-model"/);
  // The block says the feature is on (critical review 08.11, F-Apariencia):
  // «Modelo de Fal fal-ai/wizper» was a service key, not a word for the desk.
  assert.match(src, /id="cfg-voice-model">' \+ esc\(T\("admin\.system\.theme\.shared\.activo"/);
  assert.doesNotMatch(src, /voiceModel|admin\.system\.theme\.voice\.model_label/);
  // The model is named once (critical review 08.11, D7): no second
  // «Fijado por el servidor: fal-ai/wizper.» line under it.
  assert.doesNotMatch(src, /admin\.system\.theme\.voice\.model_locked/);
  // Without a Fal key the block is one line, «Sin configurar» with a link to
  // Ajustes › Integraciones (critical review 08.11, D8), not a note under a
  // dead model field.
  assert.match(src, /if \(!voiceConfigured\) return offHtml\("voice"/);
  assert.doesNotMatch(src, /admin\.system\.theme\.voice\.not_configured/);
  // La política del servidor (core/ai_policy.py) solo acepta fal-ai/wizper y
  // descarta cualquier otro valor guardado: un input + «Guardar modelo» era
  // un formulario que nunca podía guardar nada distinto.
  assert.doesNotMatch(src, /cfg-voice-save/);
  assert.doesNotMatch(src, /post\("\/api\/admin\/chat-voice"/);
  assert.doesNotMatch(src, /id="cfg-(?:fal-)?key"/i);
  assert.doesNotMatch(src, /type="password"[^>]*(?:fal|voice)/i);
});
