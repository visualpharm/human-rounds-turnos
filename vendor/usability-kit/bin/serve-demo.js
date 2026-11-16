#!/usr/bin/env node
'use strict';
/* Demo server: seeds a store from test/fixtures/demo-store.json (plus
 * synthesized PNG evidence so thumbnails render), serves the panel in a plain
 * host shell. For screenshot capture and local poking only — it has no auth.
 *
 *   node bin/serve-demo.js [port]
 *
 * POST /run finishes a fake preflight 300 ms later, so the Running… state and
 * the 303-back flow can be seen without Playwright. */

const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');
const { URL } = require('url');
const store = require('../src/store');
const { createHandler, page } = require('../src/panel/api');

const PORT = Number(process.argv[2] || process.env.PORT || 4317);
const FIXTURE = path.join(__dirname, '..', 'test', 'fixtures', 'demo-store.json');

/* ---- tiny PNG writer: a vertical two-tone gradient, 320x200, no deps ---- */

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i += 1) {
    c ^= buf[i];
    for (let k = 0; k < 8; k += 1) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type, data) {
  const out = Buffer.alloc(8 + data.length + 4);
  out.writeUInt32BE(data.length, 0);
  out.write(type, 4, 'ascii');
  data.copy(out, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + data.length)), 8 + data.length);
  return out;
}

function pngFor(seedText, w = 320, h = 200) {
  let seed = 0;
  for (const ch of seedText) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = seed % 360;
  const top = hsl(hue, 0.32, 0.72);
  const bottom = hsl((hue + 40) % 360, 0.38, 0.5);
  const bar = hsl(hue, 0.45, 0.36);
  const raw = Buffer.alloc((w * 3 + 1) * h);
  let o = 0;
  for (let y = 0; y < h; y += 1) {
    raw[o] = 0; o += 1; /* filter: none */
    const t = y / (h - 1);
    for (let x = 0; x < w; x += 1) {
      let [r, g, b] = mix(top, bottom, t);
      if (y > h * 0.62 && y < h * 0.7) [r, g, b] = bar; /* a UI-ish band */
      else if (y > h * 0.72 && x > w * 0.6) [r, g, b] = mix([r, g, b], [255, 255, 255], 0.25);
      raw[o] = r; raw[o + 1] = g; raw[o + 2] = b; o += 3;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 2; /* 8-bit truecolor */
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

function hsl(h, s, l) {
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h / 30) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)))));
  };
  return [f(0), f(8), f(4)];
}
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

/* ---- seed the demo store ---- */

const storeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ukit-demo-'));
const demo = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
for (const f of demo.findings) {
  for (const o of f.occurrences || []) {
    for (const slot of ['before', 'after']) {
      const rel = o.evidence && o.evidence[slot];
      if (!rel) continue;
      const target = path.join(storeDir, rel);
      if (!fs.existsSync(target)) {
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, pngFor(`${f.id}-${path.basename(rel, '.png')}`));
      }
    }
  }
}
store.save(storeDir, demo);

/* ---- the panel + api, mounted at the root ---- */

const handler = createHandler({
  storeDir,
  base: '',
  token: 'demo-token',
  canRunHere: () => true,
  requestedBy: 'demo',
  runner: (run, { finish }) => {
    setTimeout(() => finish({ critical: 0, serious: 0, moderate: 0, minor: 0 }, null), 300);
  },
});

const SHELL = (body) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Usability — demo</title>
<style>
:root{--ink:#202124;--line:#dadce0;--bg:#fff;--danger:#c5221f;--good:#188038}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{display:flex;align-items:center;gap:12px;padding:14px 16px;border-bottom:1px solid var(--line)}
header strong{font-size:16px;font-weight:700}
main{padding:20px 16px 48px}
</style></head>
<body>
<header><strong>Usability</strong><span>demo store</span></header>
<main>${body}</main>
</body></html>`;

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://demo.local');
  if (url.pathname === '/' || url.pathname === '/index.html') {
    const s = store.load(storeDir);
    const status = url.searchParams.get('status') || 'all';
    const out = page({ store: s, base: '', status, now: new Date().toISOString() });
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(SHELL(`<style>${out.css}</style>${out.html}`));
    return;
  }
  if (await handler(req, res)) return;
  res.writeHead(404, { 'content-type': 'text/plain' });
  res.end('not found');
});

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`usability-kit demo: http://127.0.0.1:${PORT} (store: ${storeDir})\n`);
});
