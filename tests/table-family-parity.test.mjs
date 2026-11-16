import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

// Step 1 of the table-family consolidation: the manifest freezes what every
// affected screen rendered BEFORE the shared family took over. These are the
// bytes the migration has to reproduce — or explicitly list in `changes`.
const manifest = JSON.parse(
  fs.readFileSync(new URL('./fixtures/table-family-parity.json', import.meta.url), 'utf8')
);

test('el manifiesto congeló el encabezado de solicitudes tal como es hoy', () => {
  const header = manifest.before.render['admin-solicitudes.header'];
  assert.ok(header, 'before.render debe fijar admin-solicitudes.header');
  for (const state of ['asc', 'desc', 'off']) {
    assert.ok(header[state], `falta el estado ${state}`);
    assert.match(header[state], /class="sol-cols"/);
    assert.match(header[state], /data-sol-sort="/);
  }
  // la flecha ad-hoc: triángulo fantasma, en una sola columna
  assert.match(header.asc, /sol-ch-on" data-sol-sort="paciente"[^>]*><span class="sol-ch-t">Paciente<\/span><span class="sol-sar sol-sar-u">/);
  assert.match(header.desc, /sol-sar-d/);
  assert.equal((header.asc.match(/sol-sar-off/g) || []).length, 9);
});

test('el manifiesto congeló las seis copias ad-hoc de encabezado ordenable', () => {
  const src = manifest.before.source;
  assert.equal(Object.keys(src).length, 6);
  // la firma de cada copia, tal como estaba en origin/main
  assert.match(src['admin-queue.th'], /data-cola-sort/);          // atributo propio
  assert.match(src['admin-queue.th'], /queue-arrow/);             // flecha propia (span + glifo)
  assert.match(src['admin-patients.thead'], /" ▲"/);              // flecha como sufijo de texto
  assert.match(src['admin-users.theadHtml'], /aria-sort/);        // sin flecha ni title
  assert.doesNotMatch(src['admin-users.theadHtml'], /▲|▼/);
  assert.match(src['admin-content.catalog-th'], /n > 10/);        // ordenable sólo con lista larga
  assert.match(src['admin-content.medcal-th'], /data-med-sort/);  // botón dentro del th
  assert.match(src['admin-content.medcal-th'], /"asc"/);          // direcciones string, no ±1
  assert.match(src['admin-solicitudes.colsHtml'], /sol-sar/);     // triángulo CSS fantasma
});

test('el manifiesto declara las dos familias y sus motores', () => {
  assert.equal(manifest.family.admin.engine, 'static/platform/admin-table.mjs');
  assert.equal(manifest.family.admin.css, 'admin-direct.css');
  assert.equal(manifest.family.admin.members.length, 7);
  assert.deepEqual(manifest.family.admin.joining, ['static/platform/admin-solicitudes.mjs']);
  assert.equal(manifest.family.patient.engine, 'static/platform/listing.mjs');
  // cada exención nombra su archivo: nada queda fuera de la familia por accidente
  assert.ok(manifest.exemptions.length >= 4);
  for (const ex of manifest.exemptions) assert.ok(ex.file && ex.why);
});

test('las rebanadas congeladas son los bytes reales del árbol', () => {
  // El congelamiento corrió sobre el código de origin/main; cada rebanada tiene
  // que seguir encontrándose literal en su archivo mientras ese archivo no
  // haya migrado todavía (la migración reemplaza exactamente estas líneas).
  const byFile = {
    'admin-queue.th': 'static/platform/admin-queue.mjs',
    'admin-patients.thead': 'static/platform/admin-patients.mjs',
    'admin-users.theadHtml': 'static/platform/admin-users.mjs',
    'admin-content.catalog-th': 'static/platform/admin-content.mjs',
    'admin-content.medcal-th': 'static/platform/admin-content.mjs',
    'admin-solicitudes.colsHtml': 'static/platform/admin-solicitudes.mjs'
  };
  const migrated = manifest.changes.filter((c) => c.done).map((c) => c.screen);
  for (const [name, rel] of Object.entries(byFile)) {
    if (migrated.includes(name)) continue; // ya migrada: la rebanada es historia
    const disk = fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
    assert.ok(disk.includes(manifest.before.source[name]), `${name} ya no coincide con ${rel}`);
  }
});
