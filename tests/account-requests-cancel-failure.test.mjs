// TASK-55 (26.10, persona Lucía, 390 px): cancelling a request that another
// tab had already cancelled left «No se pudo. Reintentá.» in place of the
// button — no button to retry with, and no word that it was already done.
// The server's own answer decides: no longer active → the list reloads and
// says so; any other failure → «Reintentar», a button that works.
import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime, flush} from './lineage/runtime.mjs';

function button() {
  const listeners = new Map();
  return {addEventListener:(type, fn)=>listeners.set(type,fn),click:()=>listeners.get('click')?.()};
}
function hostDOM() {
  let html = '', cards = [];
  return {style:{},getAttribute:()=>true,querySelectorAll:()=>cards,
    get innerHTML(){return html;},set innerHTML(value){
      html = value;
      cards = [...value.matchAll(/<section class="req req-rich" data-idx="(\d+)">([\s\S]*?)<\/section>/g)].map(([,idx,content])=>{
        const yes=button(), no=button(), cancel=button(), retry=button();
        const cell={html:'',get innerHTML(){return this.html;},set innerHTML(v){this.html=v;},
          querySelector:s=>s==='[data-si]'?yes:s==='[data-reintentar]'?(cell.html.includes('data-reintentar')?retry:null):no};
        cancel.parentElement=cell;
        return {content,cancel,yes,retry,cell,getAttribute:key=>key==='data-idx'?idx:null,querySelector:s=>s==='[data-cancel]'?cancel:null};
      });
    }
  };
}
function mount(responses) {
  const host=hostDOM(), posts=[], loads=[];
  const c=runtime('static/platform/account-requests.mjs',['renderList','richCardHtml','wireCards','avisoHtml','cancelConfirmHtml','diaTurno','turnoDt','esAprob','horaDe','horaTurno'],{
    host,alive:()=>true,Dates:null,injectCardsStyle(){},renderFeed(){},titleRowHtml:()=>'<h1>Mis solicitudes</h1>',
    esPasado:()=>false,fechaConcreta:()=>false,richAvatarHtml:()=>'',centroInfo:{},
    nombreServicio:it=>it.servicio_id,esc:String,statusLabels:()=>({pendiente:['','Pending']}),MTC_ICO_XS:'',
    load(){loads.push(true);},
    fetch:async(url,options)=>{posts.push({url,options});const [status,body]=responses.shift();return new Response(JSON.stringify(body),{status});}
  }, 'var avisoLista = ""; var lastItems = null;');
  c.renderList([{id:'r1',estado:'pendiente',servicio_id:'Cardiología'}]);
  return {c,host,posts,loads};
}

test('a request that was already cancelled elsewhere: the list reloads and says so', async () => {
  const m=mount([[400,{error:'no_cancelable'}]]);
  const card=m.host.querySelectorAll('.req')[0];
  card.cancel.click(); card.yes.click(); await flush(); await flush();
  assert.equal(m.posts.length,1);
  assert.equal(m.loads.length,1,'the list is read again: the card shows the real state');
  m.c.renderList([{id:'r1',estado:'cancelada',servicio_id:'Cardiología'}]);
  assert.match(m.host.innerHTML,/ya estaba cancelada/);
});

test('any other failure offers «Reintentar», and the button sends the cancel again', async () => {
  const m=mount([[500,{}],[200,{ok:true}]]);
  const card=m.host.querySelectorAll('.req')[0];
  card.cancel.click(); card.yes.click(); await flush(); await flush();
  assert.match(card.cell.innerHTML,/data-reintentar/);
  assert.match(card.cell.innerHTML,/Reintentar/);
  card.retry.click(); await flush(); await flush();
  assert.deepEqual(m.posts.map(p=>p.url),['/api/requests/r1/cancel','/api/requests/r1/cancel']);
  assert.equal(m.loads.length,1,'success reloads the list');
});
