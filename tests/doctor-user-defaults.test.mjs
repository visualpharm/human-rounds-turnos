import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../static/platform/admin-users.mjs", import.meta.url), "utf8");
const helpers = source.slice(source.indexOf("  function doctorUserDefaults("), source.indexOf("  function showDoctorCredentials("));
const context = vm.createContext({ T: (_key, _vars, fallback) => fallback, Blob, window: {}, navigator: {} });
vm.runInContext(helpers, context);
const defaults = (doctor, items) => JSON.parse(JSON.stringify(context.doctorUserDefaults(doctor, items)));
const account = (usuario, extra = {}) => ({ usuario, rol: "admin", ...extra });

test("prefills an available accented name and uniquely matching first/last account", () => {
  const result = defaults({ nombre: "Dr. Diégo Arturo Rivera" }, [account("diego.rivera"), account("diego.rivera2"), account("rosa.rivero")]);
  assert.equal(result.match, "diego.rivera");
  assert.equal(result.username, "diego.rivera3");
  assert.equal(result.candidates[0].usuario, "diego.rivera");
});
test("exact email wins over name; equal matches stay unselected", () => {
  const doctor = { nombre: "María José Fernández", email: " Maria@Example.com " };
  const items = [account("maria.fernandez"), account("cuenta", { email: "maria@example.com" })];
  assert.equal(defaults(doctor, items).match, "cuenta");
  assert.equal(defaults(doctor, [...items, account("segunda", { email: doctor.email })]).match, "");
  assert.equal(defaults({ nombre: doctor.nombre }, [account("maria.fernandez"), account("maria.jose.fernandez")]).match, "");
});
test("never guesses from one name, a partial token, owner, or another doctor's account", () => {
  const doctor = { nombre: "Diego Arturo Rivera" };
  const items = [account("diego"), account("diego.rivera99"), account("diego.rivera", { es_dueno: true }), account("diegoarturoRivera", { medico_id: "another" })];
  assert.equal(defaults(doctor, items).match, "");
  assert.equal(defaults(doctor, items).candidates.length, 2);
  assert.equal(defaults({ nombre: "Diego" }, [account("diego")]).match, "");
});
test("supports blank/short/long names and detects inactive matching accounts without enabling them", () => {
  for (const nombre of ["", "Li", "A".repeat(90)]) {
    assert.match(defaults({ nombre }, []).username, /^[a-z0-9_.-]{3,40}$/);
  }
  const result = defaults({ nombre: "Lucía Torres" }, [account("lucia.torres", { activo: false })]);
  assert.equal(result.match, "lucia.torres");
  assert.equal(result.candidates[0].activo, false);
});
test("starts clipboard write during click and resolves it with generated credentials", async () => {
  let resolve, blobPromise, called = false;
  const result = new Promise(r => { resolve = r; });
  context.ClipboardItem = context.window.ClipboardItem = class { constructor(data) { blobPromise = data["text/plain"]; } };
  context.navigator.clipboard = { write: async () => { called = true; await blobPromise; } };
  const copying = context.copyDoctorCredentials(result);
  assert.equal(called, true);
  resolve({ usuario: "test.user", password: "test-access" });
  assert.equal(await copying, true);
  assert.equal(await (await blobPromise).text(), "Usuario: test.user\nContraseña: test-access");
});
test("doctor access fallbacks match the canonical catalog, which is omitted on Spanish pages", () => {
  // The whole Spanish catalog: the admin's calendar confirm reuses the doctor's
  // own «Mis turnos» wording (admin.queue.my_appointments.*) word for word.
  const dir = new URL("../locales/es/", import.meta.url);
  const catalog = Object.assign({}, ...fs.readdirSync(dir).filter((name) => name.endsWith(".json"))
    .map((name) => JSON.parse(fs.readFileSync(new URL(name, dir), "utf8"))));
  const component = source.slice(source.indexOf("  function credentialText("), source.indexOf("  // routing propio"));
  for (const [, key, fallback] of component.matchAll(/T\("([^\"]+)", null, "([^\"]*)"\)/g)) {
    assert.equal(fallback, catalog[key], key);
  }
});
test("clipboard denial, missing API and creation failure are recoverable", async () => {
  context.navigator.clipboard = { write: () => Promise.reject(new Error("denied")) };
  assert.equal(await context.copyDoctorCredentials(Promise.resolve({ usuario: "test.user", password: "test-access" })), false);
  context.window.ClipboardItem = undefined;
  context.navigator.clipboard = undefined;
  assert.equal(await context.copyDoctorCredentials(Promise.resolve({ usuario: "test.user", password: "test-access" })), false);
  assert.equal(await context.copyDoctorCredentials(Promise.reject(new Error("creation failed"))), false);
});
