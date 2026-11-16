// Executes the complete shipped controller, not extracted functions or source
// assertions. The narrow DOM double drives the real delegated event handlers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, flush } from './helpers/auth-test-fixture.mjs';

test('one pending login owns button, row, keyboard and other-identity actions', async () => {
  const f = await fixture();
  const first = f.person('99000001'), second = f.person('99000002');
  f.dispatch('click', first.button);
  f.dispatch('click', first.row);
  f.dispatch('keydown', first.row, 'Enter');
  f.dispatch('keydown', first.row, ' ');
  f.dispatch('click', second.button);
  assert.equal(f.calls.length, 1, 'mixed activation must not issue competing session writes');
  assert.equal(JSON.parse(f.calls[0].options.body).dni, '99000001');
  assert.equal(f.calls[0].options.credentials, 'same-origin');
});

test('a rejected login releases ownership for a deliberate retry', async () => {
  const f = await fixture();
  const first = f.person('99000001'), second = f.person('99000002');
  f.dispatch('click', first.button);
  f.calls[0].reject(new Error('Offline'));
  await flush();
  assert.equal(first.button.disabled, false);
  assert.equal(f.element('tid-msg').textContent, 'Offline');
  f.dispatch('click', second.button);
  assert.equal(f.calls.length, 2);
});

test('an unsuccessful HTTP response permits retry without navigation', async () => {
  const f = await fixture();
  const first = f.person('99000001');
  f.dispatch('click', first.button);
  f.calls[0].resolve({ ok: false, json: async () => ({ error: 'Unavailable' }) });
  await flush();
  assert.equal(f.location.href, '');
  assert.equal(first.button.disabled, false);
  f.dispatch('keydown', first.row, 'Enter');
  assert.equal(f.calls.length, 2);
});

for (const mode of ['embedded', 'popup', 'direct']) {
  test(`successful ${mode} handoff stays latched and preserves origin boundaries`, async () => {
    const f = await fixture({ returnTo: 'https://attacker.invalid/', embedded: mode === 'embedded', popup: mode === 'popup' });
    const first = f.person('99000001');
    f.dispatch('click', first.button);
    f.calls[0].resolve({ ok: true, json: async () => ({ usuario: { dni: '99000001' } }) });
    await flush();
    f.dispatch('keydown', first.row, 'Enter');
    assert.equal(f.calls.length, 1, 'a completed handoff must not mint a second session');
    if (mode !== 'direct') {
      assert.equal(f.messages.length, 1);
      assert.equal(f.messages[0][1], 'https://example.invalid');
      assert.equal(f.messages[0][0].type, 'sana-test-auth-complete');
    }
    if (mode === 'popup') {
      assert.equal(f.window.closedByController, true);
      assert.equal(f.timers.length, 1);
      f.timers[0]();
    }
    assert.equal(f.location.href, mode === 'embedded' ? '' : 'https://example.invalid/account');
  });
}
