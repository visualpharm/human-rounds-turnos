// Solicitudes filters (the product owner 05.09 C1, 21.10, 26.10: «fewer than ~10 records — do not
// draw filters at all»; 15.07, 28.07: «default option — the name
// of the field, never "Todos"»). podarFiltros() is run from admin-app.js against the
// real shared rule (static/platform/admin-filter-controls.mjs).
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import * as FilterControls from '../static/platform/admin-filter-controls.mjs';

const APP = readFileSync(new URL('../static/root/admin-app.js', import.meta.url), 'utf8');
const TPL = readFileSync(new URL('../templates/admin/solicitudes.html', import.meta.url), 'utf8');
const take = (name) => {
  const match = APP.match(new RegExp(`function ${name}\\([^]*?\\n\\}`));
  assert.ok(match, `missing ${name} in admin-app.js`);
  return match[0];
};

function fakeSelect(values, value = '') {
  return { value, hidden: false, querySelectorAll: () => values.map(v => ({ value: v })) };
}

function prune(rows, { estado = 'pendiente' } = {}) {
  const els = {
    'f-medico': fakeSelect(['', 'id:med-1', 'id:med-2']),
    'f-especialidad': fakeSelect(['', 'Cardiología', 'Pediatría']),
    'f-servicio': fakeSelect(['', 'cardio', 'pediatria']),
    'f-centro': fakeSelect(['', 'norte', 'sur']),
    'f-verdict': fakeSelect(['', 'ok', 'revisar']),
    'f-sug': fakeSelect(['', 'aprobar', 'rechazar']),
    'f-estado': fakeSelect(['pendiente', 'aprobada', 'rechazada', 'todas'], estado),
    'adm-filter-toggle': { hidden: false },
  };
  const context = {
    FilterControls, ALL: rows,
    suggestFor: (s) => s.sug,
    $: (sel) => els[sel.replace(/^#/, '')],
    document: { querySelectorAll: () => Object.values(els).filter(e => 'querySelectorAll' in e) },
  };
  vm.runInNewContext(take('podarFiltros') + '\npodarFiltros();', context);
  return els;
}

const rows = (n, estados = ['pendiente']) => Array.from({ length: n }, (_, i) => ({
  estado: estados[i % estados.length], sug: i % 2 ? 'aprobar' : 'rechazar',
}));

test('fewer than ten requests: no filter selects and no phone «Filtros» toggle', () => {
  const els = prune(rows(7));
  for (const id of ['f-medico', 'f-especialidad', 'f-servicio', 'f-centro', 'f-verdict', 'f-sug', 'f-estado']) {
    assert.equal(els[id].hidden, true, id + ' on a seven-row list');
  }
  assert.equal(els['adm-filter-toggle'].hidden, true);
});

test('ten requests or more: the filters with two values come back', () => {
  const els = prune(rows(12));
  for (const id of ['f-medico', 'f-especialidad', 'f-servicio', 'f-centro', 'f-verdict', 'f-sug']) {
    assert.equal(els[id].hidden, false, id + ' on a twelve-row list');
  }
  assert.equal(els['adm-filter-toggle'].hidden, false);
});

test('the state select is the way to the history: kept on a short list with two states', () => {
  const els = prune(rows(3, ['pendiente', 'aprobada']));
  assert.equal(els['f-estado'].hidden, false);
  assert.equal(els['f-medico'].hidden, true);
  assert.equal(els['adm-filter-toggle'].hidden, false, 'the state select still needs the toggle on the phone');
});

test('a filter already set stays on a short list, so it can be cleared', () => {
  const els = prune(rows(4));
  assert.equal(els['f-medico'].hidden, true);
  const set = { ...els };
  set['f-medico'] = fakeSelect(['', 'id:med-1', 'id:med-2'], 'id:med-2');
  const context = {
    FilterControls, ALL: rows(4), suggestFor: (s) => s.sug,
    $: (sel) => set[sel.replace(/^#/, '')],
    document: { querySelectorAll: () => [set['f-medico']] },
  };
  vm.runInNewContext(take('podarFiltros') + '\npodarFiltros();', context);
  assert.equal(set['f-medico'].hidden, false);
});

test('the filters are labelled with the field name, never «Todos los…»', () => {
  const firstOption = (id) => TPL.match(new RegExp(`<select id="${id}"[^>]*><option value="">([^<]*)</option>`))[1];
  assert.match(firstOption('f-medico'), /'Médico'/);
  assert.match(firstOption('f-especialidad'), /'Especialidad'/);
  assert.match(firstOption('f-servicio'), /'Servicio'/);
  assert.doesNotMatch(TPL, /Todos los|Todas las/);
});
