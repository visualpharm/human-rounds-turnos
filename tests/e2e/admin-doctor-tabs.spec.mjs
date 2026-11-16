// tests/e2e/admin-doctor-tabs.spec.mjs -- a doctor's panel on a phone is a row of tabs.
//
// Demo check 07.11: on a phone the doctor's five screens hid behind «Menú». At compact width
// a doctor now sees every screen as one row of tabs under the brand (Cuenta as a person icon
// at the header's right), the current one marked. Audit 08.11: «Mis agendas» sat past the
// edge, so on the phone each tab carries one word (Hoy · Turnos · Pacientes · Agendas, the
// full name stays its accessible name) and the whole row fits 320px without scrolling;
// «Cerrar sesión» closes the Account screen instead of joining the row. Every other role
// keeps the «Menú» disclosure, and desktop keeps the sidebar with its group titles and full
// names (templates/admin/base.html, admin-direct.css `.adm5-side--tabs`).
import { test, expect, signInStaff, DESK, LOGIN_PHRASE } from "./lib/journey.mjs";

const MEDICO = { usuario: "e2e-dr-tabs", password: LOGIN_PHRASE, rol: "medico", medico_id: "levin-camila" };
const TABS = ["/admin/cola", "/admin/mis-turnos", "/admin/my-patients", "/admin/my-schedules"];
const SHORT = ["Hoy", "Turnos", "Pacientes", "Agendas"];
const FULL = ["Pacientes de hoy", "Mis turnos", "Mis pacientes", "Mis agendas"];

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_admin_doctor_tabs" },
  journey: { staff: [DESK, MEDICO] },
});

/** Where each nav link sits, and whether the page itself scrolls sideways. */
async function layout(page) {
  return page.evaluate(() => {
    const links = [...document.querySelectorAll("#adm-nav a[data-nav]")].map((a) => {
      const r = a.getBoundingClientRect();
      const s = getComputedStyle(a);
      return {
        path: new URL(a.href).pathname, top: Math.round(r.top), left: Math.round(r.left),
        width: Math.round(r.width), height: Math.round(r.height), shown: s.display !== "none" && r.width > 0,
        on: a.classList.contains("on"), current: a.getAttribute("aria-current"),
        text: a.innerText.trim(), name: a.getAttribute("aria-label") || a.innerText.trim(),
      };
    });
    const nav = document.getElementById("adm-nav");
    const logout = document.getElementById("adm-logout");
    return {
      links,
      logoutIn: logout ? (logout.closest("#adm-nav") ? "nav" : logout.closest(".adm5-main") ? "main" : "side") : null,
      summaryShown: getComputedStyle(document.querySelector(".adm5-menu > summary")).display !== "none",
      pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      navScroll: nav.scrollWidth - nav.clientWidth,
      titlesShown: [...document.querySelectorAll(".adm5-nav-group-title")]
        .filter((t) => t.getBoundingClientRect().width > 0).length,
    };
  });
}

test("a doctor on a phone sees every screen as a tab; the current one is marked", async ({ role }, testInfo) => {
  const { page, context, errors } = await role({ viewport: { width: 390, height: 844 } });
  await signInStaff(page, MEDICO.usuario, MEDICO.password);
  for (const path of [...TABS, "/admin/account"]) {
    await page.goto(path, { waitUntil: "load" });
    await expect(page.locator("#adm-nav")).toBeVisible();
    const shape = await layout(page);
    expect(shape.summaryShown, `${path}: no «Menú» button for a doctor`).toBe(false);
    expect(shape.titlesShown, `${path}: no group titles in the tab row`).toBe(0);
    expect(shape.pageOverflow, `${path}: the page does not scroll sideways`).toBeLessThanOrEqual(1);
    const tabs = shape.links.filter((l) => TABS.includes(l.path));
    expect(tabs.map((l) => l.path), `${path}: every screen is a tab`).toEqual(TABS);
    for (const tab of tabs) {
      expect(tab.shown, `${path}: ${tab.path} is shown without opening anything`).toBe(true);
      expect(tab.height, `${path}: ${tab.path} is a 44px target`).toBeGreaterThanOrEqual(44);
      expect(tab.left >= 0 && tab.left + tab.width <= 390, `${path}: ${tab.path} is whole on screen`).toBe(true);
    }
    expect(tabs.map((l) => l.text), `${path}: one word per tab`).toEqual(SHORT);
    expect(tabs.map((l) => l.name), `${path}: the full name stays the tab's name`).toEqual(FULL);
    expect(shape.navScroll, `${path}: the row fits without scrolling`).toBeLessThanOrEqual(1);
    expect(new Set(tabs.map((l) => l.top)).size, `${path}: the tabs are one row`).toBe(1);
    const current = shape.links.filter((l) => l.on);
    expect(current.map((l) => l.path), `${path}: the current screen is marked`).toEqual([path]);
    expect(current[0].current).toBe("page");
    const account = shape.links.find((l) => l.path === "/admin/account");
    expect(account.shown && account.top < tabs[0].top, `${path}: Cuenta sits in the header row`).toBe(true);
    expect(account.left + account.width, `${path}: Cuenta stays on screen`).toBeLessThanOrEqual(390);
    await page.screenshot({ path: testInfo.outputPath(`doctor${path.replaceAll("/", "-")}-390.png`) });
  }
  await expect(page.getByRole("link", { name: "Cuenta" })).toBeVisible();
  // On Cuenta «Cerrar sesión» closes the screen, not a fifth tab.
  expect((await layout(page)).logoutIn, "account: sign-out sits in the screen").toBe("main");
  await expect(page.locator("#adm-logout")).toBeVisible();
  // The narrowest phone: still one row, every tab whole, nothing scrolls.
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/admin/my-schedules", { waitUntil: "load" });
  const narrow = await layout(page);
  expect(narrow.pageOverflow, "320px: the page does not scroll sideways").toBeLessThanOrEqual(1);
  expect(narrow.navScroll, "320px: the tab row fits").toBeLessThanOrEqual(1);
  // ...and not by a hair: a fallback font (the panel's own still loading) or a system that
  // draws the same words a little wider must still fit (Linux draws them 10 px wider than
  // macOS: with 1 px to spare there the row ran 9 px over here). The spare room is what the
  // row is wide minus what its words, paddings and gaps need, in the panel's own font.
  await page.evaluate(() => document.fonts.ready.then(() => null));
  const spare = await page.evaluate((paths) => {
    const nav = document.getElementById("adm-nav");
    const tabs = [...nav.querySelectorAll("a[data-nav]")].filter((a) => paths.includes(new URL(a.href).pathname));
    const px = (v) => parseFloat(v) || 0;
    const navStyle = getComputedStyle(nav);
    const need = tabs.reduce((sum, a) => {
      const s = getComputedStyle(a);
      return sum + a.querySelector(".adm5-tab-short").getBoundingClientRect().width + px(s.paddingLeft) + px(s.paddingRight);
    }, 0) + px(navStyle.columnGap) * (tabs.length - 1) + px(navStyle.paddingLeft) + px(navStyle.paddingRight);
    return Math.floor(nav.clientWidth - need);
  }, TABS);
  expect(spare, "320px: the tab row keeps room for a wider font").toBeGreaterThanOrEqual(16);
  for (const tab of narrow.links.filter((l) => TABS.includes(l.path))) {
    expect(tab.left >= 0 && tab.left + tab.width <= 320, `320px: ${tab.path} is whole on screen`).toBe(true);
  }
  expect(errors).toEqual([]);
  await context.close();
});

test("a doctor at desktop width keeps the sidebar", async ({ role }) => {
  const { page, context } = await role({ viewport: { width: 1440, height: 900 } });
  await signInStaff(page, MEDICO.usuario, MEDICO.password);
  await page.goto("/admin/cola", { waitUntil: "load" });
  const shape = await layout(page);
  expect(shape.titlesShown, "the group title stays").toBeGreaterThan(0);
  const tabs = shape.links.filter((l) => TABS.includes(l.path));
  expect(new Set(tabs.map((l) => l.left)).size, "one column of links").toBe(1);
  expect(new Set(tabs.map((l) => l.top)).size, "stacked, not a row").toBe(TABS.length);
  await expect(page.getByRole("link", { name: "Cuenta" })).toHaveText("Cuenta");
  expect(tabs.map((l) => l.text), "the sidebar keeps the full names").toEqual(FULL);
  expect(shape.logoutIn, "no sign-out outside Cuenta").toBe(null);
  await context.close();
});

test("the front desk on a phone keeps the «Menú» disclosure", async ({ role }) => {
  const { page, context } = await role({ viewport: { width: 390, height: 844 } });
  await signInStaff(page, DESK.usuario, DESK.password);
  await page.goto("/admin/cola", { waitUntil: "load" });
  const summary = page.locator(".adm5-menu > summary");
  await expect(summary).toBeVisible();
  expect(await page.locator(".adm5-menu").getAttribute("open")).toBeNull();
  await expect(page.locator(".adm5-side--tabs")).toHaveCount(0);
  await summary.click();
  await expect(page.locator("#adm-nav")).toBeVisible();
  await context.close();
});
