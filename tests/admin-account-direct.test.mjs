import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

// Supplementary source contract for native staff Account, its template, the
// shared shell's repair-state rules, and the Users self-row link.

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const JS = readFileSync(join(ROOT, "static/platform/admin-account.mjs"), "utf8");
const APP = readFileSync(join(ROOT, "static", "root", "admin-app.js"), "utf8");
const USERS = readFileSync(join(ROOT, "static", "platform", "admin-users.mjs"), "utf8");
const TEMPLATE = readFileSync(join(ROOT, "templates", "admin", "account.html"), "utf8");
const BASE = readFileSync(join(ROOT, "templates", "admin", "base.html"), "utf8");

test("direct Account state is the canonical query URL with real history", () => {
  assert.match(JS, /var DIRECT_PATH = "\/admin\/account";/);
  assert.match(JS, /var DIRECT_SETUP = DIRECT_PATH \+ "\?setup=2fa";/);
  assert.match(JS, /new URLSearchParams\(here && here\.search \? here\.search : ""\)/);
  assert.match(JS, /params\.getAll\("setup"\)/);
  // exactly one setup=2fa and no other key; anything else canonicalizes to bare
  assert.match(JS, /setup\.length === 1 && setup\[0\] === "2fa" && keys\.length === 1/);
  assert.match(JS, /history\.replaceState\(\{\}, "", canonical\)/);
  assert.match(JS, /history\.pushState\(\{\}, "", DIRECT_SETUP\)/);
  assert.match(JS, /listen\(win\(\), "popstate", onPopstate\)/);
  assert.match(JS, /export function mount\(host, options\)/);
  // Page lifetime (pagehide, persisted pageshow, readiness) is the shared
  // loader's: tests/screen-boot.test.mjs.
  assert.match(TEMPLATE, /import \{ bootScreen \} from "\{\{ static\('platform\/screen-boot\.mjs'\) \}\}"/);
  assert.match(TEMPLATE, /bootScreen\(\{/);
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
  assert.doesNotMatch(TEMPLATE, /location\.hash\s*=/);
});

test("the direct 2FA notice is a real link", () => {
  assert.match(JS, /function setupHref\(\) \{ return DIRECT_SETUP; \}/);
  assert.match(JS, /<a href="' \+ esc\(setupHref\(\)\) \+ '" class="adm-2fa-go">/);
  // direct reads the canonical state
  assert.match(JS, /function wantsSetup\(\)/);
});

test("direct session expiry is delegated to the response-aware shared boundary", () => {
  assert.match(JS, /Session\.confirmar\(r\.response, r\.json, current\)/);
  assert.match(JS, /dispose\.ready = dependencies\.then/);
  assert.match(JS, /getAttribute\("data-portal-logout"\)\) \|\| "\/admin\/login";/);
  assert.match(JS, /here\.assign\(target\)/);
  assert.match(JS, /fetch\("\/api\/admin\/logout", \{ method: "POST", credentials: "same-origin" \}\)/);
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
});

test("the direct template reuses the shared shell, legacy anchors and only its own controller", () => {
  assert.match(TEMPLATE, /extends "admin\/base.html"/);
  // The single #adm-h1 band is base.html's now (the shared-shell contract
  // below pins ownership + the child's own cohort).
  assert.doesNotMatch(TEMPLATE, /<h1\b/);
  assert.match(TEMPLATE, /<p class="adm-2fa-note" id="adm-2fa-note" hidden><\/p>/);
  assert.match(TEMPLATE, /<button class="adm-logout btn btn-quiet" id="adm-logout" type="button"(?:\{% if portal_logout %\} data-portal-logout="\{\{ portal_logout \}\}"\{% endif %\})? aria-label="\{\{ t\('admin\.shell\.logout'/);
  assert.doesNotMatch(TEMPLATE, /<span class="adm-icon" aria-hidden="true">logout<\/span>/);
  // the menu item look lives once in admin-direct.css, never inline here
  assert.doesNotMatch(TEMPLATE, /#adm-logout\{/);
  assert.match(TEMPLATE, /id="sec-cuenta"/);
  assert.match(TEMPLATE, /current_user \| tojson/);
  const assets = ["platform/admin-account.mjs"];
  assertSharedShellContract(assert, TEMPLATE, assets);
  assert.match(JS, /importStatic\("platform\/passkeys\.mjs"\)/);
  assert.doesNotMatch(JS, /window\.SanaPasskeys/);
  assert.match(JS, /map\["platform\/admin-users\.mjs"\]/);
  assert.match(JS, /mod\.refresh\(\)/);
  assert.doesNotMatch(JS, /return import\(url\)\.then\(function \(mod\) \{[\s\S]*?mod\.refresh\(\)/,
    "optional Users refresh must not keep Email save disabled");
  assert.doesNotMatch(JS, /SanaUsers/);
  for (const forbidden of ["admin-app.js", "admin-users.js", "admin-table.js", "admin-router.js", "admin-sections.js"]) {
    assert.equal(TEMPLATE.includes(forbidden), false, `${forbidden} must not load on the account screen`);
  }
  // no invented dividers, cards or tabs: the controller's own sections only
  assert.doesNotMatch(TEMPLATE, /<hr\b|border-top|border-bottom|class="card|role="tab/);
});

test("the shared shell hides non-repair links for an expired-2FA session and keeps Account and Sign out", () => {
  const CSS = readFileSync(join(ROOT, "static", "root", "admin-direct.css"), "utf8");
  assert.match(BASE, /\{% block nav_footer %\}\{% endblock %\}/);
  assert.match(CSS, /body\.adm-2fa-required \.adm5-nav-group:not\(\.adm5-nav-group--standalone\)\{display:none;\}/);
  assert.match(CSS, /body\.adm-2fa-required \.adm5-nav a:not\(\[data-nav=cuenta\]\)\{display:none;\}/);
  // the sign-out button lives outside .adm5-nav, so the rule never reaches it
  assert.ok(BASE.indexOf("{% block nav_footer %}") > BASE.indexOf("</nav>"));
  // «Cerrar sesión» reads as one more menu item: same size and padding as
  // .adm5-nav a, a 44px target on touch and in the compact header
  assert.match(CSS, /\.adm5-side \.adm-logout\{display:flex;align-items:center;justify-content:flex-start;width:100%;min-height:44px;\s*padding:6px 10px;border:0;border-radius:8px;background:transparent;color:var\(--hint-ink,var\(--text-2\)\);\s*font:400 14px\/1\.4 var\(--font\)/);
  assert.match(CSS, /\.adm5-nav a\{display:block;padding:6px 10px;border-radius:8px;text-decoration:none;\s*color:var\(--hint-ink,var\(--text-2\)\);font-size:14px;\}/);
  assert.match(CSS, /\.adm5-side \.adm-logout\{width:auto;min-height:44px;\}/);
});

test("every Account action has a 44px touch target on both shells", () => {
  assert.match(JS, /#sec-cuenta \.btn\{min-width:44px;min-height:44px;box-sizing:border-box;justify-content:center;\}/);
});

test("the shared 2FA setup scales the QR and shows novice instructions plus the manual key", () => {
  assert.match(JS, /\.acct-qr svg\{display:block;width:100%;height:auto;aspect-ratio:1;\}/);
  assert.match(JS, /<ol class="acct-steps">/);
  assert.match(JS, /Instalá Google Authenticator en tu teléfono:/);
  assert.match(JS, /var GA_PLAY_URL = "https:\/\/play\.google\.com\/store\/apps\/details\?id=com\.google\.android\.apps\.authenticator2";/);
  assert.match(JS, /var GA_APPSTORE_URL = "https:\/\/apps\.apple\.com\/app\/google-authenticator\/id388497605";/);
  assert.doesNotMatch(JS, /Microsoft/);
  assert.match(JS, /data-copy-account=/);
  assert.match(JS, /inputmode="numeric"[^>]+autocomplete="one-time-code"[^>]+maxlength="6"/);
  assert.doesNotMatch(JS, /<details class="acct-ayuda"><summary>' \+ esc\(T\("admin\.system\.account\.setup\.how_summary"/);
});

test("the Account controller owns verified setup state without an app-shell branch", () => {
  assert.match(JS, /function wantsSetup\(\)/);
  assert.match(JS, /setupState = \{ gen: mySetup, token: r\.json\.setup_token \}/);
  assert.doesNotMatch(APP, /location\.hash|SanaAdminRouter|hashchange|#app/);
});

test("direct Users links the self row to /admin/account", () => {
  assert.match(USERS, /function accountHref\(\) \{\s*return "\/admin\/account";/);
});
