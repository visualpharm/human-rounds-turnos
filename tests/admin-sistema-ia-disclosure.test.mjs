// Source contracts for the 2026-11-15 pass over the «Sistema» and AI screens:
// what works stays quiet, secondary text waits behind a disclosure.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(join(ROOT, p), "utf8");
const SYSTEM = read("static/platform/admin-system.mjs");
const CHATS = read("app/modules/chat/admin_chats.html");
const USABILITY = read("static/platform/admin-usability.mjs");
const KNOWLEDGE = read("static/platform/admin-knowledge.mjs");

test("Estado: a flagged cluster goes on top whole, the healthy ones behind one disclosure", () => {
  // One card per cluster: never split into a flagged and a healthy half.
  assert.match(SYSTEM, /\(flagged \? top : rest\)\.push\(card\(filas\)\)/);
  assert.match(SYSTEM, /asks\(c\.estado\) \|\| \(c\.items \|\| \[\]\)\.some\(function \(it\) \{ return asks\(it\.estado\); \}\)/);
  assert.match(SYSTEM, /<details class="sis-rest"' \+ \(top\.length \? "" : " open"\)/);
  // The dot marks only what asks for a hand.
  assert.match(SYSTEM, /estado === "warn" \|\| estado === "error" \? '<span class="sis-dot/);
});

test("Chats: a conversation with no patient message is not listed, one opened by a photo is", () => {
  assert.match(CHATS, /Number\(r\.user_messages\) > 0 \|\| !!r\.first_message/);
});

test("Usabilidad: each finding is its title, the evidence opens in place, no problem counter", () => {
  assert.match(USABILITY, /more = node\('details'\)/);
  assert.doesNotMatch(USABILITY, /study\.admin\.story_found'/);
});

test("Wiki: what reaches the chat stays visible, the writing tips fold", () => {
  const about = KNOWLEDGE.slice(KNOWLEDGE.indexOf("function about()"));
  assert.ok(about.indexOf("about_p2") < about.indexOf("<details>"), "the publishing rule is above the disclosure");
  assert.ok(about.indexOf("tip_1") > about.indexOf("<details>"), "the tips are inside it");
});
