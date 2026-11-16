/* search-english.test.mjs — capa de morfología en INGLÉS del matcher de la
   home (static/platform/search.mjs:buscar/variantes/activeLang — unit 2 del batch
   de i18n, 2026-08-04).

   Correr:  node --test tests/search-english.test.mjs   (desde la raíz)

   The product owner, verbatim: "if the page is in English, the chips are in English,
   searches in English, and results in English" — no un label en inglés sobre
   una query en español escondida. UN SOLO motor (platform/search.mjs), dos vocabularios
   (STOP/META/variantes por idioma), elegidos por `lang` o autodetectados con
   activeLang(). Estas pruebas cubren: activeLang() en sus tres fuentes
   (document, window, degradado a español bajo Node), la morfología inglesa
   (sufijos -ologist/-iatrician/-graphy, plural, fold británico↔americano) y
   que el camino español sigue byte-idéntico (ya cubierto en
   search-match.test.mjs, acá sólo se confirma que agregar `lang` no lo
   rompe). La prueba end-to-end contra scripts/search_probe.mjs (obligatoria
   por la receta del batch, ya que el audit sólo lee HTML renderizado) cierra
   el archivo. */
import test from "node:test";
import assert from "node:assert/strict";
import * as B from "../static/platform/search.mjs";
import { probe } from "../scripts/search_probe.mjs";

// ---- activeLang(): las tres fuentes + el degradado ------------------------

test("activeLang: sin document ni window (Node puro, como scripts/search_probe.mjs), degrada a español", () => {
  assert.equal(B.activeLang(), "es");
});

test("activeLang: detecta inglés en document.documentElement.lang", () => {
  global.document = { documentElement: { lang: "en" } };
  try {
    assert.equal(B.activeLang(), "en");
  } finally {
    delete global.document;
  }
});

test("activeLang: <html lang> en cualquier otro valor (incluido es) se lee como español", () => {
  global.document = { documentElement: { lang: "es" } };
  try {
    assert.equal(B.activeLang(), "es");
  } finally {
    delete global.document;
  }
});

test("activeLang: sin <html lang>, cae a window.SANA_I18N.active", () => {
  global.window = { SANA_I18N: { active: "en" } };
  try {
    assert.equal(B.activeLang(), "en");
  } finally {
    delete global.window;
  }
});

test("activeLang: document.documentElement.lang gana por sobre window.SANA_I18N.active", () => {
  global.document = { documentElement: { lang: "es" } };
  global.window = { SANA_I18N: { active: "en" } };
  try {
    assert.equal(B.activeLang(), "es");
  } finally {
    delete global.document;
    delete global.window;
  }
});

// ---- variantes en inglés: sufijos ------------------------------------------

test("-ologist/-ology stemea al mismo tronco (gynecologist ⊂ Gynecology)", () => {
  assert.ok(B.variantes("gynecologist", "en").includes("gynecolog"));
  assert.ok(B.variantes("gynecology", "en").includes("gynecolog"));
  assert.ok(B.variantes("cardiologist", "en").includes("cardiolog"));
});

test("-iatrician/-iatrics stemean al mismo tronco (pediatrician ⊂ Pediatrics)", () => {
  assert.ok(B.variantes("pediatrician", "en").includes("pediatr"));
  assert.ok(B.variantes("pediatrics", "en").includes("pediatr"));
});

test("-graphy/-grapher comparten tronco (radiography/radiographer)", () => {
  assert.ok(B.variantes("radiography", "en").includes("radiograph"));
  assert.ok(B.variantes("radiographer", "en").includes("radiograph"));
});

test("plural -s/-es/-ies → singular como variante extra", () => {
  assert.ok(B.variantes("vaccines", "en").includes("vaccine"));
  assert.ok(B.variantes("boxes", "en").includes("box"));
  assert.ok(B.variantes("biopsies", "en").includes("biopsy"));
});

test("irregulares: obstetrician/obstetrics, diabetes/diabetic, nurse/nursing", () => {
  assert.ok(B.variantes("obstetrician", "en").includes("obstetr"));
  assert.ok(B.variantes("obstetrics", "en").includes("obstetr"));
  assert.ok(B.variantes("diabetic", "en").includes("diabet"));
  assert.ok(B.variantes("nursing", "en").includes("nurs"));
});

// ---- grafía británica ↔ americana -------------------------------------------

test("paediatrics/gynaecology/orthopaedics/haematology foldean a la grafía americana", () => {
  assert.ok(B.variantes("paediatrics", "en").includes("pediatrics"));
  assert.ok(B.variantes("gynaecology", "en").includes("gynecology"));
  assert.ok(B.variantes("orthopaedics", "en").includes("orthopedics"));
  assert.ok(B.variantes("haematology", "en").includes("hematology"));
});

test("el fold británico también stemea (paediatrician ⊂ Pediatrics)", () => {
  assert.ok(B.variantes("paediatrician", "en").includes("pediatr"));
});

// ---- variantes en español no cambian sin pasar lang o pasando "es" --------

test("variantes(w) sin lang sigue siendo español (retrocompatible, Node degrada a es)", () => {
  assert.deepEqual(B.variantes("ginecologo"), B.variantes("ginecologo", "es"));
  assert.ok(B.variantes("ginecologo").includes("ginecolog"));
});

// ---- tiers en inglés: mismo motor que en español ---------------------------

const filasEn = [
  { nombre: "General Medicine", horas: 36, palabras: ["gynecology", "pediatrics"] },
  { nombre: "Gynecology", horas: 4, palabras: [] },
  { nombre: "Electrocardiogram", horas: 147, palabras: [] },
  { nombre: "Cardiology", horas: 9, palabras: [] },
  { nombre: "Pediatrics", horas: 10, palabras: [] },
  { nombre: "Walk-in Pediatric Care", horas: 23, palabras: [] },
];
const camposEn = (s) => ({ principal: [s.nombre], resto: s.palabras });

test("match en el nombre gana a match en palabras (gyne → Gynecology antes que General Medicine)", () => {
  const out = B.buscar(filasEn, "gyne", camposEn, "en").map((r) => r.nombre);
  assert.equal(out[0], "Gynecology");
  assert.ok(out.includes("General Medicine"));
});

test("nombre que EMPIEZA con la palabra gana al substring (cardio)", () => {
  const out = B.buscar(filasEn, "cardio", camposEn, "en").map((r) => r.nombre);
  assert.equal(out[0], "Cardiology");
  assert.equal(out[1], "Electrocardiogram");
});

test("'gynecologist' (profesión) encuentra 'Gynecology' (especialidad) vía stemming", () => {
  const out = B.buscar(filasEn, "gynecologist", camposEn, "en").map((r) => r.nombre);
  assert.ok(out.includes("Gynecology"));
});

test("'paediatrician' (grafía británica + profesión) encuentra 'Pediatrics'", () => {
  const out = B.buscar(filasEn, "paediatrician", camposEn, "en").map((r) => r.nombre);
  assert.ok(out.includes("Pediatrics"));
});

// ---- stopwords y meta-palabras en inglés -----------------------------------

test("'dr' se frena igual que en español: 'dr smith' matchea lo mismo que 'smith'", () => {
  const campos = { pt: "", pb: " jane smith ", rt: "", rb: "", exc: [] };
  assert.equal(B.puntuar(campos, "dr smith", "en"), B.puntuar(campos, "smith", "en"));
});

test("meta-palabras del trámite se sueltan sólo si el query dice algo más", () => {
  const campos = { pt: "", pb: " cardiology ", rt: "", rb: "", exc: [] };
  // "book an appointment" son puras meta/stop-words salvo que agreguemos el
  // servicio: sin él, el query se queda como está (no vacía a null porque no
  // queda nada MEJOR, igual que "turno" solo en español sigue buscando "turno").
  assert.equal(B.puntuar(campos, "book an appointment for cardiology", "en"), 0);
});

test("query de puro trámite ('book an appointment') no explota: matchea contra sí mismo", () => {
  const campos = { pt: "", pb: " book appointment ", rt: "", rb: "", exc: [] };
  assert.notEqual(B.puntuar(campos, "book an appointment", "en"), null);
});

// ---- prueba end-to-end: el matcher REAL (search_probe.mjs) sobre un
// corpus inglés sintético (el catálogo en inglés real lo arma otra unidad
// del batch en paralelo) — no una reimplementación, el mismo buscar() que
// consume la home. ------------------------------------------------------

const enServices = [
  { nombre: "Gynecology", categoria: "gynecology", centro: "hosp", sede: "",
    horario: "Monday to Friday 08:00 to 12:00", palabras: ["women's health"] },
  { nombre: "Pediatrics", categoria: "pediatrics", centro: "hosp", sede: "",
    horario: "Monday to Friday 08:00 to 16:00", palabras: ["children", "kids"] },
  { nombre: "Ultrasound", categoria: "imaging", centro: "hosp", sede: "",
    horario: "Tuesday and Thursday 09:00 to 13:00", palabras: ["sonogram"] },
  { nombre: "Blood Test", categoria: "laboratory", centro: "hosp", sede: "",
    horario: "Monday to Friday 07:00 to 10:00", palabras: ["lab work", "blood work"] },
];
const enDoctors = [
  { nombre: "Jane Smith", especialidad: "Gynecology", centro: "hosp", palabras: [] },
];
const enCenters = { hosp: { name: "Community Hospital" } };

test("e2e (search_probe.mjs real): 'gynecologist' encuentra el servicio Gynecology", () => {
  const { total } = probe("gynecologist", enServices, enDoctors, enCenters, "en");
  assert.ok(total > 0);
});

test("e2e (search_probe.mjs real): 'paediatrics'/'pediatrics' encuentran Pediatrics", () => {
  assert.ok(probe("paediatrics", enServices, enDoctors, enCenters, "en").total > 0);
  assert.ok(probe("pediatrics", enServices, enDoctors, enCenters, "en").total > 0);
});

test("e2e (search_probe.mjs real): 'ultrasound' encuentra el estudio Ultrasound", () => {
  assert.ok(probe("ultrasound", enServices, enDoctors, enCenters, "en").total > 0);
});

test("e2e (search_probe.mjs real): 'blood test' encuentra el servicio de laboratorio", () => {
  const { services } = probe("blood test", enServices, enDoctors, enCenters, "en");
  assert.ok(services > 0);
});

test("e2e (search_probe.mjs real): 'dr smith' encuentra a la doctora (dr se frena)", () => {
  const { doctors } = probe("dr smith", enServices, enDoctors, enCenters, "en");
  assert.ok(doctors > 0);
});

test("e2e (search_probe.mjs real): sin lang, Node degrada a español y NO stemea inglés", () => {
  // Prueba de seguridad: sin pasar lang, probe()/buscar() autodetectan con
  // activeLang(), que bajo Node (sin document/window) da "es" — así que
  // "gynecologist" NO debe encontrar "Gynecology" por stemming inglés (sólo
  // matchearía si hubiera substring literal, que no lo hay).
  const { total } = probe("gynecologist", enServices, enDoctors, enCenters);
  assert.equal(total, 0);
});
