// tests/admin-doctor-screens-diet.test.mjs — the doctor's screens say only what
// carries information (2026-11-15).
//
// Mis turnos and Mis pacientes drew a dash in every cell with nothing to say,
// the phone row of Mis turnos captioned the DNI and spelled the age on a line
// of its own, and a doctor's request search offered to find a doctor.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const read = (rel) => fs.readFileSync(new URL("../" + rel, import.meta.url), "utf8");
const css = read("static/root/admin-direct.css").replace(/\/\*[\s\S]*?\*\//g, " ");

test("Mis turnos: a cell with nothing to say is blank and the age rides next to the name on the phone", () => {
  const src = read("static/platform/admin-my-appointments.mjs");
  assert.match(src, /var sinDato = vacia\(html\);\s+if \(sinDato\) html = "";/);
  assert.match(src, /if \(c\.key === "paciente" && conEdad\) \{/);
  assert.match(src, /<span class="mt-edad-junto" data-export-omit>, ' \+ edad \+ "<\/span>"/);
  assert.ok(!src.includes("data-unit"), "the age no longer carries a unit attribute");
  // Hidden in the wide table (the age has its own column there), shown in the phone row.
  assert.ok(css.includes(".mt-edad-junto{display:none;}"));
  assert.ok(css.includes("#sec-mis-turnos .cnt-table td[data-col='edad']{display:none;}"));
  assert.ok(css.includes("#sec-mis-turnos .cnt-table .mt-edad-junto{display:inline;font-weight:400;}"));
  // Only the room keeps a caption: a DNI and a triage colour read by themselves.
  assert.ok(css.includes("#sec-mis-turnos .cnt-table td[data-col='consultorio']::before{content:attr(data-label) \" \";}"));
  assert.ok(!css.includes("#sec-mis-turnos .cnt-table td[data-col='dni']::before"));
});

test("Mis pacientes: no dash for an unset field, and the phone card names its two dates", () => {
  const src = read("static/platform/admin-doctor-patients.mjs");
  assert.ok(!src.includes(">—<"), "no dash placeholder is rendered");
  assert.ok(!src.includes("dp-sin-entrevista"));
  assert.ok(src.includes("#sec-mis-pacientes .cnt-table td:empty{display:none;}"));
  assert.ok(src.includes("td[data-col='entrevista']::before,#sec-mis-pacientes .cnt-table td[data-col='turno']::before{content:attr(data-label)"));
});

test("Mis pacientes on a phone: the row starts on the title's edge, the triage colour closes the name's line", () => {
  // The triage cell comes first in the wide table; on the phone it is not drawn and its
  // chip rides inside the patient cell, so the card's first text sits on the title's edge
  // (admin-left-edge.spec.mjs) and the export does not repeat it in the Paciente column.
  const src = read("static/platform/admin-doctor-patients.mjs");
  assert.ok(src.includes(`'<span class="dp-triage-junto" data-export-omit>' + chip + "</span>"`));
  assert.ok(src.includes(".dp-triage-junto{display:none;}"));
  assert.ok(src.includes("#sec-mis-pacientes .cnt-table td[data-col='triage']{display:none;}"));
  assert.ok(src.includes("#sec-mis-pacientes .cnt-table .dp-triage-junto{display:inline;"));
  assert.ok(!src.includes("td[data-col='triage']{order:"), "no visual reordering of the first DOM cell");
  // The contact icons' line: the first glyph, not its tap box, stands on the card's edge, and
  // the cell does not clip the box that steps back (geometry: admin-left-edge.spec.mjs).
  assert.ok(src.includes("#sec-mis-pacientes .cnt-table td[data-col='contacto']{overflow:visible;}"));
  assert.ok(src.includes("#sec-mis-pacientes .cnt-table td[data-col='contacto']>.cnt-ico-link:first-child{margin-left:-6px;}"));
  assert.ok(src.includes("@media(max-width:640px),(max-width:720px) and (pointer:coarse){#sec-mis-pacientes .cnt-table td[data-col='contacto']>.cnt-ico-link:first-child{margin-left:-12px;}}"));
});

test("Solicitudes: a doctor searches by patient or DNI, the desk by patient or doctor", () => {
  // A doctor sees only its own requests, so «o médico» would find nothing there.
  // The doctor picker and the service/centre columns stay as they are: hiding the
  // picker could send an approval to a colleague's agenda unseen (review 15.11).
  const html = read("templates/admin/solicitudes.html");
  assert.ok(html.includes("{% if rol == 'medico' %}{{ t('admin.queue.filters.buscar_placeholder_medico', 'Paciente o DNI') }}"));
  assert.ok(!read("static/root/admin-app.js").includes("med-propio"));
});

test("Onboarding: the cards keep a 16px edge and the days are said once", () => {
  const html = read("templates/admin/doctor-workspace.html");
  assert.ok(html.includes("{% if onboarding %} dw-workspace--setup{% else %} dw-workspace--agendas{% endif %}"));
  assert.ok(!html.includes("dw-days-readonly"));
  const dw = read("static/platform/doctor-workspace.css");
  assert.ok(dw.includes(".dw-workspace--setup{padding:16px;box-sizing:border-box}"));
  // The finish row adds nothing to that edge: «Guardar y empezar» sits 16px above the bottom.
  assert.ok(dw.includes(".dw-workspace--setup>[data-finish-section]{padding:0}"));
});
