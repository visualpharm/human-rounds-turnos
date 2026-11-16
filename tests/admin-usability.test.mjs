// Usabilidad → Hallazgos follows the panel's progressive-disclosure rule
// (the product owner, 2026-10-26: «if there's no content, why to show all the ui?»).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { findingsView } from '../static/platform/admin-usability.mjs';
import * as disclosure from '../static/platform/admin-filter-controls.mjs';

const src = fs.readFileSync(new URL('../static/platform/admin-usability.mjs', import.meta.url), 'utf8');
const none = () => ({ status: new Set(), role: new Set() });
const finding = (id, status, role) => ({ id, status, role, severity: 'medium', source: 'manual', title: 'H' + id });
// Findings worth filtering (FILTER_MIN_ROWS = 10): `extra` more found/patient ones.
const plus = (list, extra = 9) => list.concat(Array.from({ length: extra }, (_, i) => finding(100 + i, list[0].status, list[0].role)));

test('no findings is the empty state: no filters and no bulk form', () => {
  const view = findingsView({ findings: [], editable: true }, none(), disclosure);
  assert.equal(view.state, 'empty');
  assert.equal(view.statusFilter, false);
  assert.equal(view.roleFilter, false);
  assert.equal(view.bulk, false);
});

test('filters that hide every finding are «no-match», not «empty», and keep the filters', () => {
  const selected = none();
  selected.status.add('dismissed');
  const data = { findings: plus([finding(1, 'found', 'patient'), finding(2, 'fixed', 'doctor')]), editable: true };
  const view = findingsView(data, selected, disclosure);
  assert.equal(view.state, 'no-match');
  assert.equal(view.statusFilter, true);
  assert.equal(view.roleFilter, true);
  assert.equal(view.bulk, false, 'nothing listed, nothing to apply a status to');
});

test('a filter with one value in the findings is not offered, unless it is set', () => {
  const data = { findings: plus([finding(1, 'found', 'patient'), finding(2, 'fixed', 'patient')]), editable: true };
  const view = findingsView(data, none(), disclosure);
  assert.equal(view.state, 'data');
  assert.equal(view.statusFilter, true);
  assert.equal(view.roleFilter, false);
  assert.equal(view.bulk, true);
  const set = none();
  set.role.add('patient');
  assert.equal(findingsView(data, set, disclosure).roleFilter, true);
});

// Product owner 05.09 C1, 21.10, 26.10: «fewer than ~10 records — do not draw filters at all».
test('THRESHOLD: under ten findings no filter is offered, even with two values; a set one stays', () => {
  const data = { findings: [finding(1, 'found', 'patient'), finding(2, 'fixed', 'doctor')], editable: true };
  const view = findingsView(data, none(), disclosure);
  assert.equal(view.state, 'data');
  assert.equal(view.statusFilter, false);
  assert.equal(view.roleFilter, false);
  const set = none();
  set.status.add('found');
  assert.equal(findingsView(data, set, disclosure).statusFilter, true);
});

test('the findings tab renders the shared empty and no-match states and keeps its actions', () => {
  assert.match(src, /Filters\.emptyStateHtml\(\{title: T\('study\.admin\.findings_empty'/);
  assert.match(src, /Filters\.noMatchHtml\(/);
  assert.match(src, /admin\.shell\.list\.clear_filters/);
  assert.match(src, /sanaImportStatic\('platform\/admin-filter-controls\.mjs'\)/);
  // «Analizar respuestas» and «Agregar hallazgo» come before the empty-state return.
  const findings = src.slice(src.indexOf('function findings()'));
  assert.ok(findings.indexOf("study.admin.analyze") < findings.indexOf("view.state === 'empty'"));
  assert.doesNotMatch(src, /if \(!rows\.length\) root\.append/);
});
