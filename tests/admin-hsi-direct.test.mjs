// Source guards for the direct /admin/hsi screen after the native cutover
// (2026-09-08): static/platform/admin-hsi.mjs is the only controller, the
// classic admin-hsi.js is physically retired, and the template boots the
// native module through its fingerprinted static URL.
//
// Ownership map — the old admin-hsi.js regexes were not dropped, they moved
// to stronger evidence: strict acknowledgements, resolver-count validation,
// per-read refresh ownership, action-select reset, malformed-metric refusal,
// abort/dispose ownership and the session boundary (confirmar with an owner
// predicate) are RUNTIME contracts pinned by executing the real module in
// tests/admin-hsi-lifecycle.test.mjs (the 55-case suite); the template's own
// bootstrap lifetime and the real-browser session/import/late behavior are
// the e2e gate's. What these source assertions prove is shape and wiring —
// never runtime behaviour, and never rendered geometry: the CSS checks pin
// the rules the module ships, not the pixels a browser paints from them.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const hsi = readFileSync(join(ROOT, "static", "platform", "admin-hsi.mjs"), "utf8");
const template = readFileSync(join(ROOT, "templates", "admin", "hsi.html"), "utf8");
const shell = readFileSync(join(ROOT, "templates", "admin", "base.html"), "utf8");
const css = readFileSync(join(ROOT, "static", "root", "admin-direct.css"), "utf8");
const manifest = JSON.parse(readFileSync(join(ROOT, "app", "modules", "turnos", "module.json"), "utf8"));
const fingerprint = readFileSync(join(ROOT, "app", "core", "static_fingerprint.py"), "utf8");
const es = JSON.parse(readFileSync(join(ROOT, "locales", "es", "admin.hsi.json"), "utf8"));
const en = JSON.parse(readFileSync(join(ROOT, "locales", "en", "admin.hsi.json"), "utf8"));

function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "");
}
const code = stripComments(hsi);

test("the classic controller is physically retired and nothing loads it", () => {
  assert.equal(existsSync(join(ROOT, "admin-hsi.js")), false,
    "admin-hsi.js stays deleted, not dormant");
  for (const name of readdirSync(join(ROOT, "templates"), { recursive: true })) {
    if (!String(name).endsWith(".html")) continue;
    const text = readFileSync(join(ROOT, "templates", String(name)), "utf8");
    assert.ok(!text.includes("admin-hsi.js"), `templates/${name} no longer references the retired controller`);
  }
  // The fingerprint registry serves the native module as an immutable
  // content-hash URL and keeps the classic name out of every served set.
  assert.match(fingerprint, /"platform\/admin-hsi\.mjs"/);
  assert.doesNotMatch(fingerprint, /"admin-hsi\.js"/);
});

test("the native module imports inert: one export, a mount registry, read-only window access", () => {
  assert.match(hsi, /const mounts = new WeakMap\(\);/);
  assert.equal((hsi.match(/^export /gm) || []).length, 1, "mount is the module's only export");
  assert.match(hsi, /export function mount\(host, options\) \{\s*\n  if \(!host\) throw new Error\("HSI host is required"\);/);
  // mounting over a live host retires the previous owner first
  assert.match(hsi, /if \(mounts\.has\(host\)\) mounts\.get\(host\)\(\);/);
  // the module reads window.SANA_STATIC, it never writes a window global
  assert.doesNotMatch(hsi, /window\.[A-Za-z_$][\w$]*\s*=[^=]/);
  // the four canonical dependencies load through the manifest, never bare imports
  assert.match(hsi, /importStatic\("platform\/admin-session\.mjs"\)/);
  assert.match(hsi, /importStatic\("platform\/i18n\.mjs"\)/);
  assert.match(hsi, /importStatic\("platform\/dates\.mjs"\)/);
  assert.match(hsi, /importStatic\("platform\/format\.mjs"\)/);
  // and each is validated, not just imported
  assert.match(hsi, /take\(mods\[0\], "platform\/admin-session\.mjs", function \(m\) \{ return typeof m\.confirmar === "function"; \}\)/);
});

test("no hash shell, no old role or global branches; the bare English path is canonicalized once", () => {
  assert.doesNotMatch(code, /SanaAdminRouter|SanaAdminSections|sanaRouteHsi|hashchange|location\.hash|pushState\(/);
  assert.doesNotMatch(code, /window\.(?:loadHsiSync|hsiCuando|hsiAplicar|renderHsiSec|routeHsi)\s*=/);
  assert.doesNotMatch(code, /SANA_ADMIN_SCREEN|CURRENT_ROLE/);
  assert.doesNotMatch(code, /DOMContentLoaded/);
  assert.match(hsi, /history\.replaceState\(\{\}, "", "\/admin\/hsi"\);/);
});

test("HSI explicitly classifies response boundaries before local errors", () => {
  assert.match(hsi, /if \(r\.status !== 401 && r\.status !== 403\) return Promise\.resolve\(current\(\)\);/);
  assert.match(hsi, /if \(r\.json && r\.json\.error === "demo_solo_lectura"\) return Promise\.resolve\(current\(\)\);/);
  // confirmar receives the mount's own liveness predicate (the third
  // argument): a disposed caller can never navigate the fresh screen.
  assert.match(hsi, /return Session\.confirmar\(r\.response, r\.json, current\)\.then\(function \(transitioned\) \{/);
  assert.doesNotMatch(code, /SanaAdminLogin|showGate|location\.(?:assign|href)/);
});

test("direct HSI links remain server routes and do not invent hash state", () => {
  assert.match(hsi, /return '<a href="\/admin\/solicitudes">' \+ label \+ "<\/a>";/);
  assert.doesNotMatch(code, /#solicitudes|data-hsi-request/);
});

test("direct template boots the native controller with the shared lifecycle pattern", () => {
  assert.match(template, /extends "admin\/base\.html"/);
  assert.match(template, /<div id="sec-hsi"><\/div>/);
  assert.match(template, /<p id="hsi-status" class="adm-empty" role="status" aria-live="polite" hidden><\/p>/);
  assert.match(template, /<template id="hsi-load-recovery">/);
  assert.match(template, /<p class="adm-empty" role="alert">/);
  assert.match(template, /<button type="button" class="btn" data-retry>/);
  assert.equal((template.match(/<script/g) || []).length, 1, "one script: the module bootstrap");
  assertSharedShellContract(assert, template, ["platform/admin-hsi.mjs"]);
  assert.equal((template.match(/static\('/g) || []).length, 2, "no extra assets: the shared loader and the controller");
  // The appearance boot shape: explicit host/status mount through the shared
  // loader, which owns the late-load notice, failure recovery, owned retry,
  // pagehide and persisted pageshow (tests/screen-boot.test.mjs).
  assert.match(template, /import \{ bootScreen \} from "\{\{ static\('platform\/screen-boot\.mjs'\) \}\}"/);
  assert.match(template, /import\("\{\{ static\('platform\/admin-hsi\.mjs'\) \}\}"\)/);
  assert.match(template, /mount: \(Hsi, onError\) => Hsi\.mount\(host, \{ status, onError \}\)/);
  assert.match(template, /recovery: "hsi-load-recovery"/);
  assert.match(template, /onFailed: \(\) => \{ status\.hidden = true; \}/);
  assert.doesNotMatch(template, /setTimeout/);
  assert.doesNotMatch(template, /admin-hsi\.js/);
  assert.doesNotMatch(shell, /static\('admin-session\.js'\)/);
  assert.doesNotMatch(shell, /static\('admin-login\.js'\)/);
});

test("the controller may only touch its owning endpoints, same-origin", () => {
  const touched = [...new Set([...hsi.matchAll(/"(\/api\/admin\/[a-z-]+(?:\/[a-z]+)?)"/g)].map((m) => m[1]))].sort();
  assert.deepEqual(touched, [
    "/api/admin/hsi-sync", "/api/admin/hsi-sync/conflicts", "/api/admin/hsi-sync/resolver",
    // read-only: the installation's HSI state (mode, reason, centers)
    "/api/admin/system/hsi",
  ], "the closed endpoint surface of the screen");
  assert.match(hsi, /credentials: "same-origin"/);
  assert.doesNotMatch(hsi, /Authorization/);
});

test("desktop HSI CSS belongs to the direct shell; the controller injects responsive rules", () => {
  assert.match(css, /\.hsi-hero\{margin:6px 0 2px;font-size:16px;color:var\(--text\);\}/);
  assert.doesNotMatch(hsi, /\.hsi-hero\{/);
  assert.match(hsi, /\.hsi-tabla \.hsi-alt\{min-height:44px;font-size:16px;\}/);
  assert.match(hsi, /container-name:hsi-table/);
  assert.match(hsi, /@container hsi-table \(max-width:900px\)\{/);
  assert.match(hsi, /font-size:16px;font-weight:600;color:var\(--text\)/);
  assert.doesNotMatch(hsi, /border-(?:top|bottom):[1-9]/);
  assert.doesNotMatch(hsi, /<hr\b|role="tab"|text-decoration:underline/);
});

test("the HSI catalogs are symmetric and cover every controller-owned string", () => {
  const esKeys = Object.keys(es).filter((key) => key !== "_comment").sort();
  const enKeys = Object.keys(en).filter((key) => key !== "_comment").sort();
  assert.deepEqual(enKeys, esKeys);
  const used = new Set([...hsi.matchAll(/T\("(admin\.hsi\.[a-z_]+)"/g)].map((match) => match[1]));
  for (const key of used) assert.equal(typeof es[key], "string", `${key} missing from es`);
  for (const key of used) assert.equal(typeof en[key], "string", `${key} missing from en`);
});

test("the template recovery line reuses the section's own catalog sentence in both shells", () => {
  // the canonical fallback in the controller is the es catalog value
  assert.match(hsi, new RegExp(`"admin\\.hsi\\.load_error", null, "${es["admin.hsi.load_error"].replace(/\./g, "\\.")}"`));
  // the template's recovery block carries the same string as its t() fallback
  assert.match(template, new RegExp(`admin\\.hsi\\.load_error', '${es["admin.hsi.load_error"].replace(/\./g, "\\.")}'`));
});

test("HSI remains a connector-gated direct-screen module", () => {
  const section = manifest.admin_sections.find((item) => item && item.id === "hsi");
  assert.deepEqual(section.screen, {
    path: "/admin/hsi", router_attr: "screen_router", module: "app.admin_panel.admin_hsi_screen",
    requires_capability: "emr_sync", requires_connector: "hsi"
  });
});

test("HSI dates use canonical dates.mjs and retain no shadow formatter", () => {
  assert.match(hsi, /importStatic\("platform\/dates\.mjs"\)/);
  assert.match(hsi, /function \(m\) \{ return typeof m\.corta === "function" && typeof m\.relativo === "function"; \}/);
  assert.match(hsi, /Dates\.corta\(/);
  assert.doesNotMatch(code, /fechaFallback|SanaDates|\bMESES\b/);
});
