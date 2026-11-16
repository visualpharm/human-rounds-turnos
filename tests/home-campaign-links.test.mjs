/* home-campaign-links.test.mjs — enlaces informativos de la home (telemedicina,
   zoonosis, donar sangre): buscables con el MISMO Search que los servicios
   (campoEnlace), filas normales de la MISMA tabla que los consultorios
   (enlaceRow → listing.js), y su integración con /api/links, el estado vacío
   y el "quizás quisiste decir…" del buscador.

   Las dos funciones viven en el <script> inline de templates/index.html (no
   son módulo): se recortan del fuente y se evalúan tal cual, y campoEnlace se
   ejercita contra el buscador REAL (platform/search.mjs) — nunca una reimplementación.

   Correr:  node --test tests/home-campaign-links.test.mjs   (desde la raíz) */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import * as Search from "../static/platform/search.mjs";
const HOME = readFileSync(new URL("../templates/index.html", import.meta.url), "utf8");

// El script inline no es módulo: recortamos cada función por su texto (del
// `function nombre(` al primer cierre en columna 0) y la evaluamos en un
// contexto propio, sin reescribir nada (mismo recurso que nav-lang-switch).
function fnFromTemplate(name) {
  const start = HOME.indexOf("function " + name + "(");
  assert.ok(start > -1, name + " existe en templates/index.html");
  const end = HOME.indexOf("\n}", start);
  assert.ok(end > start, name + " cierra en columna 0");
  return vm.runInNewContext("(" + HOME.slice(start, end + 2) + ")");
}
const campoEnlace = fnFromTemplate("campoEnlace");
const enlaceRow = fnFromTemplate("enlaceRow");

const DONAR = {
  nombre: "Doná sangre",
  href: "/blood-donation",
  descripcion: "Banco de sangre del hospital",
  palabras: ["donante", "turnos"],
  palabras_excluidas: ["turnos"],
  accion_texto: "Quiero donar",
  disponibilidad: "Lunes a viernes, 8 a 12",
  accion_href: "/blood-donation#formulario",
};

// ---- campoEnlace: el enlace se busca con el matcher REAL, exclusiones incluidas
test("campoEnlace: el enlace se encuentra por sus palabras con el buscador REAL", () => {
  const hits = Search.buscar([DONAR], "donante", campoEnlace);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].nombre, "Doná sangre");
});

test("campoEnlace: palabras_excluidas excluye de verdad — mismo contrato que servicios", () => {
  // "turnos" está en palabras, pero la exclusión curada lo tapa: el enlace
  // no aparece en ESA búsqueda aunque la palabra coincida.
  assert.equal(Search.buscar([DONAR], "turnos", campoEnlace).length, 0);
});

// ---- enlaceRow: el enlace es una fila de servicio más, no un diseño paralelo
test("enlaceRow: acción, nota y href propios; sin datos extra no inventa nada", () => {
  const row = enlaceRow(DONAR);
  assert.equal(row.nombre, "Doná sangre");
  assert.equal(row.href, "/blood-donation");
  assert.equal(row.centro, "Banco de sangre del hospital");
  // spread primero: el objeto nace en el contexto del vm y deepEqual
  // (strict) compara prototipos entre realms
  assert.deepEqual({ ...row.proximo },
    { kind: "turno", action: "Quiero donar", nota: "Lunes a viernes, 8 a 12", sort: "0" });
  // accion_href manda para el CTA; el enlace informativo queda como info_href
  assert.equal(row.turno_href, "/blood-donation#formulario");
  assert.equal(row.info_href, "/blood-donation");
  // degradación: sin accion_texto/disponibilidad/accion_href la fila sigue
  // siendo válida y el CTA cae al href del enlace
  const min = enlaceRow({ nombre: "X", href: "/x" });
  assert.deepEqual({ ...min.proximo }, { kind: "turno", action: "", nota: "", sort: "0" });
  assert.equal(min.turno_href, "/x");
  assert.equal(min.info_href, "/x");
});

// ---- integración en la home: misma tabla, /api/links, vacío y sugerencia.
// Contracto de fuente sobre el template (whitespace-flexible): el script
// inline no se puede cargar, así que se fija el contrato, no el formato.
test("los enlaces entran a la MISMA tabla que los consultorios, delante, sin re-sort", () => {
  assert.match(HOME, /Listing\.render\(m1, \{ cols: SERV_COLS, rows: lnks\.concat\(svs\.map\(servRow\)\),\s*options: \{ search: false, sort: false, profile: false, defaultSort: false \} \}\);/);
  // buscarTodo los incluye con el mismo campo que los pinta; detrás de los
  // enlaces van los centros que coinciden por nombre o dirección (estudio de
  // usabilidad 2026-10-15: «hospital» no encontraba el hospital).
  assert.match(HOME, /var lnks = Search\.buscar\(ENLACES, q, campoEnlace\);/);
  assert.match(HOME, /var cens = Search\.buscar\(CENTLIST\.map\(Search\.centroRow\), q, Search\.centroCampos\);/);
  // Un centro cuya página ES una página informativa (Zoonosis → /zoonosis,
  // oficina de prensa de Pinamar 2026-10-22) no se repite detrás de ella.
  assert.match(HOME, /var filasLnk = lnks\.map\(enlaceRow\);/);
  assert.match(HOME, /cens = cens\.filter\(function \(r\) \{ return !vistos\[r\.href\]; \}\);/);
  assert.match(HOME, /return \{ svs: svs, mds: mds, otras: otras, lnks: filasLnk\.concat\(cens\) \};/);
});

// Contraparte técnica, 2026-10-19: Telemedicina tiene que salir primera para cómo la nombra
// la gente y para los síntomas leves (en el pack de ejemplo de la demo).
test("Telemedicina se encuentra por cómo la nombra la gente y por síntomas leves", () => {
  const ENL = JSON.parse(readFileSync(new URL("../packs/example-town/enlaces.json", import.meta.url))).enlaces;
  for (const q of ["telemedicina", "consulta virtual", "videollamada", "online", "teleconsulta",
                   "atención desde casa", "fiebre", "tos", "fiebre, tos"]) {
    const hits = Search.buscar(ENL, q, campoEnlace, "es");
    assert.equal(hits[0] && hits[0].id, "telemedicina", q);
  }
});

test("/api/links puebla ENLACES sólo con una respuesta real y re-renderiza si ya había query", () => {
  assert.match(HOME, /fetch\("\/api\/links"\)\.then\(function \(r\) \{ return r\.ok \? r\.json\(\) : null; \}\)\s*\.then\(function \(d\) \{\s*if \(d && d\.enlaces\) \{\s*ENLACES = d\.enlaces;\s*if \(q\.value\.trim\(\) && Search && Listing\) renderResultados\(q\.value\);\s*\}\s*\}\)\s*\.catch\(function \(\) \{\}\);/);
});

test("el vacío y la sugerencia cuentan los enlaces: 'no encontramos nada' recién cuando tampoco hay enlaces", () => {
  // la condición de cero resultados incluye lnks, tanto para pintar el vacío…
  assert.match(HOME, /if \(!svs\.length && !mds\.length && !otras\.length && !lnks\.length\) \{\s*res\.innerHTML = '<p class="empty">'\s*\+\s*esc\(T\("home\.results\.empty"/);
  // …como para disparar "quizás quisiste decir…" y para aceptar lo sugerido
  assert.match(HOME,
    /!r\.svs\.length && !r\.mds\.length && !r\.otras\.length && !r\.lnks\.length\) \{\s*var q2 = Search\.sugerirQuery/);
  assert.match(HOME,
    /if \(r2\.svs\.length \|\| r2\.mds\.length \|\| r2\.otras\.length \|\| r2\.lnks\.length\) \{ r = r2; sugerido = q2; \}/);
});
