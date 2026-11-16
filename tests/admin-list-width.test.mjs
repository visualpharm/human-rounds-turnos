// Every panel list takes the full content width (data-list-view lifts the
// 1040px content cap in admin-direct.css), and a filter button inside a list
// cell reads in the cell's dark ink. Chats, Registro de IA and Roles y
// permisos ended at 1256px of 1440 (Roles at 938px) and the specialty of
// Médicos sat in light grey at 14px (demo, 2026-11-07).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const read = (p) => fs.readFileSync(new URL("../" + p, import.meta.url), "utf8");
const directCss = read("static/root/admin-direct.css");
const chats = read("app/modules/chat/admin_chats.html");
const register = read("templates/admin/ai-register.html");
const roles = read("static/platform/admin-roles.mjs");
const content = read("static/platform/admin-content.mjs");

test("the content cap lifts for a direct child marked as a list", () => {
  assert.match(directCss, /\.adm-content-wrap:has\(> \[data-list-view="1"\]:not\(\[style\*="none"\]\)\)\{max-width:none;\}/);
});

test("Chats and Registro de IA mark their list as a list view", () => {
  assert.match(chats, /<div class="adm-content-wrap">\s*(\{#[\s\S]*?#\}\s*)?<section id="sec-chats"[^>]*data-list-view="1"/);
  // The register's wrap is the content wrap itself: the mark goes on its direct child, the table block.
  assert.match(register, /<div class="air-table-wrap" data-list-view="1">/);
});

test("Roles y permisos: the list is a full-width list that spreads its columns, the editor keeps the cap", () => {
  const list = /function list\(roles\) \{([\s\S]*?)\n  \}/.exec(roles)?.[1] || "";
  const editor = /function editor\(role, catalog, seq\) \{([\s\S]*?)host\.innerHTML/.exec(roles)?.[1] || "";
  assert.match(list, /listView\(true\)/);
  assert.doesNotMatch(list, /fill: false/);
  // One column, the name, takes the width; no «Activo» column of «Sí» in every
  // row, a switched-off role is marked after its name. Headers are the panel's cnt-th.
  assert.match(list, /key: 'name', [^}]*flex: 1 \}\]/);
  assert.equal((list.match(/<th scope="col" class="cnt-th" data-col=/g) || []).length, 1);
  assert.doesNotMatch(list, /data-col="enabled"/);
  assert.match(list, /role\.enabled \? '' : ' <span class="roles-off">'/);
  assert.match(editor, /listView\(false\)/);
  assert.match(roles, /host\.setAttribute\('data-list-view', '1'\)/);
  assert.match(roles, /host\.removeAttribute\('data-list-view'\)/);
});

test("a filter button inside a list cell reads in the cell's dark ink and size", () => {
  // The specialty of Médicos is the one such button: it sets the specialty filter.
  assert.match(content, /class="cnt-inline-action" data-fesp-med=/);
  assert.match(directCss, /td \.cnt-inline-action\{font-size:inherit;font-weight:inherit;color:var\(--text\);[^}]*\}/);
  assert.match(directCss, /td \.cnt-inline-action:hover\{color:var\(--hover-link\);\}/);
});

test("Chats headers wear the panel's one header class", () => {
  // Bare <th> showed 16px bold dark at 1440 (demo, 2026-11-07).
  const paint = /var html = '([^\n]*)/.exec(chats)?.[1] || "";
  const heads = paint.match(/<th\b[^>]*>/g) || [];
  assert.equal(heads.length, 4);
  for (const th of heads) assert.match(th, /class="cnt-th[ "]/);
});

test("stacked catalog cards: dark values, no separator on a line edge (Servicios, Centros, Médicos)", () => {
  const tables = ["#sec-servicios .cnt-table", "#sec-centros .cnt-table", "#sec-medicos .cnt-table--medicos"];
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // The row is a wrapping flex line; a datum is a block whose dot sits in the
  // 16px gap to its left, so a datum opening a line has its dot clipped.
  const row = new RegExp(tables.map((t) => esc(t + " tr")).join(",") + "\\{display:flex;flex-wrap:wrap;[^}]*column-gap:16px;[^}]*overflow:clip;");
  assert.match(content, row);
  const cell = new RegExp(tables.map((t) => esc(t + " td[data-col]")).join(",") + "\\{[^}]*position:relative;[^}]*color:var\\(--text\\);\\}");
  assert.match(content, cell);
  assert.match(content, /td\[data-col\]:not\(\[data-col=nombre\]\)::after\{content:\\"\\\\00b7\\";position:absolute;top:0;right:100%;width:16px;[^}]*color:var\(--hint-ink/);
  // An empty datum never leaves a dot with nothing after it.
  assert.match(content, /#sec-medicos \.cnt-table--medicos td\[data-col\]:empty\{display:none;\}/);
  // The old inline separators wrapped onto line edges («20 h ·», «· Próximo día»).
  assert.doesNotMatch(content, /td ~ td::before/);
  assert.doesNotMatch(content, /content:\\" · \\" attr\(data-label\)/);
});
