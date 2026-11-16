import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, click, flush } from './helpers/queue-runtime-fixture.mjs';

async function attendance(t, status, error) {
  const f = fixture(t), seen = [];
  f.win.SANA_ADMIN_SCREEN = 'cola';
  let release;
  f.modules.set('platform/admin-session.mjs', new Promise(resolve => { release = resolve; }));
  // Import the actual session implementation after installing the isolated fetch.
  const session = await import('../static/platform/admin-session.mjs?case=' + encodeURIComponent(t.name));
  const owner = f.mount();
  f.readyTable();
  const button = click(f, '[data-asis]', { 'data-asis': 'synthetic-row', 'data-valor': 'presente' });
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].url, '/api/admin/requests/synthetic-row/attendance');
  assert.equal(f.requests[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(f.requests[0].options.body), { asistencia: 'presente' });
  const response = new Response(JSON.stringify({ error }), { status });
  f.requests[0].resolve(response);
  await flush();
  assert.equal(response.bodyUsed, true, 'queue parses the original body before delayed session confirmation');
  assert.equal(button.disabled, true);
  assert.equal(seen.length, 0);
  release({ ...session, confirmar(...args) {
    assert.equal(args[2](), true, 'owner is current when confirmation begins');
    seen.push(args);
    return session.confirmar(...args);
  } });
  await owner.ready;
  await flush();
  assert.equal(seen.length, 1);
  assert.equal(seen[0][0], response);
  assert.deepEqual(seen[0][1], { error });
  assert.equal(typeof seen[0][2], 'function');
  return { ...f, owner, button, seen };
}

test('attendance waits for a delayed session module and confirms consumed 401 against real whoami', async t => {
  const f = await attendance(t, 401, 'no_autenticado');
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].url, '/api/admin/whoami');
  assert.equal(globalThis.location.href, undefined);
  f.requests[1].resolve(new Response('{"error":"no_autenticado"}', { status: 401 }));
  await flush(); await flush();
  assert.equal(globalThis.location.href, '/admin/login?next=%2Fadmin%2Fcola');
  assert.deepEqual(f.toasts, []);
});

test('attendance preserves the exact 2FA transition after the response body has been consumed', async t => {
  const f = await attendance(t, 403, '2fa_requerido');
  await flush();
  assert.equal(globalThis.location.href, '/admin/account?setup=2fa');
  assert.equal(f.requests.length, 1, '2FA repair needs no whoami request');
  assert.deepEqual(f.toasts, []);
});

test('attendance keeps ordinary 403 local after delayed real session classification', async t => {
  const f = await attendance(t, 403, 'rol_no_autorizado');
  await flush();
  assert.equal(globalThis.location.href, undefined);
  assert.equal(f.button.disabled, false);
  // The refusal stays on screen in words, never as the server's code (TASK-48).
  assert.deepEqual(f.toasts, ['No se pudo guardar.']);
  assert.equal(f.requests.length, 1);
});

test('attendance does not redirect for a live whoami after an explicit 401', async t => {
  const f = await attendance(t, 401, 'no_autenticado');
  f.requests[1].resolve(new Response('{"usuario":"synthetic"}', { status: 200 }));
  await flush(); await flush();
  assert.equal(globalThis.location.href, undefined);
  assert.equal(f.button.disabled, false);
});

test('attendance does not redirect when its owner expires during the real whoami check', async t => {
  const f = await attendance(t, 401, 'no_autenticado');
  f.owner();
  f.requests[1].resolve(new Response('{"error":"no_autenticado"}', { status: 401 }));
  await flush(); await flush();
  assert.equal(globalThis.location.href, undefined);
  assert.equal(f.seen[0][2](), false);
  assert.deepEqual(f.toasts, [], 'no late session failure reaches the disposed screen');
});
