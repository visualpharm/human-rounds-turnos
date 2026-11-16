// Number agreement (05.11: the panel said «1 solicitudes», «1 libres»).
// static/platform/i18n.mjs's pluralCategory()/tn() are the one rule for
// every «N + noun» string; core/i18n.py's plural_category()/tn_or() are its
// server twin, pinned to the same table (tests/fixtures/plural-one.json).
// Every Tn("key", …) call in the shipped JS must name a key whose plural
// AND singular exist in the Spanish and English catalogs.
import { describe, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { tn, pluralCategory, setCatalog } from "../static/platform/i18n.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TABLE = JSON.parse(readFileSync(join(ROOT, "tests", "fixtures", "plural-one.json"), "utf8"));

describe("pluralCategory follows the shared table", () => {
  // Russian and Ukrainian are compared category by category; every other
  // language only on one/other (no catalog there carries _few/_many).
  for (const [lang, ones] of Object.entries(TABLE.one)) {
    test(lang, () => {
      const few = (TABLE.few || {})[lang], many = (TABLE.many || {})[lang];
      for (const n of TABLE.numbers) {
        if (few) {
          const want = ones.includes(n) ? "one" : few.includes(n) ? "few" : many.includes(n) ? "many" : "other";
          assert.equal(pluralCategory(n, lang), want, `${lang} ${n}`);
          continue;
        }
        const want = ones.includes(n) ? "one" : "other";
        const got = pluralCategory(n, lang) === "one" ? "one" : "other";
        assert.equal(got, want, `${lang} ${n}`);
      }
    });
  }

  test("a formatted number or a non-number", () => {
    assert.equal(pluralCategory("1", "es"), "one");
    assert.equal(pluralCategory("1,0", "es"), "one");
    assert.equal(pluralCategory("1,5", "es"), "other");
    assert.equal(pluralCategory(null, "es"), "other");
    assert.equal(pluralCategory("—", "es"), "other");
    assert.equal(pluralCategory(true, "es"), "other");
  });
});

describe("tn() picks the form", { concurrency: false }, () => {
  test("the catalog's _one for 1, the base key otherwise", () => {
    setCatalog({ "x.libres": "{n} libres", "x.libres_one": "{n} libre" });
    assert.equal(tn("x.libres", 1), "1 libre");
    assert.equal(tn("x.libres", 2), "2 libres");
    assert.equal(tn("x.libres", 0), "0 libres");
  });

  test("the Spanish fallbacks when the catalog has neither", () => {
    setCatalog({});
    assert.equal(tn("x.none", 1, null, "{n} turno", "{n} turnos"), "1 turno");
    assert.equal(tn("x.none", 3, null, "{n} turno", "{n} turnos"), "3 turnos");
  });

  test("a caller's formatted n and other vars are filled", () => {
    setCatalog({ "x.c": "Día {day}: {n} turnos", "x.c_one": "Día {day}: {n} turno" });
    assert.equal(tn("x.c", 1, { day: 4 }), "Día 4: 1 turno");
    assert.equal(tn("x.c", 1200, { day: 4, n: "1.200" }), "Día 4: 1.200 turnos");
  });

  test("a whole singular phrase is used only for exactly 1", () => {
    setCatalog({ "x.q": "¿Aprobar las {n} solicitudes?", "x.q_one": "¿Aprobar la solicitud?" });
    assert.equal(tn("x.q", 1), "¿Aprobar la solicitud?");
    assert.equal(tn("x.q", 2), "¿Aprobar las 2 solicitudes?");
    assert.equal(tn("x.q", "1,0"), "¿Aprobar la solicitud?");
  });

  test("Russian: _few for 2–4, _many for 5–20, _one for 21, the base for a fraction", () => {
    setCatalog({
      "x.f": "{n} files-other", "x.f_one": "{n} file-one", "x.f_few": "{n} files-few", "x.f_many": "{n} files-many",
    }, "ru");
    try {
      assert.equal(tn("x.f", 1), "1 file-one");
      assert.equal(tn("x.f", 2), "2 files-few");
      assert.equal(tn("x.f", 4), "4 files-few");
      assert.equal(tn("x.f", 5), "5 files-many");
      assert.equal(tn("x.f", 12), "12 files-many");
      assert.equal(tn("x.f", 21), "21 file-one");
      assert.equal(tn("x.f", 22), "22 files-few");
      assert.equal(tn("x.f", 0), "0 files-many");
      assert.equal(tn("x.f", 1.5), "1,5 files-other");
    } finally {
      setCatalog({}, "es");
    }
  });

  test("a bare unit word serves every «one» count; a whole phrase only 1", () => {
    // 2026-11-06: the demo printed the Russian plural «doctors» after 21.
    setCatalog({ "x.d": "vrachi", "x.d_one": "vrach", "x.d_few": "vracha", "x.d_many": "vrachei",
      "x.q": "Odobrit {n} zayavki?", "x.q_one": "Odobrit zayavku?" }, "ru");
    try {
      assert.equal(tn("x.d", 1), "vrach");
      assert.equal(tn("x.d", 21), "vrach");
      assert.equal(tn("x.d", 31), "vrach");
      assert.equal(tn("x.d", 22), "vracha");
      assert.equal(tn("x.d", 25), "vrachei");
      assert.equal(tn("x.q", 1), "Odobrit zayavku?");
      assert.equal(tn("x.q", 21), "Odobrit 21 zayavki?");
      setCatalog({ "x.d": "médecins", "x.d_one": "médecin" }, "fr");
      assert.equal(tn("x.d", 0), "médecin");
    } finally {
      setCatalog({}, "es");
    }
  });

  // Every counted pair of the shared Spanish catalog, read through the real
  // Russian and Ukrainian catalogs: 21 takes the singular with its own
  // number, 2/22 the «few» form, 5/25 the «many» form.
  for (const lang of ["ru", "uk"]) {
    test(`${lang}: 1, 2, 5, 21, 22, 25 through the real catalog`, () => {
      const es = sharedCatalog("es");
      const cat = sharedCatalog(lang);
      const form = { 1: "_one", 2: "_few", 5: "_many", 21: "_one", 22: "_few", 25: "_many" };
      const fill = (text, n) => text.split("{n}").join(String(n));
      setCatalog(cat, lang);
      let checked = 0;
      try {
        for (const one of Object.keys(es).filter((k) => k.endsWith("_one"))) {
          const key = one.slice(0, -4);
          if (!(key in es) || !(key + "_few" in cat) || !(key + "_many" in cat) || !(one in cat)) continue;
          // Only a singular that names its number or is a bare unit word.
          const bare = !/\{[A-Za-z_]+\}/.test(es[one]) && es[one].trim().split(/\s+/).length <= 2 && !/[.,;:?!¿¡]/.test(es[one]);
          if (!/\{n\}/.test(es[one]) && !bare) continue;
          for (const n of [1, 2, 5, 21, 22, 25]) {
            const want = fill(cat[key + form[n]], n);
            const got = tn(key, n);
            if (/\{n\}/.test(cat[key + form[n]])) {
              assert.equal(got, want, `${lang} ${key} ${n}`);
              assert.ok(got.includes(String(n)), `${lang} ${key} ${n}: ${got}`);
            } else if (bare) {
              assert.equal(got, cat[key + form[n]], `${lang} ${key} ${n}`);
            }
          }
          checked++;
        }
      } finally {
        setCatalog({}, "es");
      }
      assert.ok(checked >= 50, `only ${checked} pairs checked`);
      // The doctor count of the directory and the specialty pages.
      setCatalog(cat, lang);
      try {
        assert.equal(tn("directory.doctor", 21), cat["directory.doctor_one"]);
        assert.equal(tn("directory.doctor", 5), cat["directory.doctor_many"]);
      } finally {
        setCatalog({}, "es");
      }
    });
  }

  test("a language without _few/_many stays on the base key («many» of a million)", () => {
    setCatalog({ "x.m": "{n} turnos", "x.m_one": "{n} turno" }, "es");
    assert.equal(tn("x.m", 1000000), "1.000.000 turnos");
    setCatalog({ "x.m": "{n} records", "x.m_one": "{n} record" }, "ru");
    try {
      assert.equal(tn("x.m", 3), "3 records");
    } finally {
      setCatalog({}, "es");
    }
  });
});

// The shipped Tn calls: root *.js/*.mjs, static/platform, modules/*.
function jsFiles() {
  const out = [];
  const add = (dir, rel) => {
    if (!existsSync(dir) || !statSync(dir).isDirectory()) return;
    for (const f of readdirSync(dir)) {
      const p = join(dir, f);
      if ((f.endsWith(".js") || f.endsWith(".mjs")) && statSync(p).isFile()) out.push(join(rel, f));
    }
  };
  add(ROOT, "");
  add(join(ROOT, "static", "platform"), join("static", "platform"));
  for (const id of readdirSync(join(ROOT, "app", "modules"))) add(join(ROOT, "app", "modules", id), join("app", "modules", id));
  return out;
}

function catalog(lang) {
  const out = {};
  const dirs = [join(ROOT, "locales", lang)];
  for (const id of readdirSync(join(ROOT, "app", "modules"))) dirs.push(join(ROOT, "app", "modules", id, "locales", lang));
  for (const dir of dirs.filter((d) => existsSync(d) && statSync(d).isDirectory())) {
    for (const f of readdirSync(dir).filter((x) => x.endsWith(".json") && !x.startsWith("_"))) {
      Object.assign(out, JSON.parse(readFileSync(join(dir, f), "utf8")));
    }
  }
  return out;
}

describe("every Tn key has both forms in es and en", () => {
  const catalogs = { es: catalog("es"), en: catalog("en") };
  // A literal key only: a computed one ('study.evidence.' + kind) is skipped.
  const re = /\bTn\(\s*["']([a-z0-9_.]+)["']\s*,/g;
  const keys = new Set();
  for (const file of jsFiles()) {
    const src = readFileSync(join(ROOT, file), "utf8");
    let m;
    while ((m = re.exec(src)) !== null) keys.add(m[1] + " " + file);
  }

  test("the sweep found its call sites", () => {
    assert.ok(keys.size >= 30, `only ${keys.size} Tn calls found`);
  });

  for (const lang of ["es", "en"]) {
    test(lang, () => {
      for (const entry of keys) {
        const [key, file] = entry.split(" ");
        assert.ok(key in catalogs[lang], `${file}: ${key} missing in ${lang}`);
        assert.ok(key + "_one" in catalogs[lang], `${file}: ${key}_one missing in ${lang}`);
      }
    });
  }

  // scripts/translate_locales.py derives `_few`/`_many` for Russian and
  // Ukrainian from every counted pair of the shared catalog (the module
  // catalogs are Spanish/English only and fall back to Spanish).
  const shared = sharedCatalog("es");
  for (const lang of ["ru", "uk"]) {
    test(`${lang} has _few and _many`, () => {
      const cat = sharedCatalog(lang);
      for (const entry of keys) {
        const [key, file] = entry.split(" ");
        if (!(key in shared)) continue;
        assert.ok(key + "_few" in cat, `${file}: ${key}_few missing in ${lang}`);
        assert.ok(key + "_many" in cat, `${file}: ${key}_many missing in ${lang}`);
      }
    });
  }
});

function sharedCatalog(lang) {
  const out = {};
  const dir = join(ROOT, "locales", lang);
  for (const f of readdirSync(dir).filter((x) => x.endsWith(".json") && !x.startsWith("_"))) {
    Object.assign(out, JSON.parse(readFileSync(join(dir, f), "utf8")));
  }
  return out;
}
