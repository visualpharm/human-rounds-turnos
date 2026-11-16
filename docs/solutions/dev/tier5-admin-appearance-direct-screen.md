---
title: Tier 5 direct appearance screen — platform-core twin of #configuracion with per-block module degradation
scope: repo
area: dev
tags: [tier5, admin, appearance, theme, routing, modules, degradation, i18n]
created: 2026-08-21
refs: [tier5-admin-legal-direct-screen.md, tier5-admin-account-direct-screen.md, tier5-admin-content-direct-screens.md, marca-por-tema-copias-a-mano.md]
status: active
---

## Problem

After `/admin/legal`, three admin destinations were hash-only (`configuracion`,
`hsi`, `plan`). `configuracion` ("Apariencia y acceso") was the one that is
generic, admin-only and already API-native, but it spans three owners: the
theme, sign-in-mode and anti-bot blocks read turnos-routed APIs, the chat
dictation block reads a chat-routed API, and the colour/logo block reads the
platform branding API. The section itself is platform-core, so a direct
screen must answer 200 while a module-owned block's API answers 404 — and
the legacy controller drew EMPTY grids when a GET returned null, always drew
the voice block (misleading "key not configured" when chat is off), blanked
the whole section when the theme fetch rejected, and swallowed 401/403.

## Solution

`/admin/appearance` (`admin_panel/admin_appearance_screen.py`,
`templates/admin/appearance.html`) mounts in `routers/platform.py` right after
the legal screen and registers in
`core.app_modules._PLATFORM_ADMIN_SCREEN_PATHS["configuracion"]`, which flips
the shared sidebar href on every screen. The path is `/admin/appearance`, not
`/admin/configuration`: `/admin/settings` already exists and two synonymous
paths mislead; the visible label is "Appearance and access". The section id,
registry key, `SANA_ADMIN_SCREEN` value, `#sec-configuracion` and the hash
`#configuracion` all stay `configuracion`.

Zero new API contracts and no re-homing: theme/auth-mode/turnstile stay
turnos-routed, chat-voice stays chat-routed, branding stays platform. The
screen gate is the data APIs' own `solo_admin=True` copied verbatim, proven by
status-equality per role against BOTH `GET /api/admin/branding` (platform,
never module-gated, `permitir_marca` inert) and `GET /api/admin/theme` with
turnos enabled. No module gate; the Python contract pins the premise (turnos
and chat off → theme and chat-voice 404, branding 200, screen 200). Not an
expired-2FA repair surface. The render calls none of the five handlers.

Module degradation is a CLIENT contract inside the existing `admin-theme.js`,
applied in BOTH shells: `fetchOutcome(path)` turns each GET into
`{ok, status, json}` (a rejected fetch or an unparsable body is a failure of
THAT block, never a rejected `Promise.all`); a block renders iff its GET was
2xx with a payload (`BLOCKS`); 404 removes the block silently (feature not
installed); any 401/403 is the session boundary (direct: `#configuracion-status`
shows `admin.system.common.session_expired` + a link to `/admin`, controls not
rendered; legacy: `window.showGate()`); any other failure drops the block and
shows ONE `admin.system.theme.load_error` line (direct: the status element;
legacy: a `.cfg-msg.err.cfg-load-msg` first line). Surviving blocks keep their
order (theme, sign-in, anti-bot, voice, brand); zero blocks is never blank.

Direct-mode specifics: `directScreen()` keys on the section id; `route()`
skips the `#app` gate, the hash parse, the sibling hide and the `#adm-h1`
write (the template owns the title) and canonicalises any query or hash with
`replaceState` — no pushState/popstate, there is nothing to navigate. After a
SUCCESSFUL theme POST the direct shell does `location.reload()`: base.html's
brand text, fonts and favicon are server-injected and there is no
`sanaSetBrand`, so a reload is the only path that does not mint a third
hand-copied brand map (see `marca-por-tema-copias-a-mano.md`); the legacy
shell keeps live-apply byte-identical, and colour/logo live-apply in both.
The legacy `route()` branch, the hand-written `showOnly()` list and the dead
`soloMarca()` landing (retired role) are untouched.

Behaviour-neutral a11y/mobile additions in both shells: option cards carry
`aria-pressed`, every `.cfg-msg` is a polite status region, `.cfg-model` is
16px/44px (no iOS focus zoom), `.cfg-mk-color`/`.cfg-mk-hex` are 44px tall,
`.cfg-mk-acts .btn` and the two reset text links (`#cfg-mk-reset`,
`#cfg-mk-quitar`, still `<a>`, never `.btn`) give ≥44px targets.

Truth updates that travel with the route: `test/test_admin_screen_nav.py`
(consumer, SCREENS row, six golden rows `/admin#configuracion` →
`/admin/appearance`), `test/test_legacy_fallback_census.py` (composition
283→284, native 570→571, unique 387→388), `test/tier4_composition_inventory.json`
regenerated with `python3 test/test_app_composition.py --write-fixture`, the
two new catalog keys (`admin.system.theme.sign_in`, `admin.system.theme.load_error`)
in es and en, and the nineteen→twenty / three→two hash-only wording in
`CLAUDE.md`, `docs/modules.md`, the readiness plan and `admin-panel-y-auth.md`.

## How to apply

- A platform-core screen whose blocks read module-owned APIs does NOT move
  the APIs: keep ownership, copy the gate, and make the shared controller
  degrade per block from each GET's own outcome — in every shell that loads
  it, not only the new one.
- Never gate on a null payload with a bare `Promise.all`: one rejected fetch
  blanks the page. Per-request `{ok, status, json}`, then render from flags.
- Distinguish 404 (feature off → absent, silent), 401/403 (session boundary →
  one local message + sign-in link), and everything else (one reload line).
- A direct shell with server-injected brand chrome reloads after a theme
  change instead of growing a client brand map.
- Name the path after the visible label in English when the section id is a
  Spanish synonym of an existing path (`configuracion` vs `/admin/settings`).
