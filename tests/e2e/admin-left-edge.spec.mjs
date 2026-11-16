// ONE left edge on every staff-panel screen (the product owner 05.09, 15.09, 16.10: «one left edge»,
// «you generate proper interfaces from the very start»). On every screen, at desktop 1440 and
// phone 390, the title text, the first toolbar control and the first content (first table cell,
// the empty state, or the first line of text) start on the same x, within 1px.
//
// What "starts" means is what the eye reads as the edge: a control's box when it has a border or
// a fill, its text when it has neither; a text's own glyphs unless an ancestor below the content
// wrap paints a box (border, fill, shadow) — then that box is the edge, and the inset inside it is
// the box's own business. A control with its label to its left (label-left-of-field forms, the
// settled form-design rule) is not a toolbar control and is skipped.
//
// The gutter itself is ONE token, --adm-gutter in admin-direct.css.
// Screenshots of a few screens at both widths go to the artifacts tree as review evidence.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { test, expect, loginAdmin, artifactPath, setTheme } from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_left_edge", requests: 7, queue: true,
  doctorPatients: true, hsi: true } });
// Each test reads its own screens; every failure is a measured misalignment, a retry measures the same.
test.describe.configure({ retries: 0 });

const OUT = artifactPath("pinamar-turnos", "progressive-disclosure", "round2");
const STAGE = "after";
const WIDTHS = [[1440, 900], [390, 844]];
const SHOT = new Set(["Solicitudes", "Pacientes", "Médicos", "Cómo prepararse", "Roles y permisos", "HSI"]);

const ADMIN_SCREENS = [
  ["Solicitudes", "/admin/solicitudes"],
  ["Mis pacientes", (app) => `/admin/my-patients?doctor=${app.fixture.doctor_patients.juan_id}`],
  ["Pacientes", "/admin/pacientes"],
  ["Médicos", "/admin/doctors"],
  ["Servicios", "/admin/services"],
  ["Centros", "/admin/centers"],
  ["Cómo prepararse", "/admin/preparations"],
  ["Wiki", "/admin/knowledge"],
  ["Instrucciones de la IA", "/admin/prompts"],
  ["Chats", "/admin/chats"],
  ["Reportes", "/admin/reportes"],
  ["Apariencia y acceso", "/admin/appearance"],
  ["Datos e idiomas", "/admin/settings"],
  ["Términos y privacidad", "/admin/legal"],
  ["Captura de síntomas", "/admin/entrevistas"],
  ["Estado", "/admin/system"],
  ["Usuarios", "/admin/users"],
  ["Roles y permisos", "/admin/roles"],
  ["Registro de IA", "/admin/ai-register"],
  ["Cuenta", "/admin/account"],
  ["HSI", "/admin/hsi"],
];
// Screens measured and reported but not asserted (none at the moment).
const MEASURED_ONLY = new Set([]);

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  let previous;
  await expect.poll(async () => {
    const current = await page.evaluate(() => document.querySelector(".adm5-main")?.innerHTML.length || 0);
    const stable = current === previous;
    previous = current;
    return stable;
  }, { timeout: 6000, intervals: [200, 300, 500] }).toBe(true).catch(() => {});
}

// Runs in the page: the three edges of the current screen.
function measure() {
  const shown = (el) => {
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === "none" || cs.visibility === "hidden" || e.hidden) return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  const alpha = (color) => {
    if (!color || color === "transparent") return 0;
    const m = color.match(/rgba?\(([^)]+)\)/);
    if (!m) return 1;
    const parts = m[1].split(/[ ,/]+/).filter(Boolean);
    return parts.length > 3 ? parseFloat(parts[3]) : 1;
  };
  const wrap = [...document.querySelectorAll(".adm-content-wrap")].find(shown);
  if (!wrap) return { error: "no content wrap" };
  let pageBg = "rgba(0, 0, 0, 0)";
  for (let e = wrap; e; e = e.parentElement) {
    const bg = getComputedStyle(e).backgroundColor;
    if (alpha(bg) > 0) { pageBg = bg; break; }
  }
  // A box running the full width of the screen column (a full-bleed phone row or card) has the
  // screen's edge, not a content edge: its inset text is what lines up.
  const column = (document.querySelector(".adm5-main") || document.body).getBoundingClientRect();
  const fullBleed = (e) => {
    const r = e.getBoundingClientRect();
    return r.left <= column.left + 1 && r.right >= column.right - 1;
  };
  const paints = (e) => {
    if (fullBleed(e)) return false;
    const cs = getComputedStyle(e);
    const border = parseFloat(cs.borderLeftWidth) > 0 && cs.borderLeftStyle !== "none" && alpha(cs.borderLeftColor) > 0;
    const fill = alpha(cs.backgroundColor) > 0 && cs.backgroundColor !== pageBg;
    return border || fill || cs.boxShadow !== "none";
  };
  const label = (el) => {
    const bits = [];
    for (let e = el, i = 0; e && e !== wrap && i < 3; e = e.parentElement, i++) {
      bits.push(e.tagName.toLowerCase() + (e.id ? "#" + e.id : "")
        + (typeof e.className === "string" && e.className.trim() ? "." + e.className.trim().split(/\s+/).slice(0, 2).join(".") : ""));
    }
    return bits.join(" < ");
  };
  // Where an off edge comes from: each ancestor up to the wrap with its left, padding and margin.
  const trail = (el) => {
    const bits = [];
    for (let e = el; e && e !== wrap.parentElement; e = e.parentElement) {
      const cs = getComputedStyle(e);
      bits.push(`${e.tagName.toLowerCase()}${e.id ? "#" + e.id : ""}${typeof e.className === "string" && e.className.trim() ? "." + e.className.trim().split(/\s+/)[0] : ""}`
        + `@${Math.round(e.getBoundingClientRect().left)} p${parseFloat(cs.paddingLeft)} m${parseFloat(cs.marginLeft)}`);
    }
    return bits.join(" < ");
  };
  // The outermost painted ancestor between `el` and the wrap is the edge; else `x`.
  const edge = (el, x) => {
    let left = x;
    for (let e = el; e && e !== wrap && e !== document.body; e = e.parentElement) {
      if (paints(e)) left = e.getBoundingClientRect().left;
    }
    return Math.round(left * 10) / 10;
  };
  const textStart = (root, skip) => {
    if (!root) return null;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.nodeType === 1) {
        const tag = n.tagName.toUpperCase();
        // An icon drawn as a mask over an empty element (the wiki tree's arrow) is a mark too.
        const mask = getComputedStyle(n).maskImage || getComputedStyle(n).webkitMaskImage || "none";
        const drawn = mask !== "none" && !n.firstElementChild && !n.textContent.trim();
        const mark = ["IMG", "SVG", "CANVAS", "VIDEO"].includes(tag) || (tag === "INPUT" && ["checkbox", "radio"].includes(n.type)) || drawn;
        if (mark && shown(n) && !(skip && skip(n))) {
          return { el: n, x: n.getBoundingClientRect().left };
        }
        continue;
      }
      const el = n.parentElement;
      if (!n.textContent.trim() || !el || ["SCRIPT", "STYLE", "OPTION", "TEMPLATE", "NOSCRIPT"].includes(el.tagName) || !shown(el)) continue;
      if (skip && skip(el)) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      const rect = [...range.getClientRects()].find((r) => r.width > 0);
      if (!rect) continue;
      // A drawn marker before the text (a triage dot, a bullet) is where the line starts.
      const before = getComputedStyle(el, "::before");
      if (before.content && before.content !== "none" && before.content !== "normal" && before.display !== "none"
        && n === el.firstChild) {
        const cs = getComputedStyle(el);
        return { el, x: el.getBoundingClientRect().left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft) };
      }
      return { el, x: rect.left };
    }
    return null;
  };
  const out = {};
  const h1 = document.getElementById("adm-h1");
  if (h1 && shown(h1)) {
    const t = textStart(h1);
    if (t) out.title = { left: Math.round(t.x * 10) / 10, at: "#adm-h1" };
  }
  const CONTROL = "input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea,button,a.btn";
  const labelled = (c) => {
    if (c.closest("label, table, thead, tbody, [role=row]")) return true;
    const own = c.id && document.querySelector(`label[for="${CSS.escape(c.id)}"]`);
    if (own && shown(own) && own.getBoundingClientRect().right <= c.getBoundingClientRect().left + 1) return true;
    for (let s = c.previousElementSibling; s; s = s.previousElementSibling) {
      if (shown(s) && s.getBoundingClientRect().right <= c.getBoundingClientRect().left + 1
        && Math.abs(s.getBoundingClientRect().top - c.getBoundingClientRect().top) < 24) return true;
    }
    return false;
  };
  // A control set against the right side (a «Nuevo …» button, the column gear) is not on the
  // left edge by design; the first control in the left half of the content is.
  const middle = wrap.getBoundingClientRect().left + wrap.getBoundingClientRect().width / 2;
  const control = [...wrap.querySelectorAll(CONTROL)]
    .find((c) => shown(c) && !labelled(c) && c.getBoundingClientRect().left < middle);
  if (control) {
    const r = control.getBoundingClientRect();
    const cs = getComputedStyle(control);
    // A frameless control drawn with an icon before its words (the lists' search and its
    // magnifier) starts where the icon starts: the icon is what the eye reads as its edge.
    const icon = cs.backgroundImage !== "none" && /px$/.test(cs.backgroundPositionX)
      ? r.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.backgroundPositionX) : null;
    const x = paints(control) ? r.left
      : icon !== null ? icon : r.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
    out.control = { left: edge(control.parentElement, x), at: label(control), trail: trail(control) };
  }
  // A list's rows are its content; the line of column names above them (a table's thead, the
  // Solicitudes grid's `.sol-cols`) is not: its first slot is empty over a checkbox column.
  const cell = [...wrap.querySelectorAll("table tbody td, table tbody th, .sol-row > *")].find(shown);
  const empty = [...wrap.querySelectorAll(".adm-empty, [data-empty-state]")].find(shown);
  // The toolbar line (the first control's row) is measured as the control; content starts below it.
  const bar = control && control.getBoundingClientRect();
  const onToolbarLine = (el) => {
    if (!bar) return false;
    const r = el.getBoundingClientRect();
    return r.top < bar.bottom && r.bottom > bar.top;
  };
  let content = null;
  if (cell) {
    const row = cell.closest("tr, .sol-row");
    const first = [...row.children].find(shown) || cell;
    const t = textStart(first) || { el: first, x: first.getBoundingClientRect().left };
    // A zebra-striped table is a painted box: its rows are the edge, their text is inset inside them.
    const striped = row.tagName === "TR" && [...row.parentElement.children].some((r) => shown(r) && paints(r));
    const left = striped ? Math.round(row.getBoundingClientRect().left * 10) / 10 : edge(t.el, t.x);
    content = { left, at: (striped ? "striped row " : "first cell ") + label(t.el), trail: trail(t.el) };
  } else if (empty) {
    const t = textStart(empty);
    if (t) content = { left: edge(t.el, t.x), at: "empty " + label(t.el), trail: trail(t.el) };
  } else {
    const t = textStart(wrap, onToolbarLine);
    if (t) content = { left: edge(t.el, t.x), at: "text " + label(t.el), trail: trail(t.el) };
  }
  if (content) out.content = content;
  return out;
}

// The measurements of every screen, kept across tests as review evidence. One file per worker:
// two workers rewriting one file read each other's half-written JSON (maintainer's CI, 4 workers).
function record(key, edges) {
  mkdirSync(OUT, { recursive: true });
  const file = `${OUT}/left-edge-${STAGE}-w${test.info().parallelIndex}.json`;
  const all = existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : {};
  all[key] = edges;
  writeFileSync(file, JSON.stringify(all, null, 1));
}

function check(name, width, edges) {
  record(`${width} ${name}`, edges);
  const probes = ["title", "control", "content"].filter((k) => edges[k]);
  const lefts = probes.map((k) => edges[k].left);
  const spread = lefts.length ? Math.max(...lefts) - Math.min(...lefts) : 0;
  const detail = probes.map((k) => `${k}=${edges[k].left} (${edges[k].at})`).join("; ");
  if (MEASURED_ONLY.has(name)) {
    test.info().annotations.push({ type: "measured-only", description: `${name} @${width}: spread ${spread}px — ${detail}` });
    return;
  }
  expect.soft(probes.length, `${name} @${width}: at least two edges to compare — ${detail || edges.error}`).toBeGreaterThanOrEqual(2);
  expect.soft(spread, `${name} @${width}: one left edge — ${detail}`).toBeLessThanOrEqual(1);
}

async function visit(page, name, href) {
  mkdirSync(OUT, { recursive: true });
  for (const [width, height] of WIDTHS) {
    await page.setViewportSize({ width, height });
    const resp = await page.goto(href, { waitUntil: "load" });
    expect(resp.status(), `${name} ${href}`).toBe(200);
    await settle(page);
    check(name, width, await page.evaluate(measure));
    if (SHOT.has(name)) {
      const slug = name.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z]+/g, "-");
      await page.screenshot({ path: `${OUT}/left-edge-${slug}-${width}-${STAGE}.png` });
    }
  }
}


test("admin screens: title, first toolbar control and first content share one left edge at 1440 and 390", async ({ page, app }) => {
  test.setTimeout(300_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginAdmin(page, app.baseUrl);
  for (const [name, href] of ADMIN_SCREENS) await visit(page, name, typeof href === "function" ? href(app) : href);
  await visit(page, "Pacientes de hoy", "/admin/cola");
});

// «Pacientes de hoy» on a phone: the day's patients, of every doctor or of the chosen one, run full bleed
// (their separators span the screen) but their text starts on the search's magnifier, like the
// chosen doctor's heading above them. The search has no frame at rest: its magnifier is its edge,
// and the frame it shows on hover opens outside that edge.
test("Pacientes de hoy at 390: patient rows and the chosen doctor's heading start on the search's magnifier", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAdmin(page, app.baseUrl);
  await page.goto("/admin/cola", { waitUntil: "load" });
  const search = page.locator("#sec-cola .queue-controls .adm-search").first();
  const magnifierX = () => search.evaluate((el) => {
    const cs = getComputedStyle(el);
    return Math.round(el.getBoundingClientRect().left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.backgroundPositionX));
  });
  const firstCellX = async () => {
    const cell = page.locator("#sec-cola .queue-table tbody tr[data-cola-row] td:not(.queue-td-empty)").first();
    await expect(cell).toBeVisible();
    return cell.evaluate((td) => {
      const r = document.createRange();
      r.selectNodeContents(td);
      return Math.round(r.getBoundingClientRect().x);
    });
  };
  await expect(search).toBeVisible();
  await stableBox(search, "search box");
  const edge = await magnifierX();
  expect(edge, "the magnifier is inset from the screen edge").toBeGreaterThan(0);
  expect(await firstCellX(), "first patient row of the whole day").toBe(edge);
  // Hover lights the frame up outside the edge: the magnifier and the words stay put.
  const textX = () => search.evaluate((el) => {
    const cs = getComputedStyle(el);
    return Math.round(el.getBoundingClientRect().left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft));
  });
  const wordsAtRest = await textX();
  await search.hover();
  await expect.poll(() => search.evaluate((el) => getComputedStyle(el).borderLeftColor)).not.toBe("rgba(0, 0, 0, 0)");
  expect(await magnifierX(), "the magnifier on hover").toBe(edge);
  expect(await textX(), "the words on hover").toBe(wordsAtRest);
  // stableBox, not a bare boundingBox(): the queue re-renders the toolbar under the pointer and the
  // box is null for the frame where the search is detached (the flake «reading 'x'» of null).
  expect(Math.round((await stableBox(search, "search box on hover")).x), "the hover frame opens outside the edge").toBeLessThan(edge);
  await page.mouse.move(0, 0);
  // The doctor is a link inside its row: choosing it narrows the same list.
  const doctor = page.locator("#sec-cola button.queue-doctor-link:not([data-cola-doctor='_sin_medico'])").first();
  // click() scrolls the link in itself and retries when the list re-renders under it.
  await doctor.click();
  const head = page.locator("#sec-cola .queue-doctor-head h2");
  await expect(head).toBeVisible();
  expect(Math.round((await stableBox(head, "chosen doctor's heading")).x), "chosen doctor's heading").toBe(edge);
  expect(await firstCellX(), "first patient row of the chosen doctor").toBe(edge);
  // The choice is kept in the account: leave the next test the whole day.
  await page.locator("#sec-cola .queue-doctor-all").click();
  await expect(head).toHaveCount(0);
});

// Médicos on a phone stacks each doctor into a card: name, then «specialty · center · hours · day»,
// then whatever wraps («52% · Sin cuenta»). EVERY line of the card starts on the same x as the
// specialty — the old five-square occupancy box (92px, right-aligned) pushed its line ~58px in
// (ui-expert 2026-11-15). Measured by glyphs per visual line, not by markup order.
test("Médicos at 390: every line of a doctor's card starts on one left edge", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAdmin(page, app.baseUrl);
  await page.goto("/admin/doctors", { waitUntil: "load" });
  const rows = page.locator("#sec-medicos .cnt-table--medicos tbody tr");
  await expect(rows.first()).toBeVisible();
  await settle(page);
  const cards = await rows.evaluateAll((trs) => trs.slice(0, 6).map((tr) => {
    const lines = new Map();
    for (const td of tr.querySelectorAll("td[data-col]")) {
      if (getComputedStyle(td).display === "none") continue;
      const r = document.createRange();
      r.selectNodeContents(td);
      for (const rect of r.getClientRects()) {
        if (rect.width < 1 || rect.height < 1) continue;
        const top = Math.round(rect.top / 4);
        const left = Math.round(rect.left);
        lines.set(top, Math.min(lines.has(top) ? lines.get(top) : Infinity, left));
      }
    }
    // The name line carries the checkbox before it; the lines under it are the card's body.
    const tops = [...lines.keys()].sort((a, b) => a - b);
    return { text: tr.innerText.replace(/\s+/g, " ").slice(0, 80), lefts: tops.slice(1).map((t) => lines.get(t)) };
  }));
  expect(cards.length, "doctor cards on the phone").toBeGreaterThan(0);
  for (const card of cards) {
    if (card.lefts.length < 1) continue;
    const spread = Math.max(...card.lefts) - Math.min(...card.lefts);
    expect.soft(spread, `${card.text}: lines start at ${card.lefts.join(", ")}`).toBeLessThanOrEqual(1);
  }
});

test("Hoja de ruta (Pinamar theme only) shares the same left edge", async ({ page, app }) => {
  test.setTimeout(120_000);
  await loginAdmin(page, app.baseUrl);
  await setTheme(app.dbName, "pinamar");
  try {
    await expect.poll(async () => (await page.request.get("/admin/plan")).status(), { timeout: 15_000 }).toBe(200);
    await visit(page, "Hoja de ruta", "/admin/plan");
  } finally {
    await setTheme(app.dbName, "humanrounds");
  }
});

test("Mis turnos (the doctor's own agenda) shares the same left edge", async ({ page, app }) => {
  test.setTimeout(120_000);
  const juan = app.fixture.doctor_patients;
  // The doctor lands where their account sends them (agenda or onboarding), then opens the agenda.
  await page.goto(app.baseUrl + "/admin/login");
  await page.fill("#gate-user", juan.medico_user);
  await page.fill("#gate-pass", juan.medico_pass);
  await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login");
  await visit(page, "Mis turnos", "/admin/mis-turnos");
});

// Mis pacientes on a phone: the contact icons open their own line of the card. The first glyph,
// not its 44px tap box, stands on the name's edge, and the box's left part still takes the tap
// (the cell does not clip what steps back past it).
test("Mis pacientes at 390: the first contact glyph stands on the name's edge with its whole tap box", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAdmin(page, app.baseUrl);
  await page.goto(`/admin/my-patients?doctor=${app.fixture.doctor_patients.juan_id}`, { waitUntil: "load" });
  const link = page.locator("#sec-mis-pacientes td[data-col='contacto'] > .cnt-ico-link:first-child").first();
  await expect(link).toBeVisible();
  const m = await link.evaluate((a) => {
    const row = a.closest("tr");
    const name = row.querySelector(".dp-pac-nombre");
    const r = document.createRange();
    r.selectNodeContents(name);
    const box = a.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + 2, box.top + box.height / 2);
    const de = document.scrollingElement;
    return {
      glyph: Math.round(a.querySelector(".cnt-ico").getBoundingClientRect().left),
      name: Math.round(r.getBoundingClientRect().left),
      boxW: Math.round(box.width),
      leftTaps: !!hit && (hit === a || a.contains(hit)),
      hscroll: de.scrollWidth > de.clientWidth,
    };
  });
  expect(m.glyph, "first contact glyph on the name's edge").toBe(m.name);
  expect(m.boxW, "the phone tap box").toBe(44);
  expect(m.leftTaps, "the tap box's left part is not clipped by the cell").toBe(true);
  expect(m.hscroll, "no horizontal scroll").toBe(false);
});

test("Usabilidad (study installations) shares the same left edge", async ({ launchApp, browser }) => {
  test.setTimeout(180_000);
  const app = await launchApp({ dbName: "sana_turnos_test_e2e_admin_left_edge_study",
    env: { TURNOS_STUDY: "1", TURNOS_DEMO_MODE: "1" } });
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await loginAdmin(page, app.baseUrl);
    await visit(page, "Usabilidad", "/admin/usability");
  } finally {
    await context.close();
  }
});
