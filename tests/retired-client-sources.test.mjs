import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, readdirSync, copyFileSync, symlinkSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const checker = fileURLToPath(new URL('../scripts/check-retired-client-sources.mjs', import.meta.url));
const root = fileURLToPath(new URL('..', import.meta.url));
const check = (dir) => spawnSync(process.execPath, [checker, dir], { encoding: 'utf8' });

test('checkout contains no retired client implementations, including ignored files', () => {
  const result = check(root);
  assert.equal(result.status, 0, result.stderr);
});

for (const name of ['auth-test.js', 'passkeys.mjs', 'turnstile.mjs', 'my-requests.js', 'lab-results.js', 'admin-alerts.js', 'admin-reports.js', 'admin-theme.js', 'admin-hsi.js', 'admin-my-appointments.js', 'admin-doctor-patients.js',
'admin-patients.js', 'admin-pacientes.js', 'admin-preparations.js', 'admin-preparaciones.js']) {
for (const shape of ['file', 'directory', 'dangling symlink']) {
  test(`${name} retirement rejects a restored ${shape}, but permits its native module`, () => {
    const fixture = mkdtempSync(join(tmpdir(), 'turnos-auth-retirement-'));
    try {
      mkdirSync(join(fixture, 'static', 'platform'), { recursive: true });
      const nativeName = name === 'admin-theme.js' ? 'admin-appearance.mjs' : name.replace(/\.js$/, '.mjs');
      writeFileSync(join(fixture, 'static', 'platform', nativeName), 'export const mount = () => null;');
      assert.equal(check(fixture).status, 0, 'canonical module must remain allowed');
      const retired = join(fixture, name);
      if (shape === 'file') writeFileSync(retired, '// synthetic restored twin');
      else if (shape === 'directory') mkdirSync(retired);
      else symlinkSync('missing-auth-target', retired);
      const result = check(fixture);
      assert.equal(result.status, 1, result.stderr);
      assert.ok(result.stderr.includes(`Retired client source remains: ${name}`), result.stderr);
    } finally {
      rmSync(fixture, { recursive: true, force: true });
    }
  });
}
}

test('actual pre-commit rejects a restored simulator before executing verification', () => {
  const fixture = mkdtempSync(join(tmpdir(), 'turnos-auth-hook-'));
  try {
    mkdirSync(join(fixture, 'scripts'));
    copyFileSync(checker, join(fixture, 'scripts/check-retired-client-sources.mjs'));
    copyFileSync(join(root, 'scripts/pre-commit.sh'), join(fixture, 'scripts/pre-commit.sh'));
    copyFileSync(join(root, 'scripts/orphan_guard.sh'), join(fixture, 'scripts/orphan_guard.sh'));
    writeFileSync(join(fixture, 'auth-test.js'), '// synthetic restored twin');
    const result = spawnSync('/bin/bash', ['scripts/pre-commit.sh'], {
      cwd: fixture, encoding: 'utf8', timeout: 10000,
      env: { ...process.env, SKIP_PRECOMMIT: '0' },
    });
    assert.ifError(result.error);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stderr, /Retired client source remains: auth-test\.js/);
    assert.match(result.stdout, /blocked by a physically restored retired client source/);
    assert.doesNotMatch(result.stdout, /frontend suite \(node --test\)/);
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});

test('retirement gate rejects a restored file, directory and dangling symlink', () => {
  const fixture = mkdtempSync(join(tmpdir(), 'turnos-retirement-'));
  try {
    assert.equal(check(fixture).status, 0);
    mkdirSync(join(fixture, 'static', 'platform'), { recursive: true });
    writeFileSync(join(fixture, 'static', 'platform', 'dni-scan.mjs'), 'export const parsePayload = () => null;');
    writeFileSync(join(fixture, 'static', 'platform', 'dni-barcode.mjs'), 'export const readDni = async () => null;');
    writeFileSync(join(fixture, 'static', 'platform', 'age.mjs'), 'export const edadDesde = () => null;');
    writeFileSync(join(fixture, 'static', 'platform', 'prep.mjs'), 'export const secciones = () => [];');
    assert.equal(check(fixture).status, 0, 'the canonical platform destinations stay allowed');
    // Content and Git visibility do not change the physical retirement rule.
    writeFileSync(join(fixture, 'account-form.js'), 'window.SanaAccount = {};');
    writeFileSync(join(fixture, 'dependents.js'), 'window.SanaDependents = {};');
    writeFileSync(join(fixture, 'admin-charts.js'), 'window.SanaCharts = {};');
    writeFileSync(join(fixture, 'admin-hours-editor.js'), 'window.SanaHoursEditor = {};');
    writeFileSync(join(fixture, 'admin-users.js'), 'window.SanaUsers = {};');
    writeFileSync(join(fixture, 'admin-prompts.js'), 'window.SanaPrompts = {};');
    writeFileSync(join(fixture, 'admin-legal.js'), 'window.SanaLegal = {};');
    writeFileSync(join(fixture, 'admin-knowledge.js'), 'window.SanaKnowledge = {};');
    writeFileSync(join(fixture, 'chat-ui.js'), 'window.SanaChatUI = {};');
    writeFileSync(join(fixture, 'chat-widget.js'), 'window.SanaChat = {};');
    writeFileSync(join(fixture, 'admin-login.js'), 'window.SanaAdminLogin = {};');
    writeFileSync(join(fixture, 'admin-preferences.js'), 'window.SanaPreferences = {};');
    writeFileSync(join(fixture, 'admin-session.js'), 'window.SanaSession = {};');
    writeFileSync(join(fixture, 'admin-solicitudes.js'), 'window.SanaSolicitudes = {};');
    writeFileSync(join(fixture, 'search.js'), 'window.SanaSearch = {};');
    writeFileSync(join(fixture, 'listing.js'), 'window.SanaListing = {};');
    writeFileSync(join(fixture, 'offer.js'), 'window.SanaOffer = {};');
    writeFileSync(join(fixture, 'week.js'), 'window.SanaWeek = {};');
    symlinkSync('missing-confirm-target', join(fixture, 'confirm.js'));
    writeFileSync(join(fixture, 'ui-core.js'), 'window.UICore = {};');
    writeFileSync(join(fixture, 'specialty-browser.js'), 'window.SpecialtyBrowser = {};');
    writeFileSync(join(fixture, 'app.html'), '<html></html>');
    writeFileSync(join(fixture, 'panel.html'), '<html></html>');
    writeFileSync(join(fixture, 'directorio.html'), '<html></html>');
    mkdirSync(join(fixture, 'tiles.js'));
    symlinkSync('missing-target', join(fixture, 'passkeys.js'));
    symlinkSync('missing-dni-scan-target', join(fixture, 'dni-scan.mjs'));
    writeFileSync(join(fixture, 'dni-barcode.mjs'), 'export const readDni = async () => null;');
    symlinkSync('missing-age-target', join(fixture, 'age.mjs'));
    writeFileSync(join(fixture, 'prep.mjs'), 'export const secciones = () => [];');
    const result = check(fixture);
    assert.equal(result.status, 1);
    assert.ok(result.stderr.includes('confirm.js'), 'a dangling classic confirmation alias must be rejected');
    for (const name of ['account-form.js', 'dependents.js', 'admin-charts.js', 'admin-hours-editor.js', 'chat-ui.js', 'chat-widget.js', 'admin-login.js', 'admin-preferences.js', 'admin-session.js', 'admin-solicitudes.js', 'admin-users.js', 'admin-prompts.js', 'admin-legal.js', 'admin-knowledge.js', 'search.js', 'listing.js', 'offer.js', 'week.js', 'tiles.js', 'passkeys.js', 'age.mjs', 'dni-scan.mjs', 'dni-barcode.mjs', 'prep.mjs', 'ui-core.js', 'specialty-browser.js', 'app.html', 'panel.html', 'directorio.html']) {
      assert.ok(result.stderr.includes(name), result.stderr);
    }
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});

test('routes gate refuses to report success without a test database', () => {
  const env = { ...process.env };
  delete env.TEST_DATABASE_URL;
  delete env.DATABASE_URL;
  const result = spawnSync('/bin/bash', ['scripts/run_routes_tests.sh'], {
    cwd: root, env, encoding: 'utf8',
  });
  assert.equal(result.status, 2, result.stderr);
  assert.match(result.stderr, /managed test database is required/);
});

test('failed pre-commit retains the complete report, not only its final passing tests', () => {
  const fixture = mkdtempSync(join(tmpdir(), 'turnos-hook-report-'));
  try {
    mkdirSync(join(fixture, 'scripts'));
    mkdirSync(join(fixture, 'bin'));
    copyFileSync(join(root, 'scripts/pre-commit.sh'), join(fixture, 'scripts/pre-commit.sh'));
    copyFileSync(join(root, 'scripts/orphan_guard.sh'), join(fixture, 'scripts/orphan_guard.sh'));
    writeFileSync(join(fixture, 'bin/python3'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    writeFileSync(join(fixture, 'scripts/run_routes_tests.sh'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    // The suite runs through scripts/run_frontend_tests.mjs (two lanes).
    writeFileSync(join(fixture, 'bin/node'), '#!/bin/sh\nif [ "$1" = "--test" ] || [ "$1" = "scripts/run_frontend_tests.mjs" ]; then\n echo "not ok - first failing case"\n i=0; while [ "$i" -lt 100 ]; do echo "ok - later case"; i=$((i+1)); done\n exit 1\nfi\nexit 0\n', { mode: 0o755 });
    const reports = join(fixture, 'reports');
    const result = spawnSync('/bin/bash', ['scripts/pre-commit.sh'], {
      cwd: fixture, encoding: 'utf8', env: {
        ...process.env, PATH: join(fixture, 'bin') + ':' + process.env.PATH,
        SKIP_PRECOMMIT: '0', TEST_DATABASE_URL: 'synthetic-unused', TURNOS_TEST_ARTIFACT_DIR: reports,
      },
    });
    assert.equal(result.status, 1, result.stderr);
    assert.match(result.stdout, /Full failing frontend report preserved at:/);
    const files = readdirSync(reports);
    assert.equal(files.length, 1);
    assert.match(readFileSync(join(reports, files[0]), 'utf8'), /^not ok - first failing case/);
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});

test('pre-commit rejects an Account render failure even when frontend and routes pass', () => {
  const fixture = mkdtempSync(join(tmpdir(), 'turnos-hook-accounts-'));
  try {
    mkdirSync(join(fixture, 'scripts'));
    mkdirSync(join(fixture, 'bin'));
    copyFileSync(join(root, 'scripts/pre-commit.sh'), join(fixture, 'scripts/pre-commit.sh'));
    copyFileSync(join(root, 'scripts/orphan_guard.sh'), join(fixture, 'scripts/orphan_guard.sh'));
    writeFileSync(join(fixture, 'scripts/run_routes_tests.sh'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    writeFileSync(join(fixture, 'bin/node'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    writeFileSync(join(fixture, 'bin/python3'), '#!/bin/sh\ncase "$*" in\n *"discover -s tests -p test_accounts_jinja.py"*) echo "FAIL: exact Account render changed"; exit 1;;\n *) exit 0;;\nesac\n', { mode: 0o755 });
    const reports = join(fixture, 'reports');
    const result = spawnSync('/bin/bash', ['scripts/pre-commit.sh'], {
      cwd: fixture, encoding: 'utf8', env: {
        ...process.env, PATH: join(fixture, 'bin') + ':' + process.env.PATH,
        SKIP_PRECOMMIT: '0', TEST_DATABASE_URL: 'synthetic-unused', TURNOS_TEST_ARTIFACT_DIR: reports,
      },
    });
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stdout, /Full failing Accounts report preserved at:/);
    const files = readdirSync(reports);
    assert.equal(files.length, 1);
    assert.match(readFileSync(join(reports, files[0]), 'utf8'), /exact Account render changed/);
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});
