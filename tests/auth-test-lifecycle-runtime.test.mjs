import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, flush, deferred, response, identity, Format, Dates } from './helpers/auth-test-fixture.mjs';

test('the complete simulator renders shared country-aware phone, name, DNI and birth date formatting', async () => {
  const f = await fixture({ holdLists: true });
  f.lists[0].resolve(response({ identidades: [{ ...identity('99000001', 'ANA'),
    apellido: 'DE LA CRUZ', contacto: '011 15 3547-7302' }] }));
  await f.controller.ready;
  const html = f.element('tid-rows').innerHTML;
  assert.match(html, /\+54 9 11 3547-7302/);
  assert.match(html, /99\.000\.001/);
  assert.match(html, /1 de enero de 1990/);
  assert.match(html, /Ana/);
  assert.match(html, /De la Cruz/);
  assert.doesNotMatch(html, /011 15|01\/01\/1990|>ANA</);
  f.controller.destroy();
});

for (const [name, module] of [['Format', Format], ['Dates', Dates]]) {
  test(`held native ${name} prevents premature identity reads and activation`, async () => {
    const dependency = deferred();
    const f = await fixture({ ['load' + name]: () => dependency.promise });
    assert.equal(f.element('tid-new').disabled, true);
    f.click('tid-random'); f.submit();
    assert.equal(f.calls.length, 0); assert.equal(f.lists.length, 0);
    dependency.resolve(module); await f.controller.ready;
    assert.equal(f.element('tid-new').disabled, false);
    assert.equal(f.lists.length, 1); f.controller.destroy();
  });
  test(`rejected native ${name} fails closed without starting identity operations`, async () => {
    const dependency = deferred();
    const f = await fixture({ ['load' + name]: () => dependency.promise });
    dependency.reject(new Error('Dependency failed'));
    await assert.rejects(f.controller.ready, /Dependency failed/);
    f.click('tid-random'); f.submit();
    assert.equal(f.calls.length, 0); assert.equal(f.lists.length, 0);
  });
}

test('mount is idempotent and destroy removes every owned listener and focus timer', async () => {
  const f = await fixture();
  assert.equal(f.remount(), f.controller);
  f.openDraft('99000001', 'First');
  assert.equal(f.pendingTimers.size, 1);
  f.controller.destroy(); f.controller.destroy();
  assert.equal(f.pendingTimers.size, 0);
  f.click('tid-random'); f.submit();
  assert.equal(f.calls.length, 0);
  const second = f.remount();
  assert.notEqual(second, f.controller);
  await second.ready;
  f.click('tid-random');
  assert.equal(f.calls.length, 1);
  second.destroy();
});

test('repeated submit, random and row activation share one pending write', async () => {
  const f = await fixture();
  f.openDraft('99000001', 'First'); f.submit(); f.submit();
  f.click('tid-random'); f.dispatch('keydown', f.person('99000002').row, 'Enter');
  assert.equal(f.calls.length, 1);
  assert.equal(JSON.parse(f.calls[0].options.body).identidad.nombre, 'First');
  f.controller.destroy();
});

for (const action of ['save', 'random', 'login']) {
  test(`${action} retains write ownership through a delayed response body`, async () => {
    const f = await fixture();
    if (action === 'save') { f.openDraft('99000001', 'First'); f.submit(); }
    else if (action === 'random') f.click('tid-random');
    else f.dispatch('click', f.person('99000001').button);
    const body = deferred();
    f.calls[0].resolve({ ok: false, json: () => body.promise });
    await flush();
    f.openDraft('99000002', 'Second'); f.submit(); f.click('tid-random');
    f.dispatch('keydown', f.person('99000003').row, 'Enter');
    assert.equal(f.calls.length, 1);
    assert.equal(f.element('tid-save').disabled, true);
    body.resolve({ error: 'Retry deliberately' }); await flush();
    assert.equal(f.element('tid-save').disabled, false);
    f.submit(); assert.equal(f.calls.length, 2);
    f.controller.destroy();
  });
}

for (const ok of [true, false]) {
  test(`old save ${ok ? 'success' : 'failure'} cannot close or overwrite a newer draft`, async () => {
    const f = await fixture();
    f.openDraft('99000001', 'First'); f.submit();
    f.click('tid-close'); f.openDraft('99000002', 'Second');
    f.calls[0].resolve(response({ error: 'Old error' }, ok)); await flush();
    assert.equal(f.element('tid-dialog').open, true);
    assert.equal(f.element('tid-name').value, 'Second');
    assert.equal(f.element('tid-form-msg').textContent, '');
    f.controller.destroy();
  });
}

test('failed save retains the active draft and allows a deliberate retry', async () => {
  const f = await fixture();
  f.openDraft('99000001', 'Retain'); f.submit();
  f.calls[0].resolve(response({ error: 'Validation error' }, false)); await flush();
  assert.equal(f.element('tid-name').value, 'Retain');
  assert.equal(f.element('tid-dialog').open, true);
  assert.equal(f.element('tid-form-msg').textContent, 'Validation error');
  assert.equal(f.element('tid-form-msg').className, 'tid-msg err');
  f.submit(); assert.equal(f.calls.length, 2);
  f.controller.destroy();
});

test('a successful save on the current draft closes the dialog, reports saved and releases the busy state', async () => {
  const f = await fixture({ holdLists: true });
  f.openDraft('99000001', 'Edit'); f.submit();
  assert.equal(f.element('tid-save').disabled, true);
  f.calls[0].resolve(response({ identidad: identity('99000001', 'Edit') })); await flush();
  assert.equal(f.element('tid-dialog').open, false);
  assert.equal(f.element('tid-msg').textContent, 'Persona guardada.');
  assert.equal(f.lists.length, 2, 'a successful save must reload the list');
  f.lists[1].resolve(response({ identidades: [identity('99000001', 'Edited')] })); await flush();
  assert.match(f.element('tid-rows').innerHTML, /Edited/);
  assert.equal(f.element('tid-save').disabled, false);
  assert.equal(f.element('tid-random').disabled, false);
  f.openDraft('99000002', 'Again'); f.submit();
  assert.equal(f.calls.length, 2, 'busy state must allow another deliberate save');
  f.controller.destroy();
});

test('an old successful save cannot close a reopened draft but still refreshes the list', async () => {
  const f = await fixture({ holdLists: true });
  f.openDraft('99000001', 'First'); f.submit();
  f.click('tid-close'); f.openDraft('99000002', 'Second');
  f.calls[0].resolve(response({ identidad: identity('99000001', 'First') })); await flush();
  assert.equal(f.element('tid-dialog').open, true);
  assert.equal(f.element('tid-name').value, 'Second');
  assert.equal(f.element('tid-form-msg').textContent, '');
  assert.equal(f.element('tid-msg').textContent, '', 'a stale owner must not claim the newer draft was saved');
  assert.equal(f.lists.length, 2, 'the post-success list refresh still runs');
  f.controller.destroy();
});

test('invalid form never dispatches a write', async () => {
  const f = await fixture();
  f.openDraft('invalid', 'Retain'); f.element('tid-form').valid = false; f.submit();
  assert.equal(f.calls.length, 0); assert.equal(f.element('tid-save').disabled, false);
  f.controller.destroy();
});

test('stale initial list cannot erase a created identity; refresh restores the complete list', async () => {
  const f = await fixture({ holdLists: true });
  f.click('tid-random');
  f.calls[0].resolve(response({ identidad: identity('99000002', 'New') })); await flush();
  assert.match(f.element('tid-rows').innerHTML, /New/);
  assert.equal(f.lists.length, 2);
  f.lists[0].resolve(response({ identidades: [] })); await flush();
  assert.match(f.element('tid-rows').innerHTML, /New/);
  f.lists[1].resolve(response({ identidades: [identity('99000001', 'Existing'), identity('99000002', 'New')] }));
  await flush();
  assert.match(f.element('tid-rows').innerHTML, /Existing/);
  assert.match(f.element('tid-rows').innerHTML, /New/);
  f.controller.destroy();
});

test('refresh failure does not erase the newly confirmed identity', async () => {
  const f = await fixture({ holdLists: true });
  f.click('tid-random'); f.calls[0].resolve(response({ identidad: identity('99000002', 'New') }));
  await flush(); f.lists[1].reject(new Error('Offline')); await flush();
  assert.match(f.element('tid-rows').innerHTML, /New/);
  assert.equal(f.element('tid-msg').textContent, 'Offline');
  f.controller.destroy();
});

test('an older list response arriving after a newer applied list cannot overwrite it', async () => {
  const f = await fixture({ holdLists: true });
  f.openDraft('99000001', 'Edit'); f.submit();
  f.calls[0].resolve(response({ identidad: identity('99000001', 'Edit') })); await flush();
  assert.equal(f.lists.length, 2);
  f.lists[1].resolve(response({ identidades: [identity('99000001', 'Edited')] }));
  await flush();
  assert.match(f.element('tid-rows').innerHTML, /Edited/);
  f.lists[0].resolve(response({ identidades: [identity('99000003', 'Stale')] }));
  await flush();
  assert.match(f.element('tid-rows').innerHTML, /Edited/);
  assert.doesNotMatch(f.element('tid-rows').innerHTML, /Stale/);
  f.controller.destroy();
});

test('destroy/remount keeps an in-flight write locked and ignores old form completion', async () => {
  const f = await fixture();
  f.openDraft('99000001', 'Keep'); f.submit(); f.controller.destroy();
  const next = f.remount();
  f.submit(); f.click('tid-random'); assert.equal(f.calls.length, 1);
  f.calls[0].resolve(response({})); await next.ready;
  assert.equal(f.element('tid-dialog').open, true);
  assert.equal(f.element('tid-name').value, 'Keep');
  assert.equal(f.element('tid-save').disabled, false);
  assert.equal(f.element('tid-form-msg').textContent, '', 'old owner must not leave a stale Saving message');
  next.destroy();
});

test('destroyed login owner cannot navigate or postMessage after a late success', async () => {
  const f = await fixture({ embedded: true });
  f.dispatch('click', f.person('99000001').button); f.controller.destroy();
  f.calls[0].resolve(response({ usuario: { dni: '99000001' } })); await flush();
  assert.equal(f.messages.length, 0); assert.equal(f.location.href, '');
});

test('teardown preserves a real validation error rather than clearing arbitrary form feedback', async () => {
  const f = await fixture(); f.openDraft('99000001', 'Keep'); f.submit();
  f.calls[0].resolve(response({ error: 'Correct the birth date' }, false)); await flush();
  f.controller.destroy(); const next = f.remount(); await next.ready;
  assert.equal(f.element('tid-form-msg').textContent, 'Correct the birth date');
  assert.equal(f.element('tid-name').value, 'Keep');
  next.destroy();
});

test('successful popup fallback timer cannot navigate after destroy', async () => {
  const f = await fixture({ popup: true });
  f.dispatch('click', f.person('99000001').button);
  f.calls[0].resolve(response({ usuario: { dni: '99000001' } })); await flush();
  assert.equal(f.pendingTimers.size, 1); f.controller.destroy();
  assert.equal(f.pendingTimers.size, 0);
  f.timers[0](); assert.equal(f.location.href, '');
});

test('late i18n readiness from a destroyed mount cannot start a list request', async () => {
  const language = deferred();
  const f = await fixture({ loadI18n: () => language.promise });
  f.controller.destroy(); language.resolve({ t: () => 'Late' });
  await f.controller.ready;
  assert.equal(f.lists.length, 0); assert.notEqual(f.document.title, 'Late');
});

test('malformed response releases ownership without discarding the draft', async () => {
  const f = await fixture(); f.openDraft('99000001', 'Keep'); f.submit();
  f.calls[0].resolve({ ok: true, json: async () => { throw new Error('Malformed response'); } });
  await flush(); assert.equal(f.element('tid-name').value, 'Keep');
  assert.equal(f.element('tid-save').disabled, false);
  f.submit(); assert.equal(f.calls.length, 2); f.controller.destroy();
});

test('rejected translation dependency leaves the original title and no active mutations', async () => {
  const language = deferred();
  const f = await fixture({ loadI18n: () => language.promise });
  f.document.title = 'Server-provided English title';
  assert.equal(f.element('tid-new').disabled, true);
  f.click('tid-random'); f.openDraft('99000001', 'Early'); f.submit();
  assert.equal(f.calls.length, 0);
  language.reject(new Error('Dependency unavailable'));
  await assert.rejects(f.controller.ready, /Dependency unavailable/);
  assert.equal(f.document.title, 'Server-provided English title');
  assert.equal(f.lists.length, 0);
  f.click('tid-random'); assert.equal(f.calls.length, 0);
});

test('a fresh mount after successful handoff accepts one deliberate new login', async () => {
  const f = await fixture({ embedded: true });
  const person = f.person('99000001');
  f.dispatch('click', person.button);
  f.calls[0].resolve(response({ usuario: { dni: '99000001' } })); await flush();
  f.dispatch('click', person.button); assert.equal(f.calls.length, 1);
  f.controller.destroy(); const next = f.remount(); await next.ready;
  assert.equal(person.button.disabled, false);
  f.dispatch('click', person.button); f.dispatch('keydown', person.row, 'Enter');
  assert.equal(f.calls.length, 2); next.destroy();
});
