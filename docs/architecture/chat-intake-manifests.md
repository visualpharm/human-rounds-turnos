# Manifest notes of `chat` and `intake`

The `chat` and `intake` manifests used to carry long `*_note` prose fields (one per section below) next to the data they describe. Nothing
reads them (`app/core/app_modules.py` copies unknown fields through, `test_module_manifest` pins the structure, never the
prose), so the notes live here and the manifests hold only fields the loader reads (`test_module_manifest`
`TestManifestFieldsAreRead` fails on any other field). Same arrangement as [turnos-manifest.md](turnos-manifest.md).

## `chat`

### Status

Tier 2 batch F (2026-08-07, docs/roadmap/tier2-physical-modularization-plan.md): chat_agent, chat_api(+_router), chat_language, chat_tools, whatsapp_chat, voice(+_router), mcp_api — physically moved under modules/chat/ (`git mv` + every importer repo-wide rewritten in the same commit, mirroring the shape batches D/E already established for modules/turnos/ and ai/). `routes[].module` entries below are now dotted `modules.chat.*` paths; `after` anchors both TO and FROM this manifest's routes were updated to match — `app/modules/turnos/module.json`'s own `ai.prompts` route's DELETE `after` anchor now points at `modules.chat.mcp_api` (was bare `mcp_api`). `app_modules._base_app_routes()`/`merge_routes()` splice routes from EVERY manifest together before resolving `after` anchors (not per-manifest), so a chat route anchoring on a turnos/platform-core name — and vice versa — resolves exactly as it did when both lived in one flat root. Proxy-retirement B1-B3 now provide native WhatsApp, MCP, and real streaming-SSE transports while the existing handlers/business functions remain the sole policy owners. No chat route remains reverse-proxy-only; dark mode keeps the legacy handlers as rollback.

### Requires

chat_tools.py calls into `modules.turnos.booking_requests` (`_create`/`_cancelar`) to actually create/cancel a solicitud on the patient's behalf — there is no chat without a booking surface to act on, same real (not just declarative) dependency shape `app/modules/intake/module.json` already established for its own `requires: ["turnos"]`.

### Routes

Transcribed verbatim from modules/turnos/module.json's pre-batch-F entries (same `after` anchors, only the `module` names went from bare to `modules.chat.*` dotted) — this batch is a rename-not-reorder move, same shape batches C/D/E already established, so tests/test_route_order.py's golden GET/POST/DELETE lists needed the same four names renamed in place, not reordered.

### Admin sections

`chats` (2026-09-05): the read-only conversation list, admin-only (no `roles` key), chat-module-gated at router level. Lives in the sidebar's Chat group with the wiki and the AI instructions (core/screen_nav.py).

### DB tables

`chat_messages` stays platform-shared (modules/intake/'s interview transcripts share the same table, channel='interview' — see modules/intake/module.json's own db_tables_note and tests/test_module_manifest.py's PLATFORM_DB_TABLES). `chat_handoffs` (web->WhatsApp handoff codes) and `chat_summaries` (cross-session memory) are written/read exclusively by files that moved into this module (chat_api.py, whatsapp_chat.py, chat_agent.py) — moved out of modules/turnos/module.json's db_tables in the same commit. `chat_compactions` (open-session autocompaction, docs/architecture/chat-y-whatsapp.md) used to be created only by a numbered migration (migrations/004_chat_compactions.py) and was invisible to test_module_manifest.py's regex scan of db.py; since the legacy-runner retirement (2026-08-16) its DDL lives in `_SCHEMA_SQL`, so it is now classified here with its chat siblings.

## `intake`

### Status

Primer módulo empaquetado físicamente bajo modules/<id>/ (docs/modules.md pedía eso para todo módulo nuevo). Las rutas usan el nombre punteado del módulo Python (modules.intake.*) porque routing.py gatea por module.__name__. admin_sections se completa cuando cada pantalla queda cableada en admin.html (misma regla de sinceridad que el manifiesto de turnos: describe la realidad, no el plan). Tier 2 (2026-08-05): primer módulo con `entrypoint` real (modules/intake/app.py) — su startup(ctx) registra el hook `interview_reminder` en app_modules.hooks, la prueba mínima de la migración de un scatter site a hooks (ver docs/roadmap/module-contract-design.md).

### Requires

La entrevista previa cuelga conceptualmente de un booking_request de turnos (no existe una entrevista sin un turno) — declarado real (Codex monitor review, P0, 2026-08-05: dejarlo en `[]` era declarativo-only, nunca ejercido contra el par real). `manage.py mod disable turnos` ahora se RECHAZA mientras intake esté habilitado, con la razón legible (app_modules.disable()'s dependents check); tests/test_app_modules.py y tests/test_module_runtime.py se actualizaron para deshabilitar intake PRIMERO cuando la prueba necesita dejar sólo turnos. La máquina de dependencias sigue probada contra el fixture sintético (tests/test_module_runtime.py's TestDependencyRefusalWithReason) Y ahora también contra este par real (tests/test_module_lifecycle.py). `chat` agregado en Tier 2 batch F (2026-08-07): `interview_agent.py` importa `modules.chat.chat_language` (detección de idioma, utilidad pura reutilizada del chat de reserva) — dependencia física real desde que chat_language.py se movió a un módulo deshabilitable, no sólo un archivo de raíz siempre presente; mismo criterio 'declarado real, no sólo declarativo' que el edge con turnos de arriba.

### Routes

The interview, consultation, and intake-admin surfaces are mounted exclusively through their sibling FastAPI routers. `printable` remains a separate migration slice. Every `router_attr` entry is mounted only by app_modules.app_router_entries()/mount_module_routers() when app.py loads. No `after` anchor is needed: none of intake's paths overlap an already-mounted FastAPI router, so ordering has no observable effect.

### Admin sections

Every active admin destination is a server-routed screen; the hash shell and dynamic classic-script loader were retired on 2026-09-01. `entrevistas` is admin-only because it has no `roles` list, matching its settings API's `solo_admin` gate. Its `screen` object mounts `admin_entrevistas_screen.py` under the intake module gate, while its `js` field declares the canonical fingerprinted `admin-entrevistas.mjs` asset returned by the module API. The direct template imports that module and calls its named `boot()` export; Storybook calls its named `preview()` export. The old `admin-entrevistas.js` producer and its browser globals are retired. `consulta` remains label-less because it is reached from its owning queue and uses its own direct screen.

### Locales

Tier 2 pilot (2026-08-05): the pre-existing central locales/{en,es}/intake.json catalogs physically moved here — i18n.py now merges every discovered module's `locales/<lang>/*.json` into that language's catalog (docs/modules.md), so `intake.*` keys resolve exactly as before the move, plus the new `intake.nav.entrevistas` key that used to live in the platform's own locales/*/admin.shell.json (moved here because the sidebar label is now this module's data, not the shell's).

### DB tables

El transcript de la entrevista NO tiene tabla propia: reusa chat_messages (channel='interview', key='interview:<id>'), que con este módulo pasó a ser tabla compartida de plataforma (antes era de turnos).
