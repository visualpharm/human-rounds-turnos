// chat-launcher-content.test.mjs — the floating «Chateá con IA» button after
// the demo review of 2026-11-15 (D21, P25): it steps aside for ANY content
// under it (the real line boxes of text, images, controls), not a fixed list
// of row classes, and it is hidden while the site menu is open (the menu
// already offers «Chat»).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const widget = readFileSync(new URL("../static/platform/chat-widget.mjs", import.meta.url), "utf8");
const boot = readFileSync(new URL("../templates/_chat_bootstrap.html", import.meta.url), "utf8");
const MENU_RULE = "body:has(#stznav.open) #stz-chat-launcher:not(.stzc-booking-inline){display:none;}";

test("the open site menu hides the floating button, before and after the widget loads", () => {
  assert.ok(boot.includes(MENU_RULE), "bootstrap CSS lacks the menu-open rule");
  assert.ok(widget.includes(MENU_RULE), "widget CSS lacks the menu-open rule");
});

test("the button steps aside for any content under it, measured by line boxes", () => {
  assert.doesNotMatch(widget, /LAUNCHER_ASIDE_ROWS/, "the old fixed row list is back");
  assert.match(widget, /function contentUnder\(r\)/);
  assert.match(widget, /elementsFromPoint/);
  assert.match(widget, /range\.getClientRects\(\)/);
  assert.match(widget, /ASIDE_MEDIA = \/\^\(IMG\|SVG/);
});

test("the first check runs once the page is laid out, not only on scroll", () => {
  assert.match(widget, /window\.addEventListener\("resize", queueLauncherAside\);\s*window\.setTimeout\(queueLauncherAside, 0\);/);
});
