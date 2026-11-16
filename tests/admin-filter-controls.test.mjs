import test from 'node:test';
import assert from 'node:assert/strict';
import { select, uniqueOptions } from '../static/platform/admin-filter-controls.mjs';

test('shared select markup escapes a readable long label and preserves selection', () => {
  const html=select({ className:'adm-sel', label:'Filtrar', value:'med-1', attrs:{'data-filter':'doctor'}, options:[
    {value:'', label:'Todos'}, {value:'med-1', label:'Dra. Ana <Pérez>'}
  ]});
  assert.match(html, /aria-label="Filtrar"/);
  assert.match(html, /data-filter="doctor"/);
  assert.match(html, /value="med-1" selected/);
  assert.match(html, /Dra\. Ana &lt;Pérez&gt;/);
});

test('shared filter options preserve raw doctor keys while omitting an unassigned row', () => {
  const rows=[
    {doctor:'id:med-1', name:'Dra. Ana Pérez'},
    {doctor:'', name:''},
    {doctor:'id:med-1', name:'Dra. Ana Pérez'},
    {doctor:'nombre:Legacy', name:'Dr. Bruno López'},
  ];
  assert.deepEqual(uniqueOptions(rows, row=>row.doctor, row=>row.name), [
    {value:'nombre:Legacy', label:'Dr. Bruno López'},
    {value:'id:med-1', label:'Dra. Ana Pérez'},
  ]);
});
