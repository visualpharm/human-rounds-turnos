// format.mjs — cómo se escribe un teléfono, un mail y una edad en todo
// el panel (responsable del producto, 2026-07-28). El módulo es puro (no toca el DOM), así que se
// importa y corre de verdad en vez de mirarle el texto.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

// El bloque de país (fase 2b, 2026-07-30) vive en packs/ar/country.json —
// en producción lo inyecta el servidor como window.SANA_COUNTRY_PHONE antes
// de este módulo (ver format.mjs). Acá se inyecta desde el MISMO archivo que
// lee format.py, para que el test de paridad de más abajo compare las dos
// runtimes contra una sola fuente, no contra dos copias que podrían derivar.
const country = JSON.parse(
  readFileSync(new URL("../packs/ar/country.json", import.meta.url), "utf8")
);
globalThis.SANA_COUNTRY_PHONE = country.phone;
const F = await import("../static/platform/format.mjs");

test("digits deja sólo los dígitos; null y undefined son vacío, el cero es el valor 0", () => {
  assert.equal(F.digits(null), "");
  assert.equal(F.digits(undefined), "");
  assert.equal(F.digits(""), "");
  assert.equal(F.digits(0), "0");
  assert.equal(F.digits(30123456), "30123456");
  assert.equal(F.digits("30.123.456"), "30123456");
  assert.equal(F.digits(" DNI: 30 123 456-7 "), "301234567");
  assert.equal(F.digits("abc"), "");
  assert.equal(F.dni("30123456"), "30.123.456");
  assert.equal(F.dni("x"), "");
});

test("el mismo número escrito de cinco formas da el mismo teléfono", () => {
  // Es el caso real: el paciente lo carga en su cuenta, la mesa lo copia del
  // DNI y el padrón lo trae de otra manera. País, 9 de celular y 15 histórico
  // son adornos del mismo número.
  const formas = [
    "+5492254402237",
    "005492254402237",
    "02254 15 40-2237",
    "2254402237",
    "(2254) 40-2237"
  ];
  for (const v of formas) {
    assert.equal(F.telNacional(v), "2254402237", `no normalizó ${v}`);
  }
});






/* LA forma canónica (responsable del producto, 2026-07-28). El 9 distingue un celular de un fijo:
   +54 11 4321-5678 y +54 9 11 4321-5678 son dos líneas distintas, así que no
   se inventa — sale sólo si el número venía marcado como celular. */
test("escribe la forma canónica, con el 9 sólo cuando es celular", () => {
  assert.equal(F.telTexto("+5491135477302"), "+54 9 11 3547-7302");   // celular AMBA
  assert.equal(F.telTexto("1143215678"), "+54 11 4321-5678");         // fijo AMBA
  assert.equal(F.telTexto("+5492254123456"), "+54 9 2254 12-3456");   // celular Pinamar
  assert.equal(F.telTexto("(02254) 40-1234"), "+54 2254 40-1234");    // fijo Pinamar
  assert.equal(F.telTexto("2214831234"), "+54 221 483-1234");         // La Plata, área de 3
});

test("el 15 histórico es un celular, y se escribe con el 9", () => {
  assert.equal(F.telTexto("02254 15 123456"), "+54 9 2254 12-3456");
  assert.equal(F.telTexto("011 15 3547-7302"), "+54 9 11 3547-7302");
  // sin marca no se adivina: agregarle el 9 sería otro número
  assert.equal(F.telTexto("2254123456"), "+54 2254 12-3456");
});

test("guardar usa la misma forma, sin separadores", () => {
  assert.equal(F.telE164("02254 15 123456"), "+5492254123456");
  assert.equal(F.telE164("(02254) 40-1234"), "+542254401234");
  assert.equal(F.telE164("no tiene"), "");
});

test("el link va a WhatsApp, que es por donde el hospital le escribe", () => {
  const html = F.telHtml("02254 15 402237");
  assert.match(html, /href="https:\/\/wa\.me\/5492254402237"/);
  assert.doesNotMatch(html, /href="tel:/);
  assert.match(html, />\+54 9 2254 40-2237</);
});

test("un 0800 no se corta por característico ni va a WhatsApp", () => {
  assert.equal(F.telTexto("0800-222-1002"), "0800-222-1002");
  assert.equal(F.telWa("0800-222-1002"), "");
});

test("lo que no es un teléfono argentino se muestra como vino", () => {
  // Inventarle un formato a un número extranjero lo haría ilegible; se
  // respeta lo cargado.
  assert.equal(F.telTexto("+1 415 555 0000"), "+1 415 555 0000");
  assert.equal(F.telTexto("no tiene"), "no tiene");
  assert.equal(F.telTexto(""), "");
});

test("un mail es clickeable y un contacto elige solo qué es", () => {
  assert.match(F.emailHtml("rosa@ejemplo.com"), /href="mailto:rosa@ejemplo\.com"/);
  assert.match(F.contactoHtml("rosa@ejemplo.com"), /mailto:/);
  assert.match(F.contactoHtml("2254402237"), /wa\.me/);
  assert.equal(F.contactoHtml(""), "");
});

test("el contacto del paciente dice de quién es (problems.md №4)", () => {
  // Responsable del producto, 2026-11-03: el teléfono no se imprime; es un icono que abre el
  // marcador, y de quién es va en el título (tooltip), no como texto.
  const icos = (h) => (h.match(/class="cnt-ico-link"/g) || []).length;
  const uno = F.contactoQuienHtml({ contacto: "2254402237", contacto_de: "paciente" });
  assert.match(uno, /href="tel:\+542254402237"[^>]*title="[^"]*Del paciente"/);
  assert.doesNotMatch(uno, /cnt-contacto-de/);
  // Responsable del producto, 2026-11-04: junto al teléfono va el icono de WhatsApp (wa.me, móvil
  // argentino con 549), con el mismo título y el mismo valor para copiar.
  assert.match(uno, /<a class="cnt-wa-link" href="https:\/\/wa\.me\/5492254402237" target="_blank" rel="noopener" data-copy="\+54 2254 40-2237"[^>]*title="Escribirle por WhatsApp[^"]*Del paciente"/);
  assert.match(uno, /whatsapp-sf-regular\.svg/);
  assert.equal((uno.match(/class="cnt-wa-link"/g) || []).length, 1);
  assert.doesNotMatch(F.contactoIconoHtml("rosa@ejemplo.com"), /wa\.me/, "un mail no tiene WhatsApp");
  assert.doesNotMatch(F.contactoIconoHtml("0800-222-1002"), /wa\.me/, "un 0800 no tiene WhatsApp");
  assert.doesNotMatch(uno.replace(/<[^>]*>/g, ""), /\d/, "ningún número impreso como texto");
  assert.match(F.contactoQuienHtml({ contacto: "2254402237", contacto_de: "responsable", contacto_responsable: "Ana Pérez" }),
    /title="[^"]*De quien sacó el turno: Ana Pérez"/);
  assert.match(F.contactoQuienHtml({ contacto: "2254402237", contacto_de: "responsable" }), /title="[^"]*De quien sacó el turno"/);
  assert.match(F.contactoQuienHtml({ contacto: "2254402237", contacto_de: "solicitud" }), /Dado al pedir el turno/);
  assert.match(F.contactoQuienHtml({ contacto: "2254402237", contacto_de: "responsable", contacto_responsable: "<b>x</b>" }), /&lt;b&gt;x/);
  assert.equal(F.contactoQuienHtml({ contacto: "" }), "");
  assert.equal(F.contactoQuienHtml(null), "");
  // Si la cuenta tiene OTRO número, van los dos iconos, cada uno con su procedencia
  // (the product owner, hub, 2026-10-23).
  const dos = F.contactoQuienHtml({ contacto: "2254402237", contacto_de: "solicitud",
    contacto_cuenta: "2254100002", contacto_cuenta_de: "paciente" });
  assert.match(dos, /Dado al pedir el turno[\s\S]*tel:\+542254100002[^>]*title="[^"]*En la cuenta del paciente/);
  assert.equal(icos(dos), 2);
  assert.match(F.contactoQuienHtml({ contacto: "2254402237", contacto_de: "solicitud",
    contacto_cuenta: "2254100002", contacto_cuenta_de: "responsable", contacto_responsable: "Ana Pérez" }),
    /En la cuenta de Ana Pérez, quien sacó el turno/);
  assert.match(F.contactoQuienHtml({ contacto: "2254402237", contacto_de: "solicitud",
    contacto_cuenta: "2254100002", contacto_cuenta_de: "responsable" }), /En la cuenta de quien sacó el turno"/);
  assert.equal(icos(uno), 1);
  // «Mis turnos» y «Mis pacientes» lo escriben igual: un médico no ve dos
  // teléfonos del mismo paciente sin saber de quién es cada uno.
  const turnos = readFileSync(new URL("../static/platform/admin-my-appointments.mjs", import.meta.url), "utf8");
  assert.match(turnos, /Format\.contactoQuienHtml\(t, ""\)/);
  assert.doesNotMatch(turnos, /Format\.contactoHtml\(/);
});

test("escapa lo que le pasen", () => {
  assert.doesNotMatch(F.emailHtml('"><script>x</script>'), /<script>/);
});

test("la edad se dice en años, y en meses sólo cuando todavía no cumplió uno", () => {
  const hoy = new Date();
  const iso = (y, m, d) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  const hace = (anios, meses = 0) => {
    // Día 1, no el de hoy: restar meses cayendo en un día que ese mes no
    // tiene (29-31) hace rodar la fecha al mes siguiente y el test fallaba
    // sólo los días 29 al 31 ("5 meses" daba "4 meses" un 29 de julio).
    const d = new Date(hoy.getFullYear() - anios, hoy.getMonth() - meses, 1);
    return iso(d.getFullYear(), d.getMonth() + 1, d.getDate());
  };
  assert.equal(F.edad(hace(47)), 47);
  assert.equal(F.edadTexto(hace(47)), "47");
  // Un bebé no tiene "0 años": en pediatría los meses SON el dato.
  assert.equal(F.edadTexto(hace(0, 5)), "5 meses");
  assert.equal(F.edadTexto(hace(0, 1)), "1 mes");
  assert.equal(F.edad(null), null);
  assert.equal(F.edadTexto(null), "");
});

test("la fecha corta lleva el mes en letras", () => {
  // 05/05 y 11/08 en la misma columna obligan a adivinar cuál es el mes.
  assert.equal(F.fechaCorta("2015-03-14"), "14 Mar 2015");
  assert.equal(F.fechaCorta("2026-07-28T10:30:00"), "28 Jul 2026");
  assert.equal(F.fechaCorta(null), "");
});

/* El sitio escribe teléfonos desde los DOS lados: format.mjs en el navegador
   (panel y wizard) y format.py en el servidor (páginas de centro, servicio y
   médico). Dos runtimes, dos archivos — pero UNA regla. Este test no mira el
   texto del código: CORRE las dos y compara. Si alguien toca una sola, falla.
   Para otro país se cambia el bloque PAIS de los dos, y esto lo verifica. */
test("format.py escribe exactamente igual que format.mjs", () => {
  const casos = [
    "+54 9 11 3547-7302", "+5491135477302", "01135477302", "011 15 3547-7302",
    "+54 11 4321-5678", "1143215678", "+54 9 2254 12-3456", "02254 15 123456",
    "+54 2254 40-1234", "(02254) 40-1234", "2254401234", "2214831234",
    "005492254402237", "0800-222-1002", "+1 415 555 0000", "no tiene", "",
    "(02254) 49-1670 / 49-1671", "107", "112", "(011) 4000-0001"
  ];
  const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(new URL("..", import.meta.url).pathname)})
from app.core import format
casos = json.loads(sys.argv[1])
print(json.dumps([[format.tel_text(c), format.tel_e164(c), format.tel_wa(c), format.tel_href(c)] for c in casos]))
`, JSON.stringify(casos)], { encoding: "utf8" });
  assert.equal(py.status, 0, py.stderr);
  const desdePython = JSON.parse(py.stdout);
  casos.forEach((c, i) => {
    assert.deepEqual(
      [F.telTexto(c), F.telE164(c), F.telWa(c), F.telHref(c)],
      desdePython[i],
      `format.mjs y format.py no coinciden en ${JSON.stringify(c)}`
    );
  });
});

/* El link marca lo que la página muestra (revisión de la demo 15.11: la
   página de donación decía «+800 400 0001» y llamaba al 01140000001). */
test("telHref: el tel: de un número completo es su E.164; un 107 queda 107", () => {
  assert.equal(F.telHref("(011) 4000-0001"), "+541140000001");
  assert.equal(F.telTexto("(011) 4000-0001"), "+54 11 4000-0001");
  assert.equal(F.telHref("107"), "107");
  assert.equal(F.telHref("+1 415 555 0000"), "+14155550000");
  assert.equal(F.telHref(""), "");
});

/* Un teléfono que no se puede llamar no se guarda (TASK-51, 26.10): el
   registro traía «2254» escrito como valor, la persona tipeaba el número
   completo encima y quedaba 22542254000000 — y se aceptaba. Una sola regla,
   en los dos runtimes: un número del país completo, o uno extranjero escrito
   con su +. */
test("telValido: completo del país o extranjero con +, igual en format.py", () => {
  const validos = ["2254 550000", "02254 15 550000", "+54 9 2254 55-0000", "11 3547-7302",
    "+1 415 555 0000", "+34 612 345 678"];
  const invalidos = ["22542254000000", "2254", "2254 2254 550000", "550000", "0800-222-1002",
    "+54 2254", "no tiene", "", "@", "+1 23"];
  validos.forEach(v => assert.equal(F.telValido(v), true, v));
  invalidos.forEach(v => assert.equal(F.telValido(v), false, v));
  const casos = validos.concat(invalidos);
  const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(new URL("..", import.meta.url).pathname)})
from app.core import format
print(json.dumps([format.tel_valido(c) for c in json.loads(sys.argv[1])]))
`, JSON.stringify(casos)], { encoding: "utf8" });
  assert.equal(py.status, 0, py.stderr);
  assert.deepEqual(JSON.parse(py.stdout), casos.map(c => F.telValido(c)));
});

/* El DNI se escribe con puntos de miles, en TODOS los admin: format.mjs
   (dni, extraído de las cuatro copias que había en admin-*.js el
   2026-08-31) es la única implementación — no hay espejo en el servidor
   porque ningún render del lado servidor escribe DNIs. El cero inicial de
   un DNI viejo corto no se pierde: agrupamos dígitos, no reformateamos un
   número. */
test("el DNI lleva puntos de miles, una sola regla en todo el sitio", () => {
  assert.equal(F.dni("30123456"), "30.123.456");
  assert.equal(F.dni(30123456), "30.123.456");
  assert.equal(F.dni("3.012.345"), "3.012.345"); // ya venía con puntos: idempotente
  assert.equal(F.dni("30-1234-56"), "30.123.456"); // guiones/suciedad se limpian
  assert.equal(F.dni("0012345"), "0.012.345"); // el cero inicial es un dígito más
  assert.equal(F.dni(""), "");
  assert.equal(F.dni(null), "");
  assert.equal(F.dni("sin número"), "");
});

/* El DNI que se muestra se copia (responsable del producto, 2026-11-04): UNA función para todo el
   sitio. Lleva los dígitos pelados en data-copy-dni, el número con puntos a la
   vista y el iconito sólo como adorno (el CSS lo muestra donde hay mouse). */
test("dniHtml: puntos a la vista, dígitos pelados para copiar, prefijo opcional, vacío si no hay DNI", () => {
  const h = F.dniHtml("30123456");
  assert.match(h, /^<span class="dni-cp" role="button" tabindex="0" data-copy-dni="30123456" /);
  assert.match(h, />30\.123\.456<span class="dni-cp-ico" aria-hidden="true"><\/span><\/span>$/);
  assert.match(h, /title="Copiar DNI"/);
  assert.match(F.dniHtml("30.123.456", "DNI "), /data-copy-dni="30123456"[^>]*>DNI 30\.123\.456</);
  assert.equal(F.dniHtml(""), "");
  assert.equal(F.dniHtml(null), "");
  assert.equal(F.dniHtml("sin número"), "");
});

test("Dates.pasada: «2 de octubre» sin hora; con año sólo si no es el actual", async () => {
  const D = await import("../static/platform/dates.mjs");
  const hoy = new Date("2026-11-04T12:00:00-03:00");
  assert.equal(D.pasada("2026-10-02", hoy), "2 de octubre");
  assert.equal(D.pasada("2025-10-15", hoy), "15 de octubre de 2025");
  assert.equal(D.pasada("", hoy), "");
  // Lo de hoy que ya pasó: «Hoy», como corta() en el teléfono.
  assert.equal(D.pasada("2026-11-04", hoy), "Hoy");
  assert.equal(D.pasada("2026-11-04", hoy), D.corta("2026-11-04", "", hoy));
});

test("dniHtml: el iconito de copiar mide lo mismo que los de teléfono y WhatsApp (20px)", () => {
  const src = readFileSync(new URL("../static/platform/format.mjs", import.meta.url), "utf8");
  // Lo que se ve es el dibujo: la máscara (-webkit- y estándar) lo pinta a este tamaño.
  const dibujo = [...src.matchAll(/mask:url\(" \+ COPIA_DNI_ICONO \+ "\) center\/(\d+)px (\d+)px no-repeat/g)].map((m) => [m[1], m[2]]);
  const tel = src.match(/\.cnt-ico\{display:block;width:(\d+)px;height:(\d+)px;/);
  assert.equal(dibujo.length, 2, "both mask declarations of the copy icon are where this test looks");
  assert.ok(tel, "the contact icon rule is where this test looks");
  for (const d of dibujo) assert.deepEqual(d, [tel[1], tel[2]]);
});

test("dniHtml: el iconito de copiar no ensancha ninguna tabla (ocupa 18px en la fila) y su caja no recorta el dibujo", () => {
  const src = readFileSync(new URL("../static/platform/format.mjs", import.meta.url), "utf8");
  const r = src.match(/\.dni-cp-ico\{display:inline-block;width:(\d+)px;height:(\d+)px;margin-left:(\d+)px;/);
  const m = src.match(/mask:url\(" \+ COPIA_DNI_ICONO \+ "\) center\/(\d+)px \d+px no-repeat/);
  assert.ok(r && m, "the icon box rule and its mask size are where this test looks");
  const ancho = +r[1], alto = +r[2], margen = +r[3], dibujo = +m[1];
  // Lo que ocupa en la línea, como el de 14px de antes: la columna DNI de la
  // cola a 1280 tiene 3px de aire; con 24px (4 + 20) Asistencia quedaba afuera.
  // Sin márgenes negativos: una caja que sobresale ensancha la celda que la contiene.
  assert.ok(margen + ancho <= 18, `the copy icon takes ${margen + ancho}px of the row, more than 18px`);
  // El dibujo de 20px va centrado en la caja: sólo puede sobrarle lo vacío del borde.
  const svg = readFileSync(new URL("../static/icons/copy-sf-regular.svg", import.meta.url), "utf8");
  const lado = +svg.match(/viewBox="0 0 (\d+) \d+"/)[1];
  const nums = svg.match(/d="([^"]+)"/)[1].match(/-?\d+(?:\.\d+)?/g).map(Number);
  const xs = nums.filter((_, i) => i % 2 === 0), ys = nums.filter((_, i) => i % 2 === 1);
  const k = dibujo / lado, fueraX = (dibujo - ancho) / 2, fueraY = (dibujo - alto) / 2;
  assert.ok(Math.min(...xs) * k >= fueraX && Math.max(...xs) * k <= dibujo - fueraX, "the box cuts the drawing left or right");
  assert.ok(Math.min(...ys) * k >= fueraY && Math.max(...ys) * k <= dibujo - fueraY, "the box cuts the drawing top or bottom");
});

test('schedule summaries share the same weekday ordering and interval format in Python and ES',()=>{
  const bands=[{dias:[4,3],desde:'08:00',hasta:'14:00',cada_min:20},{dias:[0,1],desde:'09:00',hasta:'12:00',cada_min:30},{dias:[]}];
  const result=spawnSync('python3',['-c','import json,sys; from app.core.format import schedule_band; print(json.dumps([schedule_band(band) for band in json.loads(sys.argv[1])],ensure_ascii=False))',JSON.stringify(bands)],
    {encoding:'utf8',env:{...process.env,TURNOS_ENV_FILE:'/tmp/slice6-empty-env',TURNOS_FAKE_AI:'1',TURNOS_SKIP_HSI:'1',DATABASE_URL:''}});
  assert.equal(result.status,0,result.stderr);
  assert.deepEqual(bands.map(F.scheduleBand),JSON.parse(result.stdout));
  assert.equal(F.scheduleBand(bands[0]),'Mié y jue, 08:00 a 14:00 · turnos de 20 min');
});

/* «Resuelta por» (Hospital, 2026-11-13): la base guarda auto:web, mesa:<usuario> o
   medico:<usuario>, y la tarjeta lo mostraba tal cual. Una sola función en los dos
   runtimes escribe quién resolvió; el valor guardado no se toca. */
test("resueltaPor escribe quién resolvió la solicitud, igual en format.py", () => {
  const casos = [
    ["auto:web", null, false], ["auto:web", null, true],
    ["mesa:lucia", null, false], ["mesa:lucia", "Lucía Gómez", false],
    ["medico:drperez", null, false], ["medico:drperez", "PEREZ JUAN", true],
    ["whatsapp:bot", null, false], ["lucia", null, false], ["", null, false], [null, null, true]
  ];
  assert.equal(F.resueltaPor("auto:web"), "Automática: el paciente sacó el turno en la página");
  assert.equal(F.resueltaPor("auto:web", null, true), "Automática (web)");
  assert.equal(F.resueltaPor("mesa:lucia"), "Mesa de entrada · lucia");
  assert.equal(F.resueltaPor("mesa:lucia", "Lucía Gómez"), "Mesa de entrada · Lucía Gómez");
  assert.equal(F.resueltaPor("medico:drperez", "PEREZ JUAN"), "Médico · Perez Juan");
  assert.equal(F.resueltaPor("medico:drperez"), "Médico · drperez");
  assert.equal(F.resueltaPor("whatsapp:bot"), "bot");
  assert.equal(F.resueltaPor("lucia"), "lucia");
  assert.equal(F.resueltaPor(""), "");
  assert.equal(F.resueltaPor(null), "");
  const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(new URL("..", import.meta.url).pathname)})
from app.core import format
print(json.dumps([format.resolved_by_text(v, n, c) for v, n, c in json.loads(sys.argv[1])], ensure_ascii=False))
`, JSON.stringify(casos)], { encoding: "utf8",
    env: { ...process.env, TURNOS_ENV_FILE: "/tmp/slice6-empty-env", TURNOS_FAKE_AI: "1", TURNOS_SKIP_HSI: "1", DATABASE_URL: "" } });
  assert.equal(py.status, 0, py.stderr);
  assert.deepEqual(casos.map(([v, n, c]) => F.resueltaPor(v, n, c)), JSON.parse(py.stdout));
});
