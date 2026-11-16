// Guardas de fuente para el ranking de asistencia en el panel (2026-07-27).
// Mismo patrón que admin-cola.test.mjs: node --test sobre el texto de los
// archivos. Lo que se clava acá son decisiones que ya se discutieron una vez y
// que un refactor descuidado desharía sin que nada más se queje.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const pac = readFileSync(join(ROOT, 'static/platform/admin-patients.mjs'), 'utf8');
const cola = readFileSync(join(ROOT, 'static/platform/admin-queue.mjs'), 'utf8');
const app = readFileSync(join(ROOT, 'static', 'root', 'admin-app.js'), 'utf8');

test('la lista de pacientes tiene columna de asistencia y se puede ordenar por ella', () => {
  // Un ranking que no se puede ordenar no es un ranking.
  assert.match(pac, /key:\s*"ranking",\s*label:\s*T\("admin\.queue\.patients\.col\.attendance",\s*null,\s*"Asistencia"\)/);
  assert.match(pac, /th\[data-sort\]/);
  assert.match(pac, /aria-sort=/);
});

test('ordenar también funciona con teclado', () => {
  // La mesa de admisión incluye a una persona que usa lector de pantalla:
  // ordenar sólo con el mouse la deja afuera de la columna.
  assert.match(pac, /keydown/);
  assert.match(pac, /e\.key !== "Enter" && e\.key !== " "/);
});

test('sin historial no se muestra número', () => {
  // El puntaje de quien no tiene historia es el promedio del hospital. Verlo
  // repetido en veinte filas se lee como veinte notas puestas a gente que
  // todavía no hizo nada.
  // Ahora la asistencia son tres barras sin número en la celda: sin historia no
  // hay barra llena (BARRAS no tiene nivel) y el globo lo dice con palabras.
  assert.ok(!/pat-rank-n/.test(pac), 'la celda ya no escribe el porcentaje');
  assert.match(pac, /var llenas = BARRAS\[r\.nivel\] \|\| 0;/);
  // Y sin barra llena no se dibuja nada (2026-11-05): tres barras vacías en
  // la mitad de las filas eran un «nada» repetido.
  assert.match(pac, /var llenas = BARRAS\[r\.nivel\] \|\| 0;\n\s*if \(!llenas\) return "";/);
  assert.match(pac, /if \(r\.sin_historial\) return T\("admin\.queue\.patients\.no_attendance_history"/);
});

test('los motivos se listan uno por línea, nunca pegados con puntos medios', () => {
  // ui-rules: una lista son bullets de a uno, jamás 2+ ítems unidos por " · ".
  assert.ok(!/motivos[^\n]*join\(" · "\)/.test(pac), 'motivos no se unen con punto medio');
  assert.ok(!/motivos\|\|\[\]\)\.join\(' · '\)/.test(app), 'idem en el diálogo de asistencia');
  assert.match(app, /attendance-dialog-porque/);
});

test('la ficha aclara que el puntaje no bloquea nada', () => {
  // Sin esa línea un puntaje bajo se lee como una sanción, y no lo es: el
  // bloqueo del canal web es otra regla, con su propio umbral.
  assert.match(pac, /no bloquea nada/);
});

test('el ranking se calcula en el server, no en el cliente', () => {
  // Un cliente que recalcule el puntaje se desincroniza del mail y del resto
  // del panel. El cliente sólo pinta lo que vino.
  assert.ok(!/Math\.pow\(0\.5/.test(pac), 'el decaimiento no se recalcula en JS');
  assert.ok(!/PRIOR/.test(pac), 'el prior no vive en el cliente');
});

test('Pacientes de hoy no tiene columnas Historial ni Asistencia: las barras van junto al nombre', () => {
  // Product owner, 2026-10-26: «show the prognosis as the bars»; 2026-11-09: next to
  // the name, like a phone's signal, and Atendió / No vino only in the card.
  assert.doesNotMatch(cola, /key:\s*"(?:historial|asistencia)"/);
  assert.match(cola, /'<span class="queue-pat-line">' \+ pac \+ historialBarrasHtml\(t\)/);
});

test('en la cola el historial se dibuja con barras de la pieza compartida; el número no se pinta', () => {
  // Al lado del nombre de alguien que está por entrar, un "51" se lee como
  // una nota escolar: son las tres barras de attendance-forecast.mjs
  // (nivel + en cuántos turnos se basa como nombre accesible).
  const m = /function historialBarrasHtml\([\s\S]*?\n  \}/.exec(cola);
  assert.ok(m, 'historialBarrasHtml existe');
  assert.match(m[0], /Forecast\.cellHtml\(t\.ranking, T\)/);
  assert.ok(!/puntaje/.test(m[0]), 'el puntaje no se pinta');
});

test('tres bandas en todas las listas: «Suele venir» no se ofrece y un filtro guardado con ella cae en «Viene siempre»', () => {
  const pac = readFileSync(join(ROOT, 'static/platform/admin-patients.mjs'), 'utf8');
  const mis = readFileSync(join(ROOT, 'static/platform/admin-my-appointments.mjs'), 'utf8');
  for (const src of [pac, mis, cola]) assert.doesNotMatch(src, /suele-venir"\s*[:,]|usually_comes/);
  const bandas = /BANDAS = \[([\s\S]*?)\];/.exec(pac);
  assert.ok(bandas, 'la lista del filtro existe');
  assert.deepEqual([...bandas[1].matchAll(/\["([a-z-]+)"/g)].map(x => x[1]), ['viene-siempre', 'falta-a-veces', 'falta-seguido', 'sin-historial']);
  assert.match(pac, /if \(FILTROS\.banda === "suele-venir"\) FILTROS\.banda = "viene-siempre";/);
});
