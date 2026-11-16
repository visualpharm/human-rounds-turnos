// «Pacientes de hoy» is one list of the day (2026-11-08): each doctor is a
// link in the doctor column that narrows the list to that doctor, and the
// narrowed list names the doctor once above it (`.queue-doctor-head`, its
// key in data-cola-elegido) with «Ver todos los médicos» to go back.
//
// The narrowing is a saved preference of the account, so a later visit can
// already be on that doctor, or on another one: this helper gets to the
// doctor from any of those states.
import { expect } from "@playwright/test";

export async function pickQueueDoctor(page, doctorId, { timeout = 30_000 } = {}) {
  // A doctor's own «Pacientes de hoy» is already only her patients: no doctor
  // column, no heading, nothing to choose.
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout });
  if (await page.evaluate(() => window.CURRENT_ROLE === "medico")) return;
  const chosen = page.locator(`#sec-cola .queue-doctor-head[data-cola-elegido="${doctorId}"]`);
  const all = page.locator("#sec-cola .queue-doctor-all");
  const link = page.locator(`#sec-cola button.queue-doctor-link[data-cola-doctor="${doctorId}"]`).first();
  await expect(async () => {
    if (await chosen.count()) return;
    if (await all.count()) await all.click({ timeout: 2000 });
    await link.click({ timeout: 2000 });
    await expect(chosen).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout, intervals: [500, 1000, 2000] });
}

// Back to every doctor of the day (no-op when the list is not narrowed).
export async function showAllQueueDoctors(page) {
  const all = page.locator("#sec-cola .queue-doctor-all");
  if (await all.count()) await all.click();
  await expect(all).toHaveCount(0);
}
