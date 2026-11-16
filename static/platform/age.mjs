// age.mjs — ruteo por edad de Pinamar Turnos.
//
// Módulo ES canónico (fila 4.1 del audit del mantenedor, slice acotado): antes era
// el UMD anterior con global de navegador + CommonJS; ese archivo se borró y
// éste es el dueño único del cálculo. Todos los consumidores (service.js, la
// home) lo importan por su namespace — no publica ningún contrato global.
//
// Se sirve con huella de contenido (core/static_fingerprint.py,
// CLIENT_LOADED): su URL vigente vive en window.SANA_STATIC y los consumidores
// la resuelven con import() dinámico, fail-closed. Si el import falla, cada
// consumidor sigue el camino fail-open de siempre: sin edad conocida no se
// bloquea nada (ver service.js / templates/index.html).
//
// Cero dependencias y cero acceso a `window` a nivel de módulo: sólo lo lee
// dentro de funciones, con guard `typeof window`, para el SanaI18n opcional —
// así el módulo se puede importar y testear en Node sin fabricar nada.
//
// API:
//   edadDesde(nacimientoIso, hoy?)        -> edad en años cumplidos (o null)
//   aptoPorEdad(servicio, edad)           -> { ok, motivo }
//   alternativaPorEdad(servicios, s, edad)-> { servicio, motivo } | null
//
// Un servicio trae edad:{min,max} (null = sin límite). La edad se compara con
// años cumplidos: min=16 => 16+ ; max=15 => hasta los 15 inclusive.

function _int(v) { var n = parseInt(v, 10); return isFinite(n) ? n : null; }

var I18n = await (
    (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"])
      : import(new URL("./i18n.mjs", import.meta.url))
  );
  function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}

// Trampa de pluralización: años/año en castellano no calca a years/year en
// inglés (1 años -> 1 years old es un bug). Cada idioma resuelve su propia
// gramática en su propia clave del catálogo — la clave que se le pasa a la
// función T siempre es un string literal (nunca una clave armada
// concatenando un sufijo a mano: el audit y el test de fallbacks sólo
// reconocen la forma con comillas pegadas al paréntesis), así que acá se
// elige con un if/else cuál de las dos llamadas hacer, no se arma la clave.

/* Años cumplidos entre nacimiento (ISO YYYY-MM-DD) y `hoy` (Date | iso | undefined). */
export function edadDesde(nacimientoIso, hoy) {
  if (!nacimientoIso) return null;
  var m = String(nacimientoIso).trim().match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (!m) return null;
  var y = +m[1], mo = +m[2], d = +m[3];
  var ref = (hoy instanceof Date) ? hoy : (hoy ? new Date(hoy) : new Date());
  if (isNaN(ref.getTime())) ref = new Date();
  var edad = ref.getFullYear() - y;
  var mm = (ref.getMonth() + 1) - mo;
  if (mm < 0 || (mm === 0 && ref.getDate() < d)) edad--;
  if (edad < 0 || edad > 130) return null;
  return edad;
}

function _edadObj(servicio) {
  var e = (servicio && servicio.edad) || {};
  return { min: _int(e.min), max: _int(e.max) };
}

/* ¿El servicio corresponde a esta edad? Si no sabemos la edad, no bloqueamos. */
export function aptoPorEdad(servicio, edad) {
  if (edad == null || !isFinite(edad)) return { ok: true, motivo: "" };
  var e = _edadObj(servicio);
  if (e.min != null && edad < e.min) {
    var motivoMin = (e.min === 1)
      ? T("intake.age.min_or_more_one", { n: e.min }, "es para personas de " + e.min + " años o más")
      : T("intake.age.min_or_more_other", { n: e.min }, "es para personas de " + e.min + " años o más");
    return { ok: false, motivo: motivoMin };
  }
  if (e.max != null && edad > e.max) {
    var motivoMax = (e.max === 1)
      ? T("intake.age.max_up_to_one", { n: e.max }, "es para chicos de hasta " + e.max + " años")
      : T("intake.age.max_up_to_other", { n: e.max }, "es para chicos de hasta " + e.max + " años");
    return { ok: false, motivo: motivoMax };
  }
  return { ok: true, motivo: "" };
}

function _norm(s) {
  return String(s || "").toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/\(.*?\)/g, " ");
}
/* núcleo del nombre: saca los calificativos de edad y palabras vacías. */
function _baseName(nombre) {
  return _norm(nombre)
    .replace(/\b(infantil|infantiles|pediatr\w*|geriatr\w*|obstetric\w*|adultos?|3ra edad|tercera edad|de nino enfermo|nino enfermo|demanda espontanea|sin turno|admision)\b/g, " ")
    .replace(/[^a-z0-9 ]/g, " ")
    .replace(/\s+/g, " ").trim();
}
function _tokens(s) {
  return _baseName(s).split(" ").filter(function (t) { return t.length > 2; });
}
function _sinLimite(servicio) {
  var e = _edadObj(servicio);
  return e.min == null && e.max == null;
}
var _GENERAL = /medicina general|clinica medica|guardia|pediatr/;

/* Mejor alternativa apta para la edad cuando `servicio` queda bloqueado.
   Devuelve { servicio, motivo } o null (si el servicio ya es apto o no hay opción). */
export function alternativaPorEdad(servicios, servicio, edad) {
  if (!servicio) return null;
  if (aptoPorEdad(servicio, edad).ok) return null;
  if (!Array.isArray(servicios)) return null;

  var cands = servicios.filter(function (s) {
    return s && s.id !== servicio.id && aptoPorEdad(s, edad).ok;
  });
  if (!cands.length) return null;

  var base = _baseName(servicio.nombre);
  var baseToks = _tokens(servicio.nombre);
  var scored = cands.map(function (s) {
    var sc = 0;
    var sb = _baseName(s.nombre);
    if (sb && sb === base) sc += 100;
    else if (sb && base && (sb.indexOf(base) === 0 || base.indexOf(sb) === 0)) sc += 60;
    else {
      var st = _tokens(s.nombre), overlap = 0;
      for (var i = 0; i < baseToks.length; i++) if (st.indexOf(baseToks[i]) !== -1) overlap++;
      if (overlap) sc += 25 * overlap;
    }
    if (s.categoria === servicio.categoria) sc += 8;
    if (s.centro === servicio.centro) sc += 5;
    if (_GENERAL.test(_norm(s.nombre))) sc += (sc === 0 ? 12 : 4); // red de contención
    return { s: s, sc: sc };
  }).sort(function (a, b) { return b.sc - a.sc; });

  var best = scored[0];
  if (!best || best.sc <= 0) return null;
  var alt = best.s;

  var motivo;
  if (_sinLimite(alt)) motivo = T("intake.age.all_ages", null, "atiende todas las edades");
  else if (_baseName(alt.nombre) === base) motivo = T("intake.age.same_age_version", null, "es la versión para tu edad");
  else motivo = T("intake.age.matches_age", null, "sí corresponde a tu edad");
  return { servicio: alt, motivo: motivo };
}
