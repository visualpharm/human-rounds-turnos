// tests/e2e/doctor-queue-header.spec.mjs -- the header of the doctor's «Pacientes de hoy»
// (/admin/cola), as the product owner asked for it (the doctor-queue requirements).
//
// Product owner, 2026-10-26, on a screenshot of the live screen under a doctor: «I have said so much
// about this form, and still they did not redo it». What the screenshot showed and what
// each test below pins:
//   R1  the day controls are ONE row: the short date («Dom 8 nov») on the left, then
//       previous / «Hoy» / next and the month as a calendar icon, all of one height; «Hoy»
//       is written once (the product owner, 2026-11-08); no control drops to a second line, on the
//       desktop or on the phone;
//   R2  the search box shows its whole placeholder («Paciente o DNI» next to the magnifier);
//   R3  the AI notice («Contenido elaborado con inteligencia artificial…») shows only
//       where the screen carries AI content, next to it — never as a banner over an
//       empty day, never over the list: since 2026-11-09 it sits in the appointment
//       card, right above the text the AI wrote;
//   R4  «Captura de síntomas» is a real switch of the panel (the one of «Límites»), at body
//       size, not a small line with a browser-default button; flipping it survives a reload;
//   R5  «Horarios de atención» is not a stray link under the buttons: the doctor's
//       schedules are «Mis agendas» in the section navigation.
//
// Two doctors: Levin has a patient today (with a finished pre-visit interview, i.e. AI
// content in the row), Ruiz has nothing today. Each test's screenshots (390, 1024, 1280,
// 1440) land in its own output folder under test-results/.
import { test, expect, runFixture, signInStaff, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS } from "./lib/journey.mjs";
import { saveShot } from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const LEVIN = { id: "levin-camila", usuario: "e2e-dr-levin-cola", password: LOGIN_PHRASE };
const RUIZ = { id: "ruiz-sergio", usuario: "e2e-dr-ruiz-cola", password: LOGIN_PHRASE };
const AI_NOTICE = "Contenido elaborado con inteligencia artificial";
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_doctor_queue_header" },
  journey: {
    staff: [
      { usuario: LEVIN.usuario, password: LEVIN.password, rol: "medico", medico_id: LEVIN.id },
      { usuario: RUIZ.usuario, password: RUIZ.password, rol: "medico", medico_id: RUIZ.id },
    ],
  },
});
// Seeded once per running app (its own throwaway database), never once per module: a
// worker that starts a new app after a failure seeds that app again.
//
// The screen must not depend on the day or the hour of the run. The pack's Levin sees
// patients Monday to Friday, so on a weekend her day had no free hours and «Mostrar
// horarios libres» (shown only when the day has some) was missing: R1 at 1024/1280 and
// R11 went red every Saturday and Sunday (07.11). The seed gives her the same hours
// every day of the week, the way the panel saves an agenda (the hours of a day are not
// cut at the current time, so the hour of the run does not matter). And «today» is the
// installation's day: a run that crosses midnight ART seeds her visit on the new day too.
const ART = "America/Argentina/Buenos_Aires";
const DELHOY = { dni: "30555001", nombre: "Ana", apellido: "Delhoy", doctor_id: LEVIN.id, doctor_name: "Camila Levin",
  servicio_id: SERVICE_ID, centro: "sampleton-hospital" };
const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6];
const seededApps = new WeakMap(); // app -> the ART day its «today» visit was seeded for
const artDay = () => new Intl.DateTimeFormat("en-CA", { timeZone: ART }).format(new Date());

// Levin's hours are not her own: the service «Cardiología» owns them (Monday to Friday,
// 8 to 12), so it is the service whose agenda the admin opens to every day.
async function cardiologyEveryDay(role) {
  const admin = await role({ viewport: DESKTOP });
  try {
    await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
    const list = await admin.page.request.get("/api/admin/content/services");
    expect(list.ok(), await list.text()).toBe(true);
    const service = (await list.json()).items.find((s) => s.id === SERVICE_ID);
    expect(service?.agenda, "the pack's Cardiología owns an agenda").toBeTruthy();
    const agenda = service.agenda.franjas
      ? { ...service.agenda, franjas: service.agenda.franjas.map((f) => ({ ...f, dias: EVERY_DAY })) }
      : { ...service.agenda, dias: EVERY_DAY };
    const put = await admin.page.request.put(`/api/admin/content/services/${SERVICE_ID}`,
      { data: { ...service, agenda, confirmar_reinicio_agendas: true } });
    expect(put.status(), await put.text()).toBe(200);
  } finally {
    await admin.context.close();
  }
}

async function seedToday(app, role) {
  const today = artDay();
  if (seededApps.get(app) === today) return;
  if (!seededApps.has(app)) {
    await cardiologyEveryDay(role);
    await runFixture(app, "seed-visit", { spec: { ...DELHOY, when: "today", account: true } });
    // Her history for the attendance forecast («Historial»): came once, missed twice —
    // «Falta a veces», two amber bars of three (F1).
    for (const [when, asistencia] of [["-150", "presente"], ["-100", "ausente"], ["-50", "ausente"]]) {
      await runFixture(app, "seed-visit", { spec: { ...DELHOY, when, interview: false, asistencia } });
    }
  } else {
    // Midnight ART passed since the seed: her visit (with its interview) on the new day.
    await runFixture(app, "seed-visit", { spec: { ...DELHOY, when: "today" } });
  }
  seededApps.set(app, today);
}

// The AI notice is painted from the day's data: it is there when a row of the day carries
// its interview. The maintainer's CI (b36fec69, a container under load) twice painted Delhoy's row
// without it and never repainted, while every local run, cold or repeated, had it; the
// seed writes the interview and the doctor's switch from another process. So a test about
// WHERE the notice sits first proves the server serves the interview, then opens the page —
// and when the server does not, it fails saying so, with what the day did carry.
async function interviewServed(context) {
  await expect.poll(async () => {
    const res = await context.request.get("/api/admin/day-queue");
    if (!res.ok()) return `day-queue answered ${res.status()}`;
    const data = await res.json();
    const turnos = (data.grupos || []).flatMap((g) => g.turnos || []);
    if (turnos.some((t) => t.entrevista)) return "served";
    return JSON.stringify({ turnos: turnos.length, entrevista_config: data.entrevista_config ?? null });
  }, { message: "the doctor's day carries the seeded pre-visit interview", timeout: 15_000 }).toBe("served");
}

async function openQueue(role, who, viewport, { interview = false, columns = null } = {}) {
  const r = await role({ viewport });
  await signInStaff(r.page, who.usuario, who.password);
  // A desk that picked its own columns in the gear: saved before the screen opens.
  if (columns) {
    const put = await r.page.request.put("/api/admin/preferences", { data: { key: "day_queue.columns", value: columns } });
    expect(put.ok()).toBe(true);
  }
  if (interview) await interviewServed(r.context);
  await r.page.goto("/admin/cola", { waitUntil: "load" });
  await expect(r.page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12_000 });
  await expect(r.page.locator("#sec-cola")).not.toHaveAttribute("aria-busy", "true");
  // The day's reconciling load repaints the screen once: measure after it, not across it.
  await expect(r.page.locator("#cola-live")).toHaveText(/\S/, { timeout: 12_000 });
  return r;
}

/** Boxes of the day controls: the date, every button of the day navigation, the month view. */
async function dayControlBoxes(page) {
  return page.locator(".queue-date-picker").evaluate((picker) => {
    const sel = ".queue-date-label, .queue-day-seg button, .queue-month-btn";
    return [...picker.querySelectorAll(sel)].filter((el) => el.offsetParent).map((el) => {
      // A button of the day's segment is measured by the segment's outline: the frame
      // belongs to the segment, so the eye compares its 44 px with the month's (2026-11-09).
      const seg = el.closest(".queue-day-seg");
      const b = (seg || el).getBoundingClientRect();
      return { text: el.textContent.trim() || el.getAttribute("aria-label"), top: b.top, bottom: b.bottom, height: b.height, cls: el.className };
    });
  });
}

// Evidence of what the doctor sees, taken BEFORE the claims so a failing run still shows it.
async function shots(page, testInfo, name) {
  // The day row is repainted when the calendar arrives: shoot once its icons have loaded.
  await page.waitForFunction(() => [...document.querySelectorAll("#sec-cola img")].every((i) => i.complete && i.naturalWidth > 0));
  await saveShot(testInfo, page, testInfo.outputPath(`cola-${name}.png`), { fullPage: true });
}

for (const [label, viewport] of [["desktop 1440", DESKTOP], ["phone 390", PHONE]]) {
  test(`R1+R2 (${label}): one row of day controls of one height; the search shows its whole placeholder`, async ({ app, role }, testInfo) => {
    await seedToday(app, role);
    const { page, errors } = await openQueue(role, LEVIN, viewport, { interview: true });
    await expect(page.locator("#cola-results")).toContainText("Delhoy");
    await shots(page, testInfo, `con-turnos-${viewport.width}`);
    await expect(page.locator(".queue-day-hoy")).toHaveAttribute("aria-current", "date");
    await expect(page.locator(".queue-date-label"), "the date is the short date, not a second «Hoy»").not.toContainText("Hoy");
    expect(await page.locator("#sec-cola .queue-controls").evaluate((el) => (el.innerText.match(/\bHoy\b/g) || []).length),
      "«Hoy» once on the controls").toBe(1);

    // The day controls are repainted once the calendar of occupied days arrives
    // (paintDateNav): measure the settled row, not a node that is being replaced.
    let boxes = [];
    await expect.poll(async () => {
      boxes = await dayControlBoxes(page);
      return boxes.filter((b) => !/queue-date-label/.test(b.cls)).length;
    }, { message: "previous, today, next and the month view are segment buttons" }).toBeGreaterThanOrEqual(4);
    const buttons = boxes.filter((b) => !/queue-date-label/.test(b.cls));
    const heights = new Set(buttons.map((b) => Math.round(b.height)));
    expect([...heights], "every day-control button has the same height").toHaveLength(1);
    expect(buttons[0].height, "touch-sized").toBeGreaterThanOrEqual(40);
    const mids = buttons.map((b) => (b.top + b.bottom) / 2);
    expect(Math.max(...mids) - Math.min(...mids), `the day controls share one line: ${JSON.stringify(boxes)}`).toBeLessThanOrEqual(2);
    const date = boxes.find((b) => /queue-date-label/.test(b.cls));
    expect(Math.abs((date.top + date.bottom) / 2 - mids[0]), "the date sits on that same line").toBeLessThanOrEqual(2);
    if (viewport.width >= 1024) {
      for (const sel of ["#sec-cola .queue-search", "#sec-cola .cnt-gear-btn[data-cola-gear]"]) {
        const b = await stableBox(page.locator(sel), sel);
        expect(Math.abs(b.y + b.height / 2 - mids[0]), `${sel} is on the same line`).toBeLessThanOrEqual(2);
      }
      // «Ver mes» open: the month makes the row tall, the search and the column picker stay
      // on the day's line instead of sliding to the middle of the calendar (audit 2026-11-05).
      await page.locator("[data-cola-calendar-toggle]").click();
      await expect(page.locator(".queue-month-calendar")).toBeVisible();
      await expect.poll(async () => {
        const open = (await dayControlBoxes(page)).filter((b) => !/queue-date-label/.test(b.cls));
        if (!open.length) return "not painted yet";
        const mid = (open[0].top + open[0].bottom) / 2;
        const off = [];
        for (const sel of ["#sec-cola .queue-search", "#sec-cola .cnt-gear-btn[data-cola-gear]"]) {
          const b = await stableBox(page.locator(sel), sel);
          if (Math.abs(b.y + b.height / 2 - mid) > 2) off.push(sel);
        }
        return off;
      }, { message: "with the month open the search and the column picker stay on the day's line" }).toEqual([]);
      const month = await stableBox(page.locator(".queue-month-calendar"), "month calendar");
      const search = await stableBox(page.locator("#sec-cola .queue-search"), "queue search");
      // The search's edge is its magnifier: its frame opens outside it (negative margin, 2026-11-09).
      const out = await page.locator("#sec-cola .queue-search").evaluate((el) => -parseFloat(getComputedStyle(el).marginLeft) || 0);
      expect(Math.abs(month.x - (search.x + out)), "the month opens under the toolbar, at its left edge").toBeLessThanOrEqual(1);
      expect(month.y, "the month opens under the toolbar").toBeGreaterThan(search.y + search.height);
      await page.locator("[data-cola-calendar-toggle]").click();
      await expect(page.locator(".queue-month-calendar")).toHaveCount(0);
    }
    await expect(page.locator(".queue-date-picker a"), "no stray link inside the day controls").toHaveCount(0);

    const search = page.locator("#sec-cola .queue-search");
    const fit = await search.evaluate((input) => {
      const cs = getComputedStyle(input);
      const ctx = document.createElement("canvas").getContext("2d");
      ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      const inner = input.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      return { text: input.placeholder, need: ctx.measureText(input.placeholder).width, inner };
    });
    expect(fit.text).toBe("Paciente o DNI");
    expect(fit.need, `the placeholder fits the box (${JSON.stringify(fit)})`).toBeLessThanOrEqual(fit.inner);
    // One left edge for everything under the title (the product owner 16.10, m410310).
    // Where each thing visibly starts: a box's edge, a paragraph's first letter.
    const parts = [".queue-search", ".queue-date-label", ".queue-entrevista-switch"];
    for (const sel of parts) await expect(page.locator("#sec-cola " + sel)).toBeVisible();
    // The header is repainted while the day settles: compare the edges of the settled nodes.
    const edgesNow = () => page.evaluate((sels) => Object.fromEntries(sels.map((sel) => {
      const el = document.querySelector("#sec-cola " + sel);
      if (!el) return [sel, null];
      // A paragraph starts at its first letter; the search at its magnifier,
      // whose frame opens outside it (negative margin, 2026-11-09).
      const text = el.tagName === "P" ? parseFloat(getComputedStyle(el).paddingLeft)
        : el.tagName === "INPUT" ? -parseFloat(getComputedStyle(el).marginLeft) || 0 : 0;
      return [sel, Math.round(el.getBoundingClientRect().left + text)];
    })), parts);
    const misaligned = (edges) => Object.keys(edges).filter((k) => edges[k] !== edges[".queue-search"]
      && !(viewport.width >= 1024 && k === ".queue-date-label" && edges[k] !== null)); // same line, after the search
    let edges = {};
    await expect.poll(async () => misaligned(edges = await edgesNow()), { message: "one left edge" }).toEqual([]);
    expect(edges[".queue-search"], "one left edge").not.toBeNull();
    expect(errors).toEqual([]);
  });
}

// Between the phone and a wide desktop the row may not hold everything: the day stays
// one group and the icons (filters, column picker, download) move down TOGETHER.
// At 1280 the column picker used to drop alone to a line of its own.
for (const width of [1024, 1280]) {
  test(`R1 (${width}px): no control is left alone on a line of its own`, async ({ app, role }, testInfo) => {
    await seedToday(app, role);
    const { page, errors } = await openQueue(role, LEVIN, { width, height: 900 }, { interview: true });
    await expect(page.locator("#cola-results")).toContainText("Delhoy");
    await shots(page, testInfo, `con-turnos-${width}`);
    const parts = { search: ".queue-search", date: ".queue-date-label", today: ".queue-day-seg [data-cola-hoy]", month: ".queue-month-btn", filters: "[data-filter-toggle]", gear: ".cnt-gear-btn[data-cola-gear]", download: ".tbl-export-btn" };
    for (const sel of Object.values(parts)) await expect(page.locator("#sec-cola " + sel)).toBeVisible();
    const lines = () => page.evaluate((sels) => Object.fromEntries(Object.entries(sels).map(([k, sel]) => {
      const el = document.querySelector("#sec-cola " + sel);
      if (!el) return [k, null];
      const b = el.getBoundingClientRect();
      const out = el.tagName === "INPUT" ? -parseFloat(getComputedStyle(el).marginLeft) || 0 : 0; // the magnifier
      return [k, { mid: Math.round((b.top + b.bottom) / 2), left: Math.round(b.left + out) }];
    })), parts);
    // The day (date, Hoy, Ver mes) sits beside the search; the filters, the column picker
    // and «Descargar» are one group that stays on one line, under the search's left edge
    // when it wraps.
    const offLine = (at) => Object.keys(at).filter((k) => k !== "search" && (!at[k]
      || Math.abs(at[k].mid - at[["filters", "gear", "download"].includes(k) ? "filters" : "today"].mid) > 2));
    let at = {};
    await expect.poll(async () => offLine(at = await lines()), { message: "the day shares one line; the icons share another (or the same)" }).toEqual([]);
    if (Math.abs(at.filters.mid - at.today.mid) > 2) {
      expect(at.filters.left, `the icons' line starts at the search's left edge: ${JSON.stringify(at)}`).toBe(at.search.left);
    }
    expect(errors).toEqual([]);
  });
}

test("R3: the AI notice sits next to the AI content and never over an empty day", async ({ app, role }, testInfo) => {
  await seedToday(app, role);
  const ruiz = await openQueue(role, RUIZ, DESKTOP);
  await expect(ruiz.page.locator("#sec-cola :is(.adm-empty, .queue-show-empty)")).toBeVisible();
  await shots(ruiz.page, testInfo, "dia-vacio-1440");
  const phone = await openQueue(role, RUIZ, PHONE);
  await expect(phone.page.locator("#sec-cola :is(.adm-empty, .queue-show-empty)")).toBeVisible();
  await shots(phone.page, testInfo, "dia-vacio-390");
  await expect(ruiz.page.getByText(AI_NOTICE), "an empty day shows no AI notice").toHaveCount(0);
  await expect(phone.page.getByText(AI_NOTICE)).toHaveCount(0);

  // A day with an interview: no notice over the list (the product owner, 2026-11-09); the card the
  // name opens carries it once, right above the summary the AI wrote.
  const levin = await openQueue(role, LEVIN, DESKTOP, { interview: true });
  await expect(levin.page.locator("#cola-results")).toContainText("Delhoy");
  await expect(levin.page.locator("#sec-cola").getByText(AI_NOTICE), "no notice over the list").toHaveCount(0);
  await levin.page.locator("#cola-results tr", { hasText: "Delhoy" }).locator(".queue-pat-link").click();
  const card = levin.page.locator(".queue-card");
  await expect(card).toBeVisible();
  const notice = card.getByText(AI_NOTICE);
  await expect(notice, "the card carries the notice once").toHaveCount(1);
  await expect(notice).toBeVisible();
  const order = await card.evaluate((el) => {
    const n = el.querySelector(".queue-card-ai"), r = el.querySelector(".queue-card-resumen");
    return { notice: !!n, summary: !!r, above: !!(n && r && n.getBoundingClientRect().bottom <= r.getBoundingClientRect().top) };
  });
  expect(order, "the notice sits right above the AI summary").toEqual({ notice: true, summary: true, above: true });
  await shots(levin.page, testInfo, "ficha-ia-1440");
});

test("R4: «Captura de síntomas» is the panel's switch at body size and its state survives a reload", async ({ app, role }) => {
  await seedToday(app, role);
  const { page } = await openQueue(role, RUIZ, DESKTOP);
  const row = page.locator(".queue-entrevista-cfg");
  await expect(row).toBeVisible();
  await expect(row.locator(".queue-entrevista-switch")).toContainText("Captura de síntomas");
  await expect(row.locator("button:not(.queue-icon-btn)"), "no browser-default button").toHaveCount(0);
  // «¿Qué es?» is an icon on the switch's line, with a whole tap area.
  const what = row.locator(".queue-icon-btn[data-cola-explainer]");
  if (await what.count()) {
    const b = await stableBox(what, "explainer icon");
    expect(b.height, "a 44 px tap area").toBeGreaterThanOrEqual(44);
  }
  await expect(row.locator("a"), "no door poster while the capture is off").toHaveCount(0);
  const sw = row.locator(".hor-switch input[type=checkbox][role=switch]");
  await expect(sw).toHaveCount(1);
  await expect(sw).not.toBeChecked();
  const size = await row.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(size, "body-size text, not small print").toBeGreaterThanOrEqual(16);
  await expect.poll(() => row.locator(".hor-switch span").evaluate((el) => {
    const b = el.getBoundingClientRect();
    return { w: Math.round(b.width), h: Math.round(b.height) };
  }), { message: "the same track as the «Límites» switches" }).toEqual({ w: 48, h: 28 });

  await row.locator(".hor-switch").click();
  await expect(sw).toBeChecked();
  await page.reload({ waitUntil: "load" });
  await expect(page.locator(".queue-entrevista-cfg .hor-switch input")).toBeChecked({ timeout: 12_000 });
  // back to how the doctor had it, saved before the next test reads it
  const saved = page.waitForResponse((res) => res.url().includes("my-settings") && res.request().method() === "POST");
  await page.locator(".queue-entrevista-cfg .hor-switch").click();
  await expect(page.locator(".queue-entrevista-cfg .hor-switch input")).not.toBeChecked();
  expect((await saved).ok()).toBe(true);
});

test("R5: the doctor's schedules live in the section navigation, not as a link under the buttons", async ({ app, role }) => {
  await seedToday(app, role);
  const { page } = await openQueue(role, LEVIN, DESKTOP);
  await expect(page.locator("#sec-cola").getByText("Horarios de atención")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Mis agendas" })).toHaveAttribute("href", /\/admin\/my-schedules/);
});

// Product owner, 2026-10-26, second round: «no need of the line w doctors name (if this is already the
// doctor who logged in) and no need for counters», «the page loads w the list of all doctors on
// the screen briefly», «buttons too tall and ugly, status could be designed better».
test("R6: the doctor's own screen has no row with his own name", async ({ app, role }) => {
  await seedToday(app, role);
  const { page } = await openQueue(role, LEVIN, DESKTOP);
  await expect(page.locator("#cola-results")).toContainText("Delhoy");
  await expect(page.locator("#sec-cola"), "his name is the account, not a heading").not.toContainText("Camila Levin");
});

test("R7: the desk's first paint lists no doctor without appointments, and no counter", async ({ app, role }) => {
  await seedToday(app, role);
  const r = await role({ viewport: DESKTOP });
  await signInStaff(r.page, ADMIN_USER, ADMIN_PASS);
  // The server's own document, before any script runs: what the page shows first.
  const first = await (await r.context.request.get("/admin/cola")).text();
  const section = first.slice(first.indexOf('id="sec-cola"'), first.indexOf('id="queue-initial-data"'));
  expect(section, "the patient of the day is in the first paint").toContain("Delhoy");
  expect(section, "no doctor without appointments in the first paint").not.toContain("Sergio Ruiz");
  await r.page.goto("/admin/cola", { waitUntil: "load" });
  await expect(r.page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12_000 });
  const more = r.page.locator("#sec-cola .queue-show-empty");
  if (await more.count()) await expect(more, "no number on the button").toHaveText("Ver agendas sin turnos");
  await expect(r.page.locator("#sec-cola"), "no «N agendas» counter").not.toContainText(/\d+ agendas?/);
});

// Product owner, 2026-11-09: no Atendió / No vino on this screen, a card for the appointment instead —
// attendance is marked in the appointment card the patient's name opens; the row keeps
// only the «done» weight.
test("R8: attendance is one compact control in the card; a recorded status is not a button", async ({ app, role }, testInfo) => {
  await seedToday(app, role);
  const { page } = await openQueue(role, LEVIN, DESKTOP);
  await expect(page.locator("#cola-results")).toContainText("Delhoy");
  const row = page.locator("#cola-results tr", { hasText: "Delhoy" });
  await expect(row, "no attendance in the row").not.toContainText(/Atendió|No vino/, { useInnerText: true });
  const card = await unmarkedCard(page);
  const seg = card.locator(".queue-attendance-btns");
  const boxes = await seg.locator("button").evaluateAll((els) => els.map((el) => {
    const b = el.getBoundingClientRect();
    return { top: Math.round(b.top), h: Math.round(b.height) };
  }));
  expect(boxes, "Atendió and No vino side by side").toHaveLength(2);
  expect(boxes[0], "one height, one line").toEqual(boxes[1]);
  expect(boxes[0].h, "touch-sized, not a tall pill").toBeGreaterThanOrEqual(40);
  expect(boxes[0].h).toBeLessThanOrEqual(48);
  await shots(page, testInfo, "asistencia-botones-1440");

  await seg.getByRole("button", { name: /atendió/i }).click();
  const marked = card.locator(".queue-attendance-marked");
  await expect(marked).toContainText("Atendió");
  const ink = await page.evaluate(() => getComputedStyle(document.body).color);
  await expect.poll(() => marked.locator(".queue-attendance-yes").evaluate((el) => {
    const cs = getComputedStyle(el);
    return { border: cs.borderTopWidth, tag: el.tagName, color: cs.color };
  }), { message: "a status in plain dark text, not a button nor a link" })
    .toEqual({ border: "0px", tag: "SPAN", color: ink });
  await expect(marked.locator(".attendance-dot--good")).toHaveCount(1);
  await expect(row, "the row takes the done weight").toHaveClass(/(^|\s)queue-tr-done(\s|$)/);
  await shots(page, testInfo, "asistencia-marcada-1440");
  await card.locator(".solmod-x").click();
  await expect(card).toHaveCount(0);
});

// R8 records «Atendió» for Delhoy on the server (the pencil only reopens the buttons on
// this screen), so the tests below that need the unmarked card reopen it the same way.
async function unmarkedCard(page) {
  // The day's reconciling load repaints the rows: open the card after it, not before.
  await expect(page.locator("#cola-live")).toHaveText(/\S/, { timeout: 12_000 });
  await page.locator("#cola-results tr", { hasText: "Delhoy" }).locator(".queue-pat-link").click();
  const card = page.locator(".queue-card");
  await expect(card).toBeVisible();
  const fix = card.locator(".queue-attendance-marked [data-corregir]");
  if (await fix.count()) await fix.click();
  await expect(card.locator(".queue-attendance-btns button")).toHaveCount(2);
  return card;
}

// The maintainer's CI, 2026-10-27: at 1024px the attendance segment ran out of the pane. The row now
// holds the name, the bars and the orden; the card holds the buttons: both stay inside.
for (const width of [390, 768, 1024, 1280, 1440]) {
  test(`R9 (${width}px): the row and the card keep their content inside`, async ({ app, role }, testInfo) => {
    await seedToday(app, role);
    const { page } = await openQueue(role, LEVIN, { width, height: 900 });
    await expect(page.locator("#cola-results")).toContainText("Delhoy");
    const row = page.locator("#cola-results tr", { hasText: "Delhoy" });
    const fit = await row.evaluate((tr) => {
      const pane = tr.closest(".cnt-table-wrap").getBoundingClientRect();
      const out = [...tr.querySelectorAll(".queue-pat-link, .queue-forecast, .queue-orden")]
        .map((el) => ({ cls: el.className, b: el.getBoundingClientRect() }))
        .filter(({ b }) => b.width > 0 && (b.right > pane.right + 1 || b.right > innerWidth + 1 || b.left < pane.left - 1))
        .map(({ cls, b }) => ({ cls, left: Math.round(b.left), right: Math.round(b.right) }));
      // Every control, triage chip and forecast fits its own column.
      const spills = [...tr.children].flatMap((td) => {
        const box = td.getBoundingClientRect();
        return [...td.querySelectorAll("button:not(.queue-pat-link), .queue-triage, .queue-forecast")]
          .filter((el) => { const b = el.getBoundingClientRect(); return b.width > 0 && (b.left < box.left - 1 || b.right > box.right + 1); })
          .map((el) => `${td.getAttribute("data-col")}: ${el.textContent.trim() || el.className}`);
      });
      return { out, spills: [...new Set(spills)] };
    });
    expect(fit.out, "nothing runs past the table pane or the screen").toEqual([]);
    expect(fit.spills, "every cell's content stays in its own column").toEqual([]);

    const card = await unmarkedCard(page);
    await shots(page, testInfo, `ficha-${width}`);
    const inCard = await card.evaluate((el) => {
      const c = el.getBoundingClientRect();
      const btns = [...el.querySelectorAll(".queue-attendance-btns button")].map((b) => b.getBoundingClientRect());
      return { onScreen: c.left >= -1 && c.right <= innerWidth + 1,
        inside: btns.every((b) => b.left >= c.left - 1 && b.right <= c.right + 1), n: btns.length };
    });
    expect(inCard, "the card on the screen, both buttons inside it").toEqual({ onScreen: true, inside: true, n: 2 });
    await card.locator(".queue-attendance-btns").getByRole("button", { name: /atendió/i }).click();
    await expect(card.locator(".queue-attendance-marked")).toContainText("Atendió");
    await expect(card.locator(".queue-attendance-marked .attendance-dot")).toHaveCount(1);
    await card.locator(".solmod-x").click();
  });
}

// Product owner, 2026-10-29: at 1280 the «Historial» heading was cut to «Historial…». The history
// is now bars next to the name (2026-11-09); every heading the gear can still turn on
// wraps instead of being clipped.
const EVERY_COLUMN = ["confirmo", "solicitado", "cobertura", "entrevista"];
for (const [who, account, columns] of [["doctor", LEVIN, null], ["desk", { usuario: ADMIN_USER, password: ADMIN_PASS }, null],
  ["doctor-all-columns", LEVIN, EVERY_COLUMN]])
for (const width of [1280, 1440]) {
  test(`R9b (${who} ${width}px): no heading of the day table is cut`, async ({ app, role }, testInfo) => {
    await seedToday(app, role);
    const { page } = await openQueue(role, account, { width, height: 900 }, { interview: true, columns });
    await expect(page.locator("#cola-results")).toContainText("Delhoy");
    await expect(page.locator("#cola-results th[data-col=historial], #cola-results th[data-col=asistencia]"), "no history nor attendance column").toHaveCount(0);
    await saveShot(testInfo, page.locator("#cola-results thead"), testInfo.outputPath(`cola-encabezado-${who}-${width}.png`));
    await shots(page, testInfo, `encabezados-${who}-${width}`);
    const cut = () => page.evaluate(() => [...document.querySelectorAll("#cola-results th.cnt-th")].filter((th) => th.offsetParent).map((th) => {
      const b = th.getBoundingClientRect();
      const outside = [...th.children].filter((c) => c.offsetParent).some((c) => {
        const k = c.getBoundingClientRect();
        return k.right > b.right + 0.5 || k.left < b.left - 0.5 || k.bottom > b.bottom + 0.5;
      });
      return { col: th.dataset.col, clipped: th.scrollWidth > th.clientWidth + 1 || th.scrollHeight > th.clientHeight + 1 || outside,
        w: Math.round(b.width), sw: th.scrollWidth, sh: th.scrollHeight, ch: th.clientHeight };
    }));
    try {
      await expect.poll(async () => (await cut()).filter((c) => c.clipped), { message: "headings cut off" }).toEqual([]);
    } finally {
      // the picked columns are this account's saved preference: back to «never chose»
      if (columns) await page.request.put("/api/admin/preferences", { data: { key: "day_queue.columns", value: null } });
    }
  });
}

// Requirement H3 of the doctor-queue requirements: the doctor's search promises
// a name or DNI, and a typed date still opens that day.
test("R10: a date typed in the doctor's search opens that day", async ({ app, role }) => {
  await seedToday(app, role);
  const { page } = await openQueue(role, LEVIN, DESKTOP);
  await expect(page.locator(".queue-day-hoy")).toHaveAttribute("aria-current", "date");
  const day = new Date(Date.now() + 7 * 86_400_000);
  const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  await page.locator("[data-cola-buscar]").fill(iso);
  await expect(page.locator(".queue-date-label")).toHaveText(new RegExp(`\\b${day.getDate()}\\b`), { timeout: 12_000 });
  await expect(page.locator(".queue-day-hoy")).not.toHaveAttribute("aria-current", "date");
  // The search text is a saved preference: leave it empty for the next screens.
  const saved = page.waitForResponse((res) => res.url().includes("/api/admin/preferences") && res.request().method() !== "GET");
  await page.locator("[data-cola-buscar]").fill("");
  await saved;
});

// Product owner 27.10 (q1044534, «Hidden, per the checkbox»): free hours are hidden by default, the
// «Mostrar horarios libres» checkbox shows them and the choice survives a reload.
test("R11: free hours are hidden until the checkbox, and the choice is remembered", async ({ app, role }) => {
  await seedToday(app, role);
  const { page } = await openQueue(role, LEVIN, DESKTOP);
  await expect(page.locator("#cola-results")).toContainText("Delhoy");
  const free = page.locator("#cola-results .queue-tr-free");
  const box = page.getByRole("checkbox", { name: "Mostrar horarios libres" });
  // The checkbox waits behind the funnel, closed again after a reload.
  const openFilters = async () => {
    const funnel = page.locator("#sec-cola [data-filter-toggle]");
    if ((await funnel.getAttribute("aria-expanded")) !== "true") await funnel.click();
  };
  await openFilters();
  await expect(box).not.toBeChecked();
  await expect(free, "no free hour rows by default").toHaveCount(0);
  let saved = page.waitForResponse((res) => res.url().includes("/api/admin/preferences") && res.request().method() !== "GET");
  await box.check();
  await expect(free.first()).toBeVisible();
  await saved;
  await page.reload({ waitUntil: "load" });
  await expect(page.locator("#cola-live")).toHaveText(/\S/, { timeout: 12_000 });
  await openFilters();
  await expect(page.getByRole("checkbox", { name: "Mostrar horarios libres" })).toBeChecked();
  await expect(free.first()).toBeVisible();
  // back to the default for the other screens
  saved = page.waitForResponse((res) => res.url().includes("/api/admin/preferences") && res.request().method() !== "GET");
  await page.getByRole("checkbox", { name: "Mostrar horarios libres" }).uncheck();
  await expect(free).toHaveCount(0);
  await saved;
});

// Product owner 27.10 (doctor patient-history requirements F1, F3): the history is
// three bars — as many filled as the level. Since 2026-11-09 they sit next to the name, like
// a phone's signal, on every width: the bars are a button that opens the explanation with
// that patient's own line and the legend; Escape and a tap elsewhere close it.
// COLA_SHOTS_DIR (+ COLA_SHOTS_TAG) also writes the screen with the explanation open.
async function shotOpen(page, testInfo, width) {
  const dir = process.env.COLA_SHOTS_DIR;
  const name = `${process.env.COLA_SHOTS_TAG || "historial"}-${width}.png`;
  await saveShot(testInfo, page, dir ? `${dir}/${name}` : testInfo.outputPath(name), { fullPage: false });
}

for (const [label, viewport] of [["desktop 1440", DESKTOP], ["phone 390", PHONE]]) {
  test(`R12 (${label}): the history is three bars next to the name and they explain themselves`, async ({ app, role }, testInfo) => {
    await seedToday(app, role);
    const { page, errors } = await openQueue(role, LEVIN, viewport);
    await expect(page.locator("#cola-results")).toContainText("Delhoy");
    const row = page.locator("#cola-results tr", { hasText: "Delhoy" });
    const bars = row.locator(".queue-pat-line .queue-forecast");
    // 3 past visits, plus today's once R8/R9 have marked it (the file's tests share one database).
    await expect(bars).toHaveAttribute("aria-label", /^Falta a veces · según [34] turnos$/);
    await expect(bars.locator(".med-occupancy-segment")).toHaveCount(3);
    await expect(bars.locator(".med-occupancy-segment.is-filled")).toHaveCount(2);
    // On the name's line, right after the name link.
    const line = await row.locator(".queue-pat-line").evaluate((el) => {
      const name = el.querySelector(".queue-pat-link").getBoundingClientRect();
      const b = el.querySelector(".queue-forecast").getBoundingClientRect();
      return { after: b.left >= name.right - 1, sameLine: Math.abs((b.top + b.bottom) / 2 - (name.top + name.bottom) / 2) <= 12 };
    });
    expect(line, "the bars follow the name on its line").toEqual({ after: true, sameLine: true });
    const pop = page.locator("#forecast-info-pop");
    const trigger = row.locator(".forecast-bars-btn");
    await trigger.click();
    await expect(pop).toBeVisible();
    await expect(pop.locator(".forecast-pop-patient")).toHaveText(/^Falta a veces · según [34] turnos$/);
    for (const text of ["Viene siempre", "Falta a veces", "Falta seguido", "Sin historial todavía"]) await expect(pop).toContainText(text);
    const box = await stableBox(pop, "forecast popover");
    expect(box.x, "inside the screen").toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
    await shotOpen(page, testInfo, viewport.width);
    await page.keyboard.press("Escape");
    await expect(pop).toBeHidden();
    await trigger.click();
    await expect(pop).toBeVisible();
    await page.locator(".adm-h1, h1").first().click();
    await expect(pop).toBeHidden();
    await expect(page.locator(".queue-card"), "the bars open the explanation, not the card").toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

