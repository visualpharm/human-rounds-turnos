// tests/e2e/booking-hold-release-race.spec.mjs -- releasing a time while its keep-alive post is still on its way.
//
// The wizard keeps the patient's hold alive with a fire-and-forget POST /api/slot-hold each time a step that
// can take a while opens (Tu cuenta, Confirmá). "Cambiar horario" releases the hold with a DELETE. When the
// keep-alive is still in flight at that moment it answers AFTER the DELETE, and the old code took the time
// again for the token it had just let go: the patient is back on the grid and the time he left stays taken
// for fifteen minutes, so the next patient sees it as busy.
//
// The keep-alive here is held behind a gate, the patient changes the time, the gate opens and the late answer
// lands. Then: the page holds no token, and another patient is still offered the time (the server holds nothing).
import { test, expect, seedPatient, offeredSlots, hasSlot, expectOfferedAgain, showSlot } from "./lib/journey.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_hold_release_race" } });

const GENERAL = { id: "medicina-general-riverside-clinic" };
const patient = (n, nombre, apellido) => ({
  dni: `30277${String(n).padStart(3, "0")}`, nombre, apellido,
  email: `${nombre}.${apellido}`.toLowerCase().replace(/[^a-z.]/g, "") + "@example.invalid",
  contacto: `+54922542${String(n).padStart(5, "0")}`,
});

const isHoldPost = (req) => new URL(req.url()).pathname === "/api/slot-hold" && req.method() === "POST";

test("a keep-alive in flight when the patient changes the time does not take the time again", async ({ app, role }) => {
  test.setTimeout(180_000);
  const a = await role({ session: (await seedPatient(app, patient(1, "Aldo", "Soltero"))).session });
  const b = await role({ session: (await seedPatient(app, patient(2, "Berta", "Vecina"))).session });

  // Only the keep-alives (they carry the token) wait; the first take of the time goes straight through.
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const parked = [];
  const answered = new Set();
  await a.page.route("**/api/slot-hold", async (route) => {
    const req = route.request();
    if (!isHoldPost(req) || !JSON.parse(req.postData() || "{}").token) { await route.continue(); return; }
    parked.push(req);
    await gate;
    await route.continue();
  });
  a.page.on("requestfinished", (req) => { if (isHoldPost(req)) answered.add(req); });

  const slots = await offeredSlots(a.page, GENERAL.id);
  const days = [...new Set(slots.map((s) => s.dia))];
  const slot = slots.filter((s) => s.dia === days[1])[0];

  await test.step("1. A picks a time and reaches Confirmá while the keep-alive is held on its way", async () => {
    await (await showSlot(a.page, slot)).click();
    await expect(a.page.locator("#wz-send")).toBeVisible();
    await expect.poll(() => parked.length, { message: "the keep-alive is on its way" }).toBeGreaterThan(0);
    expect(await a.page.evaluate(() => localStorage.getItem("st_hold_token")), "A holds the time").toBeTruthy();
  });

  await test.step("2. A changes the time at once: the hold is released and the grid is back", async () => {
    await a.page.locator("#wz-change-time").click();
    await expect(a.page.locator(".wz-seg-opt").first()).toBeVisible();
    await expect.poll(() => a.page.evaluate(() => localStorage.getItem("st_hold_token")), { message: "the page forgot the token" }).toBeNull();
  });

  await test.step("3. the late keep-alive answers: it takes nothing", async () => {
    release();
    await expect.poll(() => parked.every((req) => answered.has(req)), { message: "every held keep-alive has answered" }).toBe(true);
    expect(await a.page.evaluate(() => localStorage.getItem("st_hold_token")), "the page holds no token again").toBeNull();
  });

  await test.step("4. B is offered the time A left", async () => {
    const seen = await expectOfferedAgain(b.page, GENERAL.id, slot, "the time A left is offered to B (nobody holds it)");
    expect(hasSlot(seen, slot)).toBe(true);
    await (await showSlot(b.page, slot)).click();
    await expect(b.page.locator("#wz-send"), "B gets the time, not a 'just taken' notice").toBeVisible();
  });

  for (const [who, label] of [[a, "A"], [b, "B"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});
