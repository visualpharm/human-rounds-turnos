import { fileURLToPath } from "node:url";
import path from "node:path";

// Extra host names the dev server answers on (a private network name to open
// the catalog from a phone): comma-separated STORYBOOK_ALLOWED_HOSTS, kept out
// of the tree.
const TAILNET_HOSTS = (process.env.STORYBOOK_ALLOWED_HOSTS || "")
  .split(",").map((h) => h.trim()).filter(Boolean);

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Tier 5 slice 3 (docs/roadmap/tier5-frontend-design.md, "Interactive
// screens as Preact+HTM islands"): admin.html resolves these bare
// specifiers via an <script type="importmap"> pointing at the fingerprinted
// /static/vendor/*.module.js URLs — Storybook has no server-side templating
// pass, so its OWN Vite config aliases the SAME bare specifiers straight to
// the SAME vendored files on disk. One copy of Preact/HTM, not two: the
// story renders with the exact bytes production ships, never a separate
// npm-installed copy that could drift from what's actually vendored.
const VENDOR_ALIASES = [
  { find: /^preact$/, replacement: path.resolve(__dirname, "../static/vendor/preact.module.js") },
  { find: /^preact\/hooks$/, replacement: path.resolve(__dirname, "../static/vendor/hooks.module.js") },
  { find: /^htm$/, replacement: path.resolve(__dirname, "../static/vendor/htm.module.js") },
  { find: /^htm\/preact$/, replacement: path.resolve(__dirname, "../static/vendor/htm-preact.module.js") },
  { find: /^sana\/calendar-grid$/, replacement: path.resolve(__dirname, "../static/platform/calendar-grid.mjs") },
];

/** @type {import('@storybook/html-vite').StorybookConfig} */
const config = {
  stories: ["../stories/**/*.stories.js"],
  framework: {
    name: "@storybook/html-vite",
    options: {},
  },
  // Only the specific branding assets referenced by human-rounds.css —
  // never the repo root (that would also serve .env, .py source, etc).
  staticDirs: [
    { from: "../static/root/logo.png", to: "/logo.png" },
    { from: "../static/root/pinamar-emblema.svg", to: "/pinamar-emblema.svg" },
    // Directory stories reference the same public icon URLs as the patient
    // site. Publish this narrow public directory, never the repository root.
    { from: "../static/icons", to: "/static/icons" },
    // Los retratos SINTÉTICOS del pack de demo. Las stories nunca muestran la
    // foto de un médico real: son personas que no existen, generadas para la
    // demo (scripts/gen_demo_doctor_photos.py).
    { from: "../packs/example-town/fotos-medicos", to: "/demo-fotos" },
    // El mapa de centros de la portada (stories/CentersMap): el dibujo del
    // mismo pack de demo, una ciudad que no existe.
    { from: "../packs/example-town/centros-mapa.svg", to: "/demo-mapa.svg" },
    // Marca: los mismos PNG servidos hoy (favicon + admin) más las copias con
    // nombre legible, para la story Foundations/Brand mark.
    { from: "../static/human-rounds-mark-32.png", to: "/human-rounds-mark-32.png" },
    { from: "../static/human-rounds-mark-32-dark.png", to: "/human-rounds-mark-32-dark.png" },
    { from: "../static/human-rounds-mark-180.png", to: "/human-rounds-mark-180.png" },
    { from: "../static/human-rounds-mark-32-admin.png", to: "/human-rounds-mark-32-admin.png" },
    { from: "../static/human-rounds-mark-32-admin-dark.png", to: "/human-rounds-mark-32-admin-dark.png" },
    { from: "../static/logo-admin.png", to: "/logo-admin.png" },
    { from: "../static/brand", to: "/brand" },
  ],
  // Storybook AND Vite each reject a Host header they weren't told about
  // (DNS-rebinding protection): over Tailscale both answered 403 "Invalid
  // host", so the ndash "Storybook" tab opened to nothing. Named hosts only —
  // never `true`, which would allow any Host at all.
  core: { allowedHosts: TAILNET_HOSTS },
  viteFinal: (config) => {
    config.server = config.server || {};
    config.server.allowedHosts = TAILNET_HOSTS;
    config.resolve = config.resolve || {};
    const priorAliases = Array.isArray(config.resolve.alias)
      ? config.resolve.alias
      : Object.entries(config.resolve.alias || {}).map(([find, replacement]) => ({ find, replacement }));
    config.resolve.alias = [...VENDOR_ALIASES, ...priorAliases];
    return config;
  },
};

export default config;
