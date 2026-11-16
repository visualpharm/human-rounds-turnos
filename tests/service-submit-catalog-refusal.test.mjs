// Pedir el turno: si el servidor rechaza el médico del horario elegido (dejó
// el catálogo o no atiende el servicio, MR 70), el paciente vuelve a «Horario»
// con la frase del servidor y el resto del formulario cargado — la solución,
// no el error (remedies.py). Lo mismo que «turno_tomado».
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runtime, storage, flush} from './lineage/runtime.mjs';

globalThis.SANA_COUNTRY_PHONE = JSON.parse(
  readFileSync(new URL('../packs/ar/country.json', import.meta.url), 'utf8')).phone;
const Format = await import('../static/platform/format.mjs');

const NOMBRES = ['normPhone', 'onlyDigits', 'esc', 'mismoTelefono', 'telefonoDelTurno', 'telefonoParaCuenta',
  'cuentaAActualizar', 'saveIdent', 'loadIdent', 'casoChatListo', 'submit'];

function wizard(status, body) {
  const button = {}, message = {style: {}}, calls = {horario: 0, holds: []};
  const wiz = {slotMedicoId: 'tomas', slotMedico: 'Tomás Hueso', slotDia: '2026-11-02', slotHora: '09:00',
    slotCuando: 'lunes 9:00', preferencia: 'mañana', token: 'orden-1'};
  const c = runtime('app/modules/turnos/service.mjs', NOMBRES, {
    Format, IDENT_KEY: 'st_identity', localStorage: storage({}), AUTH_MODE: 'native',
    host: {}, $: selector => selector === '#wz-send' ? button : message,
    servicio: {id: 'svc', centro: 'center'}, wiz,
    saveHoldToken: t => calls.holds.push(t), olvidarOrdenHandoff() {}, renderSuccess() {},
    recordarSlotEnUrl() {}, evStep() {}, stepHorario: () => { calls.horario++; }, stepCuenta() {},
    fetch: async () => new Response(JSON.stringify(body), {status})
  }, 'var horarioNotice = null;');
  return {c, wiz, calls, message, button};
}

const usuario = {dni: '30111400', nombre: 'Carla', apellido: 'Reserva', contacto: '+5492254100002'};

for (const codigo of ['medico_inexistente', 'medico_no_atiende_servicio']) {
  test(`${codigo}: vuelve a Horario con la frase del servidor y conserva la orden`, async () => {
    const frase = 'Ese profesional no atiende este servicio. Elegí otro horario.';
    const w = wizard(400, {error: frase, codigo});
    w.c.submit(usuario, usuario);
    await flush(); await flush();
    assert.equal(w.calls.horario, 1);
    assert.equal(w.c.horarioNotice, frase);
    assert.equal(w.wiz.slotMedicoId, null);
    assert.equal(w.wiz.slotDia, null);
    assert.equal(w.wiz.token, 'orden-1', 'la orden ya cargada no se pierde');
    assert.equal(w.button.disabled, false);
  });
}

test('límite de turnos: la frase del servidor y el link a Mis turnos, donde se cancela (TASK-49)', async () => {
  const detalle = 'Ya tenés 1 turno de Cardiología pedido sin atender, que es el máximo. Para pedir otro horario, cancelá el que tenés en Mis turnos y pedí uno nuevo.';
  const w = wizard(409, {error: 'limite_turnos_activos', limite: 1, abiertas: 1, detalle,
    accion: {texto: 'Mis turnos', url: '/account#turnos'}});
  w.c.submit(usuario, usuario);
  await flush(); await flush();
  assert.equal(w.calls.horario, 0);
  assert.match(w.message.innerHTML, /Ya tenés 1 turno de Cardiología/);
  assert.match(w.message.innerHTML, /<a [^>]*href="\/account#turnos"[^>]*>Mis turnos<\/a>/);
  assert.equal(w.button.disabled, false);
});

test('catálogo no disponible: se queda en Confirmá con el botón activo para reintentar', async () => {
  const frase = 'No pudimos consultar los servicios en este momento. Probá de nuevo en unos minutos.';
  const w = wizard(503, {error: frase, codigo: 'catalogo_no_disponible'});
  w.c.submit(usuario, usuario);
  await flush(); await flush();
  assert.equal(w.calls.horario, 0);
  assert.equal(w.message.textContent, frase);
  assert.equal(w.button.disabled, false);
  assert.equal(w.wiz.slotMedicoId, 'tomas', 'el horario elegido sigue elegido');
});
