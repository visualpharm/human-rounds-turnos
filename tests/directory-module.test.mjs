import test from "node:test";
import assert from "node:assert/strict";

const styles = new Map();
globalThis.document = {
  getElementById(id) { return styles.get(id) || null; },
  createElement(tag) { return { tagName: tag.toUpperCase(), id: "", textContent: "" }; },
  head: { appendChild(node) { styles.set(node.id, node); } },
};
globalThis.window = {};

const Directory = await import("../static/platform/directory.mjs");

test("directory.mjs owns the renderer without publishing a compatibility global", () => {
  assert.equal(window.SanaDirectory, undefined);
  assert.equal(typeof Directory.render, "function");
});

test("render fetches and escapes the real directory payload", async () => {
  globalThis.fetch = async (url) => {
    assert.equal(url, "/api/directory");
    return {
      async json() {
        return { clusters: [
          { id: "cardio", nombre: "Cardio <adultos>", top: 1, n_medicos: 2,
            avatares: [{ ph: "background-position:0 0" }] },
          { id: "vacunas", nombre: "Vacunas & prevención", n_servicios: 1,
            icon: "/static/icons/vaccine.svg" },
        ] };
      },
    };
  };
  const host = { innerHTML: "" };
  await Directory.render(host, { compact: false });

  assert.match(host.innerHTML, /href="\/d\/cardio"/);
  assert.match(host.innerHTML, /Cardio &lt;adultos&gt;/);
  assert.match(host.innerHTML, /Vacunas &amp; prevención/);
  assert.equal((host.innerHTML.match(/href="\/d\/cardio"/g) || []).length, 1);
  assert.ok(styles.has("sana-dir-styles"));
});

test("a failed directory request removes the loading skeleton", async () => {
  globalThis.fetch = async () => { throw new Error("offline"); };
  const host = { innerHTML: "" };
  await Directory.render(host, { compact: true });
  assert.equal(host.innerHTML, "");
});

// Portrait band: the server sends up to 6 real portraits and the card draws
// every one of them; the doctors without one are counted in «N médicos» only,
// never in a «+N» badge (demo review 2026-11-15). Every case below executes
// the real render() against a single-cluster payload.
async function cardOf(cluster) {
  globalThis.fetch = async () => ({
    async json() { return { clusters: [Object.assign({ top: 1 }, cluster)] }; },
  });
  const host = { innerHTML: "" };
  await Directory.render(host, { compact: true });
  return host.innerHTML;
}
const fotos = (n) => Array.from({ length: n }, (_, i) => ({ ph: "background-position:-" + (i * 44) + "px 0" }));
const countFaces = (html) => (html.match(/class="dc-av dc-av--ph"/g) || []).length;

test("the walk-in card has one line, its icon, and always closes the row, whatever its top", async () => {
  const mk = (id, top, extra) => Object.assign({ id, nombre: id, top, n_medicos: 3, avatares: fotos(3) }, extra);
  globalThis.fetch = async () => ({
    async json() {
      return { clusters: [
        mk("demanda-espontanea", 1.5, { walkin: true, n_medicos: 0, avatares: [], icon: "/static/icons/clinica.png",
          desc: "Sin turno: guardia las 24 horas en el hospital." }),
        mk("a", 1), mk("b", 2), mk("c", 3), mk("d", 4), mk("e", 5), mk("f", 6),
      ] };
    },
  });
  const host = { innerHTML: "" };
  await Directory.render(host, { compact: true });
  const ids = [...host.innerHTML.matchAll(/href="\/d\/([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(ids, ["a", "b", "c", "d", "e", "demanda-espontanea"]);
  assert.match(host.innerHTML, /<div class="dc-sub">Sin turno, por llegada<\/div>/);
  assert.doesNotMatch(host.innerHTML, /dc-desc|dc-where/);
  assert.equal(countFaces(host.innerHTML), 15);
});

test("seven walk-in clusters never push the cut below zero: at most six cards, all walk-in", async () => {
  const wk = (i) => ({ id: "w" + i, nombre: "w" + i, top: i, n_medicos: 0, avatares: [], walkin: true, desc: "d" + i });
  globalThis.fetch = async () => ({
    async json() {
      return { clusters: [{ id: "a", nombre: "a", top: 1, n_medicos: 3, avatares: fotos(3) }]
        .concat([1, 2, 3, 4, 5, 6, 7].map(wk)) };
    },
  });
  const host = { innerHTML: "" };
  await Directory.render(host, { compact: true });
  const ids = [...host.innerHTML.matchAll(/class="dir-card" href="\/d\/([^"]+)"/g)].map((m) => m[1]);
  assert.equal(ids.length, 6);
  assert.ok(!ids.includes("a"));
});

for (const [total, portraits] of [[5, 5], [6, 6], [7, 6], [12, 6], [6, 5], [2, 1], [4, 2]]) {
  test(total + " doctors with " + portraits + " portraits: " + portraits + " faces, no badge, the count in the line", async () => {
    const html = await cardOf({ id: "cardio", nombre: "Cardiología", n_medicos: total, avatares: fotos(portraits) });
    assert.equal(countFaces(html), portraits);
    assert.doesNotMatch(html, /dc-av--more|>\+\d+</);
    assert.match(html, new RegExp(total + " médicos</div>"));
  });
}

test("a cluster with no portraits stays in the list: no card, a row with its icon and no number", async () => {
  globalThis.fetch = async () => ({
    async json() { return { clusters: [{
      id: "vacunas", nombre: "Vacunas y prevención", top: 4,
      n_medicos: 3, n_servicios: 2, avatares: [], icon: "/static/icons/vacunas-prevencion.png",
    }] }; },
  });
  const host = { innerHTML: "" };
  await Directory.render(host, { compact: false });
  assert.doesNotMatch(host.innerHTML, /dir-card/);
  assert.match(host.innerHTML, /class="dir-row" href="\/d\/vacunas"/);
  assert.match(host.innerHTML, /<span class="dr-chip"><img src="\/static\/icons\/vacunas-prevencion\.png/);
  assert.doesNotMatch(host.innerHTML, /class="dr-n"/);
});
