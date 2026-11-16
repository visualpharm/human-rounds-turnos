import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";

test("chart stories synchronously render the ten production chart shapes and an empty state", async () => {
  const priorDocument = globalThis.document;
  globalThis.document = {
    getElementById: () => null,
    createElement: () => ({ style: {}, innerHTML: "", textContent: "", id: "" }),
    head: { appendChild() {} },
  };
  const vite = await createServer({ logLevel: "silent", server: { middlewareMode: true },
    appType: "custom", optimizeDeps: { noDiscovery: true, include: [] } });
  try {
    const stories = await vite.ssrLoadModule("/stories/AdminCharts.stories.js");
    const cleanup = stories.default.beforeEach();
    for (const name of ["Scatter", "Heatmap", "Treemap", "Slope", "Lollipop", "Linea", "Densidad", "Piramide", "Tile", "Hero"]) {
      const node = stories[name]();
      assert.equal(typeof node.innerHTML, "string", `${name} returned a Node-like host synchronously`);
      assert.ok(node.innerHTML.length > 100, `${name} rendered a nontrivial production chart`);
      assert.doesNotMatch(node.innerHTML, /undefined|NaN/, `${name} uses valid synthetic fixture data`);
    }
    assert.match(stories.SinDatos().innerHTML, /rep-empty/);
    cleanup();
  } finally {
    await vite.close();
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});
