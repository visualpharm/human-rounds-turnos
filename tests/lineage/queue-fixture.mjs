import {fixture, click, flush} from '../helpers/queue-runtime-fixture.mjs';
import {readFileSync} from 'node:fs';
const previousPhone = globalThis.SANA_COUNTRY_PHONE;
globalThis.SANA_COUNTRY_PHONE = JSON.parse(readFileSync(new URL('../../packs/ar/country.json', import.meta.url))).phone;
const Format = await import('../../static/platform/format.mjs');
if (previousPhone === undefined) delete globalThis.SANA_COUNTRY_PHONE;
else globalThis.SANA_COUNTRY_PHONE = previousPhone;
export {click, flush};
export const patient = {id:'r1',paciente:'Ana Original',dni:'30123456',hora:'09:00',servicio:'Consulta',centro:'Centro',asistencia:null,elegible_asistencia:true,ai_campos:{prepaga:{valor:'Original Health'}}};
export const queue = row => ({dia:'2026-09-16',grupos:[{medico_id:'d1',medico:'Doctor One',centro:'Centro',turnos:[row],libres:[]}]});
export async function mounted(t, row = patient) {
  const f = fixture(t,{queueDom:true,initialData:queue(structuredClone(row))});
  f.modules.set('platform/format.mjs',Promise.resolve(Format));
  const owner = f.mount(); f.readyTable(); await owner.ready;
  return f;
}
export function overlayDOM(f) {
  const overlays = [];
  f.doc.body.appendChild = node => overlays.push(node);
  f.doc.createElement = () => {
    const wrap = {};
    Object.defineProperty(wrap,'innerHTML',{set(html) {
      wrap.firstChild = {html, querySelector:()=>({focus(){}}),addEventListener(){},removeEventListener(){},remove(){}};
    }});
    return wrap;
  };
  return overlays;
}
