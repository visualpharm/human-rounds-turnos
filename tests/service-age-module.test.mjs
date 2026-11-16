import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { makeFormatGate } from './fixtures/entrevistas-format-gate.mjs';
import { stays } from './helpers/wait.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PHONE = JSON.parse(readFileSync(join(ROOT, 'packs', 'ar', 'country.json'), 'utf8')).phone;
const SERVICE = pathToFileURL(join(ROOT, 'app', 'modules', 'turnos', 'service.mjs')).href;
const GATED_AGE = pathToFileURL(join(ROOT, 'tests/fixtures/gated-age.mjs')).href;
const fileUrl = (rel) => pathToFileURL(join(ROOT, rel)).href;

globalThis.SANA_COUNTRY_PHONE = PHONE;

function response(body) {
  return Promise.resolve({ json: () => Promise.resolve(body) });
}

function staticMap(ageUrl) {
  return {
    'platform/age.mjs': ageUrl,
    'platform/dates.mjs': fileUrl('static/platform/dates.mjs'),
    'platform/format.mjs': fileUrl('static/platform/format.mjs'),
    'platform/prep.mjs': fileUrl('static/platform/prep.mjs'),
    'platform/i18n.mjs': fileUrl('static/platform/i18n.mjs'),
  };
}

function installWorld() {
  const data = {
    servicio: {
      id: 'child-imaging',
      nombre: 'Ecografía infantil',
      modo: 'walkin',
      edad: { min: null, max: 15 },
    },
    centro: { name: 'Hospital' },
  };
  const gateHost = { innerHTML: '' };
  let gateLookups = 0;
  let resolveGateLookup;
  const gateLookup = new Promise((resolve) => { resolveGateLookup = resolve; });
  const document = {
    addEventListener() {},
    removeEventListener() {},
    querySelector(selector) {
      return selector === '#svc-data' ? { textContent: JSON.stringify(data) } : null;
    },
    getElementById(id) {
      if (id === 'svc-form-host') return null;
      if (id === 'svc-gate-host') {
        gateLookups += 1;
        resolveGateLookup();
        return gateHost;
      }
      return null;
    },
    createElement() {
      return { style: {}, setAttribute() {}, appendChild() {} };
    },
  };
  // Node 22+ exposes a getter-only navigator. Keep the real value when it
  // exists; only older Node versions need this browser fixture fallback.
  if (!globalThis.navigator) globalThis.navigator = { platform: 'MacIntel' };
  globalThis.document = document;
  globalThis.window = globalThis;
  globalThis.window.document = document;
  globalThis.localStorage = {
    getItem(key) {
      return key === 'st_identity' ? JSON.stringify({ nacimiento: '1980-01-01' }) : null;
    },
    setItem() {},
  };
  globalThis.fetch = (path) => {
    if (path === '/api/services') {
      return response({ servicios: [
        data.servicio,
        { id: 'adult-imaging', nombre: 'Ecografía', modo: 'walkin', edad: { min: null, max: null } },
      ] });
    }
    return response({ centros: [] });
  };
  return { gateHost, gateLookup, gateLookups: () => gateLookups };
}

function awaitDecision(promise, label) {
  let timeout;
  const deadline = new Promise((_, reject) => {
    timeout = setTimeout(() => reject(new Error(`timed out waiting for ${label}`)), 1000);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timeout));
}

test('the walk-in age gate waits for the canonical module before deciding', { timeout: 2000 }, async () => {
  const world = installWorld();
  const gate = makeFormatGate();
  globalThis.__serviceAgeGate = gate;
  const ageUrl = `${GATED_AGE}?service-age-delayed=${Date.now()}`;
  globalThis.window.SANA_STATIC = staticMap(ageUrl);
  globalThis.window.sanaImportStatic = (name) => import(globalThis.window.SANA_STATIC[name]);
  const api = await import(`${SERVICE}?service-age-boot=${Date.now()}`);
  const pending = Promise.resolve(api.boot());
  await stays(() => world.gateLookups() === 0, 'the gate does not decide while age.mjs is still loading', 30);
  assert.equal(world.gateLookups(), 0, 'the gate must not decide while age.mjs is still loading');
  assert.equal(world.gateHost.innerHTML, '');
  assert.equal(typeof globalThis.window.WzUpload, 'undefined');
  gate.release();
  await awaitDecision(world.gateLookup, 'the age gate decision after age.mjs loads');
  await pending;
  assert.equal(world.gateLookups(), 1);
  assert.match(world.gateHost.innerHTML, /No podés pedir este turno/);
  assert.match(world.gateHost.innerHTML, /Ecografía/);
});

test('an age-module load failure still advances through the fail-open path', { timeout: 2000 }, async () => {
  const world = installWorld();
  const gate = makeFormatGate();
  globalThis.__serviceAgeGate = gate;
  const ageUrl = `${GATED_AGE}?service-age-fail=${Date.now()}`;
  globalThis.window.SANA_STATIC = staticMap(ageUrl);
  globalThis.window.sanaImportStatic = (name) => import(globalThis.window.SANA_STATIC[name]);
  const api = await import(`${SERVICE}?service-age-fail-boot=${Date.now()}`);
  const pending = Promise.resolve(api.boot());
  gate.fail(new Error('offline'));
  await awaitDecision(world.gateLookup, 'the fail-open age gate decision');
  await pending;
  assert.equal(world.gateLookups(), 1, 'the failed import must not leave the page waiting forever');
  assert.equal(world.gateHost.innerHTML, '');
});
