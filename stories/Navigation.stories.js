// The native module auto-boots when evaluated. Retire that page-only lease so
// loading Storybook's index cannot leave an emergency observer on other stories.
import { installNavigationFixture } from "./navigation-fixture.mjs";
const Nav = await import("../static/platform/nav.mjs");
function retireAutoBoot() {
  Nav.mount().dispose();
}
if (document.readyState === "loading") {
  await new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, { once: true }));
}
retireAutoBoot();

let active = null;

function installFixture() {
  const cleanupFixture = installNavigationFixture();
  const trace = window.__HRNavFixture;

  return () => {
    if (active?.trace === trace) {
      active.controller.dispose();
      active.host.remove();
      active = null;
    }
    cleanupFixture();
  };
}

export default {
  title: "Public site/Navigation",
  parameters: { layout: "fullscreen" },
  beforeEach: installFixture,
};

function show(state, section, mode) {
  if (active) active.controller.dispose();
  const trace = window.__HRNavFixture;
  if (!trace) throw new Error("Navigation story fixture was not installed");
  trace.mode = mode;
  localStorage.setItem("st_nav", JSON.stringify(state));
  const host = document.createElement("header");
  host.id = "stznav";
  host.dataset.section = section;
  host.dataset.navMode = mode;
  // Explicit renderer reachability is also checked by the story contract.
  host.innerHTML = Nav.html(location.pathname, section, state);
  // The real controller requires a connected host for layout and its observer.
  document.body.appendChild(host);
  const controller = Nav.mount(host);
  active = { host, controller, trace };
  controller.ready.then(() => new Promise((resolve) => setTimeout(resolve, 0))).then(() => {
    if (active?.controller === controller) host.dataset.navReady = "true";
  });
  return host;
}

export const Anonymous = () => show({
  loggedIn: false, hasSolicitudes: false, iaDown: false,
  iniciales: "", authMode: "password",
}, "medicos", "anonymous");

export const Authenticated = () => show({
  loggedIn: true, hasSolicitudes: true, iaDown: false,
  iniciales: "ME", authMode: "password",
}, "medicos", "authenticated");
