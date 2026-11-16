import * as Prompts from "../static/platform/admin-prompts.mjs";
import { installEditorialFixture, mountEditorialStoryWhenAttached, reportEditorialMountError, setEditorialStoryRoute } from "./admin-editorial-fixture.mjs";
import { applyEditorialStyles } from "./admin-editorial-styles.mjs";

export default {
  title: "Admin content/Prompts",
  parameters: { layout: "padded" },
  beforeEach: installEditorialFixture,
};

function mountPrompts(status, trace) {
  return () => {
    Prompts.mount()?.catch((error) => reportEditorialMountError(error, status, trace));
    return Prompts.destroy;
  };
}

export const Instructions = () => {
  setEditorialStoryRoute("/admin/prompts");
  const trace = window.__HREditorialFixture;
  trace.controller = Prompts;
  const root = document.createElement("section");
  applyEditorialStyles(root);
  const heading = document.createElement("h1");
  heading.id = "adm-h1";
  const host = document.createElement("div");
  host.id = "sec-prompts";
  const status = document.createElement("div");
  status.id = "prompts-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.hidden = true;
  root.append(heading, host, status);
  mountEditorialStoryWhenAttached(root, mountPrompts(status, trace));
  return root;
};
