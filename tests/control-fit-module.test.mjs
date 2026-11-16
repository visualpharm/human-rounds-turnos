import test from "node:test";
import assert from "node:assert/strict";

const listeners = new Map();
globalThis.document = {
  readyState: "loading",
  createElement(name) {
    assert.equal(name, "canvas");
    return { getContext() { return { font: "", measureText(text) { return { width: text.length * 8 }; } }; } };
  },
  addEventListener(name, callback) { listeners.set(name, callback); },
};
globalThis.window = {};
globalThis.getComputedStyle = () => ({
  fontStyle: "normal", fontVariant: "normal", fontWeight: "400",
  fontSize: "16px", fontFamily: "sans-serif",
  paddingLeft: "8px", paddingRight: "32px",
  borderLeftWidth: "1px", borderRightWidth: "1px",
});

const ControlFit = await import("../static/platform/admin-control-fit.mjs");

test("control-fit module exports the implementation without a compatibility global", () => {
  assert.equal(window.SanaControlFit, undefined);
  assert.equal(typeof ControlFit.fit, "function");
  assert.equal(typeof ControlFit.fitAll, "function");
  assert.equal(typeof ControlFit.boot, "function");
  assert.ok(listeners.has("DOMContentLoaded"));
});

test("fit measures the selected option, so a filter is as wide as what it shows", () => {
  let property = null;
  const select = {
    matches(selector) { return selector === "select.adm-sel"; },
    options: [{ text: "Corto" }, { text: "Una opción bastante larga" }],
    selectedIndex: 1,
    style: { setProperty(name, value) { property = [name, value]; } },
  };
  ControlFit.fit(select);
  assert.deepEqual(property, ["--control-fit-width", "244px"]);
  select.selectedIndex = 0;
  ControlFit.fit(select);
  assert.deepEqual(property, ["--control-fit-width", "84px"]);
});
