import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { lanes, runFiles, DRIVES_CHROMIUM } from '../scripts/run_frontend_tests.mjs';

const root = new URL('../', import.meta.url);

// 23/10: browser files started together with the whole suite timed out on a
// loaded machine (Account save 20 s, Crear turno 30 s, consult click 2.5 s).
test('every frontend test file runs exactly once, and every Chromium file in the serial lane', () => {
  const { pure, browser } = lanes();
  const all = readdirSync(new URL('tests/', root)).filter((f) => f.endsWith('.test.mjs')).map((f) => 'tests/' + f);
  assert.deepEqual([...pure, ...browser].sort(), all.sort());
  assert.equal(new Set([...pure, ...browser]).size, all.length);
  for (const file of pure) {
    const src = readFileSync(new URL(file, root), 'utf8');
    assert.doesNotMatch(src, DRIVES_CHROMIUM, file);
    assert.doesNotMatch(src, /chromium\.launch/, file);
  }
  assert.ok(browser.includes('tests/admin-account-refresh-browser.test.mjs'));
  assert.ok(browser.includes('tests/admin-create-appointment-native.test.mjs'));
});

// 27/10: the GitLab job ran `node --test tests/*.test.mjs` under c8, so the runner's own entry block
// (20 lines of `scripts`) was never executed there: 73.10 % against a floor measured at 82.7 %.
test('pre-commit, local CI and the GitLab job run the suite through the two-lane runner', () => {
  for (const script of ['scripts/pre-commit.sh', 'scripts/ci_local.sh', '.gitlab-ci.yml']) {
    const src = readFileSync(new URL(script, root), 'utf8');
    assert.match(src, /node scripts\/run_frontend_tests\.mjs/, script);
    assert.doesNotMatch(src, /node --test tests?\/\*\.test\.mjs/, script);
  }
});

// 27/10: `process.exit()` right after the two lanes' logs were written to a pipe cut the log on the
// GitLab runner (`c8 … node scripts/run_frontend_tests.mjs | tee frontend.log`): no summary line, the
// test-count floor read 0 tests. The exit status goes through process.exitCode so stdout drains.
test('the lane runner ends by setting process.exitCode, never process.exit()', () => {
  const src = readFileSync(new URL('scripts/run_frontend_tests.mjs', root), 'utf8');
  assert.match(src, /process\.exitCode = /);
  assert.doesNotMatch(src, /process\.exit\(/);
});

// 12/11: `node --test --test-timeout` does not cover a FILE that never ends (its event loop stays
// alive after the tests passed), so one hung file hung the whole CI job. Every file now runs in
// its own process with a deadline: the hung one is killed and the run fails naming it.
test('a frontend file that never ends fails with its own name instead of hanging the run', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'lanes-hang-'));
  try {
    writeFileSync(path.join(dir, 'good.test.mjs'),
      "import test from 'node:test';\ntest('fine', () => {});\n");
    writeFileSync(path.join(dir, 'hangs.test.mjs'),
      "import test from 'node:test';\ntest('passes', () => {});\nsetInterval(() => {}, 1000);\n");
    const started = Date.now();
    const r = await runFiles(['good.test.mjs', 'hangs.test.mjs'], { cwd: dir, timeoutMs: 1500, concurrency: 2 });
    assert.ok(Date.now() - started < 15000, 'the deadline must kill the hung file');
    assert.notEqual(r.status, 0);
    assert.deepEqual(r.timedOut, ['hangs.test.mjs']);
    assert.match(r.output, /TIMEOUT: hangs\.test\.mjs/);
    assert.match(r.failures, /hangs\.test\.mjs/);
    assert.match(r.output, /^ℹ tests 2$/m, 'one aggregated summary: the passing file plus the killed one counted as a failure');
    assert.match(r.output, /^ℹ fail 1$/m, 'a killed file never reads as «fail 0»');
    assert.equal((r.output.match(/^ℹ tests /gm) || []).length, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a lane whose files all end passes and sums the tests of every file', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'lanes-ok-'));
  try {
    writeFileSync(path.join(dir, 'a.test.mjs'), "import test from 'node:test';\ntest('a1', () => {});\ntest('a2', () => {});\n");
    writeFileSync(path.join(dir, 'b.test.mjs'), "import test from 'node:test';\ntest('b1', () => {});\n");
    const r = await runFiles(['a.test.mjs', 'b.test.mjs'], { cwd: dir, timeoutMs: 20000, concurrency: 1 });
    assert.equal(r.status, 0);
    assert.deepEqual(r.timedOut, []);
    assert.match(r.output, /^ℹ tests 3$/m);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
