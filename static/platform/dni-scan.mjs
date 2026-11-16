// dni-scan.mjs — leer el DNI argentino sin tipearlo, en la mesa de admisión.
//
// El hospital ya trabaja así: "llegó el tipo y le escaneás el DNI, le ponés
// nuevo y te levanta el apellido" (reunión 2026-07-24). Los lectores que usan
// son de teclado: apoyás el documento y el aparato ESCRIBE el contenido del
// PDF417 en el campo que tenga el foco, terminando en Enter. Por eso acá no
// hay integración con ningún driver — alcanza con reconocer ese texto cuando
// aparece en un input y quedarse con el DNI.
//
// Este módulo es el dueño ÚNICO del parseo de ese texto: lo usa el lector de
// teclado (mesa) y también dni-barcode.mjs, que decodifica el mismo PDF417
// desde una FOTO con zxing-wasm cuando no hay lector (registro del paciente
// en su casa, o un teléfono en el mostrador). Una sola gramática del payload,
// dos formas de conseguirlo.
//
// Módulo ES canónico. Todos los consumidores importan su namespace; no
// publica un segundo contrato global.

const DATE_RE = /^(\d{2})\/(\d{2})\/(\d{4})$/;
const clean = (s) => (s == null ? '' : String(s)).trim();
const cleanName = (s) => clean(s).replace(/\s+/g, ' ').replace(/[\x00-\x1f\x7f]/g, '').toUpperCase();
const isDate = (s) => DATE_RE.test(clean(s));
const yearOf = (s) => { const m = DATE_RE.exec(clean(s)); return m ? Number(m[3]) : 0; };

function toIso(s) {
  const m = DATE_RE.exec(clean(s));
  if (!m) return null;
  const d = Number(m[1]), mo = Number(m[2]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  return m[3] + '-' + m[2] + '-' + m[1];
}

function normSex(s) { const v = clean(s).toUpperCase(); return (v === 'M' || v === 'F' || v === 'X') ? v : null; }

// Un lector de teclado escribe con la distribución del sistema: con el teclado
// en español, las arrobas del documento salen como comillas.
function splitFields(text) {
  if (text.indexOf('@') !== -1) return text.split('@').map(clean);
  if (text.split('"').length >= 4) return text.split('"').map(clean);
  const alt = text.split(/[|\t\r\n]+/).map(clean).filter((x) => x.length);
  return alt.length > 1 ? alt : [text];
}

// Búsqueda por FORMA, no por posición (el orden de los campos cambia entre
// tandas de documentos): el sexo es la M/F/X suelta, el DNI el numérico de 7-8
// dígitos, el trámite el de 9 o más, y la fecha de nacimiento la más vieja.
export function parsePayload(raw) {
  const text = clean(raw);
  if (!text) return null;
  const p = splitFields(text);

  let sexIdx = -1;
  for (let i = 0; i < p.length; i++) { if (normSex(p[i])) { sexIdx = i; break; } }
  if (sexIdx === -1) return null;

  const dates = [], numerics = [];
  for (let j = 0; j < p.length; j++) {
    if (isDate(p[j])) dates.push(j);
    else if (/^\d{6,}$/.test(clean(p[j]))) numerics.push(j);
  }
  let dniIdx = -1;
  for (let k = 0; k < numerics.length; k++) {
    const len = clean(p[numerics[k]]).length;
    if (len >= 7 && len <= 8 && dniIdx === -1) dniIdx = numerics[k];
  }
  if (dniIdx === -1 && numerics.length) dniIdx = numerics[0];

  const names = [];
  for (let n = 0; n < p.length; n++) {
    if (n === sexIdx) continue;
    const v = clean(p[n]);
    if (v && /[A-Za-z]/.test(v) && !isDate(v) && !/^\d+$/.test(v)) names.push(v);
  }
  const byYear = dates.slice().sort((a, b) => yearOf(clean(p[a])) - yearOf(clean(p[b])));

  const dni = dniIdx >= 0 ? clean(p[dniIdx]).replace(/\D/g, '') : '';
  const sexo = normSex(p[sexIdx]);
  if (!sexo || !/^\d{6,9}$/.test(dni)) return null;

  return {
    dni,
    apellido: names[0] ? cleanName(names[0]) : '',
    nombres: names[1] ? cleanName(names[1]) : '',
    sexo,
    nacimiento: byYear.length ? toIso(clean(p[byYear[0]])) : null
  };
}

// El documento trae los nombres en MAYÚSCULAS; en pantalla y en la ficha van
// como se escribe un nombre.
export function nombreLegible(s) {
  return clean(s).toLowerCase().replace(/(^|[\s'-])([a-záéíóúüñ])/g,
    (m, sep, c) => sep + c.toUpperCase());
}

// ¿Lo que apareció en el input lo escribió un lector, o una persona? El payload
// del DNI trae varios campos separados por "@" y es largo; nadie tipea eso. Se
// pregunta ANTES de parsear para no tratar como escaneo un apellido con arroba.
export function pareceBarcode(text) {
  const t = clean(text);
  if (t.length < 20) return false;
  return t.split('@').length >= 4 || /\d{7,8}/.test(t) && t.split(/[|\t"]/).length >= 4;
}

// Un lector escribe el documento tecla por tecla, de corrido, y casi siempre
// cierra con Enter. Mientras sigue escribiendo el texto ya "parece" un
// documento pero está incompleto: se espera a que termine (Enter, o una pausa)
// antes de leerlo. Cortarlo antes daba un DNI trunco y ninguna fecha.
const RAFAGA_MS = 150;
const PAUSA_MS = 200;
const reloj = (opts) => ({
  ahora: (opts && opts.ahora) || (() => Date.now()),
  esperar: (opts && opts.esperar) || ((fn, ms) => setTimeout(fn, ms)),
  cancelar: (opts && opts.cancelar) || ((id) => clearTimeout(id)),
});

// Engancha un input al lector de teclado. Cuando el texto que entra es un
// payload de DNI completo, lo parsea, LIMPIA el input (el operador no tiene
// por qué ver el chorizo del código) y avisa. Devuelve la función para
// desengancharlo.
//
// `onDni(patient)` recibe {dni, apellido, nombres, sexo, nacimiento}.
export function escucharLector(input, onDni, opts) {
  if (!input) return function () {};
  const r = reloj(opts);
  let pendiente = null;
  const leer = function () {
    if (pendiente != null) { r.cancelar(pendiente); pendiente = null; }
    const v = input.value;
    if (!pareceBarcode(v)) return false;
    const patient = parsePayload(v);
    if (!patient) return false;
    input.value = patient.dni;
    onDni(patient);
    return true;
  };
  const onInput = function () {
    if (pendiente != null) { r.cancelar(pendiente); pendiente = null; }
    if (pareceBarcode(input.value)) pendiente = r.esperar(leer, PAUSA_MS);
  };
  // El Enter del lector cierra el documento: no es el Enter de quien eligió
  // una sugerencia, así que no sigue viaje.
  const onKeydown = function (e) {
    if (!e || e.key !== 'Enter' || !leer()) return;
    if (e.preventDefault) e.preventDefault();
    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
  };
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', onKeydown, true);
  return function () {
    if (pendiente != null) { r.cancelar(pendiente); pendiente = null; }
    input.removeEventListener('input', onInput);
    input.removeEventListener('keydown', onKeydown, true);
  };
}

// El mismo lector, en un formulario entero: el documento se lee con el foco en
// cualquier campo. Un lector escribe decenas de teclas seguidas y cierra con
// Enter; una persona no. Se junta lo tecleado de corrido y, si resulta ser un
// documento, el campo que tenía el foco recupera el valor que tenía antes y
// `onDni(patient)` recibe los datos. Devuelve la función para desengancharlo.
export function escucharLectorEnFormulario(root, onDni, opts) {
  if (!root) return function () {};
  const r = reloj(opts);
  let buffer = '', ultima = 0, campo = null, previo = '', pendiente = null;
  const soltar = function () {
    if (pendiente != null) { r.cancelar(pendiente); pendiente = null; }
    buffer = ''; campo = null; previo = '';
  };
  const leer = function () {
    const patient = pareceBarcode(buffer) ? parsePayload(buffer) : null;
    const destino = campo, valor = previo;
    soltar();
    if (!patient) return false;
    if (destino && 'value' in destino) destino.value = valor;
    onDni(patient);
    return true;
  };
  const handler = function (e) {
    const key = e && e.key;
    if (typeof key !== 'string') return;
    const t = r.ahora();
    if (buffer && t - ultima > RAFAGA_MS) soltar();
    if (key === 'Enter') {
      if (leer() && e.preventDefault) e.preventDefault();
      return;
    }
    if (key.length !== 1) return;
    if (!buffer) {
      campo = e.target || null;
      // keydown llega antes de que la tecla entre al campo: ese es su valor previo
      previo = campo && 'value' in campo ? String(campo.value == null ? '' : campo.value) : '';
    }
    buffer += key;
    ultima = t;
    // un lector configurado sin Enter final: la pausa cierra el documento
    if (pendiente != null) r.cancelar(pendiente);
    pendiente = r.esperar(leer, PAUSA_MS);
  };
  root.addEventListener('keydown', handler, true);
  return function () { soltar(); root.removeEventListener('keydown', handler, true); };
}
