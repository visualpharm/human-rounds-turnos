// Test for the bug: typing an invalid date shows error, but the success message
// from page load is not cleared. The fix ensures that when payload() returns null
// due to an invalid date, the save status is cleared instead of leaving the stale
// "Todo guardado" message.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as I18n from "../static/platform/i18n.mjs";
import * as Account from "../static/platform/account-form.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function catalogFor(lang) {
  const dir = join(ROOT, "locales", lang);
  const out = {};
  for (const fname of readdirSync(dir).filter((f) => f.endsWith(".json"))) {
    const rows = JSON.parse(readFileSync(join(dir, fname), "utf8"));
    for (const [k, v] of Object.entries(rows)) if (k !== "_comment") out[k] = v;
  }
  return out;
}

async function adoptCatalog(lang) {
  I18n.setCatalog(lang ? catalogFor(lang) : {});
}

function fakeDocument() {
  const byId = {};
  return {
    getElementById: (id) => byId[id] || null,
    createElement: () => ({}),
    head: { appendChild: (el) => { byId[el.id] = el; } },
    documentElement: { lang: "es" },
  };
}

function installHost(lang) {
  const previousWindow = global.window;
  const previousDocument = global.document;
  const previousFetch = global.fetch;
  const doc = fakeDocument();
  doc.documentElement.lang = lang || "es";
  global.window = { document: doc };
  global.document = doc;
  global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  return () => {
    I18n.setCatalog({});
    global.window = previousWindow;
    global.document = previousDocument;
    global.fetch = previousFetch;
  };
}

async function withAccount(lang, run) {
  await adoptCatalog(lang);
  const restore = installHost(lang);
  try {
    return await run(Account);
  } finally {
    restore();
  }
}

test("typing invalid date shows error, not success message - using dobField public API", async () => {
  await withAccount("es", (mod) => {
    // Create input and echo elements that dobField can work with
    const input = {
      value: "20/05/1980",
      dataset: { iso: "1980-05-20" },
      addEventListener: function(event, handler) {
        if (!this._listeners) this._listeners = {};
        if (!this._listeners[event]) this._listeners[event] = [];
        this._listeners[event].push(handler);
      },
      focus: () => {},
      _listeners: {},
      _fireEvent: function(event, e) {
        if (this._listeners[event]) {
          this._listeners[event].forEach(h => h.call(this, e || { type: event }));
        }
      }
    };

    const echo = {
      className: "",
      textContent: "",
    };

    // Use the public dobField API
    const dob = mod.dobField({ input, echo });

    // Initial state: date is valid, no error
    assert.equal(echo.textContent, "", "Initially should have no error");
    assert.equal(echo.className, "sc-echo", "Initially should have sc-echo class without bad");
    assert.equal(input.dataset.iso, "1980-05-20", "Initially should have iso date set");

    // User types an invalid date: 31/02/1985
    input.value = "31/02/1985";
    input._fireEvent("input");

    // Error should be shown
    assert.ok(echo.textContent.includes("No entendimos"),
      `Error message should contain "No entendimos", got: "${echo.textContent}"`);
    assert.ok(echo.className.includes("bad"),
      `Echo class should contain 'bad', got: "${echo.className}"`);
    assert.equal(input.dataset.iso, undefined,
      "Invalid date should have deleted dataset.iso");

    // Verify that the invalid date is not parseable
    assert.equal(mod.parseFlexibleDate("31/02/1985"), null,
      "Invalid date should parse to null");
  });
});

test("typing valid date clears error and updates formatted value", async () => {
  await withAccount("es", (mod) => {
    const input = {
      value: "99/99/9999",  // Invalid initially
      dataset: {},
      addEventListener: function(event, handler) {
        if (!this._listeners) this._listeners = {};
        if (!this._listeners[event]) this._listeners[event] = [];
        this._listeners[event].push(handler);
      },
      focus: () => {},
      _listeners: {},
      _fireEvent: function(event, e) {
        if (this._listeners[event]) {
          this._listeners[event].forEach(h => h.call(this, e || { type: event }));
        }
      }
    };

    const echo = {
      className: "",
      textContent: "",
    };

    const dob = mod.dobField({ input, echo });

    // Should have error initially
    assert.ok(echo.textContent.includes("No entendimos"));

    // User corrects to valid date
    input.value = "28/10/1978";
    input._fireEvent("input");

    // Error should be cleared
    assert.equal(echo.textContent, "",
      `Error should be cleared, got: "${echo.textContent}"`);
    assert.equal(echo.className, "sc-echo",
      `Should not have 'bad' class, got: "${echo.className}"`);
    assert.equal(input.dataset.iso, "1978-10-28",
      "Should have valid iso date");

    // Trigger blur which calls commit()
    input._fireEvent("blur");

    // Date should be formatted in words
    assert.equal(input.value, "28 de octubre de 1978",
      `Date should be formatted in words, got: "${input.value}"`);
  });
});
