// «Pacientes de hoy» on a phone (the product owner, 2026-11-09): one patient in two short
// lines. The first holds the time, «Name, N años» (one link to the card), the
// history bars and «Orden»; the second the doctor with the service, at the
// name's left edge. No DNI on the row any more (it lives in the card), so the
// dot that followed it (09.11) is gone with it. Pinned by selector and the
// declaration that matters.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const css = fs.readFileSync(new URL('../static/platform/admin-queue.css', import.meta.url), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, ' ');

const PHONE = '@media(max-width:640px){';

function phoneBody(sel) {
  const i = css.indexOf(sel + '{');
  assert.notEqual(i, -1, 'missing rule ' + sel);
  const media = css.lastIndexOf('@media', i);
  assert.equal(css.slice(media, media + PHONE.length), PHONE, sel + ' must sit in the phone card block');
  const start = i + sel.length + 1;
  return css.slice(start, css.indexOf('}', start));
}

test('the row wraps: the time and the patient share the first line', () => {
  assert.match(phoneBody('.queue-table tr:not(:has(>td.queue-now-td))'), /display:flex;flex-wrap:wrap;align-items:baseline;/);
  assert.match(phoneBody(".queue-table tr>td[data-col='hora']"), /flex:0 0 auto;/);
  assert.match(phoneBody(".queue-table tr>td[data-col='paciente']"), /flex:1 1 calc\(100% - 96px\);/);
});

test('every other cell starts at the name, not under the time', () => {
  assert.match(phoneBody(".queue-table tr:has(>td[data-col='hora']) td:not([data-col='hora'],[data-col='paciente'])"),
    /padding-left:var\(--queue-hour-w,56px\);/);
});

test('the name, the bars and «Orden» stay one line that wraps as a whole', () => {
  assert.match(css, /\.queue-pat-line\{[^}]*display:inline-flex;[^}]*flex-wrap:wrap;/);
  assert.match(css, /\.queue-orden\{display:inline-flex;[^}]*white-space:nowrap;/);
});

test('no rule for the DNI column or its dot is left in the queue', () => {
  assert.doesNotMatch(css, /td\[data-col='dni'\]|dni-cp-ico/);
  assert.doesNotMatch(css, /td\[data-col='servicio'\]/);
});
