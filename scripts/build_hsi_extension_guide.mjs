// Print the desk's guide (extensions/hsi-humanizador/guia/guia.html) to
// extensions/dist/guia-turnos-a-hsi.pdf. The same page ships inside the
// extension: the settings' «i» opens it at #institucion.
//
//   node scripts/build_hsi_extension_guide.mjs
//
// The pictures in extensions/hsi-humanizador/guia/img come from the end-to-end run against
// the HSI test copy (extensions/hsi-humanizador/test/e2e-hsi.mjs): fictional
// patients only, never a capture of a real installation.
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import fs from "node:fs";
import path from "node:path";

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(process.env.PW_ROOT || raiz, "package.json"));
const { chromium } = require("playwright");

const salida = path.join(raiz, "extensions/dist/guia-turnos-a-hsi.pdf");
fs.mkdirSync(path.dirname(salida), { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(pathToFileURL(path.join(raiz, "extensions/hsi-humanizador/guia/guia.html")).href);
await page.pdf({ path: salida, format: "A4", printBackground: true });
await browser.close();
console.log(path.relative(raiz, salida));
