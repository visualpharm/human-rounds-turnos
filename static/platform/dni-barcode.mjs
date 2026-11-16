// dni-barcode.mjs — Tier-1 DNI reader: decode the front PDF417 in the browser.
//
// ES module, loaded lazily via dynamic import() from cuenta-form.js (a classic
// script). It decodes the dense PDF417 barcode on the FRONT of an Argentine DNI
// from a still photo using the vendored zxing-wasm reader, then parses the
// "@"-delimited payload into {dni, apellido, nombres, sexo, nacimiento}.
//
// This is free and offline. If it returns null (blurry photo, no barcode, or a
// dorso photo — the back has no barcode), the caller falls back to /api/ocr-dni.
// Parsing lives in dni-scan.mjs (shared with the admission desk's keyboard-wedge
// reader) and mirrors sana/extension/lib/pdf417-parser.js.
import {
  readBarcodes,
  setZXingModuleOverrides
} from '/vendor/zxing-wasm/es/reader/index.js';
// Payload parsing lives only in dni-scan.mjs; both the admission-desk keyboard
// reader and this photo reader produce the same text.
// dni-scan.mjs is fingerprinted (core/static_fingerprint.py, CLIENT_LOADED), so
// a fixed-path static import cannot name its current URL. The shell of every
// page that loads this module publishes the map in window.SANA_STATIC. This is
// the same fail-closed pattern as resolveUrl() in islands.mjs (null plus
// console.error, never a fixed fallback URL). Without an entry, SCAN rejects
// and readDni() rejects with it; each caller already catches that path.
function staticUrl(name) {
  const m = (typeof window !== 'undefined') && window.SANA_STATIC;
  const u = m && m[name];
  if (!u && typeof console !== 'undefined' && console.error) console.error('[static] ' + name + ' is not in window.SANA_STATIC (the shell must emit static_fingerprint.manifest_script())');
  return u || null;
}
const SCAN_URL = staticUrl('platform/dni-scan.mjs');
const SCAN = SCAN_URL ? import(SCAN_URL) : Promise.reject(new Error('[static] platform/dni-scan.mjs is not in window.SANA_STATIC'));
// readDni() reports an absent manifest entry to its callers instead of leaving
// an unhandled rejection while this module is evaluated.
SCAN.catch(() => {});

setZXingModuleOverrides({
  locateFile: (path, prefix) =>
    path.endsWith('.wasm') ? '/vendor/zxing-wasm/zxing_reader.wasm' : (prefix || '') + path
});

const BINARIZERS = ['LocalAverage', 'GlobalHistogram'];
const opts = (binarizer) => ({
  formats: ['PDF417', 'QRCode'],
  tryHarder: true, tryRotate: true, tryInvert: true,
  tryDownscale: true, tryDenoise: true,
  binarizer, maxNumberOfSymbols: 1
});

// Decode a still photo (File/Blob) → patient object, or null if unreadable.
export async function readDni(file) {
  const { parsePayload } = await SCAN;
  for (const bin of BINARIZERS) {
    let res;
    try { res = await readBarcodes(file, opts(bin)); }
    catch (_) { return null; } // wasm failed to load: let the caller OCR instead
    if (res && res.length && res[0].text) {
      const patient = parsePayload(res[0].text);
      if (patient) return patient;
    }
  }
  return null;
}
