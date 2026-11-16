// The picker renders supplied people; the account/selector stories use the
// scoped synthetic account API fixture.
globalThis.SANA_STATIC = Object.assign({}, globalThis.SANA_STATIC, {
  "platform/account-form.mjs": new URL("../static/platform/account-form.mjs", import.meta.url).href,
});
const Dependents = await import("../static/platform/dependents.mjs");
import { accountHolder, installAccountFixture } from "./account-fixture.mjs";

export default {
  title: "Public site/Dependents",
  parameters: { layout: "padded" },
  beforeEach: installAccountFixture,
};

const aptos = [
  { nombre: "Lucía", apellido: "Mora", nacimiento: "2017-04-12", vinculo: "hijo" },
  { nombre: "Tomás", apellido: "Mora", nacimiento: "2019-08-03", vinculo: "hijo" },
];

export const ElegirPersona = () => {
  const host = document.createElement("div");
  Dependents.renderElegir(host, { aptos, onSelect: (person) => { host.dataset.selected = person.nombre; } });
  return host;
};

export const AgregarPersona = () => {
  const host = document.createElement("div");
  Dependents.renderAgregar(host, {
    onAdded: (person) => { host.dataset.added = person.nombre; },
    onCancel: () => { host.dataset.cancelled = "true"; },
  });
  return host;
};

export const Cuenta = () => {
  const host = document.createElement("div");
  Dependents.renderCuenta(host);
  return host;
};

export const Selector = () => {
  const host = document.createElement("div");
  Dependents.renderSelector(host, { usuario: accountHolder, onSelect: (person) => { host.dataset.selected = person.dni; } });
  return host;
};
