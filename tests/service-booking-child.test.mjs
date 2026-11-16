import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const serviceSrc = fs.readFileSync(new URL('../app/modules/turnos/service.mjs', import.meta.url), 'utf8');

test('el wizard pregunta si es para un hijo antes de mostrar el formulario cuando no es pediátrico', () => {
  // afterCuenta chequea SD.esPediatrico primero
  assert.match(serviceSrc, /var p = SD\.plan\(deps, servicio, wiz\.usuario\);[\s\S]{0,100}var pediatrico = SD\.esPediatrico\(servicio\);/);
  // cuando NO es pediátrico (no es true), llama stepOfrecerHijoParaFormulario
  assert.match(serviceSrc, /stepOfrecerHijoParaFormulario\(function \(dep\) \{ wiz\.paciente = dep \|\| wiz\.usuario; stepConfirm\(\); \}\);/);
  // cuando SÍ es pediátrico, va directo a stepAgregarPaciente con lede especial
  assert.match(serviceSrc, /if \(pediatrico\) \{[\s\S]{0,300}stepAgregarPaciente\(function \(dep\) \{ wiz\.paciente = dep \|\| wiz\.usuario; stepConfirm\(\); \}, T\("service\.who\.pediatric_lede"/);
  assert.doesNotMatch(serviceSrc, /if \(p\.paso === "agregar"\) \{\s*stepAgregarPaciente\(function \(dep\) \{ wiz\.paciente = dep \|\| wiz\.usuario; stepConfirm\(\); \}\);/);
});

test('stepOfrecerHijoParaFormulario existe y maneja el flujo correcto', () => {
  // function exists
  assert.match(serviceSrc, /function stepOfrecerHijoParaFormulario\(onDone\)/);
  // muestra los dos botones: "Agregar un hijo..." y "Ahora no"
  assert.match(serviceSrc, /T\("service\.dependent\.add_child", null, "Agregar un hijo o persona a cargo"\)/);
  assert.match(serviceSrc, /T\("service\.common\.not_now", null, "Ahora no"\)/);
  // el botón "Ahora no" llama a onDone(null) — continúa sin agregar
  assert.match(serviceSrc, /wz-hijo-no[\s\S]{0,50}addEventListener[\s\S]{0,50}onDone\(null\)/);
  // el botón "Sí" llama a stepAgregarPaciente pasando onDone
  assert.match(serviceSrc, /wz-hijo-si[\s\S]{0,50}addEventListener[\s\S]{0,50}stepAgregarPaciente\(onDone\)/);
  // usa el mismo setTitle y setSub que stepOfrecerHijo original
  assert.match(serviceSrc, /T\("service\.dependent\.ask_title", null, "¿Sacás turnos para tus hijos\?"\)/);
  assert.match(serviceSrc, /T\("service\.dependent\.ask_sub", null, "Agregalos una vez y la próxima vez el turno sale directo, sin volver a cargar sus datos\."\)/);
});

test('stepAgregarPaciente ahora acepta un parámetro customLede opcional', () => {
  // la firma incluye el segundo parámetro
  assert.match(serviceSrc, /function stepAgregarPaciente\(onDone, customLede\)/);
  // usa el customLede si se proporciona, si no usa el fallback
  assert.match(serviceSrc, /lede: customLede \|\| T\("service\.who\.add_lede", null, "Agregalo una vez para pedir turnos a su nombre\."\),/);
});

test('el mensaje especial para pediatría usa la key service.who.pediatric_lede', () => {
  // afterCuenta pasa el lede personalizado para pediátrico
  assert.match(serviceSrc, /T\("service\.who\.pediatric_lede", null, "Pediatría atiende a menores: agregalo para pedir el turno\."\)/);
});

test('el stepOfrecerHijo original sigue existiendo', () => {
  // stepOfrecerHijo sigue definida
  assert.match(serviceSrc, /function stepOfrecerHijo\(\)/);
  // y sigue siendo llamada en el flujo original
  assert.match(serviceSrc, /if \(viaAuth && !pediatrico && !deps\.length\) \{ stepOfrecerHijo\(\); return; \}/);
});
