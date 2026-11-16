// tests/e2e/lib/harness.mjs -- shared setup/teardown for the Tier-2 browser
// E2E suite (tests/e2e/*.spec.mjs, @playwright/test; the fixtures that call this
// are in lib/fixtures.mjs). One throwaway Postgres database + one
// `python3 asgi_main.py` subprocess per test FILE (the runner owns the
// Chromium -- `launchBrowser: false`; the option stays `true` for any
// other caller that wants the harness' own browser).
//
// GATE vs CONVENIENCE mode (Codex review of ac44fe9, item 1): `npm run e2e`
// IS the pre-move gate -- a checkout missing Playwright/Chromium/Postgres
// must FAIL loudly (non-zero exit), never produce a quietly-green skipped
// suite. `setupHarness()` still never throws (it returns `{ ok:false,
// reason }` so the log always explains WHY), but callers must route that
// through `requireOk()` below, not a bare `t.skip()`. Local convenience
// mode (`npm run e2e:dev`, sets `TURNOS_E2E_ALLOW_SKIP=1`) is the only
// place that degrades to a skip -- opt-in, and never what CI/the gate runs.
//
// HERMETIC ENV (item 2): the spawned app subprocess is pointed at
// `TURNOS_ENV_FILE` (an empty file this harness writes to a scratch temp
// dir), which makes config.py's dotenv loader ignore the checkout's real
// `.env` ENTIRELY -- see config.py's `_env_path()`. Before this, the
// harness tried to neutralize TOTP/Turnstile by passing `""` for those
// three keys, which does NOT work: `os.environ.get(key)` treats `""` as
// falsy and config.get() falls through to the real `.env` anyway. A
// developer's real, populated `.env` is therefore never blanked and never
// consulted -- this is the fix, not a workaround.
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { until } from "../../helpers/wait.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(__dirname, "..", "..", ".."); // tests/e2e/lib -> repo root

// The synthetic pack the gate runs against -- NEVER the private Pinamar
// pack (`ar-b-pinamar`) or its identifiers (item 4/5). `example-town`
// ("Sampleton") is a public, fictional catalog committed at
// packs/example-town/, activated the same way any real deployment would
// activate its own pack chain (config.get("PACK_CHAIN")). Overridable for
// a future "second synthetic pack" need, but the gate itself never sets
// this to anything else.
export const GATE_PACK_CHAIN = process.env.TURNOS_E2E_PACK_CHAIN || "global,example-town";

const TEST_DB_PREFIX = "sana_turnos_test_";
const TEST_DB_NAME = new RegExp(`^${TEST_DB_PREFIX}[a-zA-Z0-9_]+$`);
let databaseRunSequence = 0;

// Each E2E file names its logical fixture for diagnostics, but that old name
// cannot also be the physical Postgres name: another node:test invocation can
// be using the same fixture at the same time. Keep the fixture's restrictive
// throwaway prefix, then append one process/run identity. PostgreSQL accepts
// at most 63 identifier bytes, so trim only the logical tail, never the
// safety prefix or unique suffix.
function allocateDbName(logicalName) {
  if (typeof logicalName !== "string" || !TEST_DB_NAME.test(logicalName)) {
    throw new Error(`invalid E2E database name: ${logicalName}`);
  }
  const suffix = `_p${process.pid}_${Date.now().toString(36)}_${(++databaseRunSequence).toString(36)}`;
  const maxLogicalLength = 63 - suffix.length;
  const physicalName = logicalName.slice(0, maxLogicalLength) + suffix;
  return physicalName;
}

/**
 * Gate-vs-convenience branch (lib/fixtures.mjs calls it for every spec) before touching
 * `state.browser`/`state.baseUrl`. Gate mode (default, what `npm run e2e`
 * runs): a missing prerequisite THROWS, so node:test reports a FAILED
 * test and the process exits non-zero -- never a silent skip. Convenience
 * mode (`TURNOS_E2E_ALLOW_SKIP=1`, `npm run e2e:dev`): skips with the
 * reason, for a developer machine that knowingly hasn't run
 * `npx playwright install chromium` yet.
 * @returns {boolean} true if the caller should proceed with `state`.
 */
export function requireOk(state, t) {
  if (state.ok) return true;
  if (process.env.TURNOS_E2E_ALLOW_SKIP === "1") {
    t.skip(state.reason);
    return false;
  }
  throw new Error(
    `E2E gate prerequisite missing (this FAILS the gate on purpose -- ` +
      `for a local convenience run that skips instead, use ` +
      `\`npm run e2e:dev\`): ${state.reason}`
  );
}

// Fixed, throwaway, non-secret -- this is a local test-only admin account
// that only ever exists inside a database this same harness drops on
// teardown. Never used against a real deployment.
export const ADMIN_USER = "admin";
export const ADMIN_PASS = "e2e-tier2-throwaway-pass";

async function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function runPython(args, opts = {}) {
  return new Promise((resolve) => {
    // fixture.py's --intake seeding resolves the doctor id through
    // catalog.doctors(), which is pack-chain-dependent -- without this,
    // the fixture subprocess falls back to config.py's default chain
    // (the private Pinamar one) instead of the gate's synthetic pack,
    // and a doctor id that only exists in example-town looks "not in the
    // active catalog" even though the spawned server itself is correctly
    // pointed at example-town.
    const proc = spawn("python3", args, {
      cwd: ROOT,
      env: { ...process.env, PACK_CHAIN: GATE_PACK_CHAIN },
      ...opts,
    });
    let out = "";
    let err = "";
    proc.stdout.on("data", (d) => (out += d));
    proc.stderr.on("data", (d) => (err += d));
    proc.on("close", (code) => resolve({ code, out, err }));
    proc.on("error", (e) => resolve({ code: -1, out: "", err: String(e) }));
  });
}

// 60 s, inside the app fixture's 120 s: at load 300-500 (the dev machine, 2026-10-25) a healthy
// server took longer than the old 20 s to answer, and the run failed before its first line.
async function waitForReady(baseUrl, timeoutMs = 60000) {
  let lastErr = null;
  try {
    await until(async () => {
      try {
        const r = await fetch(baseUrl + "/", { redirect: "manual" });
        return r.status > 0; // any real HTTP response = server is up
      } catch (e) {
        lastErr = e;
        return false;
      }
    }, `the server answers on ${baseUrl}`, { timeout: timeoutMs, interval: 300 });
  } catch {
    throw new Error(`server never answered on ${baseUrl}: ${lastErr}`);
  }
  return true;
}

// The FIRST request against a fresh process pays for uncached catalog
// merges (data_packs.py's _MERGE_CACHE, catalog.py's _JSON_CACHE -- mtime
// keyed, empty until something reads them) across the whole ~120-service
// pack chain; every later request is fast. Left unwarmed, whichever e2e
// test happens to load "/" first eats that one-time cost and can blow a
// generous-looking Playwright timeout under a loaded dev machine (seen
// intermittently on home-search). One throwaway fetch here means every
// real test navigation hits warm caches.
async function warmUp(baseUrl, timeoutMs = 20000) {
  try {
    await fetch(baseUrl + "/", { signal: AbortSignal.timeout(timeoutMs) });
  } catch {
    // best-effort -- a failed warm-up just means the first real test does
    // the same work the hard way; not fatal on its own.
  }
}

/**
 * @param {object} opts
 * @param {string} opts.dbName - throwaway Postgres database name (this
 *   process's only writer -- never reuse a name across concurrently
 *   running suites).
 * @param {boolean} [opts.intake] - also seed an interview + QR door token.
 * @param {string} [opts.doorMedico] - catalog id for the QR door token;
 *   REQUIRED when `intake` is true (must exist in `GATE_PACK_CHAIN`'s
 *   active pack -- fixture.py fails loudly if it's missing or unknown).
 * @param {boolean} [opts.hsi] - opt the spawned server into the HSI
 *   connector (`CONNECTOR_EMR_SYNC=hsi`) so `/admin/hsi` and its cluster
 *   are reachable; every other file keeps the no-connector default.
 * @param {boolean} [opts.totp] - also seed an operator with 2FA enabled
 *   (Tier 3 slice 1, fixture.py's --totp) -- `fixture.totp_user/_pass/
 *   _secret/_code` land in the returned `fixture` object.
 * @param {boolean} [opts.waitlist] - also seed a nearer approved
 *   appointment (the one a test cancels, `fixture.waitlist_dni`/
 *   `_session`) plus a later one that already opted into the waitlist for
 *   it (fixture.py's --waitlist, Tier 3 slice 1).
 * @param {boolean} [opts.queueLater] - also seed one approved appointment still ahead
 *   today (now + 4 h, capped at 23:59; fixture.py's --queue-later): the day queue
 *   offers «Cancelar turno» only before its time. `fixture.queue_later` is its name.
 * @param {boolean} [opts.hsiChanges] - also seed three approved-then-cancelled turnos
 *   for the HSI extension's panel of changes (fixture.py's --hsi-changes): one of
 *   yesterday and two still ahead. `fixture.hsi_changes.{pasado,rechazado,siguiente}`
 *   are `{id, paciente, dia, hora}`.
 * @param {boolean} [opts.queue] - also seed three TODAY-dated approved
 *   appointments across attendance states for "Pacientes de hoy"
 *   (fixture.py's --queue, Tier 3 slice 2; `fixture.queue_pendiente/
 *   _presente/_ausente` are the seeded patients' display names).
 * @param {boolean} [opts.account] - also seed a fresh patient account +
 *   open session with no appointment yet (fixture.py's --account, Tier 3
 *   slice 2 -- for a test that books ONE through the wizard itself).
 * @param {boolean|number} [opts.requests] - also seed two (or N) FICTIONAL pending
 *   booking_requests (fixture.py's --requests, Tier 5 /admin/solicitudes)
 *   for the direct-screen e2e: two rows, a detail, one reject through the
 *   real /api/admin/requests/<id>/reject DB path. `fixture.requests` (an
 *   array of `{id, dni, nombre}`) lands in the returned `fixture` object.
 * @param {boolean} [opts.bookingFirstPaint] - seed every real open slot of
 *   one public synthetic schedule as approved, so the booking first-paint
 *   gate reaches the genuine empty-availability fallback through the real
 *   slot API. The fixture derives the slots through patient_web/site_*.py, never from
 *   a duplicated schedule or intercepted HTTP response.
 * @param {boolean} [opts.doctorPatients] - seed the fictional Juan González
 *   roster/consultation plus a linked doctor account for `/admin/my-patients`.
 * @param {boolean} [opts.fresh] - an installation nobody has configured yet: no operator
 *   in the database and no environment admin, so `/setup` (the first-run wizard) is open
 *   (fixture.py's --fresh). Nothing else may be combined with it.
 * @param {boolean} [opts.launchBrowser] - launch the harness' own Chromium
 *   (default). The @playwright/test fixtures (tests/e2e-pw/fixtures.mjs) pass
 *   false: the runner owns the browser, so its trace/retry settings apply.
 * @param {string} [opts.host] - address the server binds and the browser
 *   uses (default loopback). The portal-gateway spec binds a private LAN
 *   address, because the gateway trust boundary rejects a loopback peer.
 * @returns {Promise<object>} `{ ok:true, baseUrl, fixture, ... }` or
 *   `{ ok:false, reason }`.
 */
export async function setupHarness({
  dbName, intake = false, doorMedico, totp = false, waitlist = false,
  queue = false, queueLater = false, hsiChanges = false, account = false, requests = false, doctorPatients = false,
  bookingFirstPaint = false, hsi = false, fresh = false, env = {}, host = "127.0.0.1",
  launchBrowser = true,
} = {}) {
  const logicalDbName = dbName;
  dbName = allocateDbName(logicalDbName);
  // 1) Playwright present?
  let playwright;
  try {
    playwright = await import("playwright");
  } catch {
    return { ok: false, reason: "playwright no está instalado (npm install --no-save playwright)" };
  }

  // 2) Chromium binary present? (playwright package can be installed
  // without ever running `playwright install chromium`.)
  let browser = null;
  try {
    if (launchBrowser) browser = await playwright.chromium.launch();
  } catch (e) {
    return {
      ok: false,
      reason: `chromium no disponible (correr: npx playwright install chromium) -- ${e.message}`,
    };
  }

  // 3) Postgres + app-level seed (fresh-install gate, admin operator row,
  // optional intake fixtures) via the Python fixture -- see fixture.py's
  // own docstring for why this can't just be done in JS.
  const fixtureArgs = ["tests/e2e/fixture.py", "setup", "--db", dbName];
  if (intake) {
    fixtureArgs.push("--intake");
    if (doorMedico) fixtureArgs.push("--door-medico", doorMedico);
  }
  if (totp) fixtureArgs.push("--totp");
  if (waitlist) fixtureArgs.push("--waitlist");
  if (queue) fixtureArgs.push("--queue");
  if (queueLater) fixtureArgs.push("--queue-later");
  if (hsiChanges) fixtureArgs.push("--hsi-changes");
  if (account) fixtureArgs.push("--account");
  if (requests) fixtureArgs.push("--requests", String(requests === true ? 2 : requests));
  if (doctorPatients) fixtureArgs.push("--doctor-patients");
  if (bookingFirstPaint) fixtureArgs.push("--booking-first-paint");
  if (fresh) fixtureArgs.push("--fresh");
  const setupRun = await runPython(fixtureArgs);
  let fixture;
  try {
    fixture = JSON.parse(setupRun.out.trim().split("\n").pop() || "{}");
  } catch {
    fixture = {};
  }
  if (setupRun.code !== 0 || fixture.error) {
    await browser?.close().catch(() => {});
    return {
      ok: false,
      reason: `fixture de datos no disponible: ${fixture.error || setupRun.err || "código " + setupRun.code}`,
    };
  }

  // fixture.py never prints the credential-bearing DSN (item 3) -- it
  // writes it to a short-lived mode-0600 file and hands back only that
  // file's path. Read it once, then delete it immediately; nothing here
  // logs `dbUrl`.
  let dbUrl = null;
  if (fixture.url_file) {
    try {
      dbUrl = fs.readFileSync(fixture.url_file, "utf8").trim();
    } finally {
      fs.rmSync(fixture.url_file, { force: true });
    }
  }
  if (!dbUrl) {
    await browser?.close().catch(() => {});
    await runPython(["tests/e2e/fixture.py", "teardown", "--db", dbName]);
    return { ok: false, reason: "fixture no devolvió una URL de base de datos utilizable" };
  }

  // Hermetic env file (item 2): intentionally empty, so config.py's
  // dotenv loader has somewhere harmless to point instead of the
  // checkout's real .env. Written fresh per run, deleted in
  // teardownHarness -- never reused across runs and never the same path
  // as the real .env.
  const envDir = fs.mkdtempSync(path.join(os.tmpdir(), "sana-e2e-env-"));
  const envFile = path.join(envDir, "e2e.env");
  fs.writeFileSync(
    envFile,
    "# hermetic E2E run (tests/e2e/lib/harness.mjs) -- intentionally empty.\n" +
      "# The real checkout .env is never opened while TURNOS_ENV_FILE points here.\n",
    { mode: 0o600 }
  );

  // 4) Start the real app server against the throwaway database, the
  // synthetic pack (item 4/5), and the hermetic env override (item 2).
  //
  // Native-migration campaign (docs/roadmap/native-migration-
  // campaign-20260826.md): the legacy `python3 server.py` topology is
  // retired, so the harness always spawns the one real process entrypoint,
  // asgi_main.py, with TURNOS_ASGI=1 fixed in the CHILD env (asgi_main
  // refuses to boot without it). Both managed profiles (`local-ci`,
  // `local-ci-asgi`) therefore exercise the same single topology here --
  // the flag in THIS process's env no longer picks a server.
  const port = await getFreePort();
  const baseUrl = `http://${host}:${port}`;
  const serverProc = spawn(
    "python3",
    ["asgi_main.py"],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        DATABASE_URL: dbUrl,
        PORT: String(port),
        HOST: host,
        TURNOS_ASGI: "1",
        TURNOS_FAKE_AI: "1",
        TURNOS_SKIP_HSI: "1",
        TURNOS_DNI_CLAIMS: "0",
        TURNOS_HSI_SYNC_OFF: "1",
        TURNOS_HSI_RESYNC_OFF: "1",
        TURNOS_REMINDERS_OFF: "1",
        TURNOS_WAITLIST_OFF: "1",
        TURNOS_BACKUP_OFF: "1",
        TURNOS_AI_HEALTH_OFF: "1",
        // A fresh installation has neither a database operator nor an environment admin.
        TURNOS_ADMIN_USER: fresh ? "" : ADMIN_USER,
        TURNOS_ADMIN_PASS: fresh ? "" : ADMIN_PASS,
        ...(fresh ? { TURNOS_SETUP_SKIP_GEOIP: "1" } : {}),
        PACK_CHAIN: GATE_PACK_CHAIN,
        // The ONLY mechanism keeping this run from consulting the
        // checkout's real .env (which may hold real TOTP/Turnstile
        // secrets) -- see config.py's _env_path(). No key is blanked
        // here: TOTP/Turnstile simply aren't in `envFile`, so they
        // resolve to their normal empty default, same as a fresh
        // install would see them.
        TURNOS_ENV_FILE: envFile,
        // Opt-in only (2026-09): HSI moved behind an explicit connector
        // setting, so `/admin/hsi` 404s unless a spec asks for it.
        ...(hsi ? { CONNECTOR_EMR_SYNC: "hsi" } : {}),
        ...env,
      },
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  let serverLog = "";
  serverProc.stdout.on("data", (d) => (serverLog += d));
  serverProc.stderr.on("data", (d) => (serverLog += d));

  try {
    await waitForReady(baseUrl);
    await warmUp(baseUrl);
  } catch (e) {
    serverProc.kill("SIGKILL");
    await browser?.close().catch(() => {});
    fs.rmSync(envDir, { recursive: true, force: true });
    await runPython(["tests/e2e/fixture.py", "teardown", "--db", dbName]);
    return { ok: false, reason: `${e.message}\n--- server log (tail) ---\n${serverLog.slice(-2000)}` };
  }

  return {
    ok: true,
    baseUrl,
    browser,
    serverProc,
    dbName,
    logicalDbName,
    fixture,
    envDir,
    getServerLog: () => serverLog,
  };
}

/**
 * A fresh 6-digit TOTP code for a base32 `secret` (e.g. `fixture.totp_secret`
 * from `setupHarness({ totp: true })`) -- computed by shelling out to
 * fixture.py's `totp-code`, which uses the same maintained pyotp dependency
 * as production
 * DIRECTLY (never reimplemented in JS, see fixture.py's module docstring).
 * Call this right before typing the code so a slow page/server can never
 * race the 30s window a code snapshotted at `setup` time might have.
 * @param {string} secret
 * @returns {Promise<string>} 6-digit code, or throws if fixture.py failed.
 */
export async function totpCode(secret) {
  const r = await runPython(["tests/e2e/fixture.py", "totp-code", "--secret", secret]);
  let out;
  try {
    out = JSON.parse(r.out.trim().split("\n").pop() || "{}");
  } catch {
    out = {};
  }
  if (r.code !== 0 || !out.code) {
    throw new Error(`totp-code falló: ${out.error || r.err || "código " + r.code}`);
  }
  return out.code;
}

/**
 * Read-only: `appointment_offers` created recently, grouped by status
 * ([[estado, cantidad], ...] -- fixture.py's `query-offers`, mirroring
 * db_reports.reports_appointment_offers()). Used to assert the real observable side
 * effect of a patient cancel promoting a waitlisted candidate, without a
 * test re-deriving waitlist.py's schema.
 * @param {string} dbName
 * @returns {Promise<Array<[string, number]>>}
 */
export async function queryOffers(dbName) {
  const r = await runPython(["tests/e2e/fixture.py", "query-offers", "--db", dbName]);
  let out;
  try {
    out = JSON.parse(r.out.trim().split("\n").pop() || "{}");
  } catch {
    out = {};
  }
  if (r.code !== 0) throw new Error(`query-offers falló: ${r.err || "código " + r.code}`);
  return out.ofertas || [];
}

/**
 * Mirror ONE appointment "created in HSI" on the first open slot of a service
 * (fixture.py `mirror-hsi-busy`), or remove it with `{ clear: true }`.
 * @param {string} dbName
 * @param {{ clear?: boolean, service?: string }} [opts]
 * @returns {Promise<{servicio_id?: string, dia?: string, hora?: string, medico?: string}>}
 */
export async function mirrorHsiBusy(dbName, opts = {}) {
  const args = ["tests/e2e/fixture.py", "mirror-hsi-busy", "--db", dbName];
  if (opts.clear) args.push("--clear");
  if (opts.service) args.push("--service", opts.service);
  const r = await runPython(args);
  if (r.code !== 0) throw new Error(`mirror-hsi-busy falló: ${r.err || r.out || "código " + r.code}`);
  return JSON.parse(r.out.trim().split("\n").pop() || "{}");
}

/**
 * Read-only: the most recent booking_requests row for `dni` (fixture.py's
 * `query-request`) -- `{ found:false }` if none exists yet, else
 * `{ found:true, estado, servicio_id, ai_verdict }`.
 * @param {string} dbName
 * @param {string} dni
 * @returns {Promise<object>}
 */
export async function queryRequest(dbName, dni) {
  const r = await runPython(["tests/e2e/fixture.py", "query-request", "--db", dbName, "--dni", dni]);
  let out;
  try {
    out = JSON.parse(r.out.trim().split("\n").pop() || "{}");
  } catch {
    out = { found: false };
  }
  if (r.code !== 0) throw new Error(`query-request falló: ${r.err || "código " + r.code}`);
  return out;
}

/**
 * Read-only: the most recent nota-backed booking_requests row for `nota`
 * (fixture.py's `query-nota`; a staff walk-in stores the typed note as
 * `nombre` with dni NULL) -- `{ found:false }` if none exists yet, else
 * `{ found:true, estado, servicio_id, nombre }`.
 * @param {string} dbName
 * @param {string} nota
 * @returns {Promise<object>}
 */
export async function queryNota(dbName, nota) {
  const r = await runPython(["tests/e2e/fixture.py", "query-nota", "--db", dbName, "--nota", nota]);
  let out;
  try {
    out = JSON.parse(r.out.trim().split("\n").pop() || "{}");
  } catch {
    out = { found: false };
  }
  if (r.code !== 0) throw new Error(`query-nota falló: ${r.err || "código " + r.code}`);
  return out;
}

/**
 * Tier 5 slice 4: flips ENABLED_MODULES live against the running server
 * subprocess's own database (fixture.py's `set-enabled-modules`), the
 * SAME `catalogo` config row `config.guardar()` writes -- lets an e2e
 * test prove "disable a module -> its screen 404s + its nav entry
 * disappears" through a real browser reload, no restart. `value=""`
 * restores the real default (every discovered module enabled). The
 * server's own config.py cache has a 3s TTL (config.py's `_TTL`) --
 * callers should poll/reload rather than assert immediately after this
 * resolves.
 * @param {string} dbName
 * @param {string} value e.g. "turnos" (intake excluded) or "" (default: all enabled)
 */
export async function setEnabledModules(dbName, value) {
  const r = await runPython([
    "tests/e2e/fixture.py", "set-enabled-modules", "--db", dbName, "--value", value,
  ]);
  if (r.code !== 0) throw new Error(`set-enabled-modules falló: ${r.err || "código " + r.code}`);
}

/**
 * Flips the active THEME live against the running server subprocess's own
 * database (fixture.py's `set-theme`, which calls theme.save() -- the same
 * `catalogo` config row the admin Appearance screen writes). The one
 * consumer is the Pinamar-only /admin/plan screen, which 404s under every
 * other theme. The server caches theme.current() for ~3s (core/theme.py's
 * _TTL) -- callers must wait out (or poll past) that TTL before asserting,
 * and ALWAYS restore "humanrounds" when done.
 * @param {string} dbName
 * @param {string} value "pinamar" | "humanrounds"
 */
export async function setTheme(dbName, value) {
  const r = await runPython([
    "tests/e2e/fixture.py", "set-theme", "--db", dbName, "--value", value,
  ]);
  let out;
  try {
    out = JSON.parse(r.out.trim().split("\n").pop() || "{}");
  } catch {
    out = {};
  }
  if (r.code !== 0 || out.error) {
    throw new Error(`set-theme falló: ${out.error || r.err || "código " + r.code}`);
  }
}

/**
 * Read-only: the usuarios row for `dni` (fixture.py's `query-user`, via
 * db_accounts.get_user -- pass_hash never leaves Python). `{ found:false }` if none
 * exists yet -- lets a test prove a patient created through the real
 * /admin/pacientes POST actually landed in the padrón.
 * @param {string} dbName
 * @param {string} dni
 * @returns {Promise<object>}
 */
export async function queryUser(dbName, dni) {
  const r = await runPython([
    "tests/e2e/fixture.py", "query-user", "--db", dbName, "--dni", dni,
  ]);
  let out;
  try {
    out = JSON.parse(r.out.trim().split("\n").pop() || "{}");
  } catch {
    out = { found: false };
  }
  if (r.code !== 0) throw new Error(`query-user falló: ${r.err || "código " + r.code}`);
  return out;
}

/**
 * Read-only: the latest consultations row for an interview id (fixture.py's
 * `query-consultation`, via db_clinical.consultation_latest_for_interview) --
 * `{ found:false }` until a consultation was confirmed, then
 * `{ found:true, notas, diagnosticos_n, receta, orden, ... }` (tokens as
 * booleans only). Proves /admin/consult's real POST .../confirm persisted.
 * @param {string} dbName
 * @param {string|number} interviewId
 * @returns {Promise<object>}
 */
export async function queryConsultation(dbName, interviewId) {
  const r = await runPython([
    "tests/e2e/fixture.py", "query-consultation", "--db", dbName,
    "--interview", String(interviewId),
  ]);
  let out;
  try {
    out = JSON.parse(r.out.trim().split("\n").pop() || "{}");
  } catch {
    out = { found: false };
  }
  if (r.code !== 0) throw new Error(`query-consultation falló: ${r.err || "código " + r.code}`);
  return out;
}

// accounts.py's COOKIE_NAME ("st_ses") -- a patient's session cookie, same
// shape accounts._set_cookie() sends after a real login/register. Tier 3
// slice 1/2 tests that need an ALREADY-authenticated patient (cancel/
// waitlist, booking a fresh appointment) set this directly via
// `page.context().addCookies([...])` instead of driving the login form --
// the form itself (dni+password, #sc-dni/#sc-pass/#sc-submit) is exercised
// elsewhere; what these tests cover is what happens AFTER identity is
// established (a cancel, a submit), same principle intake.spec.mjs already
// uses for its token-authorized entry points.
export const PATIENT_COOKIE_NAME = "st_ses";

export function patientCookie(baseUrl, sessionToken) {
  return { name: PATIENT_COOKIE_NAME, value: sessionToken, url: baseUrl };
}

export async function teardownHarness(state) {
  if (!state || !state.ok) return;
  await state.browser?.close().catch(() => {});
  if (state.serverProc && !state.serverProc.killed) {
    state.serverProc.kill("SIGTERM");
    await new Promise((resolve) => {
      const t = setTimeout(() => {
        state.serverProc.kill("SIGKILL");
        resolve();
      }, 3000);
      state.serverProc.once("exit", () => {
        clearTimeout(t);
        resolve();
      });
    });
  }
  if (state.envDir) fs.rmSync(state.envDir, { recursive: true, force: true });
  const teardownRun = await runPython(["tests/e2e/fixture.py", "teardown", "--db", state.dbName]);
  let teardown;
  try {
    teardown = JSON.parse(teardownRun.out.trim().split("\n").pop() || "{}");
  } catch {
    teardown = {};
  }
  if (teardownRun.code !== 0 || teardown.ok !== true) {
    throw new Error(`fixture teardown failed for ${state.dbName}: ${teardown.warn || teardownRun.err || "código " + teardownRun.code}`);
  }
}
