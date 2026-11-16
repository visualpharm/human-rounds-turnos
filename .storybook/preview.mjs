import "../static/root/human-rounds.css";
import accountTemplate from "../templates/account.html?raw";
import country from "../packs/ar/country.json";

// Admin editorial controllers import the canonical formatter at evaluation.
// The live admin shell publishes this active-pack field before those imports.
globalThis.SANA_COUNTRY_PHONE = country.phone;

// The patient Account shell owns .dcard, not the global stylesheet. Reuse
// its actual rule for the Labs story so the module's full-bleed table margins
// sit against the same 28px padding without copying a stylesheet by hand.
const accountCardRules = [...accountTemplate.matchAll(/\.(?:dcard|dcard-p(?: a(?::hover)?)?)\{[^{}]*\}/g)]
  .map(([rule]) => rule);
if (!accountCardRules.some((rule) => rule.startsWith(".dcard{"))) {
  throw new Error("Account card style is missing from templates/account.html");
}
const accountCardStyle = document.createElement("style");
accountCardStyle.textContent = accountCardRules.join("\n");
document.head.appendChild(accountCardStyle);

// Mirror the server's inline i18n bootstrap before native modules evaluate.
// Storybook has no `/api/i18n/*` endpoint; importing the committed catalog
// files keeps preview text on the real English catalog path rather than
// silently exercising Spanish call-site fallbacks.
const catalogModules = import.meta.glob("../locales/en/*.json", {
  eager: true,
  import: "default",
});
const catalog = Object.assign({}, ...Object.values(catalogModules));
document.documentElement.lang = "en";
window.SANA_I18N = Object.assign({}, window.SANA_I18N, {
  active: "en",
  catalog,
});

// Native listing/search/hours/dates TLA-import through window.SANA_STATIC in
// the browser (same fail-closed map the patient shell publishes). Storybook
// has no server manifest, so the preview supplies Vite URLs before any
// story module evaluates. Do not add a relative fallback inside those
// modules — that would hide a missing production manifest.
window.SANA_STATIC = Object.assign({}, window.SANA_STATIC, {
  "platform/i18n.mjs": new URL("../static/platform/i18n.mjs", import.meta.url).href,
  "platform/search.mjs": new URL("../static/platform/search.mjs", import.meta.url).href,
  "platform/listing.mjs": new URL("../static/platform/listing.mjs", import.meta.url).href,
  "platform/chat-ui.mjs": new URL("../static/platform/chat-ui.mjs", import.meta.url).href,
  "platform/hours.mjs": new URL("../static/platform/hours.mjs", import.meta.url).href,
  "platform/dates.mjs": new URL("../static/platform/dates.mjs", import.meta.url).href,
  "platform/prep.mjs": new URL("../static/platform/prep.mjs", import.meta.url).href,
  "platform/format.mjs": new URL("../static/platform/format.mjs", import.meta.url).href,
  "platform/admin-session.mjs": new URL("../static/platform/admin-session.mjs", import.meta.url).href,
  "platform/admin-table.mjs": new URL("../static/platform/admin-table.mjs", import.meta.url).href,
});
// admin-preferences statically imports its ./i18n.mjs sibling, which a raw
// asset URL cannot resolve after build. Publish the Vite-bundled production
// exports through a tiny Storybook-only ESM bridge instead.
globalThis.__HRStoryAdminPreferences = await import("../static/platform/admin-preferences.mjs");
window.SANA_STATIC["platform/admin-preferences.mjs"] = "data:text/javascript," + encodeURIComponent(
  "const m=globalThis.__HRStoryAdminPreferences;export const load=m.load,get=m.get,set=m.set;");
window.sanaImportStatic = (name) => import(window.SANA_STATIC[name]);

// The catalog has no admin backend. Keep shared column preferences local to
// this preview rather than issuing GET/PUT requests to nonexistent APIs.
const storyPreferences = {};
const storyFetch = window.fetch.bind(window);
window.fetch = (input, options) => {
  const url = typeof input === "string" ? input : input.url;
  if (new URL(url, window.location.href).pathname !== "/api/admin/preferences") return storyFetch(input, options);
  if (options?.method === "PUT") {
    const { key, value } = JSON.parse(options.body || "{}");
    storyPreferences[key] = value;
  }
  return Promise.resolve(new Response(JSON.stringify({ items: storyPreferences }), { headers: { "Content-Type": "application/json" } }));
};

// Both live sites are one CSS file skinned by [data-theme] on <html>
// (see docs/architecture/design-icons-y-temas.md) — the toolbar picks
// which one the canvas renders as, same as tema.py does on the server.
const withTheme = (storyFn, context) => {
  document.documentElement.setAttribute("data-theme", context.globals.theme || "humanrounds");
  return storyFn();
};

// Default = "humanrounds" (2026-08-05, palette recolor design-loop): the
// public brand is Human Rounds (CLAUDE.md), so the catalog opens in that
// palette by default — Pinamar stays one click away in the toolbar.
export const globalTypes = {
  theme: {
    name: "Theme",
    description: "Site theme (data-theme)",
    defaultValue: "humanrounds",
    toolbar: {
      icon: "paintbrush",
      items: [
        { value: "humanrounds", title: "Human Rounds" },
        { value: "pinamar", title: "Pinamar" },
      ],
      dynamicTitle: true,
    },
  },
};

/** @type { import('@storybook/html-vite').Preview } */
const preview = {
  decorators: [withTheme],
  parameters: {
    layout: "padded",
  },
};

export default preview;
