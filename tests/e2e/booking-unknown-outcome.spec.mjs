// tests/e2e/booking-unknown-outcome.spec.mjs -- the booking wizard when the database broke under the save.
//
// Why it exists: a commit that broke may have written (`db.DbCommitUnknown`), so the answer must not invite a
// retry; the patient gets the solution, never the error — the sentence and a link to «Mis turnos», where the
// request shows up if it landed (core/deps.py `_commit_unknown`, docs/decisions/2026-11.md). The body itself is
// pinned server-side by tests/test_db_unavailable_answer.py (TestTheAnswers); a real broken commit cannot be
// provoked from a browser, so this spec answers the wizard's POST with that same body and checks what the
// patient sees, at 390 wide.
import { test, expect, seedPatient, offeredSlots, showSlot } from "./lib/journey.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_booking_unknown_outcome" },
});

const CARDIO = { id: "cardiologia-sampleton-hospital" };
const SENTENCE = "No sabemos si quedó guardado. Revisá antes de repetir.";
const UNKNOWN = {
  error: SENTENCE, detalle: SENTENCE, outcome: "unknown",
  accion: { texto: "Ver mis turnos", url: "/my-requests" },
};

test("a save of unknown outcome tells the patient to check «Mis turnos», with the link, no «probá de nuevo»", async ({ app, role }) => {
  test.setTimeout(180_000);
  const P = { dni: "30277001", nombre: "Ines", apellido: "Incierta", email: "ines.incierta@example.invalid", contacto: "+5492254177001" };
  const { page } = await role({ session: (await seedPatient(app, P)).session });
  await page.setViewportSize({ width: 390, height: 844 });
  const slots = await offeredSlots(page, CARDIO.id);
  const days = [...new Set(slots.map((s) => s.dia))];
  expect(days.length, "the grid offers several days").toBeGreaterThan(1);
  const slot = slots.find((s) => s.dia === days[1]);
  await (await showSlot(page, slot)).click();
  await expect(page.locator("#wz-send")).toBeVisible();

  await page.route((url) => url.pathname === "/api/requests", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return route.fulfill({ status: 503, contentType: "application/json; charset=utf-8", body: JSON.stringify(UNKNOWN) });
  });
  await page.locator("#wz-send").click();

  const message = page.locator("#wz-msg");
  await expect(message).toContainText(SENTENCE, { useInnerText: true });
  await expect(message.getByRole("link", { name: "Ver mis turnos" })).toHaveAttribute("href", "/my-requests");
  await expect(page.locator("#wz-send"), "no second send: the request may already exist").toBeDisabled();
  await expect(message, "no invitation to retry").not.toContainText("Probá de nuevo", { useInnerText: true });
  const box = await message.boundingBox();
  const lineHeight = await message.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight) || 24);
  expect(Math.round(box.height / lineHeight), "two lines at most at 390").toBeLessThanOrEqual(2);
  // The frame for review: the run's output folder (Playwright's outputDir), first screen only.
  await message.scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath("booking-unknown-outcome-390.png") });
});
