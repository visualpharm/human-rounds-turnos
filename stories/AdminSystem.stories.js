import * as System from "../static/platform/admin-system.mjs";
import {
  installEditorialFixture, mountEditorialStoryWhenAttached,
  setEditorialStoryRoute, setEditorialSystemStatus,
} from "./admin-editorial-fixture.mjs";
import { applyEditorialStyles } from "./admin-editorial-styles.mjs";

export default {
  title: "Admin content/System",
  parameters: { layout: "padded" },
  beforeEach: installEditorialFixture,
};

function mountSystem(host, trace) {
  return () => {
    const dispose = System.mount(host);
    trace.dispose = dispose;
    return dispose;
  };
}

function systemStory(status) {
  setEditorialStoryRoute("/admin/system");
  setEditorialSystemStatus(status);
  const trace = window.__HREditorialFixture;
  trace.controller = System;
  const root = document.createElement("section");
  applyEditorialStyles(root);
  const heading = document.createElement("h1");
  heading.id = "adm-h1";
  heading.textContent = "System";
  const badge = document.createElement("span");
  badge.id = "nav-sistema-badge";
  badge.style.display = "none";
  const host = document.createElement("div");
  host.id = "sec-sistema";
  root.append(heading, badge, host);
  mountEditorialStoryWhenAttached(root, mountSystem(host, trace));
  return root;
}

export const Integrations = () => systemStory(200);
export const Unavailable = () => systemStory(403);
export const Unauthorized = () => systemStory(401);
