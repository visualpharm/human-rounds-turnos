// Browser acceptance for the physical tiles.mjs cutover. It covers every
// production surface that renders the shared tile component and proves the
// deleted classic script/global never participates.
//
// Port of tests/e2e/tiles-canonical.e2e.mjs (3 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName); there is no requireOk()/skip path. Each journey gets a fresh runner
//   page whose viewport is set with page.setViewportSize() before the first goto.
// - Evidence screenshots: capture() still writes to TURNOS_TILES_EVIDENCE_DIR only when that
//   variable is set (same file names, full page), now through `saveShot`, which also attaches
//   the picture to the report.
// - The wait for the first tile is web-first (`toBeVisible`, 10 s as before); the tile
//   count, the viewport-overflow check and the runtime contract (no global, module
//   requested, no failures) stay point-in-time reads.
// - Locators: `#centros .cmap-name` (the home's map of the centers, centers-map.mjs, which
//   the example pack carries since 2026-11-05; tiles.mjs still styles its rows of centers
//   without a point), `[data-tiles] .tile`, `[data-tiles] .tile--static` stay CSS -
//   the tile class names are the component under test.
import path from "node:path";
import { test, expect, saveShot } from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_tiles_canonical" } });

const EVIDENCE_DIR = process.env.TURNOS_TILES_EVIDENCE_DIR || "";

function observe(page) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => failures.push({
    pathname: new URL(request.url()).pathname,
    error: request.failure()?.errorText || "request failed",
  }));
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname;
    if (pathname === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) failures.push({ pathname, status: response.status() });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return { failures, requested };
}

async function assertCanonicalRuntime(page, seen) {
  expect(await page.evaluate(() => window.SanaTiles)).toBeUndefined();
  expect(seen.requested.some((pathname) => /\/static\/platform\/tiles\.[a-f0-9]{8}\.mjs$/.test(pathname)),
    "the browser must request the fingerprinted platform tiles module").toBe(true);
  expect(seen.requested.some((pathname) => pathname.endsWith("/tiles.js")),
    "the deleted tiles.js compatibility script must never be requested").toBe(false);
  expect(seen.failures).toEqual([]);
}

async function assertFits(page) {
  const result = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  expect(result.overflow, `page overflows ${result.viewport}px viewport by ${result.overflow}px`).toBeLessThanOrEqual(1);
}

async function capture(testInfo, page, name) {
  if (!EVIDENCE_DIR) return;
  await saveShot(testInfo, page, path.join(EVIDENCE_DIR, name), { fullPage: true });
}

const journeys = [
  { name: "home", url: "/", width: 390, selector: "#centros .cmap-name", shot: "home-centers-390.png" },
  { name: "centers", url: "/centers", width: 1280, selector: "[data-tiles] .tile", shot: "centers-1280.png" },
  { name: "workshops", url: "/d/grupos", width: 768, selector: "[data-tiles] .tile--static", shot: "workshops-768.png" },
];

for (const journey of journeys) {
  test(`${journey.name} renders through canonical tiles.mjs at ${journey.width}px`, async ({ page }, testInfo) => {
    const seen = observe(page);
    await page.setViewportSize({ width: journey.width, height: 900 });
    await page.goto(journey.url, { waitUntil: "load", timeout: 15_000 });
    await expect(page.locator(journey.selector).first()).toBeVisible({ timeout: 10_000 });
    expect(await page.locator(journey.selector).count()).toBeGreaterThan(0);
    await assertCanonicalRuntime(page, seen);
    await assertFits(page);
    await capture(testInfo, page, journey.shot);
  });
}

function apart(a, b) {
  return a.x + a.width <= b.x + 0.5 || b.x + b.width <= a.x + 0.5 || a.y + a.height <= b.y + 0.5 || b.y + b.height <= a.y + 0.5;
}

for (const width of [390, 1280]) {
  test(`home centers are a black-and-white map whose names open the route at ${width}px`, async ({ page }) => {
    const seen = observe(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/", { waitUntil: "load", timeout: 15_000 });
    await expect(page.locator("#centros .cmap-name").first()).toBeVisible({ timeout: 10_000 });
    // No photo tile on the home: the drawing is the pack's own file, served by us and loaded.
    expect(await page.locator("#centros .tile").count()).toBe(0);
    const img = page.locator("#centros .cmap-img");
    expect(await img.getAttribute("src")).toMatch(/^\/webdata\/centros-mapa\.svg\?v=[0-9a-f]{12}$/);
    await expect.poll(() => img.evaluate((el) => el.complete && el.naturalWidth > 0),
      { message: "the map drawing loads", timeout: 10_000 }).toBe(true);
    expect(seen.requested.some((p) => /\/static\/platform\/centers-map\.[a-f0-9]{8}\.mjs$/.test(p)),
      "the fingerprinted map module").toBe(true);
    // The OpenStreetMap credit the data's licence asks for.
    const credit = page.locator("#centros .cmap-attr");
    await expect(credit).toBeVisible();
    expect(await credit.getAttribute("href")).toBe("https://www.openstreetmap.org/copyright");
    expect((await credit.innerText()).trim()).toMatch(/OpenStreetMap/);

    // Every center with a point and a route is named on the map, once, as the link to its route.
    const centros = (await (await page.request.get("/api/centers")).json()).centros
      .filter((c) => typeof c.map_x === "number" && typeof c.map_y === "number" && c.directions_url);
    expect(centros.length).toBeGreaterThan(1);
    const names = page.locator("#centros .cmap-name");
    expect(await names.count()).toBe(centros.length);
    for (const c of centros) {
      const link = page.locator(`#centros .cmap-name[aria-label=${JSON.stringify(`Cómo llegar a ${c.name}`)}]`);
      expect(await link.count(), `${c.name} is named once`).toBe(1);
      expect(await link.getAttribute("title")).toBe(`Cómo llegar a ${c.name}`);
      expect(await link.getAttribute("href")).toMatch(/^https:\/\/www\.google\.com\/maps\/dir\/\?api=1&destination=/);
      expect(await link.getAttribute("target")).toBe("_blank");
    }
    // Large type for the eldest reader: 17 px for a center, 16 px for a service under
    // it, a service on one line (a break after its comma reads as two places).
    for (const n of await page.locator("#centros .cmap-name--main").all()) {
      expect(await n.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(17);
    }
    for (const sub of await page.locator("#centros .cmap-name--sub").all()) {
      expect((await sub.innerText()).trim()).not.toMatch(/^Servicio de|^-/i);
      const m = await sub.evaluate((el) => {
        const cs = getComputedStyle(el);
        return { size: parseFloat(cs.fontSize), line: parseFloat(cs.lineHeight), h: el.getBoundingClientRect().height };
      });
      expect(m.size).toBeGreaterThanOrEqual(16);
      expect(m.h, "a service under a center stays on one line").toBeLessThan(m.line * 1.5);
    }
    // Two places named at one dot keep a visible space between them.
    for (const label of await page.locator("#centros .cmap-place .cmap-label").all()) {
      const gaps = await label.evaluate((el) => {
        const g = [...el.querySelectorAll(":scope > .cmap-group")].map((x) => x.getBoundingClientRect());
        return g.slice(1).map((b, i) => b.top - g[i].bottom);
      });
      for (const gap of gaps) expect(gap, "places at one dot stand apart").toBeGreaterThanOrEqual(6);
    }
    // One dot per place, a 44 px target; every name inside the frame, clear of the
    // other names, of every dot and of the credit.
    const frame = await stableBox(page.locator("#centros .cmap"), "map frame");
    const places = await page.locator("#centros .cmap-place").all();
    const dots = [];
    for (const place of places) {
      const dot = place.locator(".cmap-dot");
      expect(await dot.count()).toBe(1);
      const d = await stableBox(dot, "place dot");
      expect(d.width).toBeGreaterThanOrEqual(44);
      expect(d.height).toBeGreaterThanOrEqual(44);
      const cx = d.x + d.width / 2, cy = d.y + d.height / 2;
      dots.push({ x: cx - 8, y: cy - 8, width: 16, height: 16 });
    }
    const labels = [];
    for (const label of await page.locator("#centros .cmap-place .cmap-label").all()) labels.push(await stableBox(label, "place name"));
    const attr = await stableBox(credit, "map credit");
    labels.forEach((b, i) => {
      expect(b.x >= frame.x - 0.5 && b.y >= frame.y - 0.5 && b.x + b.width <= frame.x + frame.width + 0.5
        && b.y + b.height <= frame.y + frame.height + 0.5, `name ${i} inside the frame`).toBe(true);
      expect(apart(b, attr), `name ${i} clear of the credit`).toBe(true);
      // 10 px from every other dot at least (its own is 6 px off).
      const wide = { x: b.x - 9, y: b.y - 9, width: b.width + 18, height: b.height + 18 };
      dots.forEach((d, j) => { if (j !== i) expect(apart(wide, d), `name ${i} clear of dot ${j}`).toBe(true); });
      labels.forEach((o, j) => { if (j > i) expect(apart(b, o), `names ${i} and ${j} apart`).toBe(true); });
    });
    await assertFits(page);
    expect(seen.failures).toEqual([]);
  });
}

// A crowded town on the example map (1 unit ≈ 7.7 m): the hospital with two
// services in its building, a clinic 40 m from another one (two addresses,
// too close for any zoom), two centers 300 m apart (never one dot).
function crowd(body) {
  const c = body.centros;
  const by = Object.fromEntries(c.map((x) => [x.slug, x]));
  const h = by["sampleton-hospital"], r = by["riverside-clinic"], d = by["diagnostic-center"];
  const route = (q) => "https://www.google.com/maps/dir/?api=1&destination=" + encodeURIComponent(q + ", Sampleton");
  const center = (o) => ({ phone: "", email: "", description: "", foto: "", bookable: false, locality: "", place_main: false, ...o });
  const at = (p, dx, dy, address) => ({ address, place: address.toLowerCase(), map_x: p.map_x + dx, map_y: p.map_y + dy, directions_url: route(address) });
  const building = { address: h.address, place: h.place, map_x: h.map_x, map_y: h.map_y, directions_url: h.directions_url };
  c.push(
    center({ slug: "blood-bank", name: "Blood Bank", ...building }),
    center({ slug: "oncology-day", name: "Oncology, Day Hospital", ...building }),
    center({ slug: "riverside-diabetes", name: "Riverside Diabetes Center", ...at(r, 0.004, 0.003, "Station St. 470") }),
    center({ slug: "integral-health", name: "Integral Health Center of Sampleton", ...at(d, 0.03, 0.025, "Central Dr. 600") }),
    center({ slug: "disability-day", name: "Disability Coordination and Day Center", ...at(d, 0.066, 0.03, "Central Dr. 900") }),
  );
  return body;
}

for (const [width, maxLine] of [[390, 36], [1440, 60]]) {
  test(`a crowded town at ${width}px: every name nearer its own dot than any other, a shared dot leads nowhere`, async ({ page }) => {
    const seen = observe(page);
    await page.route("**/api/centers", async (route) => {
      const res = await route.fetch();
      await route.fulfill({ response: res, json: crowd(await res.json()) });
    });
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/", { waitUntil: "load", timeout: 15_000 });
    await expect(page.locator("#centros .cmap-name").first()).toBeVisible({ timeout: 10_000 });
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    const m = await page.evaluate(() => {
      const R = 8;
      const gap = (r, p) => Math.max(0, Math.hypot(Math.max(r.left - p.x, 0, p.x - r.right), Math.max(r.top - p.y, 0, p.y - r.bottom)) - R);
      const frame = document.querySelector("#centros .cmap").getBoundingClientRect();
      const places = [...document.querySelectorAll("#centros .cmap-place")].map((li) => {
        const el = li.querySelector(".cmap-dot"), d = el.getBoundingClientRect();
        return {
          dot: { x: (d.left + d.right) / 2, y: (d.top + d.bottom) / 2 },
          href: el.tagName === "A" ? el.getAttribute("href") : null,
          groups: [...li.querySelectorAll(".cmap-group")].map((g) => [...g.querySelectorAll(".cmap-name")].map((n) => {
            const r = n.getBoundingClientRect();
            return { name: n.getAttribute("aria-label").replace(/^Cómo llegar a /, ""), href: n.getAttribute("href"),
              r: { left: r.left, top: r.top, right: r.right, bottom: r.bottom } };
          })),
        };
      });
      const ownership = [];
      places.forEach((pl, i) => pl.groups.forEach((names) => {
        const own = Math.min(...names.map((n) => gap(n.r, pl.dot)));
        names.forEach((n) => places.forEach((o, j) => {
          if (j !== i) ownership.push({ name: n.name, own, other: gap(n.r, o.dot), of: o.groups[0][0].name });
        }));
      }));
      const boxes = places.flatMap((p, place) => p.groups.flat().map((n) => ({ name: n.name, place, ...n.r })));
      return {
        frame: { left: frame.left, top: frame.top, right: frame.right, bottom: frame.bottom },
        places, ownership, boxes,
        lines: [...document.querySelectorAll("#centros .cmap-lines line")].map((l) =>
          Math.hypot(l.x2.baseVal.value - l.x1.baseVal.value, l.y2.baseVal.value - l.y1.baseVal.value)),
        rows: [...document.querySelectorAll("#centros .cmap-rest .cl-row")].map((row) =>
          [...row.querySelectorAll(".cl-name")].map((n) => n.textContent.trim())),
      };
    });
    // No name nearer another dot than its own (the product owner's hub, 2026-11-05): 10 px
    // clear of every other dot, and 8 px further from it than from its own.
    for (const o of m.ownership) {
      expect(o.other >= 10 && o.other >= o.own + 8,
        `${o.name}: ${o.other.toFixed(1)} px from the dot of ${o.of}, ${o.own.toFixed(1)} from its own`).toBe(true);
    }
    // Names inside the frame and apart; a line, where one is needed, stays short.
    for (const [i, b] of m.boxes.entries()) {
      expect(b.left >= m.frame.left - 0.5 && b.right <= m.frame.right + 0.5 && b.top >= m.frame.top - 0.5
        && b.bottom <= m.frame.bottom + 0.5, `${b.name} inside the frame`).toBe(true);
      for (const o of m.boxes.slice(i + 1)) {
        expect(b.right <= o.left + 0.5 || o.right <= b.left + 0.5 || b.bottom <= o.top + 0.5 || o.bottom <= b.top + 0.5,
          `${b.name} and ${o.name} apart`).toBe(true);
        // Two places' names read as two (ui-expert, 2026-11-06: one 6 px over
        // the other in one column read as one name): one above the other,
        // 20 px between them; side by side, 8 px.
        if (o.place === b.place) continue;
        const dx = Math.max(o.left - b.right, b.left - o.right, 0), dy = Math.max(o.top - b.bottom, b.top - o.bottom, 0);
        expect(!((dx < 4.5 && dy < 19.5) || (dy < 4.5 && dx < 7.5)),
          `${b.name} and ${o.name} far enough apart to read as two (${dx.toFixed(1)}, ${dy.toFixed(1)} px)`).toBe(true);
      }
    }
    for (const len of m.lines) expect(len, "a short line").toBeLessThanOrEqual(maxLine + 8);
    // A dot leads where all its names lead: one place, its route; two
    // addresses at one dot, no link at all (the names are the links).
    const placeOf = (name) => m.places.find((p) => p.groups.some((g) => g.some((n) => n.name === name)));
    for (const p of m.places) {
      const routes = [...new Set(p.groups.map((g) => g[0].href))];
      expect(p.href, `the dot of ${p.groups[0][0].name}`).toBe(routes.length === 1 ? routes[0] : null);
    }
    const river = placeOf("Riverside Clinic");
    expect(river, "the clinic 40 m away shares the dot").toBe(placeOf("Riverside Diabetes Center"));
    expect(river.href, "a dot of two addresses is not a link").toBe(null);
    const integral = placeOf("Integral Health Center of Sampleton"), disability = placeOf("Disability Coordination and Day Center");
    expect(integral || disability, "300 m apart: one of them on the map at least").toBeTruthy();
    if (integral && disability) expect(integral, "300 m apart: two dots").not.toBe(disability);
    // Every center with a point is named once: on the map, or — where its
    // name fits nowhere clean (on the phone the one between two others 35 px
    // away) — with its place in a row under the map. The building's services
    // stand under its center there or, with the center, in a row under the map.
    const onMap = m.places.flatMap((p) => p.groups.flat().map((n) => n.name));
    const inRows = m.rows.flat();
    const mains = ["Sampleton Hospital", "Riverside Clinic", "Riverside Diabetes Center", "Lakeview Clinic",
      "Maternity Center", "Diagnostic Center", "Integral Health Center of Sampleton", "Disability Coordination and Day Center"];
    for (const name of mains) {
      expect(onMap.filter((n) => n === name).length + inRows.filter((n) => n === name).length,
        `${name} once, on the map or under it`).toBe(1);
    }
    const off = mains.filter((name) => !onMap.includes(name));
    expect(off.length, `off the map: ${off.join(", ")}`).toBeLessThanOrEqual(width < 600 ? 1 : 0);
    const building = ["Sampleton Hospital", "Blood Bank", "Oncology, Day Hospital"];
    const hospital = placeOf("Sampleton Hospital");
    const under = hospital ? hospital.groups.find((g) => g[0].name === "Sampleton Hospital").map((n) => n.name) : [];
    if (under.length > 1) expect(under).toEqual(building);
    else expect(m.rows, "the building in one row under the map").toContainEqual(building);
    expect(onMap.filter((n) => n === "Blood Bank").length).toBe(under.length > 1 ? 1 : 0);
    await assertFits(page);
    expect(seen.failures).toEqual([]);
  });
}
