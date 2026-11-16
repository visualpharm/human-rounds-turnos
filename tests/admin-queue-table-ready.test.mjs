import assert from 'node:assert/strict';
import test from 'node:test';
import { fixture, flush } from './helpers/queue-runtime-fixture.mjs';

test('native queue creates its configured table only after the delayed actual import resolves', async t => {
  const f = fixture(t), configs = [], readiness = [];
  const owner = f.mount();
  owner.ready.then(() => readiness.push(true));
  await flush();
  assert.equal(f.tables.length, 0);
  assert.deepEqual(readiness, []);
  assert.ok(f.doc.count() > 0, 'mounted queue owns real event delegates, not a classic boot listener');
  assert.equal(f.doc.listeners.get('DOMContentLoaded')?.size || 0, 0);
  f.readyTable(configs);
  await owner.ready;
  assert.equal(f.tables.length, 1);
  assert.equal(configs.length, 1);
  assert.equal(configs[0].clave, 'cola');
  assert.equal(configs[0].almacen, 'st_cola_cols');
  assert.equal(configs[0].selector, 'table.queue-table');
  owner();
  assert.equal(f.tables[0].disposals, 1);
  assert.equal(f.doc.count(), 0);
});

test('native queue never constructs a late table for an already-disposed owner', async t => {
  const f = fixture(t), configs = [];
  const owner = f.mount();
  owner();
  f.readyTable(configs);
  await owner.ready;
  assert.deepEqual(configs, []);
  assert.equal(f.tables.length, 0);
});
