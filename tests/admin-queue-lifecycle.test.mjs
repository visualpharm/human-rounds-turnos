import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createDayQueueLoader, mount } from '../static/platform/admin-queue.mjs';

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = day => ({ ok: true, status: 200, json: async () => ({ day }) });
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture(factory = createDayQueueLoader, confirm = async () => false) {
  const requests = [], applied = [], failed = [];
  const owner = factory({
    request(day, signal) { const gate = deferred(); requests.push({ day, signal, ...gate }); return gate.promise; },
    confirm, apply: data => applied.push(data.day), fail: error => failed.push(error.message)
  });
  return { owner, requests, applied, failed };
}

test('module imports without browser globals or import-time effects', () => {
  assert.equal(typeof mount, 'function');
  assert.equal(typeof globalThis.document, 'undefined');
});
async function latestIntentProof(factory) {
  const f = fixture(factory), first = f.owner.load('A'), second = f.owner.load('B');
  assert.equal(f.requests.length, 2, 'B starts before stale A resolves');
  assert.equal(f.requests[0].signal.aborted, true);
  f.requests[1].resolve(response('B')); await second;
  f.requests[0].resolve(response('A')); await first; // Ignore transport abort deliberately.
  assert.deepEqual(f.applied, ['B']);
  assert.deepEqual(f.failed, []);
  f.owner.dispose();
}
test('new day aborts and supersedes old day immediately', async () => latestIntentProof(createDayQueueLoader));
test('old response body cannot repaint the current day', async () => {
  const f = fixture(), body = deferred(), old = f.owner.load('A');
  f.requests[0].resolve({ ok: true, status: 200, json: () => body.promise });
  await flush();
  const fresh = f.owner.load('B'); f.requests[1].resolve(response('B')); await fresh;
  body.resolve({ day: 'A' }); await old;
  assert.deepEqual(f.applied, ['B']); f.owner.dispose();
});
test('late failed request cannot replace the successful current day with an error', async () => {
  const f = fixture(), old = f.owner.load('A'), fresh = f.owner.load('B');
  f.requests[1].resolve(response('B')); await fresh;
  f.requests[0].reject(new Error('stale failure')); await old;
  assert.deepEqual(f.applied, ['B']); assert.deepEqual(f.failed, []); f.owner.dispose();
});
test('dispose aborts and ignores completion; a fresh owner still works', async () => {
  const f = fixture(), pending = f.owner.load('A');
  f.owner.dispose(); f.owner.dispose();
  assert.equal(f.requests[0].signal.aborted, true);
  await f.owner.load('B'); assert.equal(f.requests.length, 1);
  f.requests[0].resolve(response('A')); await pending; assert.deepEqual(f.applied, []);
  const fresh = fixture(), ready = fresh.owner.load('B');
  fresh.requests[0].resolve(response('B')); await ready;
  assert.deepEqual(fresh.applied, ['B']); fresh.owner.dispose();
});
test('stale unauthorized response never invokes session confirmation', async () => {
  let confirmed = 0;
  const f = fixture(createDayQueueLoader, async () => { confirmed++; return true; });
  const old = f.owner.load('A'), fresh = f.owner.load('B');
  f.requests[0].resolve({ ok: false, status: 401 });
  f.requests[1].resolve(response('B')); await Promise.all([old, fresh]);
  assert.equal(confirmed, 0); assert.deepEqual(f.applied, ['B']); f.owner.dispose();
});
test('session confirmation receives ownership which expires on disposal', async () => {
  const gate = deferred(); let current;
  const f = fixture(createDayQueueLoader, (_res, isCurrent) => { current = isCurrent; return gate.promise; });
  const pending = f.owner.load('A'); f.requests[0].resolve({ ok: false, status: 403 });
  await flush(); assert.equal(current(), true); f.owner.dispose(); assert.equal(current(), false);
  gate.resolve(false); await pending; assert.deepEqual(f.failed, []);
});
test('current failure reaches recovery and another request can succeed', async () => {
  const f = fixture(), first = f.owner.load('A');
  f.requests[0].resolve({ ok: false, status: 503 }); await first;
  assert.deepEqual(f.failed, ['bad-503']);
  const retry = f.owner.load('A'); f.requests[1].resolve(response('A')); await retry;
  assert.deepEqual(f.applied, ['A']); f.owner.dispose();
});
test('runtime proof rejects a deliberate regression in the real native module', async () => {
  const source = readFileSync(new URL('../static/platform/admin-queue.mjs', import.meta.url), 'utf8');
  const marker = '!disposed && ownVersion === version';
  assert.equal(source.split(marker).length, 2);
  const regressed = source.replace(marker, '!disposed');
  const module = await import('data:text/javascript;base64,' + Buffer.from(regressed).toString('base64'));
  await assert.rejects(latestIntentProof(module.createDayQueueLoader), { name: 'AssertionError' });
});
