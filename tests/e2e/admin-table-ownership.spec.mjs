// Table-width ownership on a stub origin serving the real admin-table, admin-preferences
// and i18n modules: an explicitly created table keeps its width through the generic
// sweep, a delayed preferences GET and a same-key sibling; a disposed owner cannot touch
// a fresh one; and the Storybook story renders without the admin shell globals.
//
// Port of tests/e2e/admin-table-ownership.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The stub origin comes from the `serve` fixture and the pages from the runner (the
//   original started its own http.Server and `chromium.launch`); the second page (the story)
//   is another page of the runner's context. The viewport 1200x800 is test.use().
//   The stub does not use the application: The file is `hermetic` (appOptions only names it for
//   tests/e2e-spec-isolation.test.mjs): no app or database is started for it.
// - The two hand-made promise gates (the withheld preferences GET, the page's «early explicit
//   edit» report) are holdGate(cleanup) gates: the GET waits on `held`, the early-edit POST
//   calls `arrive()`; both are released on cleanup so a failing test never leaves the server
//   hanging.
// - In-page timing: the original slept 25 ms after releasing the preferences import before
//   firing the «late generic preference callback». That sleep is replaced by awaiting the
//   real hydration (`admin-preferences.load()` resolves when the delayed GET has been applied)
//   and then the same two animation frames; the late callback and everything it must not do
//   are unchanged.
// - Assertions are plain `expect(...).toEqual(...)` on in-page snapshots: the outcomes are
//   computed inside the page at the moment of the race, which is what they check.
import { readFileSync } from "node:fs";
import { test, expect, holdGate } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_admin_table_ownership", hermetic: true },
  viewport: { width: 1200, height: 800 },
});

const tableSource = readFileSync(new URL("../../static/platform/admin-table.mjs", import.meta.url), "utf8");
const preferencesSource = readFileSync(new URL("../../static/platform/admin-preferences.mjs", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../../static/platform/i18n.mjs", import.meta.url), "utf8");
const storySource = readFileSync(new URL("../../stories/AdminTable.stories.js", import.meta.url), "utf8")
  .replace('import * as Table from "../static/platform/admin-table.mjs";', 'import * as Table from "/static/platform/admin-table.mjs";')
  .replace('import "../static/root/admin-direct.css";\n', "");

async function ownershipOutcome(page, modulePath) {
  return page.evaluate(async (path) => {
    let releasePreferences;
    const preferencesGate = new Promise((resolve) => { releasePreferences = resolve; });
    window.sanaImportStatic = (name) => {
      if (name !== "platform/admin-preferences.mjs") throw new Error("unexpected module: " + name);
      return preferencesGate.then(() => import("/static/platform/admin-preferences.mjs"));
    };
    const Table = await import(path);
    const main = document.querySelector("#main");
    const sibling = document.querySelector("#sibling");
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const generic = main._sanaAnchos;
    const explicit = Table.crear({
      clave: "explicit-main",
      almacen: "ownership-main",
      selector: "#main",
      tablas: () => [main],
      medio: () => document.querySelector("#main-wrap"),
      cols: () => [
        { key: "patient", min: 80, max: 500, flex: 1 },
        { key: "service", min: 80, max: 500, flex: 1 },
        { key: "status", min: 80, max: 500, flex: 1 },
      ],
      fill: false,
      apilado: () => false,
    });
    explicit.autoFit();
    explicit._mover(0, 260); // edit made before preference hydration finishes
    await fetch("/test/early-explicit-edit", { method: "POST" });
    releasePreferences();
    // The delayed preferences GET has been applied once load() settles.
    await (await import("/static/platform/admin-preferences.mjs")).load();
    // Simulate the late generic preference callback; it must never retake the
    // explicitly owned table after the consumer has reserved it.
    generic.programarFit();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

    const mainFirst = main.querySelector("col")?.style.width || "";
    const ownsMain = main._sanaAnchos === explicit && mainFirst === "260px";
    const siblingOwner = sibling._sanaAnchos;
    const beforeSiblingMove = main.querySelector("col")?.style.width || "";
    siblingOwner._mover(0, 340);
    const siblingWorksIndependently = sibling.querySelector("col")?.style.width === "340px"
      && main.querySelector("col")?.style.width === beforeSiblingMove;
    return {
      genericWasInstalled: !!generic,
      ownsMain,
      siblingWorksIndependently,
      distinctOwners: siblingOwner !== explicit,
    };
  }, modulePath);
}

test("explicit table ownership survives generic sweep, delayed preferences, and a same-key sibling", async ({ page, serve, cleanup }) => {
  const getGate = holdGate(cleanup);
  const earlyEdit = holdGate(cleanup);
  const origin = await serve(async (req, res) => {
    if (req.url === "/") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(`<!doctype html><section id="tables"><div id="main-wrap" style="width:900px"><table id="main"><thead><tr><th>Patient</th><th>Service</th><th>Status</th></tr></thead><tbody><tr><td>Ana</td><td>Clinic</td><td>Ready</td></tr></tbody></table></div><div id="sibling-wrap" style="width:900px"><table id="sibling"><thead><tr><th>Patient</th><th>Service</th><th>Status</th></tr></thead><tbody><tr><td>Bruno</td><td>Clinic</td><td>Ready</td></tr></tbody></table></div></section>`);
    } else if (req.url === "/static/platform/admin-table.mjs") {
      res.writeHead(200, { "Content-Type": "text/javascript" }); res.end(tableSource);
    } else if (req.url === "/static/platform/admin-preferences.mjs") {
      res.writeHead(200, { "Content-Type": "text/javascript" }); res.end(preferencesSource);
    } else if (req.url === "/static/platform/i18n.mjs") {
      res.writeHead(200, { "Content-Type": "text/javascript" }); res.end(i18nSource);
    } else if (req.url === "/stories/AdminTable.stories.js") {
      res.writeHead(200, { "Content-Type": "text/javascript" }); res.end(storySource);
    } else if (req.url === "/api/admin/preferences" && req.method === "GET") {
      await getGate.held;
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ items: { "table.widths.ownership-main": { patient: 310 } } }));
    } else if (req.url === "/test/early-explicit-edit" && req.method === "POST") {
      earlyEdit.arrive();
      res.writeHead(204).end();
    } else if (req.url === "/api/admin/preferences" && req.method === "PUT") {
      for await (const _chunk of req) { /* consume real preference writes */ }
      res.writeHead(204).end();
    } else res.writeHead(404).end();
  });
  await page.goto(origin.url + "/");
  const corrected = ownershipOutcome(page, "/static/platform/admin-table.mjs");
  // Do not open the real GET until the page has reported its early explicit
  // edit, so this is a genuine delayed-preference race rather than timing.
  await earlyEdit.arrived;
  getGate.release();
  const outcome = await corrected;
  expect(outcome).toEqual({
    genericWasInstalled: true,
    ownsMain: true,
    siblingWorksIndependently: true,
    distinctOwners: true,
  });
  const disposal = await page.evaluate(async () => {
    const Table = await import("/static/platform/admin-table.mjs");
    const main = document.querySelector("#main");
    const old = main._sanaAnchos;
    const sibling = document.querySelector("#sibling")._sanaAnchos;
    const handle = main.querySelector("[data-tbl-res]");
    handle.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 100 }));
    const draggingBefore = document.body.classList.contains("tbl-resizing");
    old.programarFit();
    old.dispose(); old.dispose();
    const released = !main._sanaAnchos && !document.body.classList.contains("tbl-resizing");
    const fresh = Table.crear({ clave: "fresh-main", selector: "#main", tablas: () => [main],
      apilado: () => false, cols: () => [
        { key: "patient", min: 80, max: 500, flex: 1 },
        { key: "service", min: 80, max: 500, flex: 1 },
        { key: "status", min: 80, max: 500, flex: 1 },
      ] });
    fresh.autoFit(); fresh._mover(0, 245);
    old._mover(0, 490); old.autoFit(); old.programarFit();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const freshUsable = main._sanaAnchos === fresh && main.querySelector("col").style.width === "245px";
    const siblingPreserved = document.querySelector("#sibling")._sanaAnchos === sibling;
    fresh.dispose();
    return { draggingBefore, released, freshUsable, siblingPreserved };
  });
  expect(disposal).toEqual({ draggingBefore: true, released: true, freshUsable: true, siblingPreserved: true });
  const storyPage = await page.context().newPage();
  await storyPage.goto(origin.url + "/");
  const story = await storyPage.evaluate(async () => {
    const mod = await import("/stories/AdminTable.stories.js");
    const rendered = mod.Compacta();
    document.body.appendChild(rendered);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return {
      hasFallbackGlobal: typeof window.sanaImportStatic !== "undefined",
      table: !!rendered.querySelector("table.cnt-table"),
      resizeHandle: !!rendered.querySelector("[data-tbl-res]"),
    };
  });
  expect(story).toEqual({ hasFallbackGlobal: false, table: true, resizeHandle: true });
  await storyPage.close();
});
