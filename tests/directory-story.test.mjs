// Behavioral contract for stories/Directory.stories.js: the story must EXECUTE
// the real directory.mjs renderer against the shared synthetic fixture — not
// hand-rolled markup — and its /api/directory interception must be scoped: it
// delegates every other URL to whatever fetch was installed before it
// (preview.mjs's wrapper included) and always restores it, so it can never
// leak into the next story. Everything here runs the story exports directly
// under a browser-shaped harness (window aliases globalThis, exactly like a
// real page, so the story's window.fetch swap IS the fetch the renderer's
// bare fetch() call sees).
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const styles = new Map();
class TestNode {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.id = "";
    this.textContent = "";
    this.innerHTML = "";
  }
}
globalThis.Node = TestNode;
globalThis.HTMLElement = TestNode;
globalThis.document = {
  getElementById(id) { return styles.get(id) || null; },
  createElement(tag) { return new TestNode(tag); },
  head: { appendChild(node) { styles.set(node.id, node); } },
};

// The "network": every call that escapes the story's interception lands here
// and is recorded, then fails — proving the story served /api/directory
// itself and delegated (not swallowed) everything else.
const networkCalls = [];
const baselineFetch = async (input) => {
  const url = typeof input === "string" ? input : (input && input.url);
  networkCalls.push(url);
  throw new Error("story test: no network for " + url);
};
globalThis.fetch = baselineFetch;
globalThis.window = globalThis;

const Story = await import("../stories/Directory.stories.js");
const fixture = await import("../stories/directory-fixture.mjs");
const ROOT = new URL("..", import.meta.url).pathname;

// directory.mjs's top-level i18n import probes /api/i18n/es.json once at
// import time (before any story runs); that call is baseline traffic, not a
// story leak. It happens synchronously during the import above.
const importTimeCalls = networkCalls.length;

async function settled() {
  // Response.json() plus directory.mjs's two promise callbacks paint the host.
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

test("story exports meet Storybook HTML's synchronous Node boundary", async () => {
  for (const story of [Story.Completo, Story.Compacto, Story.TodasSinDestacadas]) {
    const host = story();
    assert.ok(host instanceof Node,
      "Storybook HTML can only append a Node or HTML string from storyFn()");
    assert.equal(typeof host.then, "undefined",
      "a Promise is not a valid Storybook HTML story result");
  }
  await settled();
});

test("Storybook serves only the public directory assets and boots the committed catalog", () => {
  const main = readFileSync(join(ROOT, ".storybook/main.mjs"), "utf8");
  const preview = readFileSync(join(ROOT, ".storybook/preview.mjs"), "utf8");
  assert.match(main, /from: "\.\.\/static\/icons", to: "\/static\/icons"/,
    "the preview publishes the exact public icon directory, not a broad checkout");
  assert.doesNotMatch(main, /from: "\.\."\s*,\s*to:/,
    "Storybook must never expose the repository root as a static directory");
  assert.match(preview, /import\.meta\.glob\("\.\.\/locales\/en\/\*\.json"/);
  assert.match(preview, /window\.SANA_I18N[\s\S]*active: "en"/,
    "native modules must observe the bootstrap before their imports run");

  const icons = new Set(["dir-card-arrow-sf-regular.svg"]);
  for (const payload of [fixture.DIRECTORY_PAYLOAD, fixture.DIRECTORY_PAYLOAD_SIN_DESTACADAS]) {
    for (const cluster of payload.clusters) {
      if (cluster.icon) icons.add(cluster.icon.replace("/static/icons/", ""));
    }
  }
  for (const icon of icons) {
    assert.ok(existsSync(join(ROOT, "static/icons", icon)),
      `fixture icon must be a committed public asset: ${icon}`);
  }
});

test("the full story renders through the real renderer with the fixture's data", async () => {
  const host = Story.Completo();
  await settled();
  const html = host.innerHTML;

  // Fingerprints only the real renderer leaves behind: its style element.
  assert.ok(styles.has("sana-dir-styles"),
    "the real directory.mjs render() injects its #sana-dir-styles sheet");

  // Cards: exactly the fixture clusters that have top + portraits.
  const cardIds = fixture.DIRECTORY_PAYLOAD.clusters
    .filter((c) => c.top && (c.avatares || []).length).map((c) => c.id);
  assert.equal((html.match(/class="dir-card"/g) || []).length, cardIds.length);
  for (const id of cardIds) {
    assert.match(html, new RegExp('class="dir-card" href="/d/' + id + '"'));
  }

  // The long specialty name renders whole as a card title — the 2-line wrap
  // case the module CSS is written for.
  assert.match(html, /<h3>Endocrinología, diabetes y nutrición<\/h3>/);

  // n_medicos is consumed twice: 7 -> "7 médicos" (the portrait-less
  // doctors are counted there, never in a «+N» badge), and 1 -> the singular
  // "1 médico" branch.
  assert.match(html, /7 médicos<\/div>/);
  assert.match(html, /1 médico<\/div>/);
  assert.doesNotMatch(html, /dc-av--more/);

  // Icon-only rubros (top but no portraits) never become cards: they appear
  // exactly once, as list rows carrying their icon (and no bare count).
  for (const id of ["vacunas", "grupos"]) {
    assert.equal((html.match(new RegExp('href="/d/' + id + '"', "g")) || []).length, 1,
      id + " must not repeat between cards and list");
    assert.doesNotMatch(html, new RegExp('class="dir-card" href="/d/' + id + '"'));
  }
  assert.match(html, /class="dir-row" href="\/d\/vacunas"/);
  assert.match(html, /src="\/static\/icons\/vacunas-prevencion\.png/);
  assert.doesNotMatch(html, /class="dr-n"/);

  // Both sections, both headings, in full mode.
  assert.match(html, /Más buscadas/);
  assert.match(html, /Más especialidades/);
  assert.match(html, /class="dir-all"/);
});

test("the fixture's portrait band draws every portrait sent, and no badge", async () => {
  const host = Story.Completo();
  await settled();
  const html = host.innerHTML;
  assert.equal((html.match(/class="dc-av dc-av--ph"/g) || []).length, 9,
    "6 clinica faces (7 doctors, 6 portraits) + endocrino 1 + trauma 2");
  assert.doesNotMatch(html, /dc-av--more|\+\d+<\/span>/);
});

test("compact mode (my-requests empty states) renders cards only", async () => {
  const host = Story.Compacto();
  await settled();
  const html = host.innerHTML;
  assert.match(html, /class="dir-cards"/);
  assert.match(html, /Más buscadas/);
  assert.doesNotMatch(html, /dir-all/);
  assert.doesNotMatch(html, /Más especialidades/);
  // The icon-only rubros live in the dropped section — compact really omits
  // them, it does not hide them.
  assert.doesNotMatch(html, /Vacunas y prevención/);
});

test("with no portrait cluster everything falls to the all-specialties list", async () => {
  const host = Story.TodasSinDestacadas();
  await settled();
  const html = host.innerHTML;
  assert.doesNotMatch(html, /dir-cards/);
  assert.doesNotMatch(html, /dir-card/);
  assert.match(html, /Todas las especialidades/);
  assert.ok((html.match(/class="dir-row"/g) || []).length >= 2);
});

test("the story serves /api/directory itself and restores fetch afterwards", async () => {
  const before = networkCalls.length;
  Story.Completo();
  await settled();
  assert.equal(globalThis.fetch, baselineFetch,
    "the scoped wrapper must be restored — no leak into the next story");
  assert.equal(networkCalls.length, before,
    "the baseline network is untouched: the story answered /api/directory itself");
  assert.ok(!networkCalls.includes("/api/directory"),
    "/api/directory must never reach the real network from a story");
});

test("the fixture wrapper is gone before asynchronous paint or another consumer", async () => {
  Story.Completo();
  assert.equal(globalThis.fetch, baselineFetch,
    "the mock is restored before any promise continuation can run");
  await assert.rejects(
    globalThis.fetch("/api/admin/preferences"),
    /no network for \/api\/admin\/preferences/);
  assert.ok(networkCalls.includes("/api/admin/preferences"));
  await settled();
});

test("the story composes with a pre-existing global wrapper instead of clobbering it", async () => {
  // preview.mjs permanently wraps window.fetch; a story must layer on top of
  // whatever it finds, and leave exactly that behind.
  const previewFetch = globalThis.fetch;
  const previewWrapper = (input, init) => previewFetch(input, init);
  globalThis.fetch = previewWrapper;
  try {
    const before = networkCalls.length;
    const host = Story.Completo();
    await settled();
    assert.equal(globalThis.fetch, previewWrapper,
      "after the story, the pre-existing wrapper owns fetch again");
    assert.match(host.innerHTML, /dir-cards/);
    assert.equal(networkCalls.length, before);
  } finally {
    globalThis.fetch = baselineFetch;
  }
});

test("concurrent story starts do not share a fetch wrapper", async () => {
  const before = networkCalls.length;
  const full = Story.Completo();
  const compact = Story.Compacto();
  assert.equal(globalThis.fetch, baselineFetch,
    "no wrapper survives either synchronous render start");
  await settled();
  assert.match(full.innerHTML, /dir-all/);
  assert.doesNotMatch(compact.innerHTML, /dir-all/);
  assert.equal(networkCalls.length, before);
});
