import test from 'node:test';
import assert from 'node:assert/strict';

import { escucharLector, escucharLectorEnFormulario, pareceBarcode, parsePayload } from '../static/platform/dni-scan.mjs';

test('parsea un payload PDF417 por forma y elige la fecha más antigua', () => {
  assert.deepEqual(
    parsePayload('00512345678@PEREZ@ANA MARIA@F@12345678@01/02/1990@15/03/2020'),
    {
      dni: '12345678',
      apellido: 'PEREZ',
      nombres: 'ANA MARIA',
      sexo: 'F',
      nacimiento: '1990-02-01',
    },
  );
});

test('acepta separadores alternativos y DNI histórico de 6 dígitos', () => {
  assert.deepEqual(parsePayload('LOPEZ|JUAN CARLOS|M|123456|09/10/1940'), {
    dni: '123456',
    apellido: 'LOPEZ',
    nombres: 'JUAN CARLOS',
    sexo: 'M',
    nacimiento: '1940-10-09',
  });
  assert.equal(parsePayload('LOPEZ\tJUAN\t12345678\t01/01/1990'), null);
  assert.equal(parsePayload('LOPEZ|JUAN|M|sin-documento'), null);
});

test('distingue un payload del texto que una persona puede escribir', () => {
  assert.equal(pareceBarcode('PEREZ@ANA@F'), false);
  assert.equal(pareceBarcode('PEREZ@ANA MARIA@F@12345678@01/02/1990'), true);
  assert.equal(pareceBarcode('PEREZ|ANA MARIA|F|12345678|01/02/1990'), true);
});

// Reloj de prueba: las pausas corren cuando el test lo dice.
function relojFalso() {
  const pendientes = new Map();
  let n = 0;
  return {
    esperar(fn) { pendientes.set(++n, fn); return n; },
    cancelar(id) { pendientes.delete(id); },
    correr() { const fns = [...pendientes.values()]; pendientes.clear(); fns.forEach((fn) => fn()); },
  };
}

function fakeInput() {
  const listeners = new Map();
  return {
    value: '', listeners,
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type, listener) { if (listeners.get(type) === listener) listeners.delete(type); },
  };
}

test('el lector llena el DNI, avisa una vez y se puede desenganchar', () => {
  const input = fakeInput(), reloj = relojFalso();
  const received = [];
  const detach = escucharLector(input, (patient) => received.push(patient), reloj);

  input.value = 'PEREZ@ANA@F@12345678@01/02/1990';
  input.listeners.get('input')();
  reloj.correr();
  assert.equal(input.value, '12345678');
  assert.equal(received.length, 1);
  assert.equal(received[0].apellido, 'PEREZ');

  detach();
  assert.equal(input.listeners.size, 0);
  assert.equal(typeof escucharLector(null, () => {}), 'function');
});

// Un lector real escribe tecla por tecla: a mitad de camino el texto ya
// parece un documento, pero leerlo ahí daba un DNI trunco y ninguna fecha.
test('el lector espera a que el documento termine antes de leerlo', () => {
  const input = fakeInput(), reloj = relojFalso();
  const received = [];
  escucharLector(input, (patient) => received.push(patient), reloj);
  const payload = '00512345678@PEREZ@ANA MARIA@F@12345678@A@01/02/1990@15/03/2020';
  for (const ch of payload) {
    input.value += ch;
    input.listeners.get('input')();
    assert.equal(received.length, 0, 'nada se lee mientras el lector sigue escribiendo');
  }
  let frenado = false;
  input.listeners.get('keydown')({ key: 'Enter', preventDefault() {}, stopImmediatePropagation() { frenado = true; } });
  assert.equal(frenado, true, 'el Enter del lector no elige una sugerencia');
  assert.deepEqual(received, [{
    dni: '12345678', apellido: 'PEREZ', nombres: 'ANA MARIA', sexo: 'F', nacimiento: '1990-02-01',
  }]);
  assert.equal(input.value, '12345678');
  reloj.correr();
  assert.equal(received.length, 1, 'la pausa no lo lee por segunda vez');
});

test('un lector sin Enter final se lee en la pausa, en el campo y en el formulario', () => {
  const root = fakeRoot(), reloj = relojFalso();
  const leidos = [];
  escucharLectorEnFormulario(root, (p) => leidos.push(p), Object.assign({ ahora: () => 1000 }, reloj));
  teclear(root, { value: '' }, '00512345678@PEREZ@ANA MARIA@F@12345678@A@01/02/1990@15/03/2020', { enter: false });
  assert.equal(leidos.length, 0);
  reloj.correr();
  assert.equal(leidos.length, 1);
  assert.equal(leidos[0].nacimiento, '1990-02-01');
});

// Un lector de teclado con el teclado del sistema en español escribe comillas
// donde el documento trae arrobas: es el mismo payload.
test('lee el payload cuando el lector escribe comillas en lugar de arrobas', () => {
  const crudo = '00512345678"PEREZ"ANA MARIA"F"12345678"A"01/02/1990"15/03/2020';
  assert.equal(pareceBarcode(crudo), true);
  assert.deepEqual(parsePayload(crudo), {
    dni: '12345678', apellido: 'PEREZ', nombres: 'ANA MARIA', sexo: 'F', nacimiento: '1990-02-01',
  });
});

function fakeRoot() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(type, fn) { listeners.set(type, fn); },
    removeEventListener(type, fn) { if (listeners.get(type) === fn) listeners.delete(type); },
  };
}

function teclear(root, target, texto, { enter = true } = {}) {
  let evitado = false;
  for (const ch of texto) {
    // como en el navegador: keydown llega antes de que la tecla entre al campo
    root.listeners.get('keydown')({ key: ch, target, preventDefault() {} });
    target.value += ch;
  }
  if (enter) root.listeners.get('keydown')({ key: 'Enter', target, preventDefault() { evitado = true; } });
  return evitado;
}

test('el lector de formulario lee el documento desde cualquier campo y le devuelve su valor', () => {
  const root = fakeRoot();
  const telefono = { value: '2254' };
  const leidos = [];
  const soltar = escucharLectorEnFormulario(root, (p) => leidos.push(p), Object.assign({ ahora: () => 1000 }, relojFalso()));
  const evitado = teclear(root, telefono, '00512345678@PEREZ@ANA MARIA@F@12345678@A@01/02/1990@15/03/2020');
  assert.equal(evitado, true);
  assert.equal(telefono.value, '2254');
  assert.deepEqual(leidos, [{
    dni: '12345678', apellido: 'PEREZ', nombres: 'ANA MARIA', sexo: 'F', nacimiento: '1990-02-01',
  }]);
  soltar();
  assert.equal(root.listeners.size, 0);
});

test('lo que escribe una persona no se toma por un documento', () => {
  const root = fakeRoot();
  const nombre = { value: '' };
  const leidos = [];
  let t = 0;
  escucharLectorEnFormulario(root, (p) => leidos.push(p), Object.assign({ ahora: () => (t += 400) }, relojFalso()));
  const evitado = teclear(root, nombre, 'PEREZ@ANA MARIA@F@12345678@01/02/1990');
  assert.equal(evitado, false);
  assert.equal(nombre.value, 'PEREZ@ANA MARIA@F@12345678@01/02/1990');
  assert.deepEqual(leidos, []);
});
