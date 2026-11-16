import test from "node:test";
import assert from "node:assert/strict";
import * as HoursEditor from "../static/platform/admin-hours-editor.mjs";

test("the canonical hours module renders selected weekdays without shadowing its label factory", () => {
  const html = HoursEditor.editorHtml([
    { centro: "synthetic-center", franjas: [{ dias: [1, 3], desde: "09:00", hasta: "12:00" }], excepciones: [] },
  ], {});
  assert.equal((html.match(/data-franja-dia/g) || []).length, 7);
  assert.match(html, /data-franja-dia value="1" checked/);
  assert.match(html, /data-franja-dia value="3" checked/);
  assert.doesNotMatch(html, /data-franja-dia value="2" checked/);
});


test('booking limits use canonical numeric fields with blank inheritance, including explicit one',()=>{
  const html=HoursEditor.limitsHtml([{franjas:[{cupos:{web:0,overbooking_percent:0}}]}],1,{diasAnticipacion:'',patientDefault:10,daysDefault:45});
  assert.doesNotMatch(html,/<details|<summary/);
  assert.match(html,/Límites de reserva/);
  assert.match(html,/id="med-turnos-max" min="1" max="60" step="1" data-limits-patient-max data-limits-patient-max-count/);
  assert.match(html,/value="1" placeholder="10"/);
  assert.match(html,/id="med-dias-anticipacion"[^>]+value="" placeholder="45"/);
  assert.match(html,/class="hor-limit-sub" hidden/);
  assert.equal((html.match(/class="hor-limit-row"/g)||[]).length,5);
  const inherited=HoursEditor.limitsHtml([],null,{});
  assert.match(inherited,/data-limits-patient-max-count[^>]+value=""/);
});
test('mixed legacy quota flags remain mixed until explicitly edited',()=>{
  const html=HoursEditor.limitsHtml([{franjas:[{cupos:{web:0,overbooking_percent:10}},{cupos:{web:1,overbooking_percent:30}}]}],null);
  assert.match(html,/data-limits-web data-mixed="1"/);
  assert.match(html,/data-limits-overbooking data-mixed="1"/);
  assert.match(html,/data-limits-overbooking-percent[^>]+value=""/);
});

// La sesión de médico no dibuja los tres límites de reserva: el servidor los
// descarta (own_fields en admin_content.py) y dibujarlos prometía un «Guardado.»
// que revertía los valores (hallazgo del audit de onboarding, 2026-10-15).
test('medico session hides admin-owned booking limits but keeps web and overbooking',()=>{
  const html=HoursEditor.limitsHtml([{franjas:[{cupos:{web:1}}]}],1,{bookingLimits:false,diasAnticipacion:'',patientDefault:10,daysDefault:45});
  assert.match(html,/Límites de reserva/);
  assert.match(html,/data-limits-web/);
  assert.match(html,/data-limits-overbooking/);
  assert.doesNotMatch(html,/med-turnos-max/);
  assert.doesNotMatch(html,/med-dias-anticipacion/);
  assert.doesNotMatch(html,/med-apertura-turnos/);
  assert.equal((html.match(/class="hor-limit-row"/g)||[]).length,2);
});

test('each center block carries its own booking window, blank inherits, and the medico session does not draw it',()=>{
  const centros=[{centro:'hospital',franjas:[],excepciones:[],dias_anticipacion:14},{centro:'caps-norte',franjas:[],excepciones:[]}];
  const html=HoursEditor.editorHtml(centros,{diasAnticipacion:7,daysDefault:31});
  assert.match(html,/id="med-ci0-dias-anticipacion"[^>]+value="14"/);
  assert.match(html,/id="med-ci1-dias-anticipacion"[^>]+value="" placeholder="7"/);
  assert.equal((html.match(/data-centro-dias/g)||[]).length,2);
  // Named for the center, and one line saying where the number comes from
  // (the doctor's own 7 here), which swaps as the field is typed into.
  assert.equal((html.match(/>Días de anticipación en este centro<\/label>/g)||[]).length,2);
  assert.match(html,/data-own-text="Propio de este centro\. Vacío: hereda 7\." data-inherited-text="Heredado del médico: 7\.">Propio de este centro\. Vacío: hereda 7\.</);
  assert.match(html,/data-inherited-text="Heredado del médico: 7\.">Heredado del médico: 7\.</);
  assert.doesNotMatch(html,/Hasta cuántos días antes se puede reservar en este centro/);
  const medico=HoursEditor.editorHtml(centros,{bookingLimits:false,diasAnticipacion:7,daysDefault:31});
  assert.doesNotMatch(medico,/data-centro-dias/);
});

test('real delegated steppers respect inheritance, bounds and disabled controls; switches update hidden quota fields',async t=>{
  const original=globalThis.document, handlers=new Map();
  globalThis.document={addEventListener:(name,fn)=>handlers.set(name,fn),head:{appendChild(){}},createElement:()=>({})};
  t.after(()=>{if(original===undefined)delete globalThis.document;else globalThis.document=original;});
  await import('../static/platform/admin-hours-editor.mjs?delegated-slice6');
  const emitted=[], input={min:'1',max:'60',step:'1',value:'',disabled:false,dataset:{inherited:'10'},hasAttribute:name=>name==='data-inherited',dispatchEvent:e=>emitted.push(e.type)};
  const button={dataset:{step:'1'},closest:()=>({querySelector:()=>input})};
  handlers.get('click')({target:{closest:()=>button}});
  assert.equal(input.value,'10');assert.deepEqual(emitted,['input','change']);
  input.value='60';handlers.get('click')({target:{closest:()=>button}});assert.equal(input.value,'60');
  input.value='1';button.dataset.step='-1';handlers.get('click')({target:{closest:()=>button}});assert.equal(input.value,'');
  input.disabled=true;button.dataset.step='1';handlers.get('click')({target:{closest:()=>button}});assert.equal(input.value,'');
  const sub={hidden:true},percent={value:'20',disabled:true,closest:()=>sub},enabled=[{value:'0'},{value:'0'}],values=[{value:'0'},{value:'0'}];
  const editor={querySelector:()=>percent,querySelectorAll:selector=>selector==='[data-overbooking-enabled]'?enabled:values};
  const switchInput={checked:true,indeterminate:true,closest:()=>editor,matches:selector=>selector==='[data-limits-overbooking]',removeAttribute(){}};
  handlers.get('change')({target:switchInput});
  assert.deepEqual(enabled.map(x=>x.value),['1','1']);assert.deepEqual(values.map(x=>x.value),['20','20']);assert.equal(sub.hidden,false);assert.equal(percent.disabled,false);assert.equal(switchInput.indeterminate,false);
  switchInput.checked=false;handlers.get('change')({target:switchInput});
  assert.deepEqual(enabled.map(x=>x.value),['0','0']);assert.deepEqual(values.map(x=>x.value),['0','0']);assert.equal(sub.hidden,true);assert.equal(percent.disabled,true);
});
