import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { mount } from "../static/platform/admin-preparations.mjs";
import { t as i18nT, setCatalog as i18nSetCatalog } from "../static/platform/i18n.mjs";

const tick = () => new Promise((r) => setImmediate(r));
async function drain(n = 10) { while (n--) await tick(); }
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const response = (status, body) => ({ status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) });
const data = (src) => "data:text/javascript," + encodeURIComponent(src);
const PREP = "export const SECCIONES=[['antes','Antes de venir'],['traer','Qué llevar'],['avisar','Avisá'],['no_hace_falta','No hace falta'],['despues','Después']]";
const I18N = "export const t=(k,v,f)=>globalThis.__prepT&&globalThis.__prepT[k]||f||k";
const SESSION = "export const confirmar=(r,b,current)=>{globalThis.__prepConfirm.push([r.status,b,current()]);return Promise.resolve(false)}";
const TABLE = "export const crear=()=>({programarFit(){globalThis.__prepFits++},dispose(){globalThis.__prepDisposes++}})";
function attrs(raw) { const out = {}; for (const m of String(raw).matchAll(/([\w:-]+)(?:=\"([^\"]*)\")?/g)) out[m[1]] = m[2] ?? ""; return out; }
// The controller escapes text, but a browser exposes decoded control values.
// This fixture remains a flat tag scanner, not a layout/browser substitute.
function controlText(raw) {
  return String(raw).replace(/&(amp|lt|gt|quot|#39);/g,
    (_, entity) => ({amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'"})[entity]);
}
class El {
  constructor(tag = "div", at = {}) { this.tagName = tag.toUpperCase(); this.attrs = at; this.id = at.id || ""; this.className = at.class || ""; this.dataset = {}; Object.entries(at).forEach(([k,v]) => { if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v; }); this.children = []; this.listeners = new Map(); this.isConnected = true; this.style = {}; this.value = at.value || ""; this.checked = Object.hasOwn(at, "checked"); this._html = ""; }
  set innerHTML(v) {
    this._html = String(v); this.children = [];
    for (const m of this._html.matchAll(/<([\w-]+)\b([^>]*)>/g)) {
      const e = new El(m[1], attrs(m[2]));
      e.value = controlText(e.value);
      if (e.tagName === "TEXTAREA") {
        const start = m.index + m[0].length;
        const end = this._html.indexOf("</textarea>", start);
        e.value = controlText(this._html.slice(start, end === -1 ? undefined : end));
      }
      e.parentNode = this; this.children.push(e);
    }
  }
  get innerHTML() { return this._html; }
  addEventListener(k, fn) { this.listeners.set(k, fn); } removeEventListener(k) { this.listeners.delete(k); }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  querySelectorAll(s) { const out = []; const match = (e) => s[0] === "#" ? e.id === s.slice(1) : s[0] === "." ? e.className.split(/\s+/).includes(s.slice(1)) : s.startsWith("a[") ? e.tagName === "A" && e.attrs.href && e.attrs.href.startsWith("/admin/preparations") : e.tagName === s.toUpperCase(); const walk = (x) => x.children.forEach((c) => { if (match(c)) out.push(c); walk(c); }); walk(this); return out; }
  closest(s) { return s === 'a[href^="/admin/preparations"]' && this.tagName === "A" && this.attrs.href?.startsWith("/admin/preparations") ? this : null; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  appendChild(e) { e.parentNode = this; this.children.push(e); return e; }
  removeChild(e) { this.children = this.children.filter((x) => x !== e); e.parentNode = null; }
}
function fixture(t, routes, search = "", manifest = null) {
  const host = new El("div", { id: "sec-preparaciones" }), head = new El("head"), styles = new Map(), calls = [], pushed = [];
  const doc = { head, getElementById(id) { return styles.get(id) || (id === host.id ? host : null); }, createElement(tag) { const e = new El(tag); return e; } };
  const old = ["window","document","location","history","fetch","__prepConfirm","__prepFits","__prepDisposes","__prepT"].map((k) => [k,Object.getOwnPropertyDescriptor(globalThis,k)]);
  globalThis.__prepConfirm=[]; globalThis.__prepFits=0; globalThis.__prepDisposes=0; globalThis.document=doc;
  globalThis.location={search, pathname:"/admin/preparations"}; globalThis.history={pushState(...x){pushed.push(x)}};
  const winListeners=new Map(); globalThis.window={document:doc,location:globalThis.location,history:globalThis.history,addEventListener(k,fn){winListeners.set(k,fn)},removeEventListener(k){winListeners.delete(k)},SANA_STATIC:manifest || {"platform/prep.mjs":data(PREP),"platform/i18n.mjs":data(I18N),"platform/admin-session.mjs":data(SESSION),"platform/admin-table.mjs":data(TABLE)}};
  globalThis.fetch=(url, options={}) => { calls.push({url,options,method:options.method||"GET"}); return routes(url, options.method||"GET", calls); };
  t.after(()=>old.forEach(([k,d])=>d?Object.defineProperty(globalThis,k,d):delete globalThis[k]));
  return {host,head,calls,pushed,winListeners, run:(options)=>mount(host,options), fire(el,type="click"){ (el.listeners.get(type) || host.listeners.get(type))({target:el,button:0,preventDefault(){}}); }};
}
const item = {id:"eco",nombre:"Ecografía",resumen:"Simple",match:["eco"],ayuno:{required:true,horas:8,agua_permitida:true},antes:["Traer orden"],fuentes:["hospital"],sin_preparacion:false};

test("shared title is the only heading and restores its prior state on disposal", async t => {
  const f = fixture(t,()=>Promise.resolve(response(200,{items:[item]})));
  const title = new El("h1"); title.textContent="Shared title";title.className="adm-h1";
  const d = f.run({titleElement:title}); await d.ready;
  assert.equal(title.textContent,"Cómo prepararse");
  assert.equal(f.host.querySelectorAll("h1").length,0);
  f.fire(f.host.querySelector("#pa-nueva"));
  assert.equal(title.textContent,"Nueva preparación");
  d(); assert.equal(title.textContent,"Shared title");assert.equal(title.className,"adm-h1");
});

test("late read and retired disposal cannot change a newer title owner", async t => {
  const oldRead=deferred();let gets=0;
  const f=fixture(t,()=>++gets===1?oldRead.promise:Promise.resolve(response(200,{items:[item]})),"?item=eco");
  const title=new El("h1");title.textContent="Shared title";title.className="adm-h1";
  const old=f.run({titleElement:title});await drain();
  const current=f.run({titleElement:title});await current.ready;
  assert.equal(title.textContent,item.nombre);
  oldRead.resolve(response(200,{items:[{...item,nombre:"Stale title"}]}));await old.ready;
  old();assert.equal(title.textContent,item.nombre);
  current();assert.equal(title.textContent,"Shared title");
});

test("disposal does not overwrite a shared title changed by another surface", async t => {
  const f=fixture(t,()=>Promise.resolve(response(200,{items:[item]})));
  const title=new El("h1");title.textContent="Shared title";title.className="adm-h1";
  const d=f.run({titleElement:title});await d.ready;
  title.textContent="New surface title";d();
  assert.equal(title.textContent,"New surface title");
});

test("an independent host takes over the shared title and a retired owner cannot restore over it", async t => {
  const rayos = { ...item, id: "rayos", nombre: "Radiografías" };
  const f = fixture(t, () => Promise.resolve(response(200, { items: [item, rayos] })), "?item=eco");
  const title = new El("h1"); title.textContent = "Shared title"; title.className = "adm-h1";
  const otherHost = new El("div", { id: "sec-preparaciones-otra" });
  const first = f.run({ titleElement: title }); await first.ready;
  assert.equal(title.textContent, item.nombre);
  globalThis.location.search = "?item=rayos";
  const second = mount(otherHost, { titleElement: title }); await second.ready;
  assert.equal(title.textContent, rayos.nombre);
  first(); assert.equal(title.textContent, rayos.nombre, "obsolete disposer cannot restore over the newer owner");
  second(); assert.equal(title.textContent, "Shared title");
});

test("ownership appends only its own class and restores the exact original class list", async t => {
  const f = fixture(t, () => Promise.resolve(response(200, { items: [item] })));
  const title = new El("h1"); title.textContent = "Shared title"; title.className = "adm-h1 extra-marker";
  const d = f.run({ titleElement: title }); await d.ready;
  assert.equal(title.className, "adm-h1 extra-marker cnt-h1");
  d();
  assert.equal(title.className, "adm-h1 extra-marker");
});

test("a delayed independent host cannot reclaim a title reserved by a newer mount", async t => {
  const held = deferred(); let reads = 0;
  const f = fixture(t, () => ++reads === 1 ? held.promise : Promise.resolve(response(200, {items:[item]})));
  const title = new El("h1"); title.textContent = "Shared title"; title.className = "adm-h1";
  const first = f.run({titleElement:title}); await drain();
  globalThis.location.search = "?item=eco";
  const second = mount(new El("div"), {titleElement:title}); await second.ready;
  assert.equal(title.textContent, item.nombre);
  held.resolve(response(200, {items:[item]})); await first.ready;
  assert.equal(title.textContent, item.nombre, "late older render must not steal newer title");
  first(); assert.equal(title.textContent, item.nombre);
  second(); assert.equal(title.textContent, "Shared title");
});

test("title updates and disposal preserve classes added by another component", async t => {
  const f = fixture(t, () => Promise.resolve(response(200, {items:[item]})));
  const title = new El("h1"); title.textContent = "Shared title"; title.className = "adm-h1";
  const d = f.run({titleElement:title}); await d.ready;
  title.className += " external-marker";
  f.fire(f.host.querySelector("#pa-nueva"));
  assert.match(title.className, /\bexternal-marker\b/);
  d(); assert.equal(title.textContent, "Shared title");
  assert.equal(title.className, "adm-h1 external-marker");
});

test("direct query and popstate own the editor despite a competing legacy fragment", async t => {
  const other = {...item, id:"other", nombre:"Other preparation"};
  const f = fixture(t, () => Promise.resolve(response(200,{items:[item,other]})), "?item=eco");
  globalThis.location.hash = "#preparaciones?item=other";
  const d = f.run(); await d.ready;
  assert.equal(f.host.querySelector("#pa-nombre").value, item.nombre);
  globalThis.location.search = "?item=other";
  globalThis.location.hash = "#preparaciones?item=eco";
  f.winListeners.get("popstate")(); await drain();
  assert.equal(f.host.querySelector("#pa-nombre").value, other.nombre);
  assert.equal(f.pushed.length,0); d();
});

test("successful but delayed preparation import prevents reads and rendering until ready", async t => {
  const held = deferred(); globalThis.__prepBootGate = held.promise;
  t.after(() => { held.resolve(); delete globalThis.__prepBootGate; });
  const f = fixture(t, () => Promise.resolve(response(200,{items:[item]})));
  window.SANA_STATIC["platform/prep.mjs"] = data("await globalThis.__prepBootGate;\n" + PREP);
  const d = f.run(); let ready = false; d.ready.then(() => { ready = true; });
  await drain();
  assert.equal(ready,false); assert.equal(f.calls.length,0);
  assert.equal(f.host.innerHTML, ""); assert.equal(f.head.children.length,0);
  held.resolve(); await d.ready;
  assert.equal(f.calls.length,1); assert.ok(f.host.querySelector("#pa-nueva")); d();
});

test("empty catalog opens an editable creation form and the next save updates its acknowledged identity", async t => {
  const items = [];
  const f = fixture(t, (_url,method) => {
    if (method === "GET") return Promise.resolve(response(200,{items}));
    if (method === "POST") items.push({...item});
    return Promise.resolve(response(200,{ok:true,item}));
  });
  const d = f.run(); await d.ready;
  assert.equal(f.host.querySelector("#pa-nueva"),null);
  assert.equal(f.host.querySelector("#pa-nombre").value,"");
  f.host.querySelector("#pa-nombre").value = item.nombre;
  f.fire(f.host.querySelector("#pa-guardar")); await drain();
  assert.equal(f.pushed.at(-1)[2],"/admin/preparations?item=eco");
  f.fire(f.host.querySelector("#pa-guardar")); await drain();
  assert.deepEqual(f.calls.filter(c=>c.method!=="GET").map(c=>[c.method,c.url]),
    [["POST","/api/admin/content/preparations"],["PUT","/api/admin/content/preparations/eco"]]);
  d();
});

test("failed post-save refresh preserves the editor and allows another save", async t => {
  let gets = 0, puts = 0;
  const f = fixture(t, (_url, method) => {
    if (method === "PUT") { puts++; return Promise.resolve(response(200, {ok:true,item})); }
    gets++;
    return Promise.resolve(gets === 2 ? response(500,{}) : response(200,{items:[item]}));
  }, "?item=eco");
  const dispose = f.run(); await dispose.ready;
  f.host.querySelector("#pa-nombre").value = "Preserve saved text";
  f.fire(f.host.querySelector("#pa-guardar")); await drain();
  assert.equal(f.host.querySelector("#pa-nombre")?.value, "Preserve saved text");
  assert.match(f.host.querySelector("#pa-msg").textContent, /No se pudo cargar/);
  f.fire(f.host.querySelector("#pa-guardar")); await drain();
  assert.equal(puts,2);
  assert.ok(f.host.querySelector("#pa-guardar"));
  dispose();
});

test("initial read failure offers an owned retry that becomes inert after disposal", async t => {
  let gets = 0;
  const f = fixture(t, () => Promise.resolve(++gets === 1
    ? response(503, {error: "temporarily_unavailable"}) : response(200, {items: [item]})));
  const d = f.run(); await d.ready;
  const retry = f.host.querySelector("#pa-retry");
  assert.ok(retry, "data errors need their own recovery, not only import errors");
  const callback = retry.listeners.get("click");
  f.fire(retry); await drain();
  assert.ok(f.host.querySelector("#pa-nueva")); assert.equal(gets, 2);
  callback(); await drain(); assert.equal(gets, 2, "removed recovery control has no authority");
  d(); callback(); await drain(); assert.equal(gets, 2);
});

test("the actual editor preserves explicit fasting-note label and action semantics", async t => {
  const f = fixture(t, () => Promise.resolve(response(200, {items: [{...item, _origen: "json+db"}]})), "?item=eco");
  const d = f.run(); await d.ready;
  const labels = f.host.querySelectorAll("label");
  assert.ok(labels.some(el => el.getAttribute("for") === "pa-ay-nota"));
  assert.equal(f.host.querySelector("#pa-ay-horas").getAttribute("inputmode"), "numeric");
  for (const id of ["pa-guardar", "pa-restaurar", "pa-deshabilitar"])
    assert.equal(f.host.querySelector("#" + id).getAttribute("type"), "button");
  assert.equal(f.host.querySelector("#pa-msg").getAttribute("aria-live"), "polite");
  d();
});

test("machine mutation error codes become readable while validation sentences remain intact", async t => {
  let error = "no_autenticado";
  const f = fixture(t, (u,m) => Promise.resolve(m === "GET" ? response(200,{items:[item]}) : response(400,{error})), "?item=eco");
  const d = f.run(); await d.ready;
  f.fire(f.host.querySelector("#pa-guardar")); await drain();
  assert.equal(f.host.querySelector("#pa-msg").textContent, "No se pudo completar la acción.");
  error = "El nombre es obligatorio.";
  f.fire(f.host.querySelector("#pa-guardar")); await drain();
  assert.equal(f.host.querySelector("#pa-msg").textContent, error);
  d();
});

test("missing dependency rejects readiness and notifies the current loader once", async t => {
  const f = fixture(t, () => { throw new Error("must not fetch"); }, "", {});
  const errors = []; const d = f.run({onError: e => errors.push(e)});
  await assert.rejects(d.ready, /Missing Preparations dependency/);
  assert.equal(errors.length, 1); assert.equal(f.calls.length, 0);
  assert.equal(f.host.listeners.size, 0); assert.equal(f.winListeners.size, 0);
  d();
});
test("a late dependency failure cannot notify or repaint a disposed owner", async t => {
  const f = fixture(t, () => { throw new Error("must not fetch"); }, "", {});
  const errors = []; const d = f.run({onError: e => errors.push(e)});
  d(); f.host.innerHTML = "replacement owner";
  await assert.rejects(d.ready, /Missing Preparations dependency/);
  assert.equal(errors.length, 0); assert.equal(f.host.innerHTML, "replacement owner");
});
test("initial render failure releases styles and listeners before rejecting readiness", async t => {
  const manifest = {"platform/prep.mjs": data(PREP), "platform/i18n.mjs": data(I18N),
    "platform/admin-session.mjs": data(SESSION),
    "platform/admin-table.mjs": data('export function crear(){throw new Error("table boot failed")}')};
  const f = fixture(t, () => Promise.resolve(response(200,{items:[item]})), "", manifest);
  const errors = []; const d = f.run({onError: e => errors.push(e)});
  await assert.rejects(d.ready, /table boot failed/);
  assert.equal(errors.length,1); assert.equal(f.head.children.length,0);
  assert.equal(f.host.listeners.size,0); assert.equal(f.winListeners.size,0);
  d();
});

test("fixture control values decode escaped content without parsing it as markup", () => {
  const host = new El();
  host.innerHTML = '<input id="name" value="A &amp; B &quot;C&quot;"><textarea id="body">&lt;script&gt; &amp;lt; &quot;x&quot; &#39;y&#39;\nsecond line</textarea>';
  assert.equal(host.querySelector("#name").value, 'A & B "C"');
  assert.equal(host.querySelector("#body").value, '<script> &lt; "x" \'y\'\nsecond line');
  assert.equal(host.querySelector("script"), null);
});

test("mount imports real dependency contracts, renders the list, and disposes its table", async (t) => {
  const f=fixture(t,()=>Promise.resolve(response(200,{items:[item]}))); const dispose=f.run(); await dispose.ready;
  assert.match(f.host.innerHTML,/Ecografía/); assert.match(f.host.innerHTML,/8 h/); assert.equal(globalThis.__prepFits,1); dispose(); assert.equal(globalThis.__prepDisposes,1);
});
test("editor save preserves unknown fields, converts blank hours to null, and removes preparacion", async (t) => {
  const f=fixture(t,(url,m)=> { if(m==="GET") return Promise.resolve(response(200,{items:[{...item,preparacion:"old"}]})); return Promise.resolve(response(200,{ok:true,item:{...item}})); },"?item=eco");
  const dispose=f.run(); await dispose.ready; f.host.querySelector("#pa-ay-horas").value=""; f.fire(f.host.querySelector("#pa-guardar")); await drain();
  const post=f.calls.find(x=>x.method==="PUT"); const body=JSON.parse(post.options.body); assert.deepEqual(body.fuentes,["hospital"]); assert.equal(body.sin_preparacion,false); assert.equal(body.ayuno.horas,null); assert.equal("preparacion" in body,false); dispose();
});
test("delete only accepts its distinct acknowledgement contract", async (t) => {
  const f=fixture(t,(url,m)=>Promise.resolve(response(200,m==="GET"?{items:[item]}:{ok:true,item:item})),"?item=eco"); const d=f.run(); await d.ready; f.fire(f.host.querySelector("#pa-deshabilitar")); await drain(); assert.match(f.host.querySelector("#pa-msg").textContent||"",/No se pudo/); d();
});
test("stale read cannot replace a newer route", async (t) => {
  const first=deferred(); let n=0; const f=fixture(t,()=>++n===1?first.promise:Promise.resolve(response(200,{items:[item]}))); const d=f.run(); await drain(); globalThis.location.search="?item=eco"; f.winListeners.get("popstate")({}); await drain(); first.resolve(response(200,{items:[]})); await drain(); assert.match(f.host.innerHTML,/Ecografía/); d();
});
test("ordinary 403 is local and session confirmation receives a current predicate", async (t) => {
  const f=fixture(t,()=>Promise.resolve(response(403,{error:"solo_admin"}))); const d=f.run(); await d.ready; assert.deepEqual(globalThis.__prepConfirm[0].slice(0,2),[403,{error:"solo_admin"}]); assert.match(f.host.innerHTML,/No se pudo cargar/); d();
});
test("the actual admin-session module leaves an ordinary 403 local", async (t) => {
  const manifest={"platform/prep.mjs":data(PREP),"platform/i18n.mjs":data(I18N),"platform/admin-session.mjs":new URL("../static/platform/admin-session.mjs",import.meta.url).href,"platform/admin-table.mjs":data(TABLE)};
  const f=fixture(t,()=>Promise.resolve(response(403,{error:"solo_admin"})),"",manifest); const d=f.run(); await d.ready;
  assert.match(f.host.innerHTML,/No se pudo cargar/); assert.equal(globalThis.location.href,undefined); d();
});
test("Add opens a new editor even when the catalog already has items", async (t) => {
  const f=fixture(t,()=>Promise.resolve(response(200,{items:[item]}))); const d=f.run(); await d.ready;
  f.fire(f.host.querySelector("#pa-nueva")); assert.match(f.host.innerHTML,/Nueva preparación/); assert.ok(f.host.querySelector("#pa-guardar")); d();
});
test("an unknown routed item is actionable instead of becoming a blank editor", async (t) => {
  const f=fixture(t,()=>Promise.resolve(response(200,{items:[item]})),"?item=missing"); const d=f.run(); await d.ready;
  assert.match(f.host.innerHTML,/No se encontró esta preparación/); assert.equal(f.host.querySelector("#pa-guardar"),null); d();
});
test("typing after Save starts keeps the current draft when the acknowledgement arrives", async (t) => {
  const save=deferred(); const f=fixture(t,(url,m)=>m==="GET"?Promise.resolve(response(200,{items:[item]})):save.promise,"?item=eco"); const d=f.run(); await d.ready;
  const name=f.host.querySelector("#pa-nombre"); f.fire(f.host.querySelector("#pa-guardar")); name.value="Edited after save"; f.fire(name,"input"); save.resolve(response(200,{ok:true,item:{...item,nombre:"server"}})); await drain();
  assert.equal(f.host.querySelector("#pa-nombre").value,"Edited after save"); assert.equal(f.calls.filter(x=>x.method==="GET").length,1); d();
});
test("restore and disable retain their explicit confirmation boundary", async (t) => {
  const edited={...item,_origen:"json+db"}; const f=fixture(t,(url,m)=>Promise.resolve(response(200,m==="GET"?{items:[edited]}:{ok:true,item:edited})),"?item=eco"); const d=f.run(); await d.ready;
  let asks=0; globalThis.window.confirm=()=>{asks+=1;return false;}; f.fire(f.host.querySelector("#pa-restaurar")); f.fire(f.host.querySelector("#pa-deshabilitar")); await drain();
  assert.equal(asks,2); assert.equal(f.calls.filter(x=>x.method!=="GET").length,0); d();
});
test("typing while the acknowledged save refresh is pending cannot be erased", async (t) => {
  const refresh=deferred(); let reads=0;
  const f=fixture(t,(url,m)=>m==="GET" ? (++reads===1 ? Promise.resolve(response(200,{items:[item]})) : refresh.promise) : Promise.resolve(response(200,{ok:true,item})),"?item=eco");
  const d=f.run(); await d.ready; f.fire(f.host.querySelector("#pa-guardar")); await drain();
  assert.equal(reads,2,"actual post-save refresh has started");
  const name=f.host.querySelector("#pa-nombre"); name.value="Newer unsaved draft"; f.fire(name,"input");
  refresh.resolve(response(200,{items:[{...item,nombre:"Saved older value"}]})); await drain();
  assert.equal(f.host.querySelector("#pa-nombre").value,"Newer unsaved draft"); d();
});
test("a failed post-save read cannot replace a newer draft with an error page", async (t) => {
  const refresh=deferred(); let reads=0;
  const f=fixture(t,(url,m)=>m==="GET" ? (++reads===1 ? Promise.resolve(response(200,{items:[item]})) : refresh.promise) : Promise.resolve(response(200,{ok:true,item})),"?item=eco");
  const d=f.run(); await d.ready; f.fire(f.host.querySelector("#pa-guardar")); await drain();
  const name=f.host.querySelector("#pa-nombre"); name.value="Keep draft despite read failure"; f.fire(name,"input");
  refresh.reject(new Error("offline")); await drain();
  assert.equal(f.host.querySelector("#pa-nombre")?.value,"Keep draft despite read failure"); d();
});
test("a route transition withdraws the previous item's mutation controls immediately", async (t) => {
  const next=deferred(); let reads=0;
  const f=fixture(t,()=>++reads===1 ? Promise.resolve(response(200,{items:[item]})) : next.promise,"?item=eco");
  const d=f.run(); await d.ready; globalThis.location.search="?item=other"; f.winListeners.get("popstate")({});
  assert.equal(f.host.querySelector("#pa-guardar"),null,"old item must not remain editable under the new URL");
  next.resolve(response(200,{items:[{...item,id:"other",nombre:"Other"}]})); await drain();
  assert.equal(f.host.querySelector("#pa-nombre").value,"Other"); d();
});
for (const typing of [false,true]) test(`create establishes persisted identity with mid-flight typing=${typing}`, async (t) => {
  const saved=deferred(); const created={...item,id:"new-prep"};
  const f=fixture(t,(url,m)=>m==="GET" ? Promise.resolve(response(200,{items:[created]})) : m==="POST" ? saved.promise : Promise.resolve(response(200,{ok:true,item:created})));
  const d=f.run(); await d.ready; f.fire(f.host.querySelector("#pa-nueva"));
  f.host.querySelector("#pa-nombre").value="First name"; f.fire(f.host.querySelector("#pa-guardar"));
  if(typing){ const name=f.host.querySelector("#pa-nombre"); name.value="Unsaved new name"; f.fire(name,"input"); }
  saved.resolve(response(200,{ok:true,item:created})); await drain();
  if(typing) assert.equal(f.host.querySelector("#pa-nombre").value,"Unsaved new name");
  assert.notEqual(f.host.querySelector("#pa-msg").textContent,"Guardando…");
  f.fire(f.host.querySelector("#pa-guardar")); await drain();
  const writes=f.calls.filter(c=>c.method!=="GET"); assert.equal(writes.length,2); assert.equal(writes[1].method,"PUT"); assert.equal(writes[1].url,"/api/admin/content/preparations/new-prep"); d();
});
for(const button of ["pa-restaurar","pa-habilitar","pa-deshabilitar"]) test(`${button} cannot overwrite edits typed during its request`,async(t)=>{
  const pending=deferred(); const original={...item,_origen:"json+db",_borrado:button==="pa-habilitar"};
  const f=fixture(t,(url,m)=>m==="GET" ? Promise.resolve(response(200,{items:[original]})) : pending.promise,"?item=eco");
  const d=f.run(); await d.ready; globalThis.window.confirm=()=>true; f.fire(f.host.querySelector("#"+button));
  const name=f.host.querySelector("#pa-nombre"); name.value="Draft while action pending"; f.fire(name,"input");
  pending.resolve(response(200,button==="pa-deshabilitar"?{ok:true,turnos:0,conservado:false}:{ok:true,item:original})); await drain();
  assert.equal(f.host.querySelector("#pa-nombre").value,"Draft while action pending"); d();
});
test("leaving the list disposes its table before editing",async(t)=>{
  const f=fixture(t,()=>Promise.resolve(response(200,{items:[item]}))); const d=f.run(); await d.ready;
  f.fire(f.host.querySelector("#pa-nueva")); assert.equal(globalThis.__prepDisposes,1); d(); assert.equal(globalThis.__prepDisposes,1);
});
test("foreign catalog links do not enter preparation routing",async(t)=>{
  const f=fixture(t,()=>Promise.resolve(response(200,{items:[item]}))); const d=f.run(); await d.ready;
  f.fire(new El("a",{href:"/admin/services"})); await drain(); assert.equal(f.calls.length,1); assert.equal(f.pushed.length,0); d();
});

/* ---- localization parity: labels resolve through the REAL i18n module ----
 * The manifest maps platform/i18n.mjs to the real static/platform/i18n.mjs
 * file URL, so the controller's dynamic import gets the SAME module instance
 * these tests drive through setCatalog() — no fake t(), and the catalog is
 * supplied before mount so every render already sits past real I18n readiness. */
const REAL_I18N = new URL("../static/platform/i18n.mjs", import.meta.url).href;
// Canonical values copied from locales/{en,es}/admin.content.json — no invented keys.
const EN = {
  "admin.content.preparations.title": "How to prepare",
  "admin.content.preparations.add_button": "Add a preparation",
  "admin.content.preparations.col_name": "Preparation",
  "admin.content.preparations.col_summary": "In one line",
  "admin.content.preparations.col_match": "Matched by",
  "admin.content.preparations.col_ayuno": "Fasting",
  "admin.content.preparations.field_name_label": "Name",
  "admin.content.preparations.field_summary_label": "In one line",
  "admin.content.preparations.field_match_label": "Matched by",
  "admin.content.preparations.field_match_hint": "One word per line. If any of these appear in the service name or in the order the patient uploads, this preparation is shown. No accents, lowercase: a scanned order loses them.",
  "admin.content.preparations.ayuno_label": "Fasting",
  "admin.content.preparations.ayuno_required": "Done fasting",
  "admin.content.preparations.ayuno_hours_label": "Hours ",
  "admin.content.preparations.ayuno_water": "Water is allowed",
  "admin.content.preparations.save_button": "Save",
  "admin.content.preparations.restore_link": "Restore the original",
  "admin.content.preparations.enable_link": "Enable",
  "admin.content.preparations.disable_button": "Disable",
  "admin.content.preparations.status_edited": "Edited",
  "admin.content.preparations.status_new": "New",
  "admin.content.preparations.status_disabled": "Disabled",
  "admin.content.preparations.new_item_title": "New preparation",
  "admin.content.preparations.help_antes": "What to do before leaving home.",
  "admin.content.preparations.help_traer": "What to wear or bring along.",
  "admin.content.preparations.help_avisar": "What the patient needs to mention on arrival: allergies, pregnancy, medication.",
  "admin.content.preparations.help_no_hace_falta": "Myths and unnecessary precautions. Clearing these up is useful information, not filler.",
  "admin.content.preparations.help_despues": "What to expect afterward and what's normal to feel."
};
const ES = {
  "admin.content.preparations.title": "Cómo prepararse",
  "admin.content.preparations.add_button": "Agregar una preparación",
  "admin.content.preparations.col_name": "Preparación",
  "admin.content.preparations.col_summary": "En una línea",
  "admin.content.preparations.col_match": "Se reconoce por",
  "admin.content.preparations.col_ayuno": "Ayuno",
  "admin.content.preparations.field_name_label": "Nombre",
  "admin.content.preparations.field_summary_label": "En una línea",
  "admin.content.preparations.field_match_label": "Se reconoce por",
  "admin.content.preparations.field_match_hint": "Una palabra por línea. Con cualquiera de éstas en el nombre del servicio o en la orden que sube la persona, se le muestra esta preparación. Sin tildes y en minúscula: una orden escaneada las pierde.",
  "admin.content.preparations.ayuno_label": "Ayuno",
  "admin.content.preparations.ayuno_required": "Se hace en ayunas",
  "admin.content.preparations.ayuno_hours_label": "Horas ",
  "admin.content.preparations.ayuno_water": "Puede tomar agua",
  "admin.content.preparations.save_button": "Guardar",
  "admin.content.preparations.restore_link": "Restaurar el original",
  "admin.content.preparations.enable_link": "Habilitar",
  "admin.content.preparations.disable_button": "Deshabilitar",
  "admin.content.preparations.status_edited": "Editada",
  "admin.content.preparations.status_new": "Nueva",
  "admin.content.preparations.status_disabled": "Deshabilitada",
  "admin.content.preparations.new_item_title": "Nueva preparación",
  "admin.content.preparations.help_antes": "Lo que tiene que hacer antes de salir de casa.",
  "admin.content.preparations.help_traer": "Lo que se lleva puesto o en la mano.",
  "admin.content.preparations.help_avisar": "Lo que la persona tiene que contar cuando llega: alergias, embarazo, medicación.",
  "admin.content.preparations.help_no_hace_falta": "Mitos y precauciones de más. Sacárselas de encima es información, no relleno.",
  "admin.content.preparations.help_despues": "Qué esperar al salir y qué es normal sentir."
};
test("localization fixtures exactly match the shipped catalogs", () => {
  for (const [lang, fixtureCatalog] of [["en", EN], ["es", ES]]) {
    const catalog = JSON.parse(readFileSync(new URL(`../locales/${lang}/admin.content.json`, import.meta.url), "utf8"));
    for (const [key, value] of Object.entries(fixtureCatalog)) {
      assert.equal(catalog[key], value, `${lang}:${key}`);
    }
  }
});
// Stands in for prep.mjs already resolved in English: SECCIONES[i][0] is the
// wire key, [i][1] the translated label the controller must render as-is.
const PREP_EN = "export const SECCIONES=[['antes','Before you come'],['traer','What to bring'],['avisar','Tell us when you arrive if'],['no_hace_falta','Not needed'],['despues','Afterward']]";
const TABLE_COLS = "export const crear=(cfg)=>{globalThis.__prepColsSeen.push(cfg.cols());return{programarFit(){},dispose(){}}}";
const l10n = (prep = PREP, table = TABLE) => ({ "platform/prep.mjs": data(prep), "platform/i18n.mjs": REAL_I18N, "platform/admin-session.mjs": data(SESSION), "platform/admin-table.mjs": data(table) });

test("the supplied translated catalog is live in the real i18n module", () => {
  // Positive control: proves the catalog actually translates known keys
  // through the same instance the controller imports, so EN assertions below
  // cannot pass on Spanish fallbacks.
  i18nSetCatalog(EN);
  assert.equal(i18nT("admin.content.preparations.save_button", null, "Guardar"), "Save");
  assert.equal(i18nT("admin.content.preparations.col_ayuno", null, "Ayuno"), "Fasting");
  assert.equal(i18nT("admin.content.preparations.status_new", null, "Nueva"), "New");
});
test("list headings, badges, fasting cells and table-engine column labels follow the supplied EN catalog", async (t) => {
  i18nSetCatalog(EN); globalThis.__prepColsSeen = [];
  t.after(() => { delete globalThis.__prepColsSeen; });
  const items = [
    item,
    { ...item, id: "creada", nombre: "Creada en db", _origen: "db", ayuno: { required: true } },
    { ...item, id: "tocada", nombre: "Tocada del JSON", _origen: "json+db", ayuno: {} },
    { ...item, id: "apagada", nombre: "Apagada", _borrado: true, ayuno: {} }
  ];
  const f = fixture(t, () => Promise.resolve(response(200, { items })), "", l10n(PREP, TABLE_COLS));
  const d = f.run(); await d.ready;
  const html = f.host.innerHTML;
  assert.match(html, /<h1 class="cnt-h1">How to prepare<\/h1>/);
  assert.match(html, /id="pa-nueva">New preparation</);
  assert.match(html, /<th class="cnt-th">Preparation<\/th>/);
  assert.match(html, /<th class="cnt-th">In one line<\/th>/);
  assert.match(html, /<th class="cnt-th">Matched by<\/th>/);
  assert.match(html, /<th class="cnt-th cnt-numeric">Fasting<\/th>/);
  assert.doesNotMatch(html, /Se reconoce por|>Preparación</);
  assert.match(html, /pa-badge--editado">New</);
  assert.match(html, /pa-badge--editado">Edited</);
  assert.match(html, /pa-badge--off">Disabled</);
  assert.doesNotMatch(html, />Nueva<|>Editada<|>Deshabilitada</);
  assert.match(html, />8 h<\/td>/);
  // "Sí" stays untranslated in BOTH controllers: no canonical catalog key
  // exists for the fasting cell value, and this slice may not add one.
  // No fasting is an empty cell, never a column of «No».
  assert.match(html, />Sí<\/td>/);
  assert.doesNotMatch(html, />No<\/td>/);
  assert.match(html, /<td class="cnt-numeric"><\/td>/);
  assert.deepEqual(globalThis.__prepColsSeen[0].map((c) => c.key), ["nombre", "resumen", "match", "ayuno"]);
  assert.deepEqual(globalThis.__prepColsSeen[0].map((c) => c.label), ["Preparation", "In one line", "Matched by", "Fasting"]);
  assert.equal(globalThis.__prepColsSeen[0][3].numeric, true);
  d();
});
test("editor labels, fasting block and Save/Restore/Disable actions follow the supplied EN catalog", async (t) => {
  i18nSetCatalog(EN);
  const edited = { ...item, _origen: "json+db" };
  const f = fixture(t, (u, m) => Promise.resolve(response(200, m === "GET" ? { items: [edited] } : { ok: true, item: edited })), "?item=eco", l10n());
  const d = f.run(); await d.ready;
  const html = f.host.innerHTML;
  assert.match(html, /for="pa-nombre">Name<\/label>/);
  assert.match(html, /for="pa-resumen">In one line<\/label>/);
  assert.match(html, /for="pa-match">Matched by<span class="pa-nota">One word per line\. If any of these appear/);
  assert.match(html, /<span class="pa-lab">Fasting<\/span>/);
  assert.match(html, / checked> Done fasting<\/label>/);
  assert.match(html, />Hours <input class="pa-in pa-in--corto" id="pa-ay-horas"/);
  assert.match(html, / checked> Water is allowed<\/label>/);
  assert.match(html, /id="pa-guardar">Save</);
  assert.match(html, /id="pa-restaurar">Restore the original</);
  assert.match(html, /id="pa-deshabilitar">Disable</);
  assert.equal(f.host.querySelector("#pa-habilitar"), null);
  assert.doesNotMatch(html, />Guardar<|Se hace en ayunas|Puede tomar agua/);
  d();
});
test("a disabled preparation offers the translated Enable action", async (t) => {
  i18nSetCatalog(EN);
  const off = { ...item, _borrado: true };
  const f = fixture(t, () => Promise.resolve(response(200, { items: [off] })), "?item=eco", l10n());
  const d = f.run(); await d.ready;
  const html = f.host.innerHTML;
  assert.match(html, /id="pa-habilitar">Enable</);
  assert.equal(f.host.querySelector("#pa-deshabilitar"), null);
  assert.equal(f.host.querySelector("#pa-restaurar"), null);
  assert.doesNotMatch(html, />Habilitar</);
  d();
});
test("section names come from Prep.SECCIONES translated labels and reads keep the wire keys", async (t) => {
  i18nSetCatalog(EN);
  const f = fixture(t, (u, m) => Promise.resolve(response(200, m === "GET" ? { items: [item] } : { ok: true, item })), "?item=eco", l10n(PREP_EN));
  const d = f.run(); await d.ready;
  const html = f.host.innerHTML;
  for (const [clave, titulo] of [["antes", "Before you come"], ["traer", "What to bring"], ["avisar", "Tell us when you arrive if"], ["no_hace_falta", "Not needed"], ["despues", "Afterward"]]) {
    assert.ok(html.includes('for="pa-' + clave + '">' + titulo), clave + " renders the Prep label");
    assert.ok(f.host.querySelector("#pa-" + clave), clave + " stays the DOM id / wire key");
  }
  for (const clave of ["antes", "traer", "no_hace_falta", "despues"])
    assert.ok(!html.includes('for="pa-' + clave + '"><span class="pa-nota"'), clave + " carries no redundant helper");
  assert.ok(html.includes('for="pa-avisar">Tell us when you arrive if<span class="pa-nota">What the patient needs to mention on arrival: allergies, pregnancy, medication.'));
  assert.doesNotMatch(html, /Antes de venir|Qué llevar/);
  f.fire(f.host.querySelector("#pa-guardar")); await drain();
  const put = f.calls.find((x) => x.method === "PUT");
  const body = JSON.parse(put.options.body);
  for (const clave of ["antes", "traer", "avisar", "no_hace_falta", "despues"])
    assert.deepEqual(body[clave], clave === "antes" ? ["Traer orden"] : [], clave + " keeps its wire key");
  assert.equal("Before you come" in body, false);
  d();
});
test("the canonical ES catalog renders list and editor in Spanish through the same path", async (t) => {
  i18nSetCatalog(ES);
  t.after(() => i18nSetCatalog({}));
  const f = fixture(t, () => Promise.resolve(response(200, { items: [item] })), "", l10n());
  const d = f.run(); await d.ready;
  let html = f.host.innerHTML;
  assert.match(html, /<h1 class="cnt-h1">Cómo prepararse<\/h1>/);
  assert.match(html, /<th class="cnt-th">Preparación<\/th>/);
  assert.match(html, /<th class="cnt-th">Se reconoce por<\/th>/);
  assert.match(html, /<th class="cnt-th cnt-numeric">Ayuno<\/th>/);
  f.fire(f.host.querySelector("#pa-nueva"));
  html = f.host.innerHTML;
  assert.match(html, /Nueva preparación<\/h1>/);
  assert.match(html, /for="pa-nombre">Nombre<\/label>/);
  assert.match(html, / id="pa-ay-req"> Se hace en ayunas<\/label>/);
  assert.match(html, />Horas <input class="pa-in pa-in--corto" id="pa-ay-horas"/);
  assert.match(html, / checked> Puede tomar agua<\/label>/);
  assert.match(html, /id="pa-guardar">Guardar</);
  assert.match(html, /for="pa-antes">Antes de venir</);
  assert.match(html, /for="pa-match">Se reconoce por<span class="pa-nota">Una palabra por línea\. Con cualquiera de éstas/);
  assert.match(html, /for="pa-avisar">Avisá<span class="pa-nota">Lo que la persona tiene que contar cuando llega: alergias, embarazo, medicación\./);
  for (const key of ["help_antes", "help_traer", "help_no_hace_falta", "help_despues"])
    assert.ok(!html.includes(ES["admin.content.preparations." + key]), key + " must not render in Spanish either");
  assert.equal(html.split('class="pa-nota"').length - 1, 2, "exactly two helpers in the ES editor");
  d();
});
test("editor keeps exactly the two necessary guidance notes and a parent-only breadcrumb", async (t) => {
  i18nSetCatalog(EN);
  t.after(() => i18nSetCatalog({}));
  const f = fixture(t, (u, m) => Promise.resolve(response(200, m === "GET" ? { items: [item] } : { ok: true, item })), "?item=eco", l10n(PREP_EN));
  const d = f.run(); await d.ready;
  const html = f.host.innerHTML;
  // Exactly two helpers survive: the match rule and the avisar guidance.
  assert.equal(f.host.querySelectorAll(".pa-nota").length, 2, "only the match hint and the avisar guidance remain");
  assert.ok(html.includes('for="pa-match">Matched by<span class="pa-nota">One word per line.'));
  assert.ok(html.includes('for="pa-avisar">Tell us when you arrive if<span class="pa-nota">What the patient needs to mention on arrival: allergies, pregnancy, medication.'));
  // The four retired helpers exist in the supplied catalog yet never render.
  for (const key of ["help_antes", "help_traer", "help_no_hace_falta", "help_despues"])
    assert.ok(!html.includes(EN["admin.content.preparations." + key]), key + " must not render");
  assert.ok(!html.includes('<span class="pa-nota"></span>'), "no empty helper spans");
  // All five section fields stay rendered with their wire values.
  assert.equal(f.host.querySelector("#pa-antes").value, "Traer orden");
  for (const clave of ["traer", "avisar", "no_hace_falta", "despues"])
    assert.equal(f.host.querySelector("#pa-" + clave).value, "", clave + " field stays present");
  // The breadcrumb keeps only the parent link; the heading names the item.
  assert.ok(html.includes('<p class="cnt-crumb"><a href="/admin/preparations">How to prepare</a></p>'));
  assert.ok(!html.includes("How to prepare</a> ›"), "no trailing separator after the parent link");
  assert.ok(!html.includes("Ecografía</p>"), "the current item name is not repeated in the breadcrumb");
  // The parent-only crumb still navigates back to the list.
  f.fire(f.host.querySelector("a"));
  await drain();
  assert.ok(f.host.querySelector("#pa-nueva"), "editor-to-list navigation survives the parent-only crumb");
  d();
});
