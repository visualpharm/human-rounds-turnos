// The public catalog listings (/doctors, /centers, center and area pages) keep their height while the
// page's scripts and data arrive late. The server paints a plain list of links inside a slot
// that reserves the drawn table's height (.lst-slot:empty / [data-tiles]:not(:has(.tile)) in
// site_shell.py); listing.mjs empties that slot on mount and, when the list has profile
// filters (audience/sex), waits for /api/account/me before its first draw. The new slot used
// to lose the reserve, so for that whole wait the page collapsed and the footer jumped into
// view and back (CLS 0.26 at 1280, 0.37 at 390 on the demo, 2026-11-10). The centers mosaic
// reserved a 168px desktop row for a ~430px one (CLS 0.18 at 1280).
//
// Deterministic: the listing/tiles module and every /api/ response are held by gates; the
// layout-shift list must stay empty while the page sits on the server markup, after the
// module mounts with the data still held, and after the data lands and the table is drawn.
import { test, expect, holdsFor, holdGate, note } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_catalog_listing_shift" } });

// There is no /services page: the services lists live on an area page (/d/<area>, services +
// doctors) and on a center page (/center/<slug>, doctors + services). Their first instance is
// discovered from the server markup of /centers and the home page.
const DRAWN = "[data-listado] .lst-body, [data-listado] .lst-empty, [data-tiles] .tile";
const PAGES = [
  { path: "/doctors" },
  { path: "/centers" },
  { find: { from: "/centers", prefix: "/center/" } },
  { find: { from: "/", prefix: "/d/" } },
];

async function resolvePath(request, base, target) {
  if (target.path) return target.path;
  const { from, prefix } = target.find;
  const html = await (await request.get(base + from)).text();
  const hrefs = [...new Set(Array.from(html.matchAll(/href="([^"?#]+)"/g), (m) => m[1]))]
    .filter((href) => href.startsWith(prefix));
  for (const href of hrefs) {
    const page = await (await request.get(base + href)).text();
    if (page.includes("data-listado")) return href;
  }
  throw new Error(`no ${prefix}* page with a listing linked from ${from}`);
}
// 390 and 1280 as asked; 640 (one 4:3 column) and 820 (two columns) cover the
// mosaic's other two reserves.
const VIEWPORTS = [
  { width: 390, height: 844 },
  { width: 640, height: 900 },
  { width: 820, height: 1180 },
  { width: 1280, height: 900 },
];
const MODULE = /\/static\/platform\/(listing|tiles)\.[a-f0-9]{8}\.mjs$/;

for (const target of PAGES) {
  for (const viewport of VIEWPORTS) {
    const name = target.path || `${target.find.prefix}* (linked from ${target.find.from})`;
    test(`${name} keeps its height while the list and its data arrive late at ${viewport.width}px`, async ({ page, app, cleanup }, testInfo) => {
      const path = await resolvePath(page.request, app.baseUrl, target);
      const module = holdGate(cleanup);
      const data = holdGate(cleanup);
      let moduleAsked = false;
      await page.route(MODULE, async (route) => {
        moduleAsked = true;
        await module.held;
        await route.continue();
      });
      await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
        await data.held;
        await route.continue();
      });
      await page.setViewportSize(viewport);
      await page.addInitScript(() => {
        window.__catalogShifts = [];
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (entry.hadRecentInput) continue;
            window.__catalogShifts.push({
              value: +entry.value.toFixed(4),
              moved: Array.from(entry.sources || [], (source) => {
                const node = source.node;
                const name = node?.id ? `#${node.id}` : (node?.tagName || "#text").toLowerCase() +
                  (typeof node?.className === "string" && node.className ? "." + node.className.trim().split(/\s+/).join(".") : "");
                return `${name} y${Math.round(source.previousRect.top)}->${Math.round(source.currentRect.top)}`;
              }),
            });
          }
        }).observe({ type: "layout-shift", buffered: true });
      });
      const shifts = () => page.evaluate(() => window.__catalogShifts);
      const none = async (phase) => {
        await holdsFor(async () => {
          const list = await shifts();
          expect(list, `${phase}: the page moved: ${JSON.stringify(list)}`).toEqual([]);
        }, { during: 500 });
      };

      // A module script holds DOMContentLoaded, so the navigation waits only for the response.
      await page.goto(`${app.baseUrl}${path}`, { waitUntil: "commit", timeout: 30_000 });
      await expect(page.locator("footer.site")).toBeAttached({ timeout: 10_000 });
      await expect.poll(() => moduleAsked, { message: "the page loads a listing or tiles module" }).toBe(true);
      await none("server markup, scripts held");

      module.release();
      await page.waitForLoadState("load");
      await none("module mounted, data held");

      data.release();
      await expect(page.locator(DRAWN).first()).toBeAttached({ timeout: 15_000 });
      await none("data landed, list drawn");
      note(testInfo, `${path} ${viewport.width}px: no layout shift`);
    });
  }
}
