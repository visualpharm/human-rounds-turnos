import test from 'node:test';
import assert from 'node:assert/strict';
import {runtime, flush} from './lineage/runtime.mjs';
// Minimal DOM surface for the real renderList/richCardHtml/wireCards chain.
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
        const yes=button(), no=button(), cancel=button();
        const cell={innerHTML:'',querySelector:s=>s==='[data-si]'?yes:no}; cancel.parentElement=cell;
        return {content,cancel,yes,getAttribute:key=>key==='data-idx'?idx:null,querySelector:s=>s==='[data-cancel]'?cancel:null};
      });
    }
  };
}
for (const retained of [false,true]) test(`lineage: cancellation targets its own request after prepend (${retained ? 'retained old card' : 'repainted card'})`, async () => {
  const host=hostDOM(), posts=[];
  const c=runtime('static/platform/account-requests.mjs',['renderList','richCardHtml','wireCards','avisoHtml','cancelConfirmHtml','diaTurno','turnoDt','esAprob','horaDe','horaTurno'],{
    host,alive:()=>true,Dates:null,injectCardsStyle(){},renderFeed(){},titleRowHtml:()=>'',
    esPasado:()=>false,fechaConcreta:()=>false,richAvatarHtml:()=>'',centroInfo:{},
    nombreServicio:it=>it.servicio_id,esc:String,statusLabels:()=>({pendiente:['','Pending']}),MTC_ICO_XS:'',
    load(){},fetch:async(url,options)=>{posts.push({url,options});return new Response('{}');}
  }, 'var avisoLista = "";');
  const old={id:'old-request',estado:'pendiente',servicio_id:'Original appointment'};
  const items=[old]; c.renderList(items);
  const oldCard=host.querySelectorAll('.req')[0];
  assert.match(oldCard.content,/Original appointment/);
  items.unshift({id:'new-request',estado:'pendiente',servicio_id:'New appointment'});
  c.renderList(items);
  const repainted=host.querySelectorAll('.req')[1];
  assert.equal(repainted.getAttribute('data-idx'),'1');
  const card=retained?oldCard:repainted;
  card.cancel.click(); card.yes.click(); await flush();
  assert.deepEqual(posts.map(x=>x.url),['/api/requests/old-request/cancel']);
  assert.equal(posts[0].options.method,'POST');
});
