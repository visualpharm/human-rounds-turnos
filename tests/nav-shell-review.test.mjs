// nav-shell-review.test.mjs — the public header after the demo review of
// 2026-11-15 (D7, P14, P27, P31): dark ink on both themes, one weight and one
// gap for every item, the session at the end, the active item marked by a bar
// (no pill), a one-column phone menu that spans the screen, and Icons8 files
// instead of hand-drawn glyphs.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as Nav from "../static/platform/nav.mjs";

const src = readFileSync(new URL("../static/platform/nav.mjs", import.meta.url), "utf8");
const tokens = readFileSync(new URL("../static/root/tokens.css", import.meta.url), "utf8");
const STATE = { loggedIn: false, hasSolicitudes: false, iaDown: false, iniciales: "", authMode: "password" };
const EXPANDED = ".stznav:not(.stznav--compact):not(.stznav--collapsed) ";

function html(path) {
  globalThis.window = {};
  delete globalThis.document;
  return Nav.html(path, "", STATE);
}

function order(sel) {
  const m = src.match(new RegExp(EXPANDED.replace(/[.()*:]/g, "\\$&") + sel.replace(/[.()*:-]/g, "\\$&") + "\\{order:(\\d)"));
  assert.ok(m, sel + ": no expanded order rule");
  return Number(m[1]);
}

test("menu text is dark, never the 50% grey (--text-2), in the bar and in the dropdown", () => {
  assert.doesNotMatch(src, /--nav-ink,var\(--text-2\)/, "a nav item still falls back to the light grey");
  assert.match(src, /\.stznav--collapsed \.stznav-menu\{--nav-ink:var\(--text\);/);
});

test("the active item is a bar under the words, never a pill", () => {
  assert.doesNotMatch(src, /\.stznav-link\.on\{[^}]*background:var\(--nav-on-bg/);
  assert.doesNotMatch(src, /stznav-cuenta\.on::before/);
  assert.match(src, /\.stznav-link\.on,[^{]*\{color:var\(--nav-on-ink,var\(--text\)\);background-image:linear-gradient/);
  // Pinamar's white pill is gone: white words and a white bar on the green.
  assert.doesNotMatch(tokens, /--nav-on-bg:#fff/);
  assert.match(tokens, /--nav-on-ink:#fff; --nav-on-bar:#fff;/);
});

test("Estudios is the active item on /d/diagnostico and on a study page", () => {
  assert.match(html("/d/diagnostico"), /class="stznav-link on" data-nav-key="estudios"/);
  assert.match(html("/study/diagnostico/x"), /class="stznav-link on" data-nav-key="estudios"/);
});

test("one row: links, Chat, Subí tu orden, language, then Ingresar/Registrarse at the end; one gap, no per-item margins", () => {
  const seq = [".stznav-public-links", ".stznav-btn--chat", ".stznav-btn--ghost", ".stznav-langs", ".stznav-session-links", ".stznav-burger"].map(order);
  assert.deepEqual(seq, [...seq].sort((a, b) => a - b), "expanded order: " + seq.join(","));
  assert.equal(order(".stznav-cuenta"), order(".stznav-session-links"));
  assert.match(src, /\.stznav-right\{gap:8px;\}/);
  assert.doesNotMatch(src, /\.stznav-btn--chat\{order:\d;margin-left/);
  assert.doesNotMatch(src, /\.stznav-btn--ghost\{order:\d;margin-left/);
});

test("phone menu: one column, plain rows, panel spans the screen", () => {
  assert.match(src, /\.stznav--collapsed \.stznav-session-links\{flex-direction:column;/);
  assert.doesNotMatch(src, /stznav-session-links\{flex-wrap:wrap/);
  assert.match(src, /\.stznav--collapsed \.stznav-menu \.stznav-btn\{justify-content:flex-start;[^}]*border:0;/);
  assert.match(src, /@media\(max-width:600px\)\{[^}]*\}\.stznav--compact \.stznav-content,\.stznav--collapsed \.stznav-menu\{left:16px;right:16px;width:auto;/);
});

test("no hand-drawn glyphs in the header: burger, order and logout are Icons8 files", () => {
  assert.doesNotMatch(src, /<svg/);
  assert.doesNotMatch(src, /<span><\/span><span><\/span><span><\/span>/);
  const page = html("/");
  assert.match(page, /class="stznav-burger"[^>]*><span class="stznav-ico"[^>]*menu-sf-regular\.svg/);
  assert.match(page, /document-sf-regular\.svg/);
  for (const f of ["menu-sf-regular.svg", "document-sf-regular.svg", "exit-sf-regular.svg"]) {
    assert.ok(readFileSync(new URL("../static/icons/" + f, import.meta.url), "utf8").includes("<svg"), f);
  }
});
