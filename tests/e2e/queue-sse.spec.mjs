// Two authenticated browsers prove an attendance mutation reaches the other
// queue view through the real SSE endpoint, without navigation or polling.
// Stream readiness is observed at the browser protocol level (CDP Network
// domain): the events request must answer 200 text/event-stream AND deliver
// the server's "ready" frame on that same connection. The retired
// window.sanaQueueEventStreamReady global was a classic-controller artifact
// and proves nothing about the native module's EventSource.
//
// Port of tests/e2e/queue-sse.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), with the
//   queue worker on as before); there is no requireOk()/skip path. The pages come from the
//   runner: browser A is the `adminPage` fixture (signed in), browser B is a second
//   `newContext()` page signed in with loginAdmin(); the original's hand-made login()
//   (form wait + fill + click + URL wait) is those helpers.
// - lib/queue-stream-observer.mjs is used as is (a CDP session on the runner's page); the
//   observers are closed in try/finally exactly as before.
// - Waits are web-first: the unassigned-doctor group and the queue text are `expect(locator)`
//   (10 s, as before); the row B «Atendido» wait is `toHaveClass` + `toContainText` on the
//   same row (the original polled both conditions in one waitForFunction). The SSE
//   delivery itself is still awaited through the protocol-level observer, not the DOM.
//   «B did not navigate» (url + framenavigated count) and the overflow measurements stay
//   point-in-time reads.
// - Locators: the CSS ones stay on purpose - `button.queue-doctor-link[data-cola-doctor]`,
//   `[data-asis][data-valor]`, `tr.cnt-tr`, `#sec-cola` are the queue markup contract.
import { test, expect, loginAdmin, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";
import { observeQueueStream } from "./lib/queue-stream-observer.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_queue_sse", queue: true } });

async function openUnassignedQueue(page, pendingName, stream) {
  await page.goto("/admin/cola", { waitUntil: "load", timeout: 15_000 });
  await stream.opened();
  // One list (2026-11-08): the unassigned rows are there, nothing to open.
  await expect(page.locator('#sec-cola button.queue-doctor-link[data-cola-doctor="_sin_medico"]').first()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#sec-cola")).toContainText(pendingName, { timeout: 10_000 });
  return stream;
}

test("attendance in browser A updates browser B without a reload", async ({ adminPage: a, newContext, app }) => {
  const b = await (await newContext()).newPage();
  await loginAdmin(b, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/account");
  let streamA; let streamB;
  try {
    const pendingName = app.fixture.queue_pendiente;
    streamA = await observeQueueStream(a);
    streamB = await observeQueueStream(b);
    await openUnassignedQueue(a, pendingName, streamA);
    await openUnassignedQueue(b, pendingName, streamB);

    const beforeUrl = b.url();
    let navigations = 0;
    b.on("framenavigated", (frame) => {
      if (frame === b.mainFrame()) navigations += 1;
    });

    const rowA = a.locator("tr.cnt-tr", { hasText: pendingName }).first();
    const day = await b.locator("#queue-initial-data").textContent().then((text) => JSON.parse(text).dia);
    const after = streamB.checkpoint();
    // Attendance is marked in the appointment card the name opens (2026-11-09).
    await rowA.locator(".queue-pat-link").click();
    await a.locator('.queue-card [data-asis][data-valor="presente"]').click();

    // SSE delivery, not a poll: browser B's open event stream receives the
    // server's queue-update frame at the protocol level, before and
    // independently of any DOM change a poll could also have produced.
    const update = await streamB.queueUpdate({ after, day });
    expect(update.day, "a fresh frame must carry this exact mutated day").toBe(day);

    const rowB = b.locator("tr.cnt-tr", { hasText: pendingName }).first();
    await expect(rowB).toBeVisible({ timeout: 10_000 });
    await expect(rowB).toHaveClass(/\bqueue-tr-done\b/, { timeout: 10_000 });
    await rowB.locator(".queue-pat-link").click();
    await expect(b.locator(".queue-card .queue-attendance-marked")).toContainText(/Atendi[oó]/i, { timeout: 10_000 });
    expect(b.url()).toBe(beforeUrl);
    expect(navigations, "browser B must not navigate to receive an SSE update").toBe(0);
  } finally {
    await streamB?.close();
    await streamA?.close();
  }
});

test("queue fits from a 360px phone through 1280px after hydration", async ({ adminPage: page }) => {
  await page.goto("/admin/cola", { waitUntil: "load", timeout: 15_000 });
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 10_000 });
  for (const width of [360, 390, 768, 1024, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `/admin/cola overflows by ${overflow}px at ${width}px`).toBeLessThanOrEqual(1);
  }
});
