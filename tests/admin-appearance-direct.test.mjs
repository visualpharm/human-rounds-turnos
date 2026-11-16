// Source guards for the direct /admin/appearance screen after the native
// cutover (2026-09-07): static/platform/admin-appearance.mjs is the only
// controller, the classic admin-theme.js is physically retired, and the
// template boots the native module through its fingerprinted static URL.
//
// Ownership map — the old admin-theme.js regexes were not dropped, they
// moved to stronger evidence: per-block degradation (404 silence, 401/403
// boundaries), the five-read order, saves/drafts/validation, logo safety
// and abort ownership are RUNTIME contracts pinned by executing the real
// module in tests/admin-appearance-lifecycle.test.mjs and
// tests/admin-appearance-input-abort.test.mjs; the template's own bootstrap
// lifetime (generation guard, deadline, pagehide/pageshow, recovery
// inertness) is pinned by executing the template's script itself in
// tests/admin-appearance-bootstrap-lifecycle.test.mjs. What these source
// assertions prove is shape and wiring — never runtime behaviour, and
// never rendered geometry: the CSS checks below pin the rules the module
// ships, not the pixels a browser paints from them.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const mjs = readFileSync(join(ROOT, "static", "platform", "admin-appearance.mjs"), "utf8");
const template = readFileSync(join(ROOT, "templates", "admin", "appearance.html"), "utf8");
const fingerprint = readFileSync(join(ROOT, "app", "core", "static_fingerprint.py"), "utf8");

test("the classic controller is physically retired and nothing loads it", () => {
  assert.equal(existsSync(join(ROOT, "admin-theme.js")), false,
    "admin-theme.js stays deleted, not dormant");
  for (const name of readdirSync(join(ROOT, "templates"), { recursive: true })) {
    if (!String(name).endsWith(".html")) continue;
    const text = readFileSync(join(ROOT, "templates", String(name)), "utf8");
    assert.ok(!text.includes("admin-theme"), `templates/${name} no longer references the retired controller`);
  }
  // The fingerprint registry serves the native module as an immutable
  // content-hash URL and keeps the classic name out of every served set.
  assert.match(fingerprint, /"platform\/admin-appearance\.mjs"/);
  assert.doesNotMatch(fingerprint, /admin-theme\.js/);
});

test("the native module imports inert: one export, a mount registry, read-only window access", () => {
  assert.match(mjs, /const mounts = new WeakMap\(\);/);
  assert.equal((mjs.match(/^export /gm) || []).length, 1, "mount is the module's only export");
  assert.match(mjs, /export function mount\(host, options\) \{\s*\n  if \(!host\) throw new Error\("Appearance host is required"\);/);
  // mounting over a live host retires the previous owner first
  assert.match(mjs, /if \(mounts\.has\(host\)\) mounts\.get\(host\)\(\);/);
  // the module reads window.SANA_STATIC, it never writes a window global
  assert.doesNotMatch(mjs, /window\.[A-Za-z_$][\w$]*\s*=[^=]/);
  // both canonical dependencies load through the manifest, never bare imports
  assert.match(mjs, /importStatic\("platform\/admin-session\.mjs"\)/);
  assert.match(mjs, /importStatic\("platform\/i18n\.mjs"\)/);
});

test("no hash shell, no old role or global branches; the bare English path is canonicalized once", () => {
  assert.doesNotMatch(mjs, /SANA_ADMIN_SCREEN|CURRENT_ROLE|sanaImportStatic|SanaAdminRouter|active_section|directStatus|sessionEnded|clearDirectStatus|soloMarca|function route\(/);
  assert.doesNotMatch(mjs, /location\.hash|hashchange|popstate|pushState\(/);
  // the retired marca role has no branch: marca cookies cannot validate, the
  // screen is admin-only end to end (contract 2026-09-07)
  assert.doesNotMatch(mjs, /CURRENT_ROLE|soloMarca|permitir_marca/);
  assert.match(mjs, /var canonicalPath = options\.canonicalPath !== undefined \? options\.canonicalPath : "\/admin\/appearance";/);
  assert.match(mjs, /history\.replaceState\(\{\}, "", canonicalPath\)/);
});

test("the controller may only touch its owning endpoints, same-origin, on the five documented reads", () => {
  assert.match(mjs, /const GET_PATHS = \[\s*"\/api\/admin\/theme", "\/api\/admin\/auth-mode", "\/api\/admin\/turnstile",\s*"\/api\/admin\/branding", "\/api\/admin\/chat-voice",\s*\];/);
  const touched = [...new Set([...mjs.matchAll(/"(\/api\/admin\/[a-z-]+(?:\/[a-z]+)?)"/g)].map((m) => m[1]))].sort();
  assert.deepEqual(touched, [
    "/api/admin/auth-mode", "/api/admin/branding", "/api/admin/branding/logo",
    "/api/admin/chat-voice", "/api/admin/theme", "/api/admin/turnstile",
  ], "the closed endpoint surface of the screen");
  assert.equal((mjs.match(/credentials: "same-origin"/g) || []).length, 2,
    "both request paths (reads and writes) ride the session cookies");
  assert.doesNotMatch(mjs, /Authorization/);
});

test("direct template declares only the native cohort and owns host, status and recovery", () => {
  assert.match(template, /extends "admin\/base\.html"/);
  assert.match(template, /<div id="sec-configuracion"><\/div>/);
  assert.match(template, /id="configuracion-status"[^>]*role="status"[^>]*aria-live="polite"[^>]*hidden/);
  assert.match(template, /<template id="appearance-load-recovery">/);
  assert.match(template, /<p class="adm-empty" role="alert">/);
  assert.match(template, /<button type="button" class="btn" data-retry>/);
  assert.equal((template.match(/<script/g) || []).length, 1, "one script: the module bootstrap");
  assertSharedShellContract(assert, template, ["platform/admin-appearance.mjs"]);
  assert.equal((template.match(/static\('/g) || []).length, 2, "no extra assets: the shared loader and the controller");
  for (const forbidden of ["admin-app.js", "admin-router.js", "admin-sections.js", "admin-table.js", "admin-legal.js"]) {
    assert.doesNotMatch(template, new RegExp(forbidden.replace(".", "\\.")), forbidden);
  }
  assert.doesNotMatch(template, /location\.hash\s*=/);
  assert.doesNotMatch(template, /<style|<hr\b|border-top|border-bottom|role="tab"/);
});

test("a11y: option cards carry aria-pressed, every message line is a polite status region", () => {
  assert.match(mjs, /aria-pressed="' \+ \(on \? "true" : "false"\) \+ '"/);
  assert.match(mjs, /function msgP\(id\) \{\s*return '<p class="cfg-msg" id="' \+ id \+ '" role="status" aria-live="polite"><\/p>';/);
  for (const id of ["cfg-msg", "cfg-auth-msg", "cfg-ts-msg", "cfg-mk-msg", "cfg-mk-logo-msg"]) {
    assert.match(mjs, new RegExp(`msgP\\("${id}"\\)`), id);
  }
  assert.doesNotMatch(mjs, /<p class="cfg-msg" id="cfg-[a-z-]+"><\/p>/, "no message line without a status role");
});

test("the shipped stylesheet rules keep 44px fields, 16px field fonts and the panel's buttons", () => {
  // No service key on screen (2026-11-08): the dictation block is its title
  // and «Activo», never «Modelo de Fal fal-ai/wizper».
  assert.doesNotMatch(mjs, /cfg-model--locked|voice\.model_label|esc\(voiceModel\)/);
  const hex = /\.cfg-mk-hex\{([^}]*)\}/.exec(mjs);
  assert.match(hex[1], /font-size:16px/);
  assert.match(hex[1], /height:44px/);
  // The logo is picked with the panel's button over a hidden file input,
  // like a doctor's photo: no system «Choose File / No file chosen».
  assert.match(mjs, /<input type="file" id="cfg-mk-file" class="cnt-foto-file"/);
  assert.match(mjs, /<label class="btn btn-quiet" for="cfg-mk-file" id="cfg-mk-pick">/);
  assert.doesNotMatch(mjs, /#cfg-mk-file\{/);
  assert.doesNotMatch(mjs, /id="cfg-mk-file"[^>]*style=/);
  assert.match(mjs, /\.cfg-mk-color\{width:52px;height:44px;/);
  // Guardar color is the panel's button: its height is the shared one.
  assert.doesNotMatch(mjs, /\.cfg-mk-acts \.btn\{/);
  assert.match(mjs, /#cfg-mk-reset,#cfg-mk-quitar\{display:inline-flex;align-items:center;min-height:44px;min-width:44px;\}/);
  assert.match(mjs, /<button type="button" id="cfg-mk-reset">/);
  assert.match(mjs, /<button type="button" id="cfg-mk-quitar">/);
  assert.doesNotMatch(mjs, /id="cfg-mk-(?:reset|quitar)" class="btn|class="btn[^"]*" id="cfg-mk-(?:reset|quitar)"/);
  // option grid stays auto-fit (fits 360px)
  assert.match(mjs, /\.cfg-themes\{display:grid;grid-template-columns:repeat\(auto-fit,minmax\(260px,1fr\)\)/);
  // no new light-gray key information, no underlines, no dividers
  assert.doesNotMatch(mjs, /text-decoration:underline|<hr\b|role="tab"/);
});

test("save semantics retained: the theme save reloads, colour keeps live-apply, exactly two reload sites", () => {
  const temaSave = /function setTema\(id\) \{[\s\S]*?\n  \}/.exec(mjs);
  assert.ok(temaSave, "setTema block found");
  assert.match(temaSave[0], /cur = id;/);
  assert.match(temaSave[0], /location\.reload\(\);/, "theme POST reloads: the brand is server-injected");
  assert.doesNotMatch(temaSave[0], /render\(\)|aplicarEnVivo/, "no hand-copied live-apply for the theme");
  // colour and logo keep live-apply
  assert.match(mjs, /aplicarEnVivo\(mkColor \? derived : null\);/);
  assert.equal((mjs.match(/location\.reload\(\)/g) || []).length, 2,
    "exactly two reload sites: the theme save and the deliberate retry action");
  assert.match(mjs, /if \(el\.closest\("#cfg-retry"\)\) \{ location\.reload\(\); return; \}/);
});

test("the direct load-error string exists in es and en, and both surfaces share it", () => {
  const es = JSON.parse(readFileSync(join(ROOT, "locales", "es", "admin.system.json"), "utf8"));
  const en = JSON.parse(readFileSync(join(ROOT, "locales", "en", "admin.system.json"), "utf8"));
  for (const key of ["admin.system.theme.load_error"]) {
    assert.equal(typeof es[key], "string", key + " (es)");
    assert.equal(typeof en[key], "string", key + " (en)");
  }
  // the canonical fallback in the controller is the es catalog value
  assert.match(mjs, new RegExp(`"admin\\.system\\.theme\\.load_error", null, "${es["admin.system.theme.load_error"].replace(/\./g, "\\.")}"`));
  // the template's recovery block carries the same string as its t() fallback
  assert.match(template, new RegExp(`admin\\.system\\.theme\\.load_error', '${es["admin.system.theme.load_error"].replace(/\./g, "\\.")}'`));
  assert.doesNotMatch(mjs, /admin\.system\.common\.session_expired|admin\.system\.theme\.sign_in/);
});
