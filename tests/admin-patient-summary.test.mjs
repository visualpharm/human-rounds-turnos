// «Resumen del paciente» (admin-patient-summary.mjs): montaje real en un DOM
// mínimo con fetch falso, el gráfico SVG con banda de rango, y el cableado
// (la pantalla directa importa el módulo fingerprinted y el controlador lo
// monta con el DNI de la entrevista).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const moduleUrl = new URL('../static/platform/admin-patient-summary.mjs', import.meta.url);
const datesUrl = new URL('../static/platform/dates.mjs', import.meta.url).href;
const src = fs.readFileSync(moduleUrl, 'utf8');
const consultaSrc = fs.readFileSync(new URL('../app/modules/intake/admin-consult.mjs', import.meta.url), 'utf8');
const consultaTpl = fs.readFileSync(new URL('../app/modules/intake/admin_consult.html', import.meta.url), 'utf8');

test('trend dates delegate to dates.mjs without a local month table', () => {
  assert.match(src, /Dates && Dates\.corta \? Dates\.corta\(fecha\)/);
  assert.doesNotMatch(src, /var MESES\s*=|SanaDates/);
});

function fakeElement() {
  return {
    innerHTML: '',
    textContent: '',
    listeners: {},
    appendChild() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
  };
}

let importCounter = 0;

test('external history is hidden when off and fetched only by its own button', async () => {
  let { api, cont } = await contextoCon({ resumen: {}, external_history_enabled: false });
  await api.mount(cont, { dni: '99000001' });
  await new Promise(r => setTimeout(r, 0));
  assert.doesNotMatch(cont.innerHTML, /rp-external/);

  ({ api, cont } = await contextoCon({ resumen: {}, external_history_enabled: true }));
  const button = fakeElement(), output = fakeElement(), block = fakeElement();
  block.querySelector = s => s === '.rp-external-load' ? button : output;
  cont.querySelector = s => s === '.rp-external' ? block : null;
  let calls = [];
  globalThis.fetch = url => {
    calls.push(url);
    return Promise.resolve({ ok: true, status: 200, json: async () => url.includes('patient-external-history')
      ? { available: true, items: [{ date: '2026-10-01', institution: { name: 'Hospital ficticio' }, summary: '<img src=x onerror=alert(1)>' }] }
      : { resumen: {}, external_history_enabled: true } });
  };
  await api.mount(cont, { dni: '99000001' });
  await new Promise(r => setTimeout(r, 0));
  assert.match(cont.innerHTML, /<details class="rp-external"><summary>/);
  assert.match(cont.innerHTML, /Historia en otras ciudades/);
  assert.equal(calls.length, 1);
  await button.listeners.click();
  assert.equal(calls.length, 2);
  assert.match(calls[1], /patient-external-history\?dni=99000001/);
  assert.match(output.innerHTML, /Hospital ficticio/);
  assert.match(output.innerHTML, /&lt;img/);
  assert.doesNotMatch(output.innerHTML, /<img/);
  assert.equal(button.disabled, false);
});

test('external history renders unavailable states and retries transport errors', async () => {
  for (const [reason, expected] of [
    ['forbidden', 'Tu usuario no tiene acceso en HSI'],
    ['not_deployed', 'La instalación de HSI no tiene la federación activa'],
    ['not_supported', 'El conector no admite historia externa'],
    ['error', 'No se pudo cargar la historia'],
    ['', 'Sin historia en otras ciudades'],
  ]) {
    const { api, cont } = await contextoCon({ resumen: {}, external_history_enabled: true });
    const button = fakeElement(), output = fakeElement(), block = fakeElement();
    block.querySelector = s => s === '.rp-external-load' ? button : output;
    cont.querySelector = s => s === '.rp-external' ? block : null;
    await api.mount(cont, { dni: '99000001' });
    await new Promise(r => setTimeout(r, 0));
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ available: reason === '', reason, items: [] }) });
    await button.listeners.click();
    assert.match(output.innerHTML, new RegExp(expected));
    if (reason === 'error') {
      globalThis.fetch = async () => { throw new Error('offline'); };
      await button.listeners.click();
      assert.equal(button.disabled, false);
      assert.equal(button.textContent, 'Reintentar');
    }
  }
});
async function contextoCon(respuesta) {
  const cont = fakeElement();
  globalThis.window = { SANA_STATIC: {
    'platform/dates.mjs': datesUrl,
    'platform/admin-session.mjs': new URL('../static/platform/admin-session.mjs', import.meta.url).href,
  } };
  globalThis.document = {
    createElement: () => fakeElement(),
    head: { appendChild() {} },
  };
  globalThis.fetch = () => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(respuesta),
  });
  const api = await import(`${moduleUrl.href}?test=${++importCounter}`);
  return { api, cont };
}

test('montar pide el resumen y pinta pendientes, preguntas y fuentes', async () => {
  const respuesta = {
    resumen: {
      titular: 'HTA 2008 + DBT2 2021; abandono de metformina en 2024',
      pendientes: [{ prioridad: 'alta', titulo: 'HbA1c en ascenso', sub: 'Sin control desde 2024',
        kpi_valor: '6,9', kpi_unidad: '%', kpi_estado: 'alerta', detalle: 'HbA1c 6,4 (2022) → 6,9 (2024).',
        fuente: 'Alepo 2024' }],
      preguntas: ['¿Sigue tomando metformina?', '¿Controles de glucemia en casa?'],
      medicacion: [{ nombre: 'Metformina', dosis: '850 mg', desde: '2021', estado: 'verificar', fuente: 'Alepo 2021' }],
      antecedentes: [{ titulo: 'Cirugía · 2011', texto: 'Colecistectomía, sin complicaciones.' }],
      tendencias: [{ id: 'glucemia_mg_dl', label: 'Glucemia', unidad: 'mg/dL', rango_ok: [70, 110],
        comentario: 'En ascenso desde 2020.',
        puntos: [['2020-03-01', 104], ['2021-06-01', 138], ['2024-11-01', 129]] }],
      advertencias: [],
      fuentes: ['Alepo: 12 registros 2020–2024', 'Este sistema: 3 solicitudes'],
    },
    generado: '2026-08-01T03:00:00-03:00',
    modelo: 'google/gemini-3-flash-preview',
    especialidad: 'Cardiología',
  };
  const { api, cont } = await contextoCon(respuesta);
  await api.mount(cont, { dni: '99000001', medicoId: 'med-1' });
  await new Promise((r) => setTimeout(r, 0));
  assert.match(cont.innerHTML, /HTA 2008 \+ DBT2 2021/);
  assert.match(cont.innerHTML, /rp-tab on" data-tab="pend"/); // Pendientes primero
  assert.match(cont.innerHTML, /Indicadores para revisar/);
  assert.match(cont.innerHTML, /rp-metrics/);
  assert.match(cont.innerHTML, /▲ 19 sobre el rango/);
  assert.match(cont.innerHTML, /Referencia 70–110/);
  assert.match(cont.innerHTML, /Prioridades para esta consulta/);
  assert.match(cont.innerHTML, /HbA1c en ascenso/);
  assert.match(cont.innerHTML, /<summary>Evidencia<\/summary>/);
  assert.match(cont.innerHTML, /Preguntas para conversar/);
  assert.match(cont.innerHTML, /¿Sigue tomando metformina\?/);
  assert.match(cont.innerHTML, /Metformina/);
  assert.match(cont.innerHTML, /Filtrado para Cardiología/);
  assert.match(cont.innerHTML, /Alepo: 12 registros/);
  // El gráfico: SVG real con banda de rango y puntos de datos reales.
  assert.match(cont.innerHTML, /<svg viewBox=/);
  assert.match(cont.innerHTML, /class="rp-spark"/);
  assert.match(cont.innerHTML, /opacity="0.09"/);
  assert.match(cont.innerHTML, /polyline/);
  assert.match(cont.innerHTML, /Dom 1 mar 2020: 104/);
  assert.match(cont.innerHTML, /<span class="rp-fuentes-t">Fuentes<\/span><ul>/);
  assert.match(cont.innerHTML, /Tendencias clínicas/);
  assert.match(cont.innerHTML, /class="rp-timeline"/);
  assert.match(cont.innerHTML, /class="rp-timeline-year">2011/);
});

test('tensión arterial combina el valor pero conserva los 2 límites de referencia', async () => {
  const respuesta = {
    resumen: {
      titular: 'Hipertensión arterial en seguimiento', pendientes: [], preguntas: [],
      medicacion: [], antecedentes: [], advertencias: [], fuentes: [],
      tendencias: [
        { id: 'ta_sistolica', label: 'TA sistólica', unidad: 'mmHg', rango_ok: [90, 130], comentario: '',
          puntos: [['2022-01-01', 132], ['2023-01-01', 138], ['2024-01-01', 146]] },
        { id: 'ta_diastolica', label: 'TA diastólica', unidad: 'mmHg', rango_ok: [60, 85], comentario: '',
          puntos: [['2022-01-01', 82], ['2023-01-01', 88], ['2024-01-01', 92]] },
      ],
    }, generado: null, modelo: 'fake', especialidad: 'Medicina general',
  };
  const { api, cont } = await contextoCon(respuesta);
  await api.mount(cont, { dni: '99000001', medicoId: 'med-1' });
  await new Promise((r) => setTimeout(r, 0));
  assert.match(cont.innerHTML, /Tensión arterial/);
  assert.match(cont.innerHTML, /146\/92/);
  assert.match(cont.innerHTML, /Referencia 90–130 \/ 60–85/);
  assert.match(cont.innerHTML, /Sistólica ≤130/);
  assert.match(cont.innerHTML, /Diastólica ≤85/);
});

test('sin DNI no monta nada, y el HTML del modelo llega escapado', async () => {
  const { api, cont } = await contextoCon({});
  await api.mount(cont, { dni: '' });
  assert.equal(cont.innerHTML, '');

  const maliciosa = {
    resumen: { titular: '<img src=x onerror=alert(1)>', pendientes: [], preguntas: [],
      medicacion: [], antecedentes: [], tendencias: [], advertencias: [], fuentes: [] },
    generado: null, modelo: 'fake', especialidad: null,
  };
  const { api: api2, cont: cont2 } = await contextoCon(maliciosa);
  await api2.mount(cont2, { dni: '1' });
  await new Promise((r) => setTimeout(r, 0));
  assert.doesNotMatch(cont2.innerHTML, /<img/);
  assert.match(cont2.innerHTML, /&lt;img/);
});

test('cableado: la consulta importa el módulo canónico y espera antes de renderizar', () => {
  assert.doesNotMatch(consultaTpl, /admin-patient-summary/);
  assert.match(consultaSrc, /importStatic\("platform\/admin-patient-summary\.mjs"\)/);
  assert.match(consultaSrc, /PatientSummary\.mount/);
  assert.match(consultaSrc, /id="cst-resumen"/);
  // Sólo con DNI: una entrevista QR sin matchear no tiene historia que buscar.
  assert.match(consultaSrc, /if \(data\.dni && PatientSummary/);
  assert.match(consultaSrc, /dispose\.ready = Promise\.all\(\[[\s\S]*?importStatic\("platform\/admin-patient-summary\.mjs"\)/);
  assert.doesNotMatch(consultaSrc + src, /SanaResumenPaciente|module\.exports/);
});


test('AI notice renders before staff clinical summary', async () => {
  const { api, cont } = await contextoCon({resumen:{titular:'Synthetic summary', pendientes:[]}});
  await api.mount(cont, {dni:'99000001',medicoId:'synthetic-doctor'});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(cont.innerHTML, /Contenido elaborado con inteligencia artificial/);
  assert.ok(cont.innerHTML.indexOf('inteligencia artificial') < cont.innerHTML.indexOf('Synthetic summary'));
});

test('a host that already carries the AI notice turns the summary\'s own off (aiNotice:false)', async () => {
  // The consultation says it once, inside its AI card; the summary below must
  // not repeat it, in the loading frame or in the rendered card.
  const { api, cont } = await contextoCon({resumen:{titular:'Synthetic summary', pendientes:[]}});
  const pending = api.mount(cont, {dni:'99000001',medicoId:'synthetic-doctor',aiNotice:false});
  assert.doesNotMatch(cont.innerHTML, /inteligencia artificial/);
  await pending;
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(cont.innerHTML, /Synthetic summary/);
  assert.doesNotMatch(cont.innerHTML, /inteligencia artificial/);
});
