// prep.mjs / prep_model.py — el par que decide qué secciones tiene una
// preparación y cómo se llaman. Son dos implementaciones del mismo modelo
// (navegador y servidor), así que el test CORRE LAS DOS contra las mismas
// entradas y compara: la paridad es la razón de ser del par.
//
// Correr: node --test tests/prep-modelo.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import * as P from "../static/platform/prep.mjs";

// Los objetos que crea el módulo viven en el realm del vm, así que su
// Array.prototype no es el de este archivo y deepStrictEqual los rechaza aunque
// sean idénticos. Compararlos como JSON es comparar lo que de verdad importa.
const plano = (v) => JSON.parse(JSON.stringify(v));

const raiz = new URL("../", import.meta.url);
const CATALOGO = JSON.parse(
  readFileSync(new URL("packs/global/estudios-preparacion.json", raiz), "utf8")
).estudios;

const porId = Object.fromEntries(CATALOGO.map((e) => [e.id, e]));

test("el ayuno se anuncia sólo cuando es un requisito", () => {
  // Si no hace falta, el dato ya viaja como ítem de "No hace falta": decirlo
  // además arriba lo diría dos veces en la misma tarjeta.
  const conAyuno = P.ayuno(porId["laboratorio-sangre"]);
  assert.equal(conAyuno.hay, true);
  assert.match(conAyuno.texto, /^Ayuno de 8 horas\./);

  const sinAyuno = P.ayuno(porId["electrocardiograma"]);
  assert.equal(sinAyuno.hay, false);
  assert.equal(sinAyuno.texto, "");
  assert.ok(
    porId["electrocardiograma"].no_hace_falta.some((x) => /Ayunar/.test(x)),
    "el dato tiene que estar en 'No hace falta'"
  );
});

test("no dice 'podés tomar agua' dos veces", () => {
  // laboratorio-sangre lo explica con más detalle en 'antes': gana el detalle.
  assert.equal(P.ayuno(porId["laboratorio-sangre"]).texto, "Ayuno de 8 horas.");
  // glucemia también lo menciona en 'antes'
  assert.ok(!/agua/i.test(P.ayuno(porId["glucemia"]).texto));
  // una entrada sin mención de agua en 'antes' sí la lleva en la frase
  const e = { ayuno: { required: true, horas: 6, agua_permitida: true }, antes: ["Vení con ropa cómoda."] };
  assert.equal(P.ayuno(e).texto, "Ayuno de 6 horas. Podés tomar agua.");
});

test("una entrada sin ninguna sección no promete nada", () => {
  assert.equal(P.vacia({}), true);
  assert.equal(P.vacia({ nombre: "X", match: ["x"] }), true);
  assert.equal(P.vacia(porId["vacunacion"]), false);
});

test("el nombre viejo del campo sigue leyéndose", () => {
  // `antes` se llamaba `preparacion` hasta 2026-07-29 y ese nombre quedó
  // guardado dentro del ai_json de las solicitudes anteriores.
  const vieja = { preparacion: ["No fumes antes."] };
  assert.deepEqual(plano(P.antes(vieja)), ["No fumes antes."]);
  assert.equal(P.secciones(vieja)[0].titulo, "Antes de venir");
});

test("las secciones salen en el orden en que se leen, y las vacías no salen", () => {
  const secs = P.secciones(porId["vacunacion"]);
  assert.deepEqual(
    plano(secs.map((s) => s.clave)),
    ["antes", "traer", "avisar", "no_hace_falta", "despues"]
  );
  const soloTraer = P.secciones({ traer: ["DNI"] });
  assert.equal(soloTraer.length, 1);
  assert.equal(soloTraer[0].tono, "normal");
  assert.equal(P.secciones({ no_hace_falta: ["Ayunar."] })[0].tono, "no");
  assert.equal(P.secciones({ avisar: ["Tenés fiebre."] })[0].tono, "aviso");
});

test("todas las entradas del catálogo tienen algo para decir", () => {
  for (const e of CATALOGO) {
    assert.ok(!P.vacia(e), `${e.id} no dice nada`);
    assert.ok(e.nombre, `${e.id} sin nombre`);
    assert.ok((e.match || []).length, `${e.id} sin keywords`);
  }
});

test("ninguna keyword de match se repite entre entradas", () => {
  // El match elige por longitud; un empate lo resolvería el orden del archivo,
  // que no es una regla que nadie haya decidido.
  const vistas = new Map();
  for (const e of CATALOGO) {
    for (const kw of e.match || []) {
      assert.ok(!vistas.has(kw), `${kw} está en ${vistas.get(kw)} y en ${e.id}`);
      vistas.set(kw, e.id);
    }
  }
});

test("prep.mjs y prep_model.py dicen exactamente lo mismo", () => {
  const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(new URL(".", raiz).pathname)})
from app.modules.turnos import prep_model
entradas = json.load(open(${JSON.stringify(new URL("packs/global/estudios-preparacion.json", raiz).pathname)}, encoding="utf-8"))["estudios"]
entradas.append({"preparacion": ["nombre viejo"], "ayuno": {"required": True, "horas": 6, "agua_permitida": False}})
entradas.append({})
out = [{"resumen": prep_model.resumen(e), "ayuno": prep_model.ayuno(e),
        "secciones": prep_model.secciones(e), "vacia": prep_model.vacia(e)}
       for e in entradas]
print(json.dumps(out, ensure_ascii=False))
`], { encoding: "utf8" });
  assert.equal(py.status, 0, py.stderr);

  const entradas = CATALOGO.concat([
    { preparacion: ["nombre viejo"], ayuno: { required: true, horas: 6, agua_permitida: false } },
    {}
  ]);
  const js = entradas.map((e) => ({
    resumen: P.resumen(e), ayuno: P.ayuno(e), secciones: P.secciones(e), vacia: P.vacia(e)
  }));
  assert.deepEqual(plano(js), JSON.parse(py.stdout));
});

// The wizard's «Cómo prepararte» card and the service page draw the SAME
// markup (responsable del producto 26.10: on «Solicitud enviada» «Antes de venir», «Qué llevar»
// did not even look bold — the wizard had its own lighter variant).
test("prep.mjs y prep_model.py dibujan la misma preparación", () => {
  const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(new URL(".", raiz).pathname)})
from app.modules.turnos import prep_model
entradas = json.load(open(${JSON.stringify(new URL("packs/global/estudios-preparacion.json", raiz).pathname)}, encoding="utf-8"))["estudios"]
entradas.append({"resumen": "Traé <todo> & \\"eso\\" 'ya'", "traer": ["DNI"]})
print(json.dumps([prep_model.cuerpo_html(e) for e in entradas], ensure_ascii=False))
`], { encoding: "utf8" });
  assert.equal(py.status, 0, py.stderr);
  const entradas = CATALOGO.concat([{ resumen: "Traé <todo> & \"eso\" 'ya'", traer: ["DNI"] }]);
  assert.deepEqual(entradas.map((e) => P.cuerpoHtml(e)), JSON.parse(py.stdout));
  // headings are headings, lists are lists, one item is a sentence
  const html = P.cuerpoHtml(porId["vacunacion"]);
  assert.match(html, /<h3 class="prep-sec-h">Antes de venir<\/h3>/);
  assert.match(html, /<h3 class="prep-sec-h">Qué llevar<\/h3>/);
  assert.match(P.cuerpoHtml({ traer: ["DNI"] }), /<p class="prep-single prep-single--normal">DNI<\/p>/);
});

test("el asistente dibuja la preparación con el cuerpo compartido", () => {
  const src = readFileSync(new URL("app/modules/turnos/service.mjs", raiz), "utf8");
  assert.match(src, /P\.cuerpoHtml\(entry\)/);
  assert.doesNotMatch(src, /wz-prep-sub|wz-prep-bullets/);
});
