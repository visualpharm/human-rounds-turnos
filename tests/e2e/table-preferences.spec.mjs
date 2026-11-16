// The real admin-preferences module behind admin-table, on a stub origin: the import of the
// preferences module and its GET are both withheld while tables are created, resized and
// their columns edited; once released, saved widths/columns hydrate, local edits win over
// the late GET, hydration repaints once (no render loop) and the edits are PUT back.
//
// Port of tests/e2e/table-preferences.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The stub origin comes from the `serve` fixture and the page from the runner (the
//   original started its own http.Server and `chromium.launch`); viewport 1200x800 is
//   test.use(). The stub does not use the application: The file is `hermetic` (appOptions only names it for
//   tests/e2e-spec-isolation.test.mjs): no app or database is started for it.
// - The withheld preferences GET is a holdGate(cleanup) gate (released on cleanup too, so a
//   failing test never leaves the server hanging).
// - The two `waitForFunction`s are retrying `expect.poll`s on the same in-page conditions
//   (inline `col` width after hydration; cached widths/columns in the preferences module).
//   «The three preference PUTs arrive within 2 s» is `expect.poll` on the recorded PUT list
//   (same 2 s) instead of a Promise.race against a timer.
// - `TABLE_SOURCE_REF` (serve admin-table.mjs from another git ref to prove the test fails on
//   the old module) is kept.
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { test, expect, holdGate } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_table_preferences", hermetic: true },
  viewport: { width: 1200, height: 800 },
});

const tableSource = process.env.TABLE_SOURCE_REF
  ? execFileSync("git", ["show", `${process.env.TABLE_SOURCE_REF}:static/platform/admin-table.mjs`], { encoding: "utf8" })
  : readFileSync(new URL("../../static/platform/admin-table.mjs", import.meta.url), "utf8");
const preferencesSource = readFileSync(new URL("../../static/platform/admin-preferences.mjs", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../../static/platform/i18n.mjs", import.meta.url), "utf8");

test("real preferences module survives delayed import and GET", async ({ page, serve, cleanup }) => {
  const getGate = holdGate(cleanup);
  const puts = [];
  const origin = await serve(async (req, res) => {
    if (req.url === "/") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end('<main style="width:1000px"><div id="saved-wrap"><table id="saved"><thead><tr><th>Name</th><th>Status</th></tr></thead><tbody><tr><td>Patient</td><td>Ready</td></tr></tbody></table></div><div id="edited-wrap"><table id="edited"><thead><tr><th>Name</th><th>Status</th></tr></thead><tbody><tr><td>Patient</td><td>Ready</td></tr></tbody></table></div></main>');
    } else if (req.url === "/static/platform/admin-table.mjs") {
      res.writeHead(200, { "Content-Type": "text/javascript" }); res.end(tableSource);
    } else if (req.url === "/static/platform/admin-preferences.mjs") {
      res.writeHead(200, { "Content-Type": "text/javascript" }); res.end(preferencesSource);
    } else if (req.url === "/static/platform/i18n.mjs") {
      res.writeHead(200, { "Content-Type": "text/javascript" }); res.end(i18nSource);
    } else if (req.url === "/api/admin/preferences" && req.method === "GET") {
      await getGate.held;
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ items: {
        "table.widths.st_adm_anchos:saved": { name: 310 },
        "table.widths.st_adm_anchos:edited": { name: 340, status: 190 },
        "table.widths.st_adm_anchos:reset": { name: 330, status: 190 },
        "table.columns.saved": ["status"], "table.columns.edited": ["status"]
      } }));
    } else if (req.url === "/test/puts") {
      res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(puts));
    } else if (req.url === "/api/admin/preferences" && req.method === "PUT") {
      let body = ""; for await (const chunk of req) body += chunk;
      puts.push(JSON.parse(body));
      res.writeHead(204).end();
    } else res.writeHead(404).end();
  });
  await page.goto(origin.url + "/");
  await page.evaluate(() => {
    let releaseImport;
    const importGate = new Promise((resolve) => { releaseImport = resolve; });
    window.__releasePreferencesImport = releaseImport;
    window.sanaImportStatic = () => importGate.then(() => import("/static/platform/admin-preferences.mjs"));
  });
  await page.evaluate(async () => {
    const Table = await import("/static/platform/admin-table.mjs");
    const cols = [
      { key: "name", label: "Name", default: true, min: 80, max: 500, flex: 1 },
      { key: "status", label: "Status", default: true, min: 80, max: 300, flex: 1 }
    ];
    const widths = (key) => Table.crear({
      clave: key, tablas: () => [document.getElementById(key)], medio: () => document.getElementById(`${key}-wrap`),
      cols: () => cols, fill: false, apilado: () => false
    });
    const resetWrap = document.createElement("div"); resetWrap.id = "reset-wrap";
    resetWrap.innerHTML = document.getElementById("edited").outerHTML.replace('id="edited"', 'id="reset"');
    document.querySelector("main").appendChild(resetWrap);
    window.savedWidths = widths("saved"); window.editedWidths = widths("edited"); window.resetWidths = widths("reset");
    window.savedWidths.autoFit(); window.editedWidths.autoFit(); window.resetWidths.autoFit();
    window.hydrationPaints = 0;
    function renderSaved() {
      window.savedColumns = Table.columnas({ clave: "saved", cols, onChange() {
        window.hydrationPaints++;
        // Mirror admin-app's render -> columnas -> onChange -> render path,
        // bounded so broken code fails an assertion rather than hangs CI.
        if (window.hydrationPaints < 4) renderSaved();
      } });
    }
    renderSaved();
    window.editedColumns = Table.columnas({ clave: "edited", cols });
    window.editedWidths._mover(0, 260); window.editedWidths._soltar();
    window.resetWidths._auto(0);
    window.editedColumns._set(["name"]);
    window.__releasePreferencesImport();
  });
  getGate.release();
  await expect.poll(() => page.evaluate(() => document.querySelector("#saved col")?.style.width)).toBe("310px");
  await expect.poll(() => page.evaluate(async () => {
    const prefs = await import("/static/platform/admin-preferences.mjs");
    const widths = prefs.get("table.widths.st_adm_anchos:edited", null);
    return !!(widths && widths.name === 260 && widths.status === 190
      && prefs.get("table.columns.edited", null)?.[0] === "name");
  })).toBe(true);
  await expect.poll(() => puts.length >= 3, { timeout: 2000, message: "preference PUTs timed out" }).toBe(true);
  const state = await page.evaluate(async () => {
    const prefs = await import("/static/platform/admin-preferences.mjs");
    return {
      hydrationPaints: window.hydrationPaints,
      savedWidth: document.querySelector("#saved col").style.width,
      editedWidth: document.querySelector("#edited col").style.width,
      savedColumns: window.savedColumns.visibles(), editedColumns: window.editedColumns.visibles(),
      cachedWidth: prefs.get("table.widths.st_adm_anchos:edited", null),
      cachedColumns: prefs.get("table.columns.edited", null),
      cachedReset: prefs.get("table.widths.st_adm_anchos:reset", null)
    };
  });
  expect(state.savedWidth).toBe("310px"); expect(state.editedWidth).toBe("260px");
  expect(state.hydrationPaints, "hydration triggers one repaint, not a recursive render loop").toBe(1);
  expect(state.savedColumns).toEqual(["status"]); expect(state.editedColumns).toEqual(["name"]);
  expect(state.cachedWidth).toEqual({ name: 260, status: 190 }); expect(state.cachedColumns).toEqual(["name"]);
  expect(state.cachedReset, "reset column stays absent while untouched widths merge").toEqual({ status: 190 });
  expect(puts.some((put) => put.key === "table.widths.st_adm_anchos:edited" && put.value.name === 260 && put.value.status === 190), JSON.stringify(puts)).toBe(true);
  expect(puts.some((put) => put.key === "table.columns.edited" && put.value[0] === "name")).toBe(true);
});
