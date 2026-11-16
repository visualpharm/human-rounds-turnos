// All three examples invoke the canonical account form renderers. Its own
// injected styles and the shared Storybook patient-site stylesheet apply.
import * as AccountForm from "../static/platform/account-form.mjs";
import { accountHolder, installAccountFixture } from "./account-fixture.mjs";

export default {
  title: "Public site/Account form",
  parameters: { layout: "padded" },
  beforeEach: installAccountFixture,
};

function host() {
  const node = document.createElement("div");
  node.style.maxWidth = "800px";
  return node;
}

export const Login = () => {
  const node = host();
  AccountForm.renderAuth(node, {
    defaultMode: "login",
    prefill: { dni: accountHolder.dni, email: accountHolder.email },
    onDone: () => { node.dataset.authDone = "true"; },
  });
  return node;
};

export const EditAccount = () => {
  const node = host();
  AccountForm.renderEditar(node, {
    usuario: { ...accountHolder },
    onSave: () => { node.dataset.saved = "true"; },
    afterGuardar: () => {},
  });
  // The account screen selects the active rail card after mounting this form.
  node.querySelector('.sc-card[data-panel="datos"]')?.classList.add("on");
  return node;
};

export const ResetPassword = () => {
  const node = host();
  AccountForm.renderReset(node, { token: "sb-expired", onDone: () => { node.dataset.resetDone = "true"; } });
  return node;
};
