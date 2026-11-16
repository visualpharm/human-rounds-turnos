// Mis turnos, 2026-11-10: the cancel question names what it cancels (an
// approved appointment is a «turno», a pending one is still a «solicitud»)
// with two real buttons; today's visits form their own «Hoy» group on top
// instead of hiding in «Anteriores»; a cancelled request that never named a
// slot still shows a date — the day it was asked for.
import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime} from './lineage/runtime.mjs';

const file = 'static/platform/account-requests.mjs';
const TODAY = '2026-11-10';
// Stand-in for platform/dates.mjs with the three functions the list reads.
const Dates = {
  hoyIsoInstalacion: () => TODAY,
  paredInstalacion: iso => { const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(String(iso)); return m ? {dia: m[1], hora: m[2]} : null; },
  pasada: dia => dia === TODAY ? 'Hoy' : 'día ' + dia,
  delServidor: iso => 'cuando ' + String(iso).slice(0, 16),
  amigable: (d, h) => d + (h ? ' ' + h : ''),
};

function list(items) {
  let html = '';
  const host = {style: {}, getAttribute: () => true, querySelectorAll: () => [],
    get innerHTML() { return html; }, set innerHTML(v) { html = v; }};
  const c = runtime(file, ['renderList', 'richCardHtml', 'mutedCardHtml', 'wireCards', 'avisoHtml', 'diaTurno', 'turnoDt',
    'esAprob', 'horaDe', 'horaTurno', 'pedidaTexto', 'turnoTexto', 'fechaAmigable', 'cancelledLabel'], {
    // platform/i18n.mjs fills {vars} into the Spanish fallback the same way.
    T: (_key, vars, fallback) => String(fallback).replace(/\{(\w+)\}/g, (_, name) => vars?.[name] ?? ''),
    host, Dates, alive: () => true, injectCardsStyle() {}, renderFeed() {}, titleRowHtml: () => '<h1>Mis turnos</h1>',
    esPasado: it => it.turno_pasado === true || /cancel/.test(it.estado), esActivo: it => it.turno_pasado !== true,
    fechaConcreta: () => false, richAvatarHtml: () => '', mutedAvatarHtml: () => '', prepBoxHtml: () => '',
    centroInfo: {}, nombreServicio: it => it.servicio_id, esc: String,
    statusLabels: () => ({pendiente: ['', 'Pendiente']}), MTC_ICO_XS: '', MTC_ICO_REPRO: '', MTC_ICO_CHEV: '',
    MT_CAL_ICON: '', MTC_ICO_DIR: '',
  }, 'var avisoLista = ""; var lastItems = null;');
  c.renderList(items);
  return html;
}

test('today\'s visits — the one to come and the one already past — sit under «Hoy», above everything', () => {
  const html = list([
    {id: 'a', estado: 'aprobada', servicio_id: 'Futuro', turno_dt: '2026-11-20T09:00:00-03:00', turno_pasado: false},
    {id: 'b', estado: 'aprobada', servicio_id: 'HoyPasado', turno_dt: TODAY + 'T08:00:00-03:00', turno_pasado: true},
    {id: 'c', estado: 'aprobada', servicio_id: 'HoyLuego', turno_dt: TODAY + 'T17:00:00-03:00', turno_pasado: false},
    {id: 'd', estado: 'aprobada', servicio_id: 'Viejo', turno_dt: '2026-10-02T10:00:00-03:00', turno_pasado: true},
  ]);
  const at = s => html.indexOf(s);
  assert.ok(at('<h2 class="mt-hist">Hoy</h2>') >= 0, '«Hoy» heading');
  assert.ok(at('Hoy</h2>') < at('HoyPasado') && at('HoyPasado') < at('HoyLuego'), 'today in time order');
  assert.ok(at('HoyLuego') < at('Próximos</h2>') && at('Próximos</h2>') < at('Futuro'), 'then the rest of what comes');
  assert.ok(at('Futuro') < at('Anteriores</h2>') && at('Anteriores</h2>') < at('Viejo'), 'then history');
  assert.equal(html.split('HoyPasado').length, 2, 'today\'s past visit is not repeated under «Anteriores»');
  // Bajo «Hoy» la tarjeta dice sólo la hora: el encabezado ya dijo el día.
  assert.doesNotMatch(html, /Hoy, \d\d:/);
  assert.match(html, /req-when">08:00</);
  assert.match(html, /pt-cuando">17:00</);
});

test('without a visit today there is no «Hoy» and no «Próximos» heading', () => {
  const html = list([
    {id: 'a', estado: 'aprobada', servicio_id: 'Futuro', turno_dt: '2026-11-20T09:00:00-03:00', turno_pasado: false},
    {id: 'd', estado: 'aprobada', servicio_id: 'Viejo', turno_dt: '2026-10-02T10:00:00-03:00', turno_pasado: true},
  ]);
  assert.doesNotMatch(html, />Hoy<\/h2>|Próximos/);
  assert.match(html, /<h2 class="mt-hist">Anteriores<\/h2>/);
});

test('a cancelled request with no slot shows the day it was asked for', () => {
  const html = list([
    {id: 'x', estado: 'cancelada', servicio_id: 'Pedida', cancelada_por: 'paciente', created: '2026-11-03T12:00:00-03:00'},
    {id: 'y', estado: 'cancelada', servicio_id: 'HoyMismo', cancelada_por: 'paciente', created: TODAY + 'T07:00:00-03:00'},
  ]);
  assert.match(html, /Pedida el día 2026-11-03/);
  assert.match(html, /Pedida hoy/);
});

test('a cancelled request offers the way back to the same service, like a rejected one', () => {
  const html = list([
    {id: 'x', estado: 'cancelada', servicio_id: 'kinesiologia', cancelada_por: 'paciente', created: '2026-11-03T12:00:00-03:00'},
  ]);
  assert.match(html, /<a class="req-btn" href="\/service\/kinesiologia\/book">Volver a solicitar<\/a>/);
});

// Review 2026-11-15 (items 11-12): unconfirmed requests are not mixed into
// «Próximos»; the request date sits with the status on the left, the right
// column says only when the appointment was; a past unconfirmed request has a
// way back.
test('unconfirmed requests get their own heading with ONE note, after the confirmed ones', () => {
  const html = list([
    {id: 'p1', estado: 'pendiente', servicio_id: 'PendUno', medico_asignado: 'Dra. Pérez'},
    {id: 'a', estado: 'aprobada', servicio_id: 'Futuro', turno_dt: '2026-11-20T09:00:00-03:00', turno_pasado: false},
    {id: 'p2', estado: 'pendiente', servicio_id: 'PendDos'},
  ]);
  const at = s => html.indexOf(s);
  assert.ok(at('Próximos</h2>') >= 0 && at('Próximos</h2>') < at('Futuro'), 'confirmed ones under «Próximos»');
  assert.ok(at('Futuro') < at('Esperan confirmación del hospital</h2>'), 'then the unconfirmed heading');
  assert.ok(at('Esperan confirmación del hospital</h2>') < at('PendUno') && at('PendUno') < at('PendDos'));
  assert.equal(html.split('class="mt-sec-note"').length, 2, 'the note is said once');
  assert.doesNotMatch(html, /asigne profesional|pt-pending-status|class="pt-note"/);
  assert.match(html, /class="pt-medico">Dra\. Pérez</, 'a known doctor is named on a pending card');
});

test('history: the request date goes left with the status, the right column only holds the appointment', () => {
  const html = list([
    {id: 'x', estado: 'cancelada', servicio_id: 'ConTurno', cancelada_por: 'paciente', created: '2026-11-03T12:00:00-03:00',
      turno_pedido: {fecha: '2026-11-07', hora: '10:00'}},
    {id: 'y', estado: 'cancelada', servicio_id: 'SinTurno', cancelada_por: 'paciente', created: '2026-11-04T12:00:00-03:00'},
  ]);
  assert.match(html, /req-left">[^]*?ConTurno[^]*?Pedida el día 2026-11-03[^]*?req-right"><div class="req-when">[^<]*2026-11-07/);
  assert.doesNotMatch(html, /req-when">Pedida/, 'the request date never sits in the appointment column');
});

test('a past request the hospital never confirmed offers to ask again', () => {
  const html = list([
    {id: 'z', estado: 'pendiente', servicio_id: 'ecografia', turno_pasado: true, created: '2026-11-01T12:00:00-03:00'},
  ]);
  assert.match(html, /El hospital no llegó a confirmarla/);
  assert.match(html, /<a class="req-btn" href="\/service\/ecografia\/book">Volver a solicitar<\/a>/);
});

function confirm(estado) {
  const c = runtime(file, ['cancelConfirmHtml'], {esc: String});
  return c.cancelConfirmHtml({id: 'r', estado});
}

test('the question says «turno» for an approved appointment and «solicitud» for a pending one', () => {
  const turno = confirm('aprobada'), solicitud = confirm('pendiente');
  assert.match(turno, /¿Cancelar el turno\?/);
  assert.match(turno, /data-si>Sí, cancelar el turno</);
  assert.match(solicitud, /¿Cancelar la solicitud\?/);
  assert.match(solicitud, /data-si>Sí, cancelar la solicitud</);
  assert.doesNotMatch(solicitud, /turno/);
});

test('the answers are two real buttons, not small links', () => {
  for (const html of [confirm('aprobada'), confirm('pendiente')]) {
    assert.match(html, /<button type="button" class="req-btn req-btn-danger-fill" data-si>/);
    assert.match(html, /<button type="button" class="req-btn" data-no>No<\/button>/);
    assert.doesNotMatch(html, /<a /);
  }
});
