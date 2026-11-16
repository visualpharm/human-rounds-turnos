import test from 'node:test';
import assert from 'node:assert/strict';
import { safePath, describeTarget, detectDeadClicks, resumeIndex } from '../app/modules/turnos/study-runner.mjs';
import { summarize, story } from '../static/platform/admin-usability.mjs';

test('path and target never contain input values, text, query strings or identifiers', () => {
  assert.equal(safePath('/admin/patients/36555444?name=Ana#secret'), '/admin/patients/:item');
  assert.equal(safePath('/Ana-Perez/36555444'), '/other');
  const target = describeTarget({ tagName: 'INPUT', value: '36555444', textContent: 'Ana',
    id: 'private', closest: () => ({}) }, 2);
  assert.deepEqual(target, { tag: 'input', interactive: true, index: 2 });
});
test('dead clicks wait 1.5 seconds, navigation and interactive controls suppress them', () => {
  const click = { kind: 'click', t: 0, target: { interactive: false } };
  assert.equal(detectDeadClicks([click], 1499).length, 0);
  assert.equal(detectDeadClicks([click], 1500).length, 1);
  assert.equal(detectDeadClicks([click, { kind: 'nav', t: 1200 }], 2000).length, 0);
  assert.equal(detectDeadClicks([{ ...click, target: { interactive: true } }], 2000).length, 0);
});
test('resuming advances only through completed tasks', () => {
  assert.equal(resumeIndex([{id:'P1'}, {id:'P2'}], [{task_id:'P1'}]), 1);
  assert.equal(resumeIndex([{id:'P1'}, {id:'P2'}], [{task_id:'P2'}]), 0);
});

test('results distinguish participation, completion, low ease and skipped tasks', () => {
  const data = {sessions:[{id:1,role:'patient',finished_at:'finished'},{id:2,role:'patient',finished_at:null}],
    definition:{roles:{patient:{tasks:[{id:'P1',instruction:'Find a doctor'}]}}},
    results:[{role:'patient',task_id:'P1',outcome:'doubt',ease:2},
      {role:'patient',task_id:'P1',outcome:'stuck',ease:1},
      {role:'doctor',task_id:'P1',outcome:'ok',ease:5}]};
  const summary=summarize(data,'patient');
  assert.equal(summary.participants,2);
  assert.equal(summary.finished,1);
  assert.equal(summary.tasks[0].completed,1);
  assert.equal(summary.tasks[0].ease,1.5);
  assert.equal(summary.tasks[0].stuck,1);
});

test('the story counts people per role and groups findings by what became of them, worst first', () => {
  const data = {definition:{roles:{patient:{tasks:[]},doctor:{tasks:[]},admin:{tasks:[]}}},
    period:{first:'2026-10-14',last:'2026-10-16'},
    sessions:[{role:'patient',finished_at:'x'},{role:'admin',finished_at:null},{role:'admin',finished_at:'y'}],
    findings:[{id:1,status:'fixed',severity:'low'},{id:2,status:'fixed',severity:'high'},
      {id:3,status:'not_done',severity:'medium'},{id:4,status:'dismissed',severity:'low'}]};
  const s = story(data);
  assert.equal(s.people,3); assert.equal(s.finished,2);
  assert.deepEqual(s.roles,{patient:1,doctor:0,admin:2});
  assert.deepEqual(s.groups.fixed.map(f => f.id),[2,1]);
  assert.equal(s.groups.in_progress.length,0);
  assert.equal(s.groups.not_done.length,1);
  assert.equal(s.findings,4);
  assert.deepEqual(s.period,{first:'2026-10-14',last:'2026-10-16'});
});
