// «Confirmá»: el teléfono que se escribe en el paso es el del TURNO. La cuenta
// sólo cambia si la persona tilda «Guardar también en mi cuenta» (o si todavía
// no tenía teléfono: completar no es pisar). Regla del responsable del producto, hub, 2026-10-23:
// «el teléfono de la cuenta no se pisa sin consentimiento del paciente».
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runtime, storage, flush} from './lineage/runtime.mjs';

// format.mjs necesita el bloque de país que en producción inyecta el servidor.
globalThis.SANA_COUNTRY_PHONE = JSON.parse(
  readFileSync(new URL('../packs/ar/country.json', import.meta.url), 'utf8')).phone;
const Format = await import('../static/platform/format.mjs');

const CUENTA = '+5492254100002';
const ESCRITO = '2254 40-1234';

const NOMBRES = ['normPhone', 'onlyDigits', 'esc', 'mismoTelefono', 'telefonoDelTurno', 'ofreceGuardarTelefono',
  'telefonoParaCuenta', 'cuentaAActualizar', 'guardarTelefonoHtml', 'contactField', 'contactValue',
  'contactoSection', 'saveIdent', 'loadIdent', 'casoChatListo', 'submit', 'successContactHtml'];

// El T de producción interpola {variables}; el de runtime.mjs sólo devuelve el castellano.
const T = (_key, vars, fallback) => fallback.replace(/\{(\w+)\}/g,
  (whole, name) => vars && vars[name] != null ? vars[name] : whole);

function wizard({wiz = {}, posts = []} = {}) {
  const button = {}, message = {style: {}};
  return runtime('app/modules/turnos/service.mjs', NOMBRES, {
    T, Format, IDENT_KEY: 'st_identity', localStorage: storage({}), AUTH_MODE: 'native',
    host: {}, $: selector => selector === '#wz-send' ? button : message,
    servicio: {id: 'svc', centro: 'center'}, wiz, saveHoldToken() {}, olvidarOrdenHandoff() {}, renderSuccess() {}, evStep() {},
    fetch: async (url, options) => {
      assert.equal(url, '/api/requests');
      posts.push(JSON.parse(options.body));
      return new Response(JSON.stringify({ok: true}));
    }
  }, 'var contactoEditando = ""; var CONTACTO_LAB = {tel: "WhatsApp", email: "Email"};' +
     'var CONTACTO_ICO = {tel: "/static/icons/whatsapp-sf-regular.svg", email: "/static/icons/email-sf-regular.svg"};' +
     'function editando(kind) { return contactoEditando === "todo" || contactoEditando === kind; }');
}

const usuario = (extra = {}) => ({dni: '30111400', nombre: 'Carla', apellido: 'Reserva', contacto: CUENTA,
  email: 'carla@example.invalid', ...extra});

test('el teléfono escrito en Confirmá viaja en la solicitud, no el de la cuenta', async () => {
  const posts = [];
  const c = wizard({wiz: {contactoTurno: ESCRITO}, posts});
  c.submit(usuario(), usuario());
  await flush();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].contacto, '2254401234', 'sólo dígitos, como siempre');
});

test('sin teléfono escrito aparte, la solicitud lleva el de la cuenta', async () => {
  const posts = [];
  const c = wizard({posts});
  c.submit(usuario(), usuario());
  await flush();
  assert.equal(posts[0].contacto, CUENTA);
});

test('reservar para un hijo: el teléfono del turno es el escrito y el hijo queda como paciente', async () => {
  const posts = [];
  const c = wizard({wiz: {contactoTurno: ESCRITO}, posts});
  c.submit(usuario(), {dni: '55111222', nombre: 'Lola'});
  await flush();
  assert.equal(posts[0].paciente_dni, '55111222');
  assert.equal(posts[0].contacto, '2254401234');
});

test('pedir el turno no deja el número del turno en la memoria de identidad del navegador', async () => {
  const c = wizard({wiz: {contactoTurno: ESCRITO}});
  c.submit(usuario(), usuario());
  await flush();
  assert.equal(JSON.parse(c.localStorage.getItem('st_identity')).contacto, CUENTA);
});

test('con un número distinto al de la cuenta se ofrece guardarlo; con el mismo escrito de otra forma, no', () => {
  const u = usuario();
  assert.equal(wizard({wiz: {contactoTurno: ESCRITO}}).ofreceGuardarTelefono(u), true);
  for (const mismo of [CUENTA, '2254 10-0002', '+54 9 2254 10-0002', '0 2254 15 10-0002']) {
    assert.equal(wizard({wiz: {contactoTurno: mismo}}).ofreceGuardarTelefono(u), false, mismo);
  }
});

test('no se ofrece guardar cuando no hay número escrito o la cuenta todavía no tiene uno', () => {
  assert.equal(wizard({wiz: {contactoTurno: ''}}).ofreceGuardarTelefono(usuario()), false);
  assert.equal(wizard({wiz: {}}).ofreceGuardarTelefono(usuario()), false);
  assert.equal(wizard({wiz: {contactoTurno: ESCRITO}}).ofreceGuardarTelefono(usuario({contacto: ''})), false);
});

test('la cuenta conserva su teléfono salvo que la persona tilde guardar', () => {
  const u = usuario();
  const c = wizard({wiz: {contactoTurno: ESCRITO}});
  assert.equal(c.telefonoParaCuenta(u, false), CUENTA);
  assert.equal(c.telefonoParaCuenta(u, true), '2254401234');
});

test('una cuenta sin teléfono lo completa con el escrito, tilde o no (completar no es pisar)', () => {
  const c = wizard({wiz: {contactoTurno: ESCRITO}});
  assert.equal(c.telefonoParaCuenta(usuario({contacto: ''}), false), '2254401234');
});

test('la tilde no vale si el número escrito está vacío: la cuenta no se borra desde Confirmá', () => {
  const c = wizard({wiz: {contactoTurno: ''}});
  assert.equal(c.telefonoParaCuenta(usuario(), true), CUENTA);
});

test('si nada de la cuenta cambia, Confirmá no manda ninguna actualización', () => {
  const u = usuario();
  const c = wizard({wiz: {contactoTurno: ESCRITO}});
  assert.equal(c.cuentaAActualizar(u, 'Carla', 'Reserva', 'carla@example.invalid', false), null);
});

test('un cambio de email sí se guarda, pero con el teléfono de la cuenta intacto', () => {
  const u = usuario();
  const c = wizard({wiz: {contactoTurno: ESCRITO}});
  const body = JSON.parse(JSON.stringify(c.cuentaAActualizar(u, 'Carla', 'Reserva', 'nuevo@example.invalid', false)));
  assert.equal(body.contacto, CUENTA);
  assert.equal(body.email, 'nuevo@example.invalid');
});

test('con la tilde, la actualización de la cuenta lleva el teléfono escrito', () => {
  const u = usuario();
  const c = wizard({wiz: {contactoTurno: ESCRITO}});
  const body = c.cuentaAActualizar(u, 'Carla', 'Reserva', 'carla@example.invalid', true);
  assert.equal(body.contacto, '2254401234');
});

test('el nombre que falta (cuenta del portal) se guarda aunque el teléfono quede sólo en el turno', () => {
  const u = usuario({nombre: '', apellido: ''});
  const c = wizard({wiz: {contactoTurno: ESCRITO}});
  const body = c.cuentaAActualizar(u, 'Ana', 'Pérez', 'carla@example.invalid', false);
  assert.equal(body.nombre, 'Ana');
  assert.equal(body.contacto, CUENTA);
});

test('la sección de contacto muestra la tilde y a quién pertenece el otro número', () => {
  const html = wizard({wiz: {contactoTurno: ESCRITO}}).contactoSection(usuario());
  assert.match(html, /id="wz-guardar-tel"/);
  assert.match(html, /Guardar también en mi cuenta/);
  assert.doesNotMatch(html, /id="wz-guardar-tel"[^>]*checked/, 'sin consentimiento previo la tilde arranca sin marcar');
  assert.match(html, /Tu cuenta sigue con el \+54 9 2254 10-0002\./, 'dice cuál es el número que la cuenta conserva');
});

test('la pantalla de éxito, cuando avisa por WhatsApp, nombra el número del turno y no el de la cuenta', () => {
  const sinEmail = usuario({email: ''});
  const c = wizard({wiz: {contactoTurno: ESCRITO, usuario: sinEmail}});
  const dicho = c.successContactHtml(false);
  assert.match(dicho, /2254 40-1234/);
  assert.doesNotMatch(dicho, /10-0002/);
  assert.match(wizard({wiz: {usuario: sinEmail}}).successContactHtml(false), /2254 10-0002/, 'sin número aparte, el de la cuenta');
});

test('la tilde recuerda la elección al volver a pintar el paso', () => {
  const html = wizard({wiz: {contactoTurno: ESCRITO, guardarTelCuenta: true}}).contactoSection(usuario());
  assert.match(html, /id="wz-guardar-tel"[^>]*checked/);
});

test('sin diferencia de números la sección no ofrece ninguna tilde', () => {
  assert.doesNotMatch(wizard({wiz: {}}).contactoSection(usuario()), /wz-guardar-tel/);
  assert.doesNotMatch(wizard({wiz: {contactoTurno: '2254 10-0002'}}).contactoSection(usuario()), /wz-guardar-tel/);
  assert.doesNotMatch(wizard({wiz: {contactoTurno: ESCRITO}}).contactoSection(usuario({contacto: ''})), /wz-guardar-tel/);
});

test('la sección muestra el número del turno en el campo, no el de la cuenta', () => {
  const html = wizard({wiz: {contactoTurno: ESCRITO}}).contactoSection(usuario());
  assert.match(html, /2254 40-1234/);
});
