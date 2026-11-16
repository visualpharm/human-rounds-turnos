// Real server-rendered Chats screen, synthetic rows at the HTTP boundary.
// The browser must execute the screen's own imports and display friendly values.
//
// Port of tests/e2e/admin-chats-formatters.e2e.mjs (2 tests, same titles, same order; the phone
// layout test was added after the port, 2026-11-07).
//
// Differences from the node:test original:
// - App and browser come from the runner (`test.use({ appOptions })`, `page`); the hand-made
//   login is `loginAdmin(...)` with `next=/admin/chats`, and the `finally { page.close() }` is gone.
// - The assertions inside the route handler cannot fail the test from there (the navigation
//   would just hang), so the handler stores the first failure, aborts the request and
//   `openChats` rethrows it from the test body.
// - The optional evidence screenshots (TURNOS_CHATS_EVIDENCE_DIR) go through `saveShot` to the
//   same file names and are attached to the report.
// - `assertFriendly` is a function of `expect(...)` calls; the negative control checks that it
//   throws with `expect(() => ...).toThrow(...)`. The rendered text and the overflow check stay
//   point-in-time reads (the rows are already on screen once `openChats` returns).
// - Locators that stay CSS: `#sec-chats` and its `tbody tr` are the screen's own markup contract.
import path from "node:path";
import { test, expect, loginAdmin, saveShot, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_chats_formatters" } });

const LONG = "Synthetic formatter check: my right knee hurts when I climb the stairs and I would like a doctor to see me this week";
const rows = [{ channel: "whatsapp", key: "5492254123456", last_at: "2026-08-20T10:07:00Z", user_messages: 2, first_message: LONG }];

async function openChats(page, app, regressed = false, data = rows) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/chats");
  // Keep real authentication, shell, module manifest and controller. Replace
  // only the server's data payload, avoiding real conversations or DB writes.
  let handlerError;
  await page.route("**/admin/chats?formatter-fixture=1", async (route) => {
    try {
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      let body = await response.text();
      const payload = /(<script type="application\/json" id="chats-rows">)[\s\S]*?(<\/script>)/;
      expect(body).toMatch(payload);
      body = body.replace(payload, "$1" + JSON.stringify(data) + "$2");
      if (regressed) {
        for (const name of ["dates", "format"]) {
          const lookup = 'window.SANA_STATIC["platform/' + name + '.mjs"]';
          expect(body.includes(lookup), lookup + " is in the page").toBe(true);
          body = body.replace(lookup, 'window.SANA_STATIC["' + name + '.mjs"]');
        }
      }
      await route.fulfill({ response, body });
    } catch (error) {
      handlerError = error;
      await route.abort();
    }
  });
  try {
    await page.goto("/admin/chats?formatter-fixture=1", { waitUntil: "load" });
  } catch (error) {
    throw handlerError || error;
  }
  await expect(page.locator("#sec-chats tbody tr").first()).toBeVisible();
  return errors;
}

function assertFriendly(text) {
  expect(text.includes("+54 9 2254 12-3456"), "WhatsApp identity uses the canonical human phone format").toBe(true);
  expect(text.includes("2026-08-20"), "date is not raw ISO fallback").toBe(false);
  expect(text.includes("10:07"), "friendly date retains the time").toBe(true);
  expect(text.includes("Synthetic formatter check"), "the actual row rendered").toBe(true);
}

test("Chats renders canonical formatters at phone and desktop widths", async ({ page, app }, testInfo) => {
  const errors = await openChats(page, app);
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    assertFriendly(await page.locator("#sec-chats").innerText());
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    expect(overflow).toBe(false);
    if (process.env.TURNOS_CHATS_EVIDENCE_DIR) {
      await saveShot(testInfo, page, path.join(process.env.TURNOS_CHATS_EVIDENCE_DIR, `chats-${width}.png`), { fullPage: true });
    }
  }
  expect(errors).toEqual([]);
});

// Demo, 07.11: at 390 the rows sat on the screen edge, the table kept its desktop width and
// the first question was cut on the right; the bare message count had no label.
test("Chats on a phone: each row on the panel gutter, the question whole, the count labelled", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openChats(page, app);
  const row = page.locator("#sec-chats tbody tr").first();
  const m = await row.evaluate((tr) => {
    const gutter = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--adm-gutter"));
    const cells = Array.from(tr.children).map((td) => td.getBoundingClientRect());
    const first = tr.querySelector(".ch-first");
    return {
      gutter, lefts: cells.map((r) => Math.round(r.left)), rights: cells.map((r) => Math.round(r.right)),
      questionTaller: first.getBoundingClientRect().height / tr.querySelector(".ch-when").getBoundingClientRect().height,
      questionCut: first.scrollWidth > first.clientWidth + 1, width: innerWidth,
    };
  });
  expect(m.gutter, "the panel gutter is set").toBeGreaterThan(0);
  expect(m.lefts[0], "the row starts on the panel gutter").toBe(m.gutter);
  expect(Math.max(...m.rights), "no cell runs past the screen").toBeLessThanOrEqual(m.width - m.gutter + 1);
  expect(m.questionCut, "the question is not cut").toBe(false);
  await expect(row.locator(".ch-first"), "the whole question is shown").toHaveText(LONG);
  expect(m.questionTaller, "the long question wraps onto more lines").toBeGreaterThan(1.5);
  await expect(row.locator(".ch-num"), "the count says what it counts").toHaveText("2");
  expect(await row.locator(".ch-num").evaluate((td) => getComputedStyle(td, "::before").content)).toBe('"Mensajes: "');
});

// Product owner, 02.11: what repeats on every row goes outside the brackets. With every conversation
// from the site, «Sitio» in each row said nothing: the Canal column is not drawn.
test("Chats from one channel only: no Canal column, no channel word in the rows", async ({ page, app }) => {
  const web = [
    { channel: "web", key: "s1", last_at: "2026-08-20T10:07:00Z", user_messages: 3, first_message: "Synthetic web question one" },
    { channel: "web", key: "s2", last_at: "2026-08-21T11:15:00Z", user_messages: 1, first_message: "Synthetic web question two" },
  ];
  await openChats(page, app, false, web);
  await expect(page.locator("#sec-chats thead th")).toHaveCount(3);
  await expect(page.locator("#sec-chats tbody tr")).toHaveCount(2);
  expect(await page.locator("#sec-chats").innerText()).not.toContain("Sitio");
});

test("negative control: retired lookup keys fail the visible formatter gate", async ({ page, app }) => {
  await openChats(page, app, true);
  const text = await page.locator("#sec-chats").innerText();
  expect(text.includes("2026-08-20 10:07")).toBe(true);
  expect(() => assertFriendly(text)).toThrow(/canonical human phone format/);
});
