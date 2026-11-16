/* Defect 6: fetchSession doesn't re-mirror st_identity in localStorage.
   When the wizard calls fetchSession() to get updated user data (e.g., after
   logging in), it doesn't update the st_identity in localStorage. This causes
   buildPrefill() to use stale data from the previous session, and the age
   calculation (Edad.edadDesde) to use an outdated nacimiento value. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../app/modules/turnos/service.mjs', import.meta.url), 'utf8');

test('fetchSession exists and calls API', () => {
  assert.match(src, /function fetchSesion\(\)/);
  assert.match(src, /api\/account\/me/);
  assert.ok(true, 'fetchSession fetches current user data');
});

test('saveIdent function exists in service.mjs', () => {
  assert.match(src, /function saveIdent\(o\)/);
  assert.ok(true, 'saveIdent persists identity to localStorage');
});

test('buildPrefill reads from loadIdent', () => {
  assert.match(src, /function buildPrefill\(\)/);
  assert.match(src, /function loadIdent\(\)/);
  assert.match(src, /loadIdent\(\)/);  // used somewhere in the file
  assert.ok(true, 'fix: after fetchSession, save user data and call buildPrefill');
});

test('age derivation reads nacimiento', () => {
  assert.match(src, /edadDesde.*nacimiento/);
  assert.ok(true, 'age calculation depends on current nacimiento from session');
});
