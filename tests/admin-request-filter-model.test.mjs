import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const APP=readFileSync(new URL('../static/root/admin-app.js', import.meta.url), 'utf8');
const take=(name)=>{
  const match=APP.match(new RegExp(`function ${name}\\([^]*?\\n\\}`));
  assert.ok(match, `missing ${name} in admin-app.js`);
  return match[0];
};

function requestModel(medicos, servicios){
  const context={MED_LIST:medicos, SVC:servicios, Format:{nombre:value=>String(value).toLowerCase().replace(/(^|\s)\S/g, c=>c.toUpperCase())}};
  const source=[
    take('_norm'), take('_mismaRaiz'),
    APP.match(/const _ESP_STOP = new Set\([^]*?\);/)[0], take('_palabras'), take('coincideEsp'),
    take('svcNombre'), take('medicoNombre'), take('medicoIdDe'), take('medicoPorId'),
    take('medicoPorNombreUnico'), take('medicoDeSolicitud'), take('medicoClaveDe'),
    take('medicoEtiquetaDe'), take('especialidadUnicaDelServicio'), take('especialidadDe'),
    'globalThis.model={medicoClaveDe, medicoEtiquetaDe, especialidadDe};'
  ].join('\n');
  vm.runInNewContext(source, context);
  return context.model;
}

test('request filters use IDs first, retain pending slot doctors, and do not invent a specialty for an unassigned ambiguous service', () => {
  const model=requestModel([
    {id:'med-1', nombre:'Dra. Ana Pérez', especialidad:'Cardiología'},
    {id:'med-2', nombre:'Dr. Bruno López', especialidad:'Pediatría'},
    {id:'med-3', nombre:'Dr. Carlos Ruiz', especialidad:'Traumatología'},
    {id:'med-4', nombre:'Dr. Carlos Ruiz', especialidad:'Neurología'},
  ], {cardio:'Cardiología', imagenes:'Diagnóstico por imágenes', laboratorio:'Laboratorio'});

  const assigned={medico_asignado_id:'med-1', medico_asignado:'DRA. ANA PÉREZ', servicio_id:'cardio'};
  const pending={slot_medico_id:'med-2', servicio_id:'cardio'};
  const unassigned={servicio_id:'imagenes'};
  const legacyDuplicate={medico_asignado:'DR. CARLOS RUIZ', servicio_id:'imagenes'};

  assert.equal(model.medicoClaveDe(assigned), 'id:med-1');
  assert.equal(model.medicoEtiquetaDe(assigned), 'Dra. Ana Pérez');
  assert.equal(model.especialidadDe(assigned), 'Cardiología');
  assert.equal(model.medicoClaveDe(pending), 'id:med-2');
  assert.equal(model.especialidadDe(pending), 'Pediatría');
  assert.equal(model.medicoClaveDe(unassigned), '');
  assert.equal(model.especialidadDe(unassigned), '');
  assert.equal(model.especialidadDe(legacyDuplicate), '');
});

// 2026-10-23: eight doctors read «id:…» in the Solicitudes doctor filter —
// requests that know their doctor only by the picked slot (no free-text name),
// whose doctor the panel's list could not resolve (disabled, or the list came
// without ids). The server names the doctor on the row; the label never falls
// back to the raw key.
test('a slot-only request whose doctor the panel list does not know still reads the doctor name', () => {
  const model=requestModel([{id:'med-1', nombre:'Dra. Ana Pérez', especialidad:'Cardiología'}], {cardio:'Cardiología'});
  const disabled={slot_medico_id:'nancy-pellegrini', medico_nombre:'Nancy Pellegrini', servicio_id:'cardio'};
  assert.equal(model.medicoClaveDe(disabled), 'id:nancy-pellegrini');
  assert.equal(model.medicoEtiquetaDe(disabled), 'Nancy Pellegrini');
});

// 2026-10-23: a doctor with upcoming appointments is not disabled (409); its
// «Ver los turnos» link opens Solicitudes on exactly that doctor's approved
// appointments, clearing saved filters that would hide some of them.
test('the 409 link opens Solicitudes filtered to the doctor and status', () => {
  const context={URLSearchParams};
  vm.runInNewContext(take('requestDeepLink')+'\nglobalThis.f=requestDeepLink;', context);
  const link=context.f('?medico=med-panel&estado=aprobada&proximos=1');
  assert.equal(link.filters.medico, 'id:med-panel');
  assert.equal(link.filters.servicio, '');
  assert.equal(link.estado, 'aprobada');
  assert.equal(link.proximos, true);
  assert.equal(context.f('?medico=med-panel&estado=aprobada').proximos, false);
  assert.equal(context.f('?medico=med-panel&estado=x').estado, 'todas');
  assert.equal(context.f(''), null);
  assert.equal(context.f('?estado=aprobada'), null);
});

// Live demo 24.10: the message counted 1 upcoming appointment, the link listed
// 5 (four already past). With `proximos` only the counted ones are listed.
test('the 409 link lists only upcoming appointments, never past ones', () => {
  const empty={q:'',medico:'',especialidad:'',servicio:'',centro:'',verdict:'',sug:''};
  const context={SOLO_PROXIMOS:true, FILTERS:empty, Date};
  vm.runInNewContext(take('matchesFilters')+'\nglobalThis.m=matchesFilters;', context);
  const day=24*3600*1000;
  assert.equal(context.m({turno_dt:new Date(Date.now()+day).toISOString()}), true);
  assert.equal(context.m({turno_dt:new Date(Date.now()-day).toISOString()}), false);
  assert.equal(context.m({}), false);
  context.SOLO_PROXIMOS=false;
  assert.equal(context.m({turno_dt:new Date(Date.now()-day).toISOString()}), true);
  // One-shot: never persisted with the saved filters.
  assert.doesNotMatch(take('saveRequestPrefs'), /SOLO_PROXIMOS/);
});
