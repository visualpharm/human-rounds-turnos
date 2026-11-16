import * as Roadmap from "../static/platform/admin-roadmap.mjs";
import {
  installEditorialFixture, mountEditorialStoryWhenAttached,
  setEditorialStoryRoute,
} from "./admin-editorial-fixture.mjs";
import { applyEditorialStyles } from "./admin-editorial-styles.mjs";

export default {
  title: "Admin content/Roadmap",
  parameters: { layout: "padded" },
  beforeEach: installEditorialFixture,
};

function mountRoadmap(host, trace) {
  return () => {
    const dispose = Roadmap.mount(host, { emrSync: false });
    trace.dispose = dispose;
    return dispose;
  };
}

export const LaunchPlan = () => {
  setEditorialStoryRoute("/admin/plan");
  const trace = window.__HREditorialFixture;
  trace.controller = Roadmap;
  const root = document.createElement("section");
  applyEditorialStyles(root);
  const heading = document.createElement("h1");
  heading.id = "adm-h1";
  heading.textContent = "Roadmap";
  const host = document.createElement("div");
  host.id = "sec-plan";
  root.append(heading, host);
  mountEditorialStoryWhenAttached(root, mountRoadmap(host, trace));
  return root;
};
