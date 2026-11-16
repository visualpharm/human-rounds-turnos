import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.SANA_COUNTRY_PHONE=JSON.parse(readFileSync(new URL('../packs/ar/country.json',import.meta.url),'utf8')).phone;

test('empty schedule offers the seven-day chooser immediately, never a dead end', async () => {
  const {scheduleEditor} = await import('../static/platform/doctor-workspace.mjs');
  const html = scheduleEditor([{centro: 'ostende', center: 'Ostende', franjas: [], editable: true}]);
  // Day-first editor: all seven weekday chips are the immediate affordance;
  // a day card with its 24h time inputs appears for each day chosen.
  assert.equal((html.match(/type="checkbox" data-day="\d+"/g) || []).length, 7);
  assert.match(html, /data-day-pick/);
  assert.doesNotMatch(html, /data-time="desde"/);
  assert.doesNotMatch(html, /type="time"/);
  assert.doesNotMatch(html, /agenda no configurada/i);
});

test('a chosen weekday renders its own card with typed 24h interval inputs', async () => {
  const {scheduleEditor} = await import('../static/platform/doctor-workspace.mjs');
  const html = scheduleEditor([{centro: 'ostende', center: 'Ostende',
    franjas: [{dias: [4], desde: '08:00', hasta: '12:00'}], editable: true}]);
  assert.match(html, /class="dw-day" data-day-row="4"/);
  assert.match(html, /class="dw-time" type="text" inputmode="numeric"/);
  assert.match(html, /data-time="desde"/);
  assert.match(html, /data-add-slot="4"/);
});

test('service-owned schedule offers its hours without editable controls', async () => {
  const {scheduleEditor} = await import('../static/platform/doctor-workspace.mjs');
  const html = scheduleEditor([{centro: 'ostende', center: 'Ostende', franjas: [{dias:[2,5], desde:'08:00', hasta:'12:00'}], editable:false, message:'El horario ya está definido para el servicio'}]);
  assert.match(html, /08:00/);
  assert.match(html, /12:00/);
  assert.match(html, /El horario ya está definido/);
  assert.doesNotMatch(html, /type="checkbox"|type="time"/);
});

test('HTTP 200 without persistence acknowledgement stays an error', async () => {
  const {requestOwner} = await import('../static/platform/doctor-workspace.mjs');
  const owner = requestOwner(async () => ({ok:true, status:200, json:async () => ({ok:false, error:'No guardado'})}));
  await assert.rejects(owner.mutate('/photo', {}), /No guardado/);
  owner.dispose();
});

test('disposed owner rejects late acknowledgement and aborts the request', async () => {
  const {requestOwner} = await import('../static/platform/doctor-workspace.mjs');
  let release, signal;
  const owner = requestOwner(async (_url, options) => { signal=options.signal; return {ok:true, status:200, json:()=>new Promise(r=>release=r)}; });
  const result = owner.mutate('/photo', {});
  await new Promise(r=>setImmediate(r));
  owner.dispose(); release({ok:true});
  assert.equal(signal.aborted, true);
  await assert.rejects(result, {name:'AbortError'});
});

test('HTTP 200 catalog save needs an actual saved item', async () => {
  const {requestOwner} = await import('../static/platform/doctor-workspace.mjs');
  const owner = requestOwner(async () => ({ok:true, status:200, json:async () => ({ok:true, item:null})}));
  await assert.rejects(owner.mutate('/api/admin/content/doctors/doctor', {}, 'PUT', true));
  owner.dispose();
});

test('catalog acknowledgement must belong to the requested doctor', async () => {
  const {requestOwner} = await import('../static/platform/doctor-workspace.mjs');
  const owner=requestOwner(async()=>({ok:true,status:200,json:async()=>({ok:true,item:{id:'foreign'}})}));
  await assert.rejects(owner.mutate('/api/admin/content/doctors/doctor',{},'PUT',true));
  owner.dispose();
});

test('photo acknowledgement needs the published portrait and status', async () => {
  const {requestOwner} = await import('../static/platform/doctor-workspace.mjs');
  const owner=requestOwner(async()=>({ok:true,status:200,json:async()=>({ok:true})}));
  await assert.rejects(owner.mutate('/api/admin/doctors/doctor/photo',{},'POST',body=>!!body.foto_url && body.status==='procesada'));
  owner.dispose();
});

test('a complete photo acknowledgement can confirm a published image', async () => {
  const {requestOwner} = await import('../static/platform/doctor-workspace.mjs');
  const owner=requestOwner(async()=>({ok:true,status:200,json:async()=>({ok:true,foto_url:'/doctor-photo/doctor?v=1',status:'procesada'})}));
  const result=await owner.mutate('/api/admin/doctors/doctor/photo',{},'POST',body=>!!body.foto_url && body.status==='procesada');
  assert.equal(result.status,'procesada');owner.dispose();
});


async function setupFixture(t, transport, editable=true, valid=true) {
  const {mount}=await import('../static/platform/doctor-workspace.mjs');
  const oldFetch=globalThis.fetch, oldFormData=globalThis.FormData;
  globalThis.fetch=transport;
  globalThis.FormData=class {constructor(form){return form.fields.entries();}};
  t.after(()=>{globalThis.fetch=oldFetch;globalThis.FormData=oldFormData;});
  function form(v=true){
    const handlers=new Map(), status={textContent:'',classList:{toggle(){}}};
    return {dataset:{},fields:new Map(),reportValidity:()=>v,querySelector:()=>status,querySelectorAll:()=>[],
      addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:name=>handlers.delete(name),
      dispatch(name){handlers.get(name)?.({preventDefault(){}});},status};
  }
  const profile=form(valid),schedule=form(valid),section=form(valid),handlers=new Map();
  profile.fields.set('nombre_pila','Ana');profile.fields.set('apellido','Example');
  const finish={click:()=>handlers.get('click')(),addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:name=>handlers.delete(name),closest:()=>section};
  const root={querySelector:sel=>({'[data-profile]':profile,'[data-schedule]':schedule,'[data-finish]':finish}[sel] || null)};
  let next=null;
  const data={editable,doctors:[{id:'doctor',centros:[{centro:'ostende',franjas:[{dias:[3],desde:'08:00',hasta:'12:00'}]}]}]};
  const dispose=mount(root,data,url=>next=url);
  t.after(dispose);
  return {profile,schedule,section,finish,dispose,data,remount(){const stop=mount(root,data,url=>next=url);t.after(stop);return stop;},next:()=>next};
}
const settle=()=>new Promise(r=>setImmediate(r));
test('single save waits for the catalog acknowledgement before completing and includes both drafts',async t=>{
  const calls=[];let release;
  const f=await setupFixture(t,async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return {ok:true,status:200,json:()=>url.endsWith('/complete')?Promise.resolve({ok:true,next:'/admin/cola'}):new Promise(r=>release=r)};});
  f.profile.dispatch('input');f.schedule.dispatch('input');f.finish.click();await settle();
  assert.equal(calls.length,1);assert.equal(calls[0].body.nombre_pila,'Ana');assert.equal(calls[0].body.centros[0].franjas[0].desde,'08:00');assert.equal(f.next(),null);
  f.finish.click();assert.equal(calls.length,1,'in-flight save coalesces repeat clicks');
  release({ok:true,item:{id:'doctor'}});await settle();
  assert.equal(calls[1].url,'/api/admin/onboarding/complete');assert.equal(f.next(),'/admin/cola');
});
test('failed catalog acknowledgement never completes onboarding',async t=>{
  const calls=[];const f=await setupFixture(t,async url=>{calls.push(url);return {ok:true,status:200,json:async()=>({ok:true,item:{id:'foreign'}})};});
  f.finish.click();await settle();assert.equal(calls.length,1);assert.equal(f.next(),null);assert.match(f.section.status.textContent,/No se pudo guardar/);
});
test('edits during save retain the new draft and cannot complete onboarding',async t=>{
  const calls=[];let release;const f=await setupFixture(t,async url=>{calls.push(url);return {ok:true,status:200,json:()=>new Promise(r=>release=r)};});
  f.finish.click();await settle();f.profile.dispatch('input');release({ok:true,item:{id:'doctor'}});await settle();
  assert.equal(calls.length,1);assert.equal(f.next(),null);assert.match(f.section.status.textContent,/cambiaron mientras/);
});
test('service-managed onboarding saves profile without sending a doctor schedule',async t=>{
  const calls=[];const f=await setupFixture(t,async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return {ok:true,status:200,json:async()=>url.endsWith('/complete')?{ok:true,next:'/admin/cola'}:{ok:true,item:{id:'doctor'}}};},false);
  f.schedule.dispatch('submit');await settle();assert.equal(calls.length,2);assert.equal('centros' in calls[0].body,false);assert.equal(f.next(),'/admin/cola');
});
// Un formulario inválido no guarda nada: el paso no puede pintar «Guardado.»
// por encima del globito del navegador (audit de onboarding, 2026-10-15).
test('invalid form saves nothing and never claims it saved',async t=>{
  const calls=[];const f=await setupFixture(t,async url=>{calls.push(url);return {ok:true,status:200,json:async()=>({ok:true,item:{id:'doctor'},next:'/admin/cola'})};},true,false);
  f.finish.click();await settle();
  assert.equal(calls.length,0,'no request leaves the page');
  assert.equal(f.section.status.textContent,'');
  assert.equal(f.next(),null);
});

// The existing first-center onboarding path uses the same per-day controls.
function newCenterFixture(f, rows=[]) {
  const newCenter={dataset:{center:''},querySelector:sel=>sel==='[data-center-select]'?{value:'ostende'}:null,
    querySelectorAll:sel=>sel==='.dw-day'?rows:[]};
  const status=f.schedule.status;
  f.schedule.querySelector=sel=>sel==='[data-center-new]'?newCenter:status;
  f.data.needs_center=true;
  return newCenter;
}
function freshDay(day, desde='09:00', hasta='13:00') {
  const inputs=[['desde',desde],['hasta',hasta],['cada_min','15']].map(([time,value])=>({dataset:{time},value}));
  const slot={dataset:{},querySelectorAll:sel=>sel==='[data-time]'?inputs:[],querySelector:()=>null};
  return {dataset:{dayRow:String(day)},querySelectorAll:sel=>sel==='.dw-slot'?[slot]:[]};
}
test('a doctor invited without a center saves its chosen center and independent day rows', async t => {
  const calls=[];
  const f=await setupFixture(t,async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return {ok:true,status:200,json:async()=>url.endsWith('/complete')?{ok:true,next:'/admin/cola'}:{ok:true,item:{id:'doctor'}}};},false);
  newCenterFixture(f,[freshDay(2),freshDay(4,'14:00','18:00')]);
  f.finish.click();await settle();
  assert.equal(calls.length,2);
  assert.deepEqual(calls[0].body.centros,[{centro:'ostende',franjas:[
    {desde:'09:00',hasta:'13:00',cada_min:15,dias:[2]},
    {desde:'14:00',hasta:'18:00',cada_min:15,dias:[4]}],excepciones:[]}]);
  assert.equal(f.next(),'/admin/cola');
});
for (const partial of [false,true]) {
  test('a first-center agenda with '+(partial?'a selected empty day':'no selected days')+' is stopped before saving', async t => {
    const calls=[];
    const f=await setupFixture(t,async url=>{calls.push(url);return {ok:true,status:200,json:async()=>({ok:true,item:{id:'doctor'},next:'/admin/cola'})};},false);
    const emptyDay={dataset:{dayRow:'4'},querySelectorAll:()=>[]};
    newCenterFixture(f,partial?[freshDay(2),emptyDay]:[]);
    f.finish.click();await settle();
    assert.equal(calls.length,0,'no request leaves the page');
    assert.equal(f.section.status.textContent,'Marcá al menos un día y cargá un horario.');
    assert.doesNotMatch(f.section.status.textContent,/Guardado|agenda\.dias/);
    assert.equal(f.next(),null);
  });
}

test('a raw validator string from the server never reaches the doctor', async () => {
  const {requestOwner} = await import('../static/platform/doctor-workspace.mjs');
  const raw='centro 1: franja 1: agenda.dias debe ser una lista no vacía de días (0=domingo..6=sábado)';
  const owner=requestOwner(async()=>({ok:false,status:400,json:async()=>({error:raw})}));
  await assert.rejects(owner.mutate('/api/admin/content/doctors/doctor',{},'PUT',true),
    error=>error.message==='No se pudo guardar. Volvé a intentar.');
  owner.dispose();
});

test('onboarding shows the sentence the server gives when something is still missing', async t => {
  const f=await setupFixture(t,async url=>({ok:!url.endsWith('/complete'),status:url.endsWith('/complete')?400:200,
    json:async()=>url.endsWith('/complete')?{error:'Antes de empezar, completá tu email.',faltan:['email']}:{ok:true,item:{id:'doctor'}}}));
  f.finish.click();await settle();
  assert.equal(f.section.status.textContent,'Antes de empezar, completá tu email.');
  assert.equal(f.next(),null);
});

for (const pending of [undefined, 'seed']) {
  test('disposing a save restores owned controls and its late response cannot unlock a remount: '+String(pending),async t=>{
    const responses=[];
    const f=await setupFixture(t,async()=>({ok:true,status:200,json:()=>new Promise(resolve=>responses.push(resolve))}));
    const readonly={disabled:true};f.finish.disabled=false;
    f.section.querySelectorAll=()=>[f.finish,readonly];
    if(pending!==undefined)f.section.dataset.pending=pending;
    f.finish.click();await settle();
    assert.equal(f.section.dataset.pending,'1');assert.equal(f.finish.disabled,true);
    f.dispose();f.dispose();
    assert.equal(f.finish.disabled,false);assert.equal(readonly.disabled,true);
    assert.equal(Object.hasOwn(f.section.dataset,'pending'),pending!==undefined);
    assert.equal(f.section.dataset.pending,pending);
    f.remount();f.finish.click();await settle();
    assert.equal(responses.length,2);
    responses[0]({ok:true,item:{id:'doctor'}});await settle();
    assert.equal(f.section.dataset.pending,'1');assert.equal(f.finish.disabled,true);assert.equal(readonly.disabled,true);
    responses[1]({ok:false,error:'Synthetic rejected save'});await settle();
    assert.equal(f.finish.disabled,false);assert.equal(readonly.disabled,true);
    assert.equal(Object.hasOwn(f.section.dataset,'pending'),pending!==undefined);
    assert.equal(f.section.dataset.pending,pending);assert.equal(f.next(),null);
  });
}
