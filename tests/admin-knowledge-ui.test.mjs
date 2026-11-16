import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../static/platform/admin-knowledge.mjs", import.meta.url), "utf8");
const contentSrc = readFileSync(new URL("../static/platform/admin-content.mjs", import.meta.url), "utf8");
const screen = readFileSync(new URL("../templates/admin/knowledge.html", import.meta.url), "utf8");
const screenPy = readFileSync(new URL("../app/admin_panel/admin_knowledge_screen.py", import.meta.url), "utf8");
const app = readFileSync(new URL("../static/root/admin-app.js", import.meta.url), "utf8");
const manifest = JSON.parse(readFileSync(new URL("../app/modules/turnos/module.json", import.meta.url), "utf8"));

test("Wiki is an admin-only direct content screen", () => {
  // The direct screen: server-gated solo_admin, its own #sec-knowledge
  // container, and the controller loaded fingerprinted.
  assert.match(screenPy, /require_admin_screen\(solo_admin=True\)/);
  assert.match(screen, /id="sec-knowledge"/);
  assert.match(screen, /SANA_STATIC\["platform\/admin-knowledge\.mjs"\]/);
  // applyRol's visibility matrix (admin-app.js) still records esAdmin, and
  // the manifest's knowledge entry carries the direct screen object.
  assert.match(app, /'nav-knowledge': esAdmin/);
  const section = manifest.admin_sections.find((entry) => entry && entry.id === "knowledge");
  assert.ok(section && section.screen && section.screen.path === "/admin/knowledge");
  assert.match(src, /export function mount\(\)/);
  assert.match(src, /addEventListener\("popstate"/);
  assert.doesNotMatch(src, /SanaAdminRouter|sanaRouteKnowledge|hashchange|location\.hash/);
});

test("Wiki keeps the server page title and removes redundant brand copy", () => {
  assert.doesNotMatch(src, /subtitulo_wiki/);
  assert.doesNotMatch(src, /Asistente de Pinamar Turnos/);
});

test("knowledge editor saves explicitly and keeps drafts plus the API contract", () => {
  assert.match(src, /\/api\/admin\/content\/knowledge/);
  assert.match(src, /function itemHref\(id\) \{ return location\.pathname \+ "\?item=" \+ encodeURIComponent\(id\); \}/);
  assert.match(src, /Nuevo artículo/);
  assert.match(src, /Nueva categoría/);
  assert.match(src, /function search\(item, q\)/);
  assert.match(src, /history\.pushState\(null, "", link\.getAttribute\("href"\)\)/);
  assert.match(src, /if \(state\.items\.length\) renderEditor\(\); else return load\(\)/);
  assert.match(src, /source_review_every_days/);
  assert.match(src, /function validate\(doc\)/);
  assert.match(src, /\^https:\\\/\\\//);
  assert.match(src, /function markDraft\(form\)/);
  assert.match(src, /state\.drafts\.set\(draftKey\(form\), values\(form\)\)/);
  assert.doesNotMatch(src, /setTimeout\(function \(\) \{ save\(formEl, false\)/);
  assert.match(src, /data-kn-title/);
  assert.match(src, /function generateTitle\(form\)/);
  assert.match(src, /form\.elements\.title\.value = out\.properties\.title/);
  assert.match(src, /method: creating \? "POST" : "PUT"/);
  assert.match(src, /Guardando…/);
  assert.match(src, /Error al guardar/);
  assert.doesNotMatch(src, /Cancelar/);
});

test("an empty wiki opens the existing article editor", () => {
  assert.match(src, /state\.items = data\.items;\s+if \(!state\.treeInitialized\)/);
  assert.match(src, /if \(state\.items\.length\) renderEditor\(\); else openNew\("articulo"\);/);
});

test("knowledge layout stacks without horizontal overflow and does not introduce dividers", () => {
  assert.match(src, /container:knowledge \/ inline-size/);
  assert.match(src, /@container knowledge \(max-width:900px\)/);
  assert.match(src, /\.kn-editor\.is-open\{position:fixed;inset:0/);
  assert.match(src, /data-kn-close/);
  assert.match(src, /aria-modal/);
  assert.doesNotMatch(src, /<hr/);
});

test("knowledge categories are single-line collapsible tree groups", () => {
  assert.match(src, /data-kn-toggle/);
  assert.match(src, /aria-expanded/);
  assert.match(src, /state\.collapsed/);
  assert.doesNotMatch(src, /<small>Categoría<\/small>/);
});

test("article editing puts an editable title and body before optional metadata", () => {
  assert.match(src, /class="kn-title" name="title" rows="1" required/);
  assert.match(src, /var body = category \? details : .*kn-field--content[\s\S]*\+ details;/);
  assert.match(src, /data-kn-title/);
  assert.match(src, /knowledge\/properties/);
  assert.match(src, /kn-sr-only/);
  assert.match(src, /aria-label="' \+ esc\(T\("admin\.content\.knowledge\.contenido_label/);
  assert.match(src, /kn-more/);
  assert.doesNotMatch(src, /data-kn-properties/);
  assert.doesNotMatch(src, /Actualizar propiedades/);
});


test("explicit saving protects drafts and keeps asynchronous title generation safe", () => {
  assert.match(src, /window\.addEventListener\("beforeunload"/);
  assert.match(src, /if \(hasUnsavedDrafts\(\)\) \{ event\.preventDefault\(\); event\.returnValue = ""; \}/);
  assert.match(src, /if \(form\._saving\) return Promise\.resolve\(null\);/);
  assert.match(src, /button\.disabled = true/);
  assert.match(src, /changedDuringSave = JSON\.stringify\(current\) !== JSON\.stringify\(doc\)/);
  assert.match(src, /state\.drafts\.set\(draftKey\(form\), retained\)/);
  assert.match(src, /function autoGrowTitle\(form\)/);
  assert.match(src, /form\.elements\.id\.value = saved\.id/);
  assert.match(src, /save\(formEl, !formEl\.elements\.id\.value\)/);
  assert.match(src, /requestedTitle = form\.elements\.title\.value, requestedContent = content/);
  assert.match(src, /form\.elements\.title\.value === requestedTitle/);
  assert.match(src, /String\(form\.elements\.content\.value \|\| ""\)\.trim\(\) === requestedContent/);
  assert.match(src, /data-kn-delete[\s\S]*window\.confirm\(T\("admin\.content\.knowledge\.confirmar_descartar_y_deshabilitar"/);
  assert.match(src, /formEl\._deleting/);
  assert.match(src, /controls\.forEach\(function \(control\) \{ control\.disabled = true; \}\)/);
  assert.match(src, /data-kn-restore[\s\S]*\.catch\(function \(err\) \{ if \(!err\.cancelled && isCurrentForm\(formEl, epoch\)\) setState/);
});
// Progressive disclosure (the product owner, 2026-10-26: «if there's no content, why to
// show all the ui?»).
test("an empty wiki shows no search and no conversation check, only what goes here", () => {
  // the search and «Probar conversaciones» are data tools the shell CSS hides
  // while the section is loading or empty
  assert.match(src, /<input id="kn-search" data-data-tool /);
  assert.match(src, /iconButton\("data-data-tool data-kn-golden"/);
  assert.match(src, /Filters\.applyDisclosure\(root, "loading"\); root\.innerHTML = '<div class="kn-wrap">/);
  assert.match(src, /Filters\.disclosure\(\{ total: activeItems\(\)\.length, shown: all\.length, filtered: !!q \}\)/);
  assert.match(src, /view === "empty"\) html = Filters\.emptyStateHtml\(/);
  // the empty wiki opens the new-article form AND paints the tree state
  assert.match(src, /function openNew\(type\) \{[^}]*renderTree\(\);/);
  // the old text that claimed a search on an empty wiki is gone
  assert.doesNotMatch(src, /No encontramos conocimiento con esa búsqueda/);
});

test("a search that matches nothing keeps the search and offers to clear it", () => {
  assert.match(src, /view === "no-match"\) html = Filters\.noMatchHtml\(/);
  assert.match(src, /admin\.content\.knowledge\.limpiar_busqueda/);
  assert.match(src, /querySelector\("\[data-clear-filters\]"\)[\s\S]{0,200}input\.value = ""; renderTree\(\)/);
});

test("the AI notice goes with the article form that proposes with AI, nowhere else", () => {
  // server-rendered (test_ai_register_notices pins its text before the
  // section) but hidden until an article form with «Generar título» shows
  assert.match(screen, /<p class="adm-ai-notice" data-ai-notice="knowledge" hidden>\{\{ t\('ai\.notice\.knowledge'/);
  assert.match(src, /var covers = !!\(formEl && formEl\.querySelector\("\[data-kn-title\]"\)\);/);
  assert.match(src, /if \(covers\) formEl\.insertBefore\(notice, formEl\.firstChild\);\s*notice\.hidden = !covers;/);
  assert.match(src, /wireEditor\(\); syncAiNotice\(\); syncEditorMode\(\); \}/);
  assert.doesNotMatch(screen, /style="font-size:16px;color:var\(--text\);">\{\{ t\('ai\.notice\.knowledge'/);
});

test("content routes hide every sibling admin section, including knowledge", () => {
  assert.match(contentSrc, /\$\$\('\.adm-content-wrap > \[id\^="sec-"\]'/);
});
