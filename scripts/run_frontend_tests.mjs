#!/usr/bin/env node
// run_frontend_tests.mjs — the frontend suite (tests/*.test.mjs) in two lanes.
//
// Pure-JS files run in parallel, as before. Files that drive a real Chromium
// (they import playwright) run one file at a time AFTER them: in one parallel
// pool, twenty-odd Chromiums started together with the rest of the suite, and
// on a loaded machine the per-action budgets of a browser test (2.5–5 s) and
// the launch inside a test's own timeout lost the race — «real Account email
// save…» timed out at 20 s, Crear turno at 30 s, a consult click at 2.5 s
// (23/10), each green alone. The browser lane still runs every file; only the
// contention between them is gone.
//
// Usage: node scripts/run_frontend_tests.mjs   (exit 0 only when both lanes pass)
import { readdirSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { availableParallelism } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEST_DIR = path.join(ROOT, "tests");

// import … from "playwright", import("playwright") or require("playwright")
export const DRIVES_CHROMIUM = /(?:\bfrom|\bimport\(|\brequire\()\s*["']playwright["']/;

export function lanes(dir = TEST_DIR) {
  const files = readdirSync(dir).filter((f) => f.endsWith(".test.mjs")).sort();
  const browser = [], pure = [];
  for (const f of files) {
    const src = readFileSync(path.join(dir, f), "utf8");
    (DRIVES_CHROMIUM.test(src) ? browser : pure)
      .push(path.join("tests", f));
  }
  return { pure, browser };
}

// A file that never ends (its event loop stays alive after the tests passed) is invisible to
// `node --test --test-timeout`, which only bounds single tests: one such file hung the whole CI job
// (12/11). Every file therefore runs in its own `node --test` process with a deadline; the hung one
// is killed (whole process group, so a Chromium it launched goes too) and the run fails naming it.
// Pure-JS files finish in seconds (the slowest takes well under a minute): 5 minutes; a browser file gets 15.
export const FILE_TIMEOUT_MS = Number(process.env.FRONTEND_FILE_TIMEOUT_MS) || 15 * 60 * 1000;
export const PURE_FILE_TIMEOUT_MS = Number(process.env.FRONTEND_FILE_TIMEOUT_MS) || 5 * 60 * 1000;

const SUMMARY_LINE = /^(?:# |ℹ )(tests|suites|pass|fail|cancelled|skipped|todo|duration_ms) ([\d.]+)\s*$/;

function runOne(file, cwd, timeoutMs) {
  return new Promise((resolve) => {
    // NODE_TEST_CONTEXT marks "inside a node:test file": left in place, the child refuses to run files.
    const env = { ...process.env };
    delete env.NODE_TEST_CONTEXT;
    const child = spawn(process.execPath, ["--test", file], {
      cwd, env, stdio: ["ignore", "pipe", "pipe"], detached: true,
    });
    let out = "", err = "", timedOut = false, settled = false;
    child.stdout.on("data", (d) => { out += d; });
    child.stderr.on("data", (d) => { err += d; });
    const killTree = () => {
      try { process.kill(-child.pid, "SIGKILL"); } catch { try { child.kill("SIGKILL"); } catch { /* gone */ } }
    };
    // 'close' also waits for the pipes, which a grandchild that escaped the group (a detached
    // browser) can hold open: the deadline resolves the file itself, shortly after the kill.
    const timer = setTimeout(() => { timedOut = true; killTree(); setTimeout(() => finish(1), 2000); }, timeoutMs);
    const finish = (status) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ file, out, err, timedOut, status: timedOut ? 1 : (status === null ? 1 : status) });
    };
    child.on("error", () => finish(1));
    child.on("close", (status) => finish(status));
  });
}

// Runs `files` (relative to `cwd`), `concurrency` at a time. Returns the combined log with ONE
// summary block (the test-count floor sums every "tests N" line it finds), the failing-test blocks
// of the files that failed, and the list of files killed by the deadline.
export async function runFiles(files, { cwd = ROOT, timeoutMs = FILE_TIMEOUT_MS, concurrency = 1 } = {}) {
  const results = new Array(files.length);
  let next = 0;
  const worker = async () => {
    while (next < files.length) {
      const i = next++;
      results[i] = await runOne(files[i], cwd, timeoutMs);
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(concurrency, files.length)) }, worker));
  const totals = {};
  let output = "", failures = "", status = 0;
  const timedOut = [];
  for (const r of results) {
    const kept = [];
    for (const line of r.out.split("\n")) {
      const m = SUMMARY_LINE.exec(line);
      if (m) totals[m[1]] = (totals[m[1]] || 0) + Number(m[2]);
      else kept.push(line);
    }
    let body = kept.join("\n");
    const at = body.lastIndexOf("✖ failing tests:");
    if (r.status && at !== -1) failures += body.slice(at) + "\n";
    if (r.timedOut) {
      timedOut.push(r.file);
      // A killed file prints no summary: count it as one failed, cancelled test so "fail 0" never reads healthy.
      totals.tests = (totals.tests || 0) + 1;
      totals.fail = (totals.fail || 0) + 1;
      totals.cancelled = (totals.cancelled || 0) + 1;
      const msg = `TIMEOUT: ${r.file} did not finish in ${Math.round(timeoutMs / 1000)} s and was killed`;
      body += `\n${msg}\n`;
      failures += `✖ ${msg}\n`;
    }
    output += body + (r.err ? r.err : "");
    if (r.status) status = 1;
  }
  if (timedOut.length) failures = `✖ failing tests:\n${failures}`;
  output += "\n";
  for (const k of ["tests", "suites", "pass", "fail", "cancelled", "skipped", "todo", "duration_ms"]) {
    output += `ℹ ${k} ${totals[k] || 0}\n`;
  }
  return { status, output, failures, timedOut };
}

// Each lane's own "✖ failing tests:" block is repeated at the very end, so a
// caller that shows only the tail of the log still sees both lanes' failures.
async function run(files, concurrency, timeoutMs) {
  const r = await runFiles(files, { concurrency, timeoutMs });
  process.stdout.write(r.output);
  return { status: r.status, failures: r.failures };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { pure, browser } = lanes();
  console.log(`frontend: ${pure.length} file(s) in parallel, then ${browser.length} browser file(s) one at a time`);
  const pureLane = await run(pure, Math.max(1, availableParallelism()), PURE_FILE_TIMEOUT_MS);
  const browserLane = browser.length ? await run(browser, 1, FILE_TIMEOUT_MS) : { status: 0, failures: "" };
  for (const [name, lane] of [["pure", pureLane], ["browser", browserLane]]) {
    if (lane.status) console.log(`\n---- ${name} lane ----\n${lane.failures || "(no failing-test block; see above)"}`);
  }
  console.log(`frontend: pure lane exit=${pureLane.status}, browser lane exit=${browserLane.status}`);
  // Not an immediate exit call: both lanes' logs were just written in one go, and exiting at once cuts a
  // write that is still pending on a pipe (`| tee frontend.log` on the GitLab runner kept 74 KB of
  // the log, no TAP summary — «only 0 tests ran»). Setting the exit code lets stdout drain first.
  process.exitCode = pureLane.status || browserLane.status ? 1 : 0;
}
