import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const users = fs.readFileSync(new URL("../static/platform/admin-users.mjs", import.meta.url), "utf8");
const content = fs.readFileSync(new URL("../static/platform/admin-content.mjs", import.meta.url), "utf8");
// admin-app.js owns applyRol() — the role-visibility matrix every direct
// screen's controller still syncs through (bootDirectScreen calls it with the
// server-verified role before painting Solicitudes).
const app = fs.readFileSync(new URL("../static/root/admin-app.js", import.meta.url), "utf8");
const usersScreen = fs.readFileSync(new URL("../templates/admin/users.html", import.meta.url), "utf8");
const table = fs.readFileSync(new URL("../static/platform/admin-table.mjs", import.meta.url), "utf8");
const pyUsers = fs.readFileSync(new URL("../app/admin_panel/admin_users.py", import.meta.url), "utf8");
const directCss = fs.readFileSync(new URL("../static/root/admin-direct.css", import.meta.url), "utf8");

test("roles are exactly the four hospital roles in frequency order", () => {
  const block = /var ROLES = \[([\s\S]*?)\n  \];/.exec(users)?.[1] || "";
  const roles = [...block.matchAll(/\["([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(roles, ["medico", "admisionista", "reportes", "admin"]);
  assert.doesNotMatch(block, /operador|marca/);
});

// i18n wraps every ROLE LABEL/DESCRIPTION in
// T(key, vars, "<castellano>") — but the stored value (ROLES[i][0], what
// travels in the POST/PUT body and gets checked against admin_users._ROLES
// and the operadores.rol CHECK in db.py) must stay a bare literal. A
// translated stored value breaks silently: the account saves with a role no
// CHECK/require_admin call recognizes, and nothing here failed before this
// test — the string-array regex above happens to already reject it (T(...)
// doesn't match `["literal`), this test just says so explicitly and also
// cross-checks the places admin_panel/AGENTS.md names.
test("a ROLE's stored value is never routed through T() — only its label/desc are", () => {
  const block = /var ROLES = \[([\s\S]*?)\n  \];/.exec(users)?.[1] || "";
  // Cada entrada es ["<valor-crudo>", T(...), T(...)] — el primer elemento
  // jamás debe ser un T(...) ni ninguna otra llamada a función.
  for (const entry of block.matchAll(/\[\s*"([a-z]+)"\s*,/g)) {
    assert.match(entry[1], /^(medico|admisionista|reportes|admin)$/,
      `stored role value "${entry[1]}" is not one of the canonical ones`);
  }
  assert.doesNotMatch(block, /\[\s*T\(/, "a role's stored value must never be the result of T()");
});

test("the four role values are the same in admin-users.mjs, admin_users.py and applyRol", () => {
  const jsRoles = [...(/var ROLES = \[([\s\S]*?)\n  \];/.exec(users)?.[1] || "")
    .matchAll(/\["([^"]+)"/g)].map((m) => m[1]).sort();
  const pyRoles = [...(/_ROLES = \(([\s\S]*?)\)/.exec(pyUsers)?.[1] || "")
    .matchAll(/"([^"]+)"/g)].map((m) => m[1]).sort();
  const appRoles = [...(/function applyRol[\s\S]*?const esAdmin[\s\S]*?\n(?:.*\n){0,6}/.exec(app)?.[0] || "")
    .matchAll(/rol\s*===\s*'([^']+)'/g)].map((m) => m[1]).sort();
  assert.deepEqual(jsRoles, ["admin", "admisionista", "medico", "reportes"]);
  assert.deepEqual(pyRoles, jsRoles);
  assert.deepEqual(appRoles, jsRoles);
});

test("user list has persisted search, role and status filters", () => {
  assert.match(users, /id="usr-q"/);
  assert.match(users, /id="usr-f-rol"/);
  assert.match(users, /id="usr-f-estado"/);
  assert.match(users, /prefsSet\("users\.filters"/);
  assert.match(users, /prefsSet\("users\.last_role"/);
});

test("doctor list supports selection and all account group actions", () => {
  assert.match(content, /data-med-select/);
  for (const action of ["invite", "disable", "reset-password", "reset-2fa", "enable-intake"]) {
    assert.match(content, new RegExp(`value="${action}"`));
  }
  // El label puede venir directo o envuelto en T(key, vars, "Usuario") (i18n).
  assert.match(content, /tab\("usuario", (?:T\([^)]*?, )?"Usuario"\)/);
  assert.match(content, /Users\.mountDoctorUser/);
  assert.match(content, /ownRouteCleanup\(function \(\) \{ userOwner\.dispose\(\); \}\)/);
  assert.doesNotMatch(content, /SanaUsers|admin-users\.js/);
});

test("Médicos is the doctor-users list: account column, Invitar médico, no login checkmark", () => {
  // Médico = usuario (the product owner, 2026-10-19): one place for doctors and their accounts.
  assert.match(content, /key: "cuenta", label: T\("admin\.content\.catalog\.doctors\.col_account", null, "Cuenta"\), "default": true/);
  assert.doesNotMatch(content, /tiene_login/);
  assert.match(content, /fetch\("\/api\/admin\/users"/);
  assert.match(content, /id="med-invite-open"/);
  assert.match(content, /var doc = \{ _invitar: true \}/);
  assert.match(content, /Users\.showInviteOutcome/);
  assert.match(content, /admin\.content\.catalog\.doctors\.no_name", null, "Médico sin nombre"/);
  assert.match(content, /admin\.content\.catalog\.doctors\.not_on_site/);
});

test("a doctor without access can link an existing unassigned user", () => {
  assert.match(users, /!item\.es_dueno && !item\.medico_id/);
  assert.match(users, /id="doctor-user-existing" name="usuario"/);
  assert.match(users, /"doctor-user-existing"\)\s*\n\s*\+ '<div class="doctor-user-link-actions/);
  assert.match(users, /doctorAction\(medicoId, "link", \{ usuario: username \}, current\)/);
  assert.match(users, /admin\.system\.users\.link_as_doctor/);
  assert.match(users, /admin\.system\.users\.create_copy_access/);
  assert.match(pyUsers, /USER_ACTIONS = frozenset\(\([^)]*"link"/);
  assert.match(pyUsers, /operator_update\([\s\S]*?link_username, rol="medico", medico_id=medico_id, link_only=True\)/);
  assert.match(directCss, /\.doctor-user-link-actions\{[^}]*flex-wrap:wrap/);
  assert.match(directCss, /@media\(max-width:520px\)\{[\s\S]*?\.doctor-user-link-actions\{[^}]*margin-left:0/);
});

test("dense lists use the page width and table widths are server-backed", () => {
  // The direct /admin/users screen is the live surface that renders the
  // user list; the wide-list CSS contract now lives in the direct shared CSS.
  assert.match(usersScreen, /platform\/admin-users\.mjs/);
  assert.doesNotMatch(usersScreen, /admin-users\.js/);
  assert.match(directCss, /\[data-list-view="1"\]/);
  assert.match(table, /Prefs\.set/);
  assert.doesNotMatch(table, /localStorage\.setItem/);
});
