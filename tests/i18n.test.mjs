// i18n.test.mjs — canonical client lookup (static/platform/i18n.mjs): catalog
// lookup, {name} substitution, empty-string translations, setCatalog, and
// failed load() adopting {}. Singleton module; tests in this file must not
// run concurrently.
import { describe, test } from "node:test";
import assert from "node:assert/strict";
import { t, setCatalog, load, lang } from "../static/platform/i18n.mjs";

describe("canonical platform/i18n.mjs", { concurrency: false }, () => {
  test("with an adopted catalog, t() resolves without a network round trip", () => {
    setCatalog({ "about.hero.intro": "About" });
    assert.equal(t("about.hero.intro"), "About");
    assert.equal(typeof lang, "string");
  });

  test("a missing key returns the key itself when there is no fallback", () => {
    setCatalog({});
    assert.equal(t("no.such.key"), "no.such.key");
  });

  test("a missing key returns the Spanish fallback when one is supplied", () => {
    setCatalog({});
    assert.equal(t("no.such.key", null, "el literal"), "el literal");
  });

  test("substitutes {name} variables", () => {
    setCatalog({ greet: "Hola {name}!" });
    assert.equal(t("greet", { name: "Ilya" }), "Hola Ilya!");
  });

  test("an empty string is a real translation, not a missing key", () => {
    setCatalog({ "date.article": "" });
    assert.equal(t("date.article", null, "el"), "");
  });

  test("setCatalog replaces the active catalog", () => {
    setCatalog({ x: "uno" });
    assert.equal(t("x"), "uno");
    setCatalog({ x: "dos" });
    assert.equal(t("x"), "dos");
  });

  test("failed fetch adopts an empty catalog and still resolves", async () => {
    const previous = global.window;
    global.window = {
      document: { documentElement: { getAttribute: () => "es" } },
      fetch: async () => { throw new Error("offline"); },
    };
    try {
      setCatalog({ keep: "no" });
      const data = await load("en");
      assert.deepEqual(data, {});
      assert.equal(t("keep", null, "fallback"), "fallback");
    } finally {
      global.window = previous;
      setCatalog({});
    }
  });

  test("a non-ok fetch response adopts an empty catalog", async () => {
    const previous = global.window;
    global.window = {
      document: { documentElement: { getAttribute: () => "es" } },
      fetch: async () => ({ ok: false, json: async () => ({ x: "no" }) }),
    };
    try {
      const data = await load("en");
      assert.deepEqual(data, {});
    } finally {
      global.window = previous;
      setCatalog({});
    }
  });
});
