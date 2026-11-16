import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { mountDoctorPatients } from "../static/platform/admin-doctor-patients.mjs";
import { placeHost, withScreenBoot, withSiblings } from "./screen-boot-fixture.mjs";
const patientsTemplate = await readFile(new URL("../templates/admin/my-patients.html", import.meta.url), "utf8");
const patientsBootstrap = [...patientsTemplate.matchAll(/<script type="module">\n([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(patientsBootstrap, "the actual patients template has a native bootstrap");
const FILTERS_URL = new URL("../static/platform/admin-filter-controls.mjs", import.meta.url).href;
const tick = () => new Promise(resolve => setImmediate(resolve)); async function settle(n = 8) { while (n--) await tick(); }
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }
class Node { constructor(id = "") { this.id=id; this.innerHTML=""; this.textContent=""; this.style={}; this.listeners=new Map(); } addEventListener(n,f){this.listeners.set(n,f)} removeEventListener(n){this.listeners.delete(n)} querySelector(){return null} querySelectorAll(){return []} appendChild(){} }
const PlacedNode = withSiblings(Node);
async function executePatientsBootstrap(source, t) {
  const host=new PlacedNode("sec-mis-pacientes"), button=new Node(), events=new Map(), timers=new Map(); let next=0,reloads=0;
  const placed=placeHost(PlacedNode,host); host.children=[];
  const saved=Object.fromEntries(["document","window","location","setTimeout","clearTimeout","addEventListener"].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
  host.replaceChildren=(...nodes)=>{host.children=nodes}; const recovery={content:{cloneNode(){return{fragment:true,children:["alert",button],querySelector(){return button}}}}};
  globalThis.document={getElementById:id=>id==="sec-mis-pacientes"?host:recovery,createElement:()=>{const node=new PlacedNode();node.children=[];return node}}; globalThis.window={addEventListener(n,f){events.set(n,f)}}; globalThis.addEventListener=(n,f)=>events.set(n,f); globalThis.location={reload(){reloads++}};
  globalThis.setTimeout=fn=>{const id=++next;timers.set(id,fn);return id};globalThis.clearTimeout=id=>timers.delete(id);
  t.after(()=>{for(const[k,v]of Object.entries(saved))v?Object.defineProperty(globalThis,k,v):delete globalThis[k]});
  const code=withScreenBoot(patientsBootstrap).replace(/"\{\{ static\('platform\/admin-doctor-patients\.mjs'\) \}\}"/,JSON.stringify("data:text/javascript,"+encodeURIComponent(source)));
  await import("data:text/javascript,"+encodeURIComponent(code)+"#"+Math.random()); return {host,button,events,timers,slowCopy:placed.slowCopy,fire(){[...timers.values()].forEach(fn=>fn())},reloads:()=>reloads};
}
function fixture(fetches, t, session="data:text/javascript,export const confirmar=(r,b,p)=>Promise.resolve(false)") {
  const host = new Node("sec-mis-pacientes"), nodes = new Map([[host.id, host]]), events = new Map(), seen=[];
  const saved = Object.fromEntries(["window","document","fetch","location","history"].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)])); const ds=[];
  globalThis.document={head:{appendChild(){}},getElementById:id=>nodes.get(id)||null,querySelector:s=>s==="#sec-mis-pacientes"?host:null,querySelectorAll:()=>[],createElement:()=>new Node()};
  globalThis.window={SANA_STATIC:{"platform/dates.mjs":"data:text/javascript,export const relativo=()=>'';export const amigable=()=>''","platform/format.mjs":"data:text/javascript,export const dni=x=>x;export const nombre=x=>x;export const contactoHtml=()=>''","platform/admin-preferences.mjs":"data:text/javascript,export const load=()=>Promise.resolve()","platform/admin-table.mjs":"data:text/javascript,export const crear=()=>({programarFit(){},dispose(){globalThis.__td++}});export const columnas=()=>({activas(){return[{key:'paciente',label:'Paciente',always:true}]},gearHtml(){return''},dispose(){globalThis.__pd++}})","platform/admin-session.mjs":session,"platform/i18n.mjs":"data:text/javascript,export const t=(k,v,f)=>f","platform/admin-filter-controls.mjs":FILTERS_URL},addEventListener(n,f){events.set(n,f)},removeEventListener(n){events.delete(n)},requestAnimationFrame:f=>{f();return 1},cancelAnimationFrame(){}};
  globalThis.location={href:"https://example.test/admin/my-patients?doctor=one",search:"?doctor=one",pathname:"/admin/my-patients"}; globalThis.history={pushState(){},replaceState(){}}; globalThis.window.location=globalThis.location; globalThis.window.history=globalThis.history; globalThis.__td=0;globalThis.__pd=0;
  globalThis.fetch=url=>{seen.push(url);const x=fetches.shift();assert.ok(x,"unexpected "+url);return x}; t.after(()=>{ds.forEach(d=>d());for(const[k,v]of Object.entries(saved))v?Object.defineProperty(globalThis,k,v):delete globalThis[k]});
  return {host,seen,run(role="admin"){const d=mountDoctorPatients({root:globalThis.document,role});ds.push(d);return d}};
}
const ROSTER={medico_id:"two",medico:{},medicos:[{id:"two",nombre:"Dr Dos"}],items:[{nombre:"Ana",dni:"1"}]};
test("retained column callbacks cannot repaint a disposed roster", async t => {
  const page = fixture([Promise.resolve(response(200, ROSTER))], t);
  window.SANA_STATIC['platform/admin-table.mjs'] = 'data:text/javascript,' + encodeURIComponent(`
    export const crear = () => ({programarFit(){}, dispose(){}});
    export const columnas = config => {
      globalThis.__doctorPickerCallback = config.onChange;
      return {activas: () => [{key:'paciente',label:'Paciente',always:true}], gearHtml: () => '', dispose(){}};
    };
  `);
  t.after(() => { delete globalThis.__doctorPickerCallback; });
  const dispose = page.run();
  await dispose.ready;
  const callback = globalThis.__doctorPickerCallback;
  assert.equal(typeof callback, 'function');
  page.host.innerHTML = 'before active repaint';
  callback();
  assert.match(page.host.innerHTML, /Ana/, 'a live column change must repaint');
  dispose();
  page.host.innerHTML = 'fresh screen';
  callback();
  assert.equal(page.host.innerHTML, 'fresh screen');
});
test("roster retry works while mounted and becomes inert after disposal", async t => {
  const page = fixture([Promise.resolve(response(500)), Promise.resolve(response(500))], t);
  const retry = new Node();
  page.host.querySelector = selector => selector === "[data-retry]" ? retry : null;
  const dispose = page.run();
  await dispose.ready;
  retry.listeners.get("click")();
  await settle();
  assert.equal(page.seen.length, 2, "live retry must make a request");
  const oldRetry = retry.listeners.get("click");
  const rendered = page.host.innerHTML;
  dispose();
  oldRetry();
  await settle();
  assert.equal(page.seen.length, 2, "disposed retry must not make a request");
  assert.equal(page.host.innerHTML, rendered);
});
test("actual roster preserves latest URL intent and renders a populated admin roster",async t=>{const p=fixture([Promise.resolve(response(200,ROSTER))],t);await p.run().ready;await settle();assert.equal(p.seen[0],"/api/admin/doctor-patients?medico=one");assert.match(p.host.innerHTML,/Ana/)});
test("ready remains pending until the initial roster response has rendered",async t=>{const load=deferred(),p=fixture([load.promise],t);const d=p.run();let done=false;d.ready.then(()=>{done=true});await settle();assert.equal(done,false);load.resolve(response(200,ROSTER));await d.ready;assert.match(p.host.innerHTML,/Ana/)});
test("a rejected required session dependency is observed through ready",async t=>{const p=fixture([],t,"data:text/javascript,throw new Error('session import rejected')");await assert.rejects(p.run().ready,/session import rejected/);assert.equal(p.seen.length,0)});
test("the actual patients template recovers rejected and held imports with reload/pagehide ownership",async t=>{const rejected=await executePatientsBootstrap("throw new Error('rejected')",t);await settle();assert.equal(rejected.host.children.length,1);rejected.button.onclick();assert.equal(rejected.reloads(),1);const held=await executePatientsBootstrap("await new Promise(()=>{});export function mountDoctorPatients(){}",t);await settle();held.fire();assert.ok(held.slowCopy(),"the late copy shows beside the screen");assert.equal(held.host.children.length,0,"the screen is left alone");held.events.get("pagehide")();assert.equal(held.slowCopy(),null)});
test("late roster completion cannot repaint a remounted medico screen",async t=>{const old=deferred(),p=fixture([old.promise,Promise.resolve(response(200,{medico_id:"self",items:[]}))],t);p.run("medico");await settle();const now=p.run("medico");await now.ready;old.resolve(response(200,ROSTER));await settle();assert.match(p.host.innerHTML,/Todavía no hay pacientes/);assert.doesNotMatch(p.host.innerHTML,/Ana/)});
test("a malformed 200 roster (absent or wrong-type items) never renders the empty roster",async t=>{
  const bodies=[null, {}, { items: "Ana" }, { items: { length: 0 } }, [{ dni: "1" }]];
  const p=fixture(bodies.map(b=>Promise.resolve(response(200,b))),t);
  for (const body of bodies){
    await p.run("medico").ready;await settle();
    assert.match(p.host.innerHTML,/No se pudo cargar/,"malformed roster "+JSON.stringify(body)+" must reach the load-error line");
    assert.ok(p.host.innerHTML.includes("data-retry"),"malformed roster "+JSON.stringify(body)+" must offer retry");
    assert.doesNotMatch(p.host.innerHTML,/Todavía no hay pacientes/,"malformed roster "+JSON.stringify(body)+" must not pose as the successful empty roster");
  }
});
test("invalid JSON on a 200 roster reaches the load-error retry path",async t=>{
  const p=fixture([Promise.resolve({status:200,ok:true,json:()=>Promise.reject(new SyntaxError("Unexpected token"))})],t);
  await p.run("medico").ready;await settle();
  assert.match(p.host.innerHTML,/No se pudo cargar/);
  assert.doesNotMatch(p.host.innerHTML,/Todavía no hay pacientes/);
});
test("valid empty rosters stay successful states",async t=>{
  const p=fixture([Promise.resolve(response(200,{medico_id:"self",medico:"Dra Yo",items:[]})),Promise.resolve(response(200,{medico_id:null,medico:null,items:[],medicos:[{id:"one",nombre:"Dr Uno"}]}))],t);
  await p.run("medico").ready;await settle();
  assert.match(p.host.innerHTML,/Todavía no hay pacientes/);
  assert.doesNotMatch(p.host.innerHTML,/No se pudo cargar/);
  globalThis.location.search="";
  await p.run("admin").ready;await settle();
  assert.match(p.host.innerHTML,/Elegí un médico/);
  assert.doesNotMatch(p.host.innerHTML,/No se pudo cargar/);
});
test("a 401 the session check does not confirm reaches the load-error retry path, not an endless «Cargando…»",async t=>{
  const p=fixture([Promise.resolve(response(401,{error:"no_autenticado"}))],t);
  await p.run("medico").ready;await settle();
  assert.match(p.host.innerHTML,/No se pudo cargar/);
  assert.ok(p.host.innerHTML.includes("data-retry"));
});
test("a confirmed 401 leaves the roster to the redirect to login",async t=>{
  const p=fixture([Promise.resolve(response(401,{error:"no_autenticado"}))],t,"data:text/javascript,export const confirmar=()=>Promise.resolve(true)");
  await p.run("medico").ready;await settle();
  assert.doesNotMatch(p.host.innerHTML,/No se pudo cargar/);
  assert.equal(p.seen.length,1);
});
test("retry after a malformed 200 roster refetches instead of replaying the broken body",async t=>{
  const p=fixture([Promise.resolve(response(200,{})),Promise.resolve(response(200,ROSTER))],t);
  const retry=new Node();
  p.host.querySelector=selector=>selector==="[data-retry]"?retry:null;
  await p.run("medico").ready;await settle();
  assert.match(p.host.innerHTML,/No se pudo cargar/);
  retry.listeners.get("click")();
  await settle();
  assert.match(p.host.innerHTML,/Ana/);
  assert.equal(p.seen.length,2,"retry must hit the network again, not a replayed broken body");
});
// The server sends both dates already in the installation timezone
// (doctor_patients._build: astimezone(tz)). A doctor whose computer runs on
// another zone (a laptop left on UTC) must still read the hospital's 09:00,
// not the browser's 12:00.
test("roster dates keep the hospital's wall clock whatever the browser's timezone",async t=>{
  const tz=process.env.TZ;process.env.TZ="UTC";
  t.after(()=>{if(tz===undefined)delete process.env.TZ;else process.env.TZ=tz;});
  const roster={medico_id:"self",medico:"Dra Yo",items:[{nombre:"Ana",dni:"1",
    ultima_entrevista:{id:7,triage:"VERDE",fecha:"2026-03-02T09:15:00-03:00"},
    proximo_turno:{fecha:"2099-03-03T09:00:00-03:00"}}]};
  const p=fixture([Promise.resolve(response(200,roster))],t);
  window.SANA_STATIC["platform/dates.mjs"]=new URL("../static/platform/dates.mjs",import.meta.url).href;
  window.SANA_STATIC["platform/admin-table.mjs"]="data:text/javascript,"+encodeURIComponent(`
    export const crear=()=>({programarFit(){},dispose(){}});
    export const columnas=()=>({activas(){return[{key:'paciente',label:'Paciente'},{key:'entrevista',label:'Entrevista'},{key:'turno',label:'Turno'}]},gearHtml(){return''},dispose(){}});
  `);
  await p.run("medico").ready;await settle();
  assert.match(p.host.innerHTML,/2 de marzo( de 2026)?, 09:15/,"the last interview at 09:15 in Pinamar");
  assert.match(p.host.innerHTML,/3 de marzo de 2099, 09:00/,"the next appointment at 09:00 in Pinamar");
  assert.doesNotMatch(p.host.innerHTML,/12:15|12:00/,"never the browser's clock");
});

// Progressive disclosure (the product owner, 2026-10-26): a search that leaves nobody is the
// shared «ningún resultado» state with a way back — never a table header over
// one line of text; a doctor with no patients sees no search, gear or table.
function disclosureHost(p) {
  const attrs = {}, input = new Node("dp-q"), cont = new Node("dp-table"), picker = new Node("dp-medico");
  input.value = "";
  p.host.setAttribute = (k, v) => { attrs[k] = String(v); };
  p.host.removeAttribute = (k) => { delete attrs[k]; };
  p.host.querySelector = (sel) => ({ "#dp-q": input, "#dp-table": cont, "#dp-medico": p.withPicker ? picker : null })[sel] || null;
  return { attrs, input, cont };
}
test("DISCLOSURE: a search that finds nobody shows the shared no-match state, no header, and clearing it restores the rows", async t => {
  const roster = { medico_id: "self", medico: "Dra Yo", items: [{ nombre: "Ana", dni: "1" }, { nombre: "Beto", dni: "2" }] };
  const p = fixture([Promise.resolve(response(200, roster))], t);
  const { attrs, input, cont } = disclosureHost(p);
  await p.run("medico").ready; await settle();
  assert.match(p.host.innerHTML, /id="dp-q"/);
  assert.match(p.host.innerHTML, /<table[\s\S]*Ana/);
  assert.equal(attrs["data-disclosure"], "data");
  input.value = "zzz";
  input.listeners.get("input")();
  assert.match(cont.innerHTML, /data-empty-state="no-match"/);
  // The i18n double returns the fallback as is (no {q} interpolation).
  assert.match(cont.innerHTML, /Ningún paciente coincide con «\{q\}»\./);
  assert.match(cont.innerHTML, /<button[^>]*data-clear-filters[^>]*>Limpiar filtros<\/button>/);
  assert.doesNotMatch(cont.innerHTML, /<th|<table/, "no empty table header over the no-match line");
  assert.equal(attrs["data-disclosure"], "no-match");
  cont.listeners.get("click")({ target: { closest: (sel) => (sel === "[data-clear-filters]" ? {} : null) } });
  assert.equal(input.value, "");
  assert.match(cont.innerHTML, /<table[\s\S]*Ana[\s\S]*Beto/);
  assert.equal(attrs["data-disclosure"], "data");
});
test("DISCLOSURE: no patients → one empty state without search, gear or header; the doctor picker is never hidden", async t => {
  const p = fixture([Promise.resolve(response(200, { medico_id: "self", medico: "Dra Yo", items: [] })),
    Promise.resolve(response(200, { medico_id: "one", medico: "Dr Uno", items: [], medicos: [{ id: "one", nombre: "Dr Uno" }, { id: "two", nombre: "Dr Dos" }] }))], t);
  const { attrs } = disclosureHost(p);
  await p.run("medico").ready; await settle();
  assert.match(p.host.innerHTML, /data-empty-state="empty"[\s\S]*Todavía no hay pacientes referidos a este médico\./);
  assert.doesNotMatch(p.host.innerHTML, /id="dp-q"|<table|<th/);
  assert.equal(attrs["data-disclosure"], "empty");
  p.withPicker = true;
  await p.run("admin").ready; await settle();
  assert.match(p.host.innerHTML, /id="dp-medico"/, "the required doctor picker stays");
  assert.doesNotMatch(p.host.innerHTML, /id="dp-q"|<table/);
  assert.equal(attrs["data-disclosure"], undefined, "the picker's band is not hidden by the shell's empty rule");
});

// Responsable del producto 2026-10-27: «triage, en curso» en la lista de un médico que no tiene la
// entrevista previa encendida. Triage, Última entrevista y Resumen sólo son
// columnas (y no entran al engranaje) cuando la respuesta trae entrevista_activa.
async function columnKeysFor(t, roster, role = "medico") {
  const p = fixture([Promise.resolve(response(200, roster))], t);
  window.SANA_STATIC["platform/admin-table.mjs"] = "data:text/javascript," + encodeURIComponent(`
    export const crear=()=>({programarFit(){},dispose(){}});
    export const columnas=cfg=>{globalThis.__dpCols=cfg.cols.map(c=>c.key);return{activas(){return cfg.cols.filter(c=>c.always||c.default)},gearHtml(){return""},dispose(){}}};
  `);
  t.after(() => { delete globalThis.__dpCols; });
  await p.run(role).ready; await settle();
  return globalThis.__dpCols;
}
const ENTREVISTA_KEYS = ["triage", "entrevista", "resumen"];
test("without entrevista_activa the roster has no triage, interview or summary column", async t => {
  const keys = await columnKeysFor(t, { medico_id: "self", medico: "Dra Yo", entrevista_activa: false, items: [{ nombre: "Ana", dni: "1", ultima_entrevista: null }] });
  assert.deepEqual(ENTREVISTA_KEYS.filter(k => keys.includes(k)), []);
  assert.ok(keys.includes("paciente") && keys.includes("turno"));
});
test("a roster that does not say entrevista_activa is treated as off", async t => {
  const keys = await columnKeysFor(t, { medico_id: "self", medico: "Dra Yo", items: [{ nombre: "Ana", dni: "1" }] });
  assert.deepEqual(ENTREVISTA_KEYS.filter(k => keys.includes(k)), []);
});
test("with entrevista_activa the roster keeps the triage, interview and summary columns", async t => {
  const keys = await columnKeysFor(t, { medico_id: "self", medico: "Dra Yo", entrevista_activa: true, items: [{ nombre: "Ana", dni: "1" }] });
  assert.deepEqual(ENTREVISTA_KEYS.filter(k => keys.includes(k)), ENTREVISTA_KEYS);
});
