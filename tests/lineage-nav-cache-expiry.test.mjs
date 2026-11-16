import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './lineage/nav-fixture.mjs';
import {flush} from './lineage/runtime.mjs';
test('lineage: 401 replaces cached logged-in navigation and request badge', async t => {
  const f = fixture(t, true), owner = f.start(); await owner.ready;
  assert.match(f.host.innerHTML, /Mi cuenta/, 'cached identity is initially visible');
  assert.ok(f.requests.some(r => r.url === '/api/account/me'));
  for (const r of f.requests) r.resolve(new Response(JSON.stringify({}), {status:r.url === '/api/account/me' ? 401 : 200}));
  await flush(); await flush();
  assert.doesNotMatch(f.host.innerHTML, /Mi cuenta|Mis turnos|Mis solicitudes|stznav-cuenta-av/);
  assert.match(f.host.innerHTML, /Ingresar/);
  const cached = JSON.parse(f.writes.filter(([key]) => key === 'st_nav').at(-1)[1]);
  assert.equal(cached.loggedIn, false); assert.equal(cached.hasSolicitudes, false);
});
