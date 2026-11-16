import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.SANA_COUNTRY_PHONE=JSON.parse(readFileSync(new URL('../packs/ar/country.json',import.meta.url),'utf8')).phone;
const {mount,photoFileFromEvent}=await import('../static/platform/doctor-workspace.mjs');
const settle=()=>new Promise(r=>setImmediate(r));
function node(){return {dataset:{},handlers:new Map(),value:'selected',disabled:false,addEventListener(n,fn){this.handlers.set(n,fn);},removeEventListener(n){this.handlers.delete(n);},fire(n,e){this.handlers.get(n)?.(e);}};}
function fixture(t,transport){
 const oldFetch=globalThis.fetch,oldReader=globalThis.FileReader;globalThis.fetch=transport;
 let fail=false;const reads=[];
 globalThis.FileReader=class {readAsDataURL(file){reads.push(file);queueMicrotask(()=>{if(fail){fail=false;this.onerror?.();}else{this.result='data:image/png;base64,'+file.data;this.onload?.();}});}abort(){this.onabort?.();}};
 const photo=node(),input=node(),preview={style:'old',setAttribute(_n,v){this.style=v;},removeAttribute(){this.style='';}},label={textContent:''},badge={textContent:''};
 const status={textContent:'',classList:{toggle(){}}};
 photo.querySelector=s=>s==='[role="status"]'?status:s==='.dw-upload-label'?label:null;
 photo.querySelectorAll=s=>s==='button,input[type="file"]'?[input]:[];
 const root={querySelector:s=>({'[data-photo]':photo,'[data-upload]':input,'[data-photo-preview]':preview,'[data-photo-status]':badge}[s]||null)};
 const dispose=mount(root,{doctors:[{id:'doctor'}],photo:{has_upload:true,foto_url:'/doctor-photo/doctor?v=old',has_original:true,status:'original'}});
 t.after(()=>{dispose();globalThis.fetch=oldFetch;globalThis.FileReader=oldReader;});
 const event=(file,text=false)=>({target:{closest:()=>text?{}:null},clipboardData:{items:[{kind:'file',getAsFile:()=>file}]},preventDefault(){this.prevented=true;this.defaultPrevented=true;}});
 return {photo,input,preview,status,reads,event,dispose,failNext:()=>fail=true};
}
const ack=n=>({ok:true,status:200,json:async()=>({ok:true,foto_url:'/doctor-photo/doctor?v='+n,has_original:true,status:'procesada'})});
const file={name:'portrait.heic',type:'application/octet-stream',size:6*1024*1024,data:'U1lOVEhFVElD'};
test('explicit image paste works on photo area and never intercepts text fields or text-only paste',()=>{
 const e={target:{closest:()=>null},clipboardData:{files:[file]}};
 assert.equal(photoFileFromEvent(e),file);
 assert.equal(photoFileFromEvent({...e,target:{closest:()=>({})}}),null);
 assert.equal(photoFileFromEvent({...e,clipboardData:{files:[]}}),null);
 assert.equal(photoFileFromEvent({...e,defaultPrevented:true}),null);
});
test('real professional mount routes paste/file/drop through one upload and keeps verified preview',async t=>{
 const calls=[];const f=fixture(t,async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return ack(calls.length);});
 const pasted=f.event(file);f.photo.fire('paste',pasted);await settle();
 assert.equal(pasted.prevented,true);assert.equal(calls.length,1);assert.match(f.preview.style,/v=1/);assert.equal(f.input.value,'');
 f.input.files=[file];f.input.fire('change',{target:f.input});await settle();assert.equal(calls.length,2);assert.match(f.preview.style,/v=2/);
 const dropped={target:{closest:()=>null},dataTransfer:{files:[file]},preventDefault(){this.prevented=true;this.defaultPrevented=true;}};
 f.photo.fire('drop',dropped);await settle();assert.equal(calls.length,3);assert.equal(dropped.prevented,true);assert.match(f.preview.style,/v=3/);
 assert.equal(calls.every(x=>x.url==='/api/admin/doctors/doctor/photo'),true);
 const text=f.event(file,true);f.photo.fire('paste',text);await settle();assert.equal(text.prevented,undefined);assert.equal(calls.length,3);
 f.dispose();f.photo.fire('paste',f.event(file));await settle();assert.equal(calls.length,3);
});
test('unsupported file drops prevent navigation; text drops and text-only pastes stay native',async t=>{
 let calls=0;const f=fixture(t,async()=>{calls++;return ack(1);});
 const event=transfer=>({target:{closest:()=>null},dataTransfer:transfer,preventDefault(){this.prevented=true;this.defaultPrevented=true;}});
 for(const transfer of [{files:[{name:'notes.pdf',type:'application/pdf'}]},{items:[{kind:'file',getAsFile:()=>({name:'text.txt',type:'text/plain'})}]},{types:['Files']}]){
  const dropped=event(transfer);f.photo.fire('drop',dropped);await settle();assert.equal(dropped.prevented,true);assert.equal(calls,0);assert.match(f.status.textContent,/JPG/);assert.match(f.preview.style,/v=old/);
 }
 const textDrop=event({types:['text/plain'],files:[]});f.photo.fire('drop',textDrop);assert.equal(textDrop.prevented,undefined);
 const paste={target:{closest:()=>null},clipboardData:{items:[{kind:'string',type:'text/plain'}]},preventDefault(){this.prevented=true;}};f.photo.fire('paste',paste);assert.equal(paste.prevented,undefined);
 const drag=event({types:['Files']});f.photo.fire('dragover',drag);assert.equal(drag.prevented,true);
});
test('repeated paste during save sends once and does not erase prior preview',async t=>{
 let resolve;const calls=[];const f=fixture(t,async(url,options)=>{calls.push(url);return new Promise(r=>resolve=r);});
 f.photo.fire('paste',f.event(file));await settle();f.photo.fire('paste',f.event(file));await settle();
 assert.equal(calls.length,1);assert.match(f.preview.style,/v=old/);assert.match(f.status.textContent,/Esperá/);
 resolve(ack(1));await settle();assert.match(f.preview.style,/v=1/);
});
test('read failure and oversize keep previous image; same-file retry can succeed',async t=>{
 let n=0;const f=fixture(t,async()=>ack(++n));f.failNext();f.photo.fire('paste',f.event(file));await settle();
 assert.equal(n,0);assert.match(f.preview.style,/v=old/);assert.match(f.status.textContent,/leer/);assert.equal(f.input.value,'');
 f.photo.fire('paste',f.event({...file,size:20*1024*1024+1}));await settle();assert.equal(n,0);assert.match(f.status.textContent,/20 MB/);
 f.photo.fire('paste',f.event(file));await settle();assert.equal(n,1);assert.match(f.preview.style,/v=1/);
});
