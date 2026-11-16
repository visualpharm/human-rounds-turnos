/* Shared-computer account chooser (responsable del producto, 2026-08-09): in the "¿Para quién es
   la atención?" picker of the Confirmá step, the account holder appears by
   NAME (not "Me" — on a family computer "Me" does not say whose session is
   open), and someone who is neither the holder nor a dependent has a real
   way out: "Ingresá con otra cuenta", which ends the session and returns to
   THIS turn (slot included) after sign-in. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = fs.readFileSync(join(ROOT, 'app/modules/turnos/service.mjs'), 'utf8');
const deps = fs.readFileSync(join(ROOT, 'static/platform/dependents.mjs'), 'utf8');

test('the patient picker shows the holder by name, with "Me" only as a last resort', () => {
  // personName(p) for every option; "Me" stays only as the fallback when the
  // session carries no name (personName returns "", never a dash — a
  // dependent without one is shown by DNI).
  assert.match(src, /var nom = personName\(p\);\s*\n\s*if \(!nom\) nom = i === 0 \? T\("service\.patient\.myself", null, "Yo"\) : "DNI " \+ formatDni\(p\.dni\);\s*\n\s*return '<button type="button" class="wz-center-choice" data-i="' \+ i \+ '">' \+ esc\(nom\)/);
});

test('"Ingresá con otra cuenta" sits in the picker, before Cancel', () => {
  const otra = src.indexOf('id="wz-paciente-otra-cuenta"');
  const cancelar = src.indexOf('id="wz-paciente-cancel"');
  assert.ok(otra > -1, 'missing the wz-paciente-otra-cuenta button');
  assert.ok(cancelar > -1, 'missing the wz-paciente-cancel button');
  assert.ok(otra < cancelar, 'the other-account link must come BEFORE Cancel');
  // reuses the control that already does this job: the picker's text link
  // (wz-change-link), not an invented new button.
  assert.match(src, /class="wz-change-link wz-center-cancel wz-center-otra" id="wz-paciente-otra-cuenta"/);
  // went through T() with the exact Spanish fallback.
  assert.match(src, /T\("service\.patient\.switch_account", null, "Ingresá con otra cuenta"\)/);
});

/* BEHAVIORAL: the switch-account link must end the session and come back to the
   SAME turn after sign-in, AND it must not pretend the switch succeeded when
   the logout request fails. Rather than assert on the source text, we pull the
   real click handler out of service.js and drive it with stubbed fetch +
   location, so the ok / non-ok / network-failure branches are exercised
   against the actual code. */
function extractHandlerBody() {
  const m = src.match(/otra\.disabled = true;[\s\S]*?\.catch\(function \(\) \{ otra\.disabled = false; \}\);/);
  assert.ok(m, 'could not find the switch-account click handler in service.js');
  // the handler fires the fetch chain but does not return it; surface it so the
  // test can await settlement after wrapping it in an IIFE.
  const withReturn = m[0].replace('fetch(', 'return fetch(');
  assert.equal(withReturn.match(/return fetch\(/g).length, 1, 'expected exactly one fetch call to anchor the chain');
  return withReturn;
}
const HANDLER_CODE = '(function (fetch, location, otra) { ' + extractHandlerBody() + ' })';

function makeLocation() {
  // assigning location.href is the "redirect"; capture it instead of navigating.
  let href = null;
  return {
    pathname: '/svc/clinica-familia',
    search: '?slot=abc%3A10',
    get href() { return href; },
    set href(v) { href = v; },
  };
}

async function runSwitch({ ok, status, reject }) {
  const location = makeLocation();
  const otra = { disabled: false };
  const fetchStub = reject
    ? () => Promise.reject(new Error('network down'))
    : () => Promise.resolve({ ok, status });
  const handler = vm.runInNewContext(HANDLER_CODE, {});
  await handler(fetchStub, location, otra);
  return { otra, location };
}

test('on a successful logout the handler redirects back to the same turn', async () => {
  const { otra, location } = await runSwitch({ ok: true, status: 200 });
  const volver = '/svc/clinica-familia?slot=abc%3A10';
  assert.equal(location.href, '/account?mode=login&next=' + encodeURIComponent(volver), 'did not redirect to login with the slot preserved');
  assert.equal(otra.disabled, true, 'control stays disabled while navigating away');
});

test('on a non-success response the handler does NOT redirect and keeps the session usable', async () => {
  // fetch only rejects on a network failure, so a 5xx (or 4xx) lands in the
  // success branch: it must NOT redirect as if the switch had worked. Restore
  // the control and leave the current session usable so the person can retry.
  const { otra, location } = await runSwitch({ ok: false, status: 500 });
  assert.equal(location.href, null, 'must not redirect as if the switch succeeded');
  assert.equal(otra.disabled, false, 'control must be restored so the person can retry');
});

test('on a network failure the handler does NOT redirect and keeps the session usable', async () => {
  const { otra, location } = await runSwitch({ reject: true });
  assert.equal(location.href, null, 'must not redirect when the request never reached the server');
  assert.equal(otra.disabled, false, 'control must be restored after a network error');
});

test('the dependent selector also shows the holder by name', () => {
  assert.match(deps, /\[p\.nombre,p\.apellido\]\.filter\(Boolean\)\.join\(" "\)\|\|T\("account\.mine\.dependents\.myself", null, "Yo"\)/);
});
