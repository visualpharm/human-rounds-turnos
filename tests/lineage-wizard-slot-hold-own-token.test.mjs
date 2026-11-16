/* Defect 3 (journey 2026-09-16): opening YOUR OWN step-2 link
   (/service/<id>/book?slot=…) in a new tab said the slot was no longer
   available. Cause: the hold token lived in sessionStorage, which is per-tab,
   so the new tab sent no ?hold= and the patient's own soft-lock counted as
   taken. The token now lives in localStorage — shared across the same
   browser's tabs — and still never travels in a page URL. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime, source} from './lineage/runtime.mjs';

function store(initial = {}) {
  const v = new Map(Object.entries(initial));
  return {getItem: k => (v.has(k) ? v.get(k) : null), setItem: (k, x) => v.set(k, String(x)),
    removeItem: k => v.delete(k), _map: v};
}

function tab(localStore, sessionStore) {
  const wiz = {holdToken: null};
  return runtime('app/modules/turnos/service.mjs', ['holdStore', 'saveHoldToken'],
    {wiz, HOLD_KEY: 'st_hold_token', window: {localStorage: localStore, sessionStorage: sessionStore}});
}

test('the hold token is kept where a second tab can read it', () => {
  const shared = store();
  const a = tab(shared, store());
  a.saveHoldToken('tok-abc');
  // A brand new tab: its own sessionStorage, the same localStorage.
  const b = tab(shared, store());
  assert.equal(b.holdStore().getItem('st_hold_token'), 'tok-abc',
    'the new tab can recover its own hold token');
});

test('the token is read through the shared store, not sessionStorage directly', () => {
  const src = source('app/modules/turnos/service.mjs');
  assert.match(src, /wiz\.holdToken = \(hs0 && hs0\.getItem\(HOLD_KEY\)\)/,
    'the initial read goes through holdStore()');
  assert.doesNotMatch(src, /sessionStorage\.getItem\(HOLD_KEY\)/,
    'no per-tab read of the hold token survives');
});

test('clearing the hold clears it for every tab', () => {
  const shared = store({'st_hold_token': 'tok-abc'});
  const a = tab(shared, store());
  a.saveHoldToken(null);
  assert.equal(shared.getItem('st_hold_token'), null);
});

test('the hold token never travels in a page URL', () => {
  const src = source('app/modules/turnos/service.mjs');
  assert.doesNotMatch(src, /[?&]hold=\(\[\^&\]\+\)/, 'no ?hold= restore block (token fixation)');
});
