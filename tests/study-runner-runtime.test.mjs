/* Execute the real runner's consent/save/disposal boundary without a browser. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mountLanding, mountRunner } from '../app/modules/turnos/study-runner.mjs';

class Element {
  constructor(tag) { this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.value='';this.hidden=false;this.disabled=false; }
  setAttribute(k,v) {this.attrs[k]=String(v);if(k==='hidden')this.hidden=true;if(k==='name')this.name=v;}
  append(...nodes) {for(const n of nodes){n.parentElement=this;this.children.push(n);}}
  replaceChildren(...nodes) {this.children=[];this.append(...nodes);}
  querySelectorAll(selector) {
    const matches=n=>selector===n.tagName.toLowerCase() ||
      (selector==='[role=alert]' && n.attrs.role==='alert') ||
      (selector==='[type=submit]' && n.attrs.type==='submit');
    const found=[];const walk=n=>{for(const c of n.children){if(matches(c))found.push(c);walk(c);}};walk(this);return found;
  }
  querySelector(s) {return this.querySelectorAll(s)[0] || null;}
  focus() {this.focused=true;}
  get elements() {return {namedItem:name=>{const find=n=>n.name===name?n:n.children.map(find).find(Boolean);return find(this);}};}
  get textContent() {return this.text || this.children.map(c=>c.textContent).join(' ');}
  set textContent(v) {this.text=String(v);}
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function settled() {for(let i=0;i<5;i++)await tick();}
function fixture(fetchImpl) {
  const previous=Object.fromEntries(['window','document','location','navigator','fetch'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
  const listeners=new Map(),calls=[];
  const document={createElement:tag=>new Element(tag),addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k)};
  const location={origin:'https://study.invalid',assign:url=>calls.push({redirect:url})};
  const window={document,location,SANA_I18N:{active:'es',catalog:{}},
    SANA_STATIC:{'platform/i18n.mjs':new URL('../static/platform/i18n.mjs',import.meta.url).href}};
  for(const [key,value] of Object.entries({window,document,location,navigator:{sendBeacon:()=>true},fetch:async(url,options)=>{
    calls.push({url,payload:JSON.parse(options.body)});
    return fetchImpl ? fetchImpl(url,options) : {ok:true,json:async()=>({accepted:100,url:'/study/s/anonymous'})};
  }}))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
  return {document,calls,listeners,restore(){for(const [key,descriptor] of Object.entries(previous)){
    if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}};
}
function payload() {return {token:'anonymous',completed:[],role:'patient',final_questions:[],group:{entry:'/',tasks:[
  {id:'P1',instruction:'Find a doctor',stop_before_save:false},{id:'P2',instruction:'Find a center',stop_before_save:false}]}};}

test('consent failure never creates a session; corrected form creates one',async()=>{
  const f=fixture();try{
    const form=new Element('form'),role=new Element('select'),consent=new Element('input'),submit=new Element('button'),error=new Element('p');
    role.name='role';consent.name='consent';consent.checked=false;submit.setAttribute('type','submit');error.setAttribute('role','alert');
    form.append(role,consent,submit,error);const dispose=mountLanding(form);
    await form.onsubmit({preventDefault(){}});assert.equal(f.calls.length,0);assert.equal(error.hidden,false);
    role.value='patient';consent.checked=true;
    await form.onsubmit({preventDefault(){}});assert.equal(f.calls[0].url,'/api/study/session');
    assert.deepEqual(f.calls[0].payload,{role:'patient',consent:true});assert.equal(f.calls[1].redirect,'/study/s/anonymous');
    dispose();assert.equal(form.onsubmit,null);
  }finally{f.restore();}
});
test('failed save keeps the task and answers; retry advances exactly one task',async()=>{
  let resultCalls=0;
  const f=fixture(async url=>({ok:!url.endsWith('/result') || ++resultCalls>1,status:503,json:async()=>({accepted:100})}));
  let dispose;
  try{
    const host=new Element('div');dispose=mountRunner(host,payload());await settled();
    host.querySelectorAll('button').find(b=>b.textContent==='Terminé').onclick();
    const form=host.querySelector('form');form.elements.namedItem('ease').value='2';form.elements.namedItem('comment').value='Search was confusing';
    await form.onsubmit({preventDefault(){}});
    assert.match(host.textContent,/Find a doctor/);assert.equal(form.elements.namedItem('comment').value,'Search was confusing');
    assert.equal(form.querySelector('[type=submit]').disabled,false);
    await form.onsubmit({preventDefault(){}});assert.match(host.textContent,/Find a center/);assert.equal(resultCalls,2);
    const events=f.calls.filter(c=>c.url?.endsWith('/event')).flatMap(c=>c.payload.events);
    assert.ok(events.length);assert.equal(JSON.stringify(events).includes('Search was confusing'),false);
  }finally{dispose?.();f.restore();}
});
test('a save resolving after disposal cannot advance or repaint the retired runner',async()=>{
  let release;
  const f=fixture(async url=>url.endsWith('/result')?new Promise(resolve=>{release=resolve;}):{ok:true,json:async()=>({accepted:100})});
  let dispose;
  try{
    const host=new Element('div');dispose=mountRunner(host,payload());await settled();
    host.querySelectorAll('button').find(b=>b.textContent==='Me trabé').onclick();
    const form=host.querySelector('form');form.elements.namedItem('ease').value='1';
    const flight=form.onsubmit({preventDefault(){}});await settled();assert.ok(release);
    dispose();const before=host.textContent;release({ok:true,json:async()=>({})});await flight;
    assert.equal(host.textContent,before);assert.equal(f.listeners.size,0);
  }finally{dispose?.();f.restore();}
});
