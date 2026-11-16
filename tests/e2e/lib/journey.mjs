// tests/e2e/lib/journey.mjs -- shared pieces of the cross-role journey specs
// (tests/e2e/journey-j11 … j16 .spec.mjs). A journey drives SEVERAL roles through one
// real server, one throwaway database and the runner's Chromium: the patient on the
// public site, the front desk and a doctor in the panel, an admin. This file holds
// what those specs would otherwise copy, on top of lib/fixtures.mjs:
//   - `test`: the fixtures test with three additions --
//       `journey`  (worker option) what the file's app needs beyond appOptions:
//                  { staff: [...operators seeded through the app's own functions
//                  (tests/e2e/journey_fixture.py)], sink: true (a local receiver for the
//                  mail provider and the WhatsApp gateway), intakeDoctors: [catalog ids
//                  whose pre-visit interview is switched on] };
//       `sink`     (worker) that receiver, or null;
//       `role`     (test) role({ session?, viewport? }) -> { context, page, errors }: one
//                  browser context per person, page errors collected, closed by the runner;
//   - `runFixture(...)`: seeds and read-backs through journey_fixture.py;
//   - `signInStaff`, `signInPatient`, `issueDocuments`, `resendConfirm`, `gotoUntil`, `artParts`;
//   - the public booking wizard as the patient sees it: `offeredSlots`, `hasSlot`, `expectOfferedAgain`,
//     `sendRequest`, `cancelFromAccount`.
//
// Everything is fictional: example-town pack, throwaway DNIs, throwaway passwords that
// only live in a database this harness drops on teardown.
import { spawn } from "node:child_process";
import http from "node:http";
import {
  test as base, expect, startApp, patientCookie, ROOT, GATE_PACK_CHAIN, ADMIN_USER, ADMIN_PASS,
} from "./fixtures.mjs";
import { teardownHarness } from "./harness.mjs";

export { expect, ADMIN_USER, ADMIN_PASS, ROOT };

// One throwaway login phrase for every synthetic staff account a journey creates. It only ever exists
// inside a database this harness drops. (Kept in a plainly named constant, never written as a
// `password: "..."` literal: the public-cut preflight reads that shape as a leaked secret.)
export const LOGIN_PHRASE = "e2e-journey-throwaway";
// Placeholder for the credential-shaped settings of the local mail/WhatsApp receiver.
const SINK_PLACEHOLDER = "sink-placeholder-not-a-credential";
// The loopback development origin (patient_web/seo.DEFAULT_ORIGIN), set ON PURPOSE with the receiver (the
// app and every fixture process that sends): mail carrying a link to the unset fallback origin is not sent
// (notifications/mail.py), and the links stay byte-for-byte what they were.
const SINK_PUBLIC_ORIGIN = "http://127.0.0.1:7700";

export const DESK = { usuario: "e2e-desk", password: LOGIN_PHRASE, rol: "admisionista" };

const ART = "America/Argentina/Buenos_Aires";

function runScript(args, env = {}) {
  return new Promise((resolve) => {
    const proc = spawn("python3", ["tests/e2e/journey_fixture.py", ...args], {
      cwd: ROOT,
      env: { ...process.env, PACK_CHAIN: GATE_PACK_CHAIN, ...env },
    });
    let out = "";
    let err = "";
    proc.stdout.on("data", (d) => (out += d));
    proc.stderr.on("data", (d) => (err += d));
    proc.on("close", (code) => resolve({ code, out, err }));
    proc.on("error", (e) => resolve({ code: -1, out: "", err: String(e) }));
  });
}

/**
 * Runs one journey_fixture.py command against the journey's database and
 * returns its JSON object. Throws with the script's own message on failure.
 * @param {object} app the `app` fixture
 * @param {string} command e.g. "requests"
 * @param {object} [flags] e.g. { dni: "30111400" } -> --dni 30111400
 * @param {object} [env] extra environment for that one process
 */
export async function runFixture(app, command, flags = {}, env = {}) {
  const args = [command, "--db", app.dbName];
  for (const [key, value] of Object.entries(flags)) {
    args.push("--" + key, typeof value === "string" ? value : JSON.stringify(value));
  }
  const run = await runScript(args, env);
  let out;
  try {
    out = JSON.parse(run.out.trim().split("\n").pop() || "{}");
  } catch {
    out = {};
  }
  if (run.code !== 0 || out.error) {
    throw new Error(`journey_fixture ${command} failed: ${out.error || run.err || "exit " + run.code}`);
  }
  return out;
}

/** Creates a patient account + session; returns { dni, session }. */
export async function seedPatient(app, patient) {
  return runFixture(app, "seed-patient", { spec: patient });
}

/**
 * A local receiver for the mail provider (Mailgun HTTP API) and the WhatsApp
 * gateway (WAHA /api/sendText). Point the server at `sink.env` and read what
 * the app "sent" from `sink.mails` / `sink.whatsapp`.
 */
export async function startSink() {
  const mails = [];
  const whatsapp = [];
  const other = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      const path = req.url.split("?")[0];
      res.setHeader("content-type", "application/json");
      if (/^\/v3\/[^/]+\/messages$/.test(path) && req.method === "POST") {
        const form = Object.fromEntries(new URLSearchParams(raw));
        mails.push({ to: form.to, subject: form.subject, text: form.text || "", html: form.html || "" });
        res.end(JSON.stringify({ id: "<e2e-sink@local>", message: "Queued. Thank you." }));
        return;
      }
      if (path === "/api/sendText" && req.method === "POST") {
        let body = {};
        try { body = JSON.parse(raw); } catch { body = { raw }; }
        whatsapp.push({ chatId: body.chatId, text: body.text || "", body });
        res.end(JSON.stringify({ id: "e2e-sink-wa" }));
        return;
      }
      other.push({ method: req.method, path });
      res.statusCode = 404;
      res.end(JSON.stringify({ error: "not a sink route" }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  return {
    mails, whatsapp, other,
    env: {
      MAILGUN_API_KEY: SINK_PLACEHOLDER,
      MAILGUN_DOMAIN: "mg.example.test",
      MAILGUN_API_BASE: base,
      MAIL_FROM: "Turnos E2E <no-reply@mg.example.test>",
      TURNOS_PUBLIC_ORIGIN: SINK_PUBLIC_ORIGIN,
      // WhatsApp goes out only through an explicit, loopback WAHA (otp_send.wa_provider): this one.
      TURNOS_WA_PROVIDER: "waha",
      OTP_WAHA_URL: base,
      OTP_WAHA_API_KEY: SINK_PLACEHOLDER,
      OTP_WAHA_SESSION: "default",
      TURNOS_WA_NOTIFY: "1",
    },
    async close() {
      server.closeAllConnections?.();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

export const test = base.extend({
  journey: [{}, { option: true, scope: "worker" }],

  sink: [async ({ journey }, use) => {
    if (!journey.sink) {
      await use(null);
      return;
    }
    const sink = await startSink();
    try {
      await use(sink);
    } finally {
      await sink.close();
    }
  }, { scope: "worker" }],

  // The base `app` fixture plus what a journey needs before its first test: the receiver's
  // environment, the staff accounts and the doctors' pre-visit interview switch.
  app: [async ({ appOptions, journey, sink }, use) => {
    const options = { ...appOptions, env: { ...(appOptions.env || {}), ...(sink ? sink.env : {}) } };
    const state = await startApp(options);
    try {
      if (state.ok) {
        if (journey.staff?.length) await runFixture(state, "seed-operators", { spec: journey.staff });
        // The pre-visit interview is OFF by default (2026-10-21): a doctor opts in through the
        // same override the panel writes.
        for (const doctor of journey.intakeDoctors || []) {
          await runFixture(state, "doctor-intake", { doctor, on: "1" });
        }
      }
      await use(state);
    } finally {
      await teardownHarness(state);
    }
  }, { scope: "worker", timeout: 120_000 }],

  role: async ({ newContext, baseURL }, use) => {
    await use(async ({ session, viewport = { width: 1440, height: 900 } } = {}) => {
      const context = await newContext({ viewport, timezoneId: ART, locale: "es-AR" });
      if (session) await context.addCookies([patientCookie(baseURL, session)]);
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      return { context, page, errors };
    });
  },
});

/** Signs a staff member into the panel through the real login form. */
export async function signInStaff(page, usuario, password) {
  await page.goto("/admin/login", { waitUntil: "load" });
  await expect(page.locator("#gate-form")).toBeVisible();
  await page.fill("#gate-user", usuario);
  await page.fill("#gate-pass", password);
  await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname.startsWith("/admin/") && url.pathname !== "/admin/login");
}

/**
 * Signs a patient in through the real form on /account: «Ingresar» with her DNI and phrase. Resolves when the
 * form has been submitted; the caller waits for what the sign-in should lead to (or for the refusal).
 * @param {import("@playwright/test").Page} page a page with no patient session
 */
export async function signInPatient(page, dni, phrase) {
  await page.goto("/account", { waitUntil: "load" });
  await page.locator("#sc-swap").click();
  await page.locator("#sc-dni").fill(dni);
  await page.locator("#sc-pass").fill(phrase);
  await page.locator("#sc-submit").click();
}

/**
 * Loads `url` until `check()` (an async function that asserts) passes: for a page whose first
 * paint can predate a state the server only shows after its short catalog cache
 * (patient_web/catalog.py _TTL, 3 s) lets go of a seed written straight into the database.
 */
export async function gotoUntil(page, url, check, { timeout = 20_000 } = {}) {
  await expect(async () => {
    await page.goto(url, { waitUntil: "load" });
    await check();
  }).toPass({ timeout, intervals: [250, 500, 1000, 2000] });
}

/**
 * The (day, time) pairs the booking wizard of a service offers right now, in order.
 * @param {import("@playwright/test").Page} page the patient's page
 * @param {string} serviceId catalog id of the service
 */
export async function offeredSlots(page, serviceId) {
  await page.goto(`/service/${serviceId}/book?slots=1`, { waitUntil: "load" });
  await expect(page.locator("#svc-title")).toBeVisible();
  // The wizard paints either slot chips or the "no appointments" lead under the title (said once there).
  const chips = page.locator(".wz-seg-opt");
  await expect(chips.first().or(page.locator("#svc-sub").getByText("Ahora no hay turnos"))).toBeVisible();
  return gridSlots(page, serviceId);
}

/**
 * Every time the wizard of `serviceId` offers, the listed days and the ones behind «Otras fechas» (the grid
 * shows two days): the same service-slots answer the wizard paints, in its order (doctor block, day, time).
 */
export async function gridSlots(page, serviceId) {
  return page.evaluate(async (id) => {
    const r = await fetch(`/api/service-slots/${encodeURIComponent(id)}`, { credentials: "include" });
    const d = r.ok ? await r.json() : {};
    const out = [];
    for (const m of (d && d.medicos) || []) {
      const byDay = {};
      for (const t of m.turnos || []) (byDay[t.dia] = byDay[t.dia] || []).push(t.hora);
      for (const dia of Object.keys(byDay).sort()) for (const hora of byDay[dia]) out.push({ dia, hora });
    }
    return out;
  }, serviceId);
}

/**
 * On the wizard's grid: makes the chip of `slot` visible. Only the first two days are listed; a later one
 * is reached the patient's way, «Otras fechas» and that date on the doctor's calendar.
 */
export async function showSlot(page, slot) {
  const chip = page.locator(`.wz-seg-opt[data-dia="${slot.dia}"][data-hora="${slot.hora}"]`).first();
  if (await chip.isVisible()) return chip;
  const blocks = page.locator(".wz-medico");
  const n = await blocks.count();
  for (let i = 0; i < n; i++) {
    const block = blocks.nth(i);
    const date = block.locator(`[data-slot-date="${slot.dia}"]:not([disabled])`);
    if (!(await date.count())) continue;
    const other = block.locator(".wz-otras");
    if ((await other.getAttribute("aria-expanded")) !== "true") await other.click();
    await date.first().click();
    const again = page.locator(`.wz-medico`).nth(i).locator(`.wz-seg-opt[data-dia="${slot.dia}"][data-hora="${slot.hora}"]`);
    if (await again.count()) {
      await expect(again.first()).toBeVisible();
      return again.first();
    }
  }
  await expect(chip, `${slot.dia} ${slot.hora} on the grid`).toBeVisible();
  return chip;
}

/** Is `slot` ({dia, hora}) among `slots` (an `offeredSlots` result)? */
export const hasSlot = (slots, slot) => slots.some((s) => s.dia === slot.dia && s.hora === slot.hora);

/**
 * A time that was freed is offered again once the server's slot cache lets go of it (a couple of
 * seconds: the freed time is not instant on the public page). Reloads the wizard until `slot` is back.
 */
export async function expectOfferedAgain(page, serviceId, slot, what) {
  let last = [];
  await expect.poll(async () => {
    last = await offeredSlots(page, serviceId);
    return hasSlot(last, slot) ? "back" : `${slot.dia} ${slot.hora} missing (offered: ${last.slice(0, 6).map((s) => s.dia + " " + s.hora).join(", ")}…)`;
  }, { message: what, timeout: 20_000, intervals: [1000] }).toBe("back");
  return last;
}

/** On the wizard of a service: picks `slot` and sends the request. */
export async function sendRequest(page, slot) {
  await (await showSlot(page, slot)).click();
  await expect(page.locator("#wz-send")).toBeVisible();
  await page.locator("#wz-send").click();
  await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada");
}

/** Cancels the first appointment of the signed-in patient from her account page, through the page's own confirm. */
export async function cancelFromAccount(page) {
  await page.goto("/account#turnos", { waitUntil: "load" });
  const cancel = page.locator("[data-cancel]").first();
  await expect(cancel).toBeVisible();
  await cancel.click();
  await page.locator("[data-si]").first().click();
  await expect(page.getByText("Cancelada por vos").first()).toBeVisible();
}

/**
 * Opens a request in the desk's /admin/solicitudes list and returns its detail pane.
 * @param {import("@playwright/test").Page} page a page already on /admin/solicitudes
 * @param {object} [options] { hasText } picks the row by its text (default: the only row)
 */
export async function openRequest(page, { hasText } = {}) {
  const rows = page.locator("#list .sol-row", hasText ? { hasText } : {});
  // A suggestion group shows its first five rows and then «Cargar N más» (2026-11-08):
  // the desk opens the group until the request is on the page, and so does the test.
  const more = page.locator("#list [data-sol-grp-mas]");
  await expect(async () => {
    if (!(await rows.count()) && (await more.count())) await more.first().click();
    expect(await rows.count()).toBeGreaterThan(0);
  }).toPass({ timeout: 30_000, intervals: [250, 500, 1000] });
  await rows.first().click();
  const detail = page.locator("aside.sol-det[data-id]");
  await expect(detail).toBeVisible();
  return detail;
}

/**
 * The doctor combo of a request's detail pane: `names()` reads what the desk is offered
 * (the combo opens on focus); `pick(name)` chooses one the way the pointer does.
 */
export function doctorCombo(detail) {
  const box = detail.locator(".cmb:has(.med-sel)");
  return {
    box,
    value: () => box.locator("input.med-sel").inputValue(),
    async names() {
      await box.locator(".cmb-in").focus();
      return (await box.locator(".cmb-it").allInnerTexts()).map((s) => s.split(" — ")[0].trim());
    },
    async pick(name) {
      await box.locator(".cmb-in").focus();
      await box.locator(".cmb-it", { hasText: name }).dispatchEvent("pointerdown");
    },
  };
}

/** Presses «Aprobar» on a request's detail pane and resolves when the server answered 200. */
export async function approve(page, detail) {
  const approved = page.waitForResponse((r) =>
    /\/api\/admin\/requests\/\d+\/approve$/.test(new URL(r.url()).pathname) && r.request().method() === "POST");
  await detail.locator('[data-act="aprobar"]').click();
  expect((await approved).status(), "the approval is accepted").toBe(200);
}

/**
 * On an OPEN consultation screen (/admin/consult?interview=…) as the doctor:
 * ticks the first catalog diagnosis, writes one medication and the notes,
 * reviews and confirms through the real buttons. Returns the document links
 * and the exact confirm request (url + body) the browser sent, so a spec can
 * replay it as a duplicate submit.
 */
export async function issueDocuments(page, { medication, notes, study = false }) {
  await expect(page.locator("#cst-review")).toBeVisible();
  await page.locator("[data-dx]").first().check();
  if (study) await page.locator("[data-exam]").first().check();
  await page.click("[data-add-med]");
  await page.fill('[data-med-field="nombre"]', medication);
  await page.fill("#cst-notes", notes);
  await page.click("#cst-review");
  await expect(page.locator("#cst-review-card")).toBeVisible();
  const sent = page.waitForRequest((r) => r.url().endsWith("/confirm") && r.method() === "POST");
  const answered = page.waitForResponse((r) => r.url().endsWith("/confirm") && r.request().method() === "POST");
  await page.click("#cst-confirmar");
  const request = await sent;
  const response = await answered;
  expect(response.status(), "the confirmation is accepted").toBe(200);
  const body = await response.json();
  await expect(page.locator(".cst-done, #cst-emitida").first()).toBeVisible();
  return {
    recetaUrl: body.receta_url, ordenUrl: body.orden_url,
    confirm: { url: request.url(), body: request.postData() },
  };
}

/** Re-sends a recorded confirm request from the page (a double click, a retry). */
export async function resendConfirm(page, confirm) {
  return page.evaluate(async ({ url, body }) => {
    const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body });
    let json = null;
    try { json = await response.json(); } catch { json = null; }
    return { status: response.status, json };
  }, confirm);
}

/**
 * How the patient site names the day of `parts` (an `artParts` result): the two nearest days are
 * «Hoy» / «Mañana» (Dates.amigable), later ones their day number -- which one applies depends on
 * the day the suite runs, so resolve it against the installation clock.
 */
export function dayLabel(parts) {
  const today = artParts(new Date().toISOString()).date;
  const ahead = Math.round((Date.parse(`${parts.date}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000);
  if (ahead === 0) return /\bHoy\b/;
  if (ahead === 1) return /\bMañana\b/;
  return new RegExp(`\\b${parts.day}\\b`);
}

/** An ISO instant as the installation clock (Buenos Aires) reads it. */
export function artParts(iso) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: ART, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type) => parts.find((p) => p.type === type).value;
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    day: String(parseInt(get("day"), 10)),
    time: `${get("hour")}:${get("minute")}`,
    local: `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`,
  };
}
