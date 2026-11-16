import * as Preparations from "../static/platform/admin-preparations.mjs";
import { installEditorialFixture, mountEditorialStoryWhenAttached, reportEditorialMountError, setEditorialStoryRoute } from "./admin-editorial-fixture.mjs";
import { applyEditorialStyles } from "./admin-editorial-styles.mjs";

export default {
  title: "Admin content/Preparations",
  parameters: { layout: "padded" },
  beforeEach: installEditorialFixture,
};

function mountPreparations(host, heading, trace) {
  return () => {
    const dispose = Preparations.mount(host, { titleElement: heading });
    dispose.ready.catch((error) => reportEditorialMountError(error, host, trace));
    return dispose;
  };
}

export const Catalog = () => {
  setEditorialStoryRoute("/admin/preparations");
  const trace = window.__HREditorialFixture;
  const root = document.createElement("section");
  applyEditorialStyles(root);
  const heading = document.createElement("h1");
  heading.id = "adm-h1";
  const host = document.createElement("div");
  host.id = "sec-preparaciones";
  root.append(heading, host);
  mountEditorialStoryWhenAttached(root, mountPreparations(host, heading, trace));
  return root;
};
