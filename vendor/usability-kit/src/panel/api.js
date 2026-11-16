'use strict';
/* The host JSON API + run endpoint (spec "Admin panel adapter").
 *
 * createHandler({storeDir, base, runner, token, canRunHere}) returns an async
 * (req, res) => boolean: the host chains it into its own admin routing behind
 * its OWN auth; false means "not mine, try the next route". The kit never
 * implements login.
 *
 *   GET  {base}/api/summary            worst counts, filter counts, last run, running
 *   GET  {base}/api/findings?status=   findings (optionally filtered)
 *   GET  {base}/api/store              the whole store (client.js mount)
 *   GET  {base}/evidence/<id>/<file>   image bytes, path-traversal safe
 *   POST {base}/run                    enqueue a preflight run; run it here when
 *                                      the server can, else leave it queued for
 *                                      the workstation runner; 303 back
 *   POST {base}/api/ingest             bearer USABILITY_KIT_TOKEN (constant-time):
 *                                      {report | study, run_id, evidence: {path: base64}}
 *   GET  {base}/api/queue              bearer: queued runs
 *   POST {base}/api/queue/<id>/claim   bearer: mark a run running
 *
 * `runner` is an async (run, {claim, finish, pushReport}) => {} the host
 * supplies when this server executes preflights itself (Playwright + the
 * project's config). It is only awaited when canRunHere() is true. */

const fs = require('fs');
const crypto = require('crypto');
const store = require('../store');
const { render } = require('./render');

const MIME = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
};

const json = (res, code, body) => {
  const payload = JSON.stringify(body);
  res.writeHead(code, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
    'cache-control': 'no-store',
  });
  res.end(payload);
};

const bearer = (req) => {
  const m = /^bearer\s+(.+)$/i.exec(String(req.headers.authorization || ''));
  return m ? m[1].trim() : null;
};

/* Constant-time token compare on fixed-length digests. */
function tokenOk(provided, expected) {
  if (!expected) return false;
  if (!provided) return false;
  const a = crypto.createHash('sha256').update(provided).digest();
  const b = crypto.createHash('sha256').update(String(expected)).digest();
  return crypto.timingSafeEqual(a, b);
}

/* Small body reader: urlencoded and JSON, capped at 8 MB (evidence is base64). */
function readBody(req, limit = 8 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) {
        reject(Object.assign(new Error('body_too_large'), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function parseBody(req, raw) {
  const type = String(req.headers['content-type'] || '').split(';')[0].trim().toLowerCase();
  const text = raw.toString('utf8');
  if (type === 'application/json' || text.startsWith('{') || text.startsWith('[')) {
    try {
      return JSON.parse(text || '{}');
    } catch {
      return {};
    }
  }
  const out = {};
  for (const pair of text.split('&')) {
    if (!pair) continue;
    const eq = pair.indexOf('=');
    const k = decodeURIComponent(eq < 0 ? pair : pair.slice(0, eq)).replace(/\+/g, ' ');
    const v = eq < 0 ? '' : decodeURIComponent(pair.slice(eq + 1)).replace(/\+/g, ' ');
    out[k] = v;
  }
  return out;
}

/* base64 evidence values -> Buffers, keyed by their report path. */
function decodeEvidence(map) {
  const out = {};
  for (const [k, v] of Object.entries(map || {})) {
    if (typeof v !== 'string' || !v) continue;
    try {
      out[k] = Buffer.from(v, 'base64');
    } catch { /* skip undecodable */ }
  }
  return out;
}

/* summary shape shared by the panel status line and external pollers. */
function summaryOf(s) {
  const counts = store.counts(s.findings);
  const worst = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  for (const f of store.worst(s, Infinity)) worst[f.severity] += 1;
  let lastRun = null;
  for (const r of s.runs) if (r.status === 'done' || r.status === 'failed') lastRun = r;
  return {
    worst,
    counts,
    last_run: lastRun ? { id: lastRun.id, kind: lastRun.kind, status: lastRun.status, finished_at: lastRun.finished_at || lastRun.requested_at } : null,
    running: s.runs.some((r) => r.status === 'queued' || r.status === 'running'),
  };
}

/* Where POST /run sends the browser back: the referer when it stays
 * same-origin-ish, else the panel base. */
function backTo(req, base) {
  const ref = String(req.headers.referer || '');
  if (/^https?:\/\/[^/?#]+/i.test(ref)) return ref;
  return base || '/';
}

/* Runs a claimed run through the host's runner, closing it either way. */
async function executeRun(cfg, run) {
  const finish = (summary, error) => {
    const s = store.load(cfg.storeDir);
    store.finishRun(s, run.id, summary, error);
    store.save(cfg.storeDir, s);
  };
  try {
    await cfg.runner(run, { finish });
  } catch (err) {
    finish(null, `runner failed: ${err && err.message ? err.message : err}`);
  }
}

function createHandler(cfg) {
  const base = String(cfg.base || '').replace(/\/+$/, '');
  /* Spec: this server may run preflights itself only when Playwright is
   * resolvable AND USABILITY_KIT_RUN_HERE=1; otherwise runs stay queued. */
  const canRunHere = cfg.canRunHere || (() => {
    if (process.env.USABILITY_KIT_RUN_HERE !== '1') return false;
    try {
      require.resolve('playwright');
      return true;
    } catch {
      return false;
    }
  });
  const prefix = `${base}/`;
  const route = (pathname) => (pathname === base ? pathname : pathname.startsWith(prefix) ? pathname.slice(prefix.length - 1) : null);

  return async function handle(req, res) {
    try {
    const url = new URL(req.url, 'http://kit.local');
    const r = route(url.pathname);
    if (!r) return false;
    const tail = r.slice(1); /* after base/, '' for the base itself */
    const method = String(req.method || 'GET').toUpperCase();

    /* ---- bearer routes ---- */
    if (tail === 'api/summary' && method === 'GET') {
      const s = store.load(cfg.storeDir);
      json(res, 200, summaryOf(s));
      return true;
    }
    if (tail === 'api/findings' && method === 'GET') {
      const s = store.load(cfg.storeDir);
      const status = url.searchParams.get('status');
      json(res, 200, store.statusFilter(s.findings, status || 'all'));
      return true;
    }
    if (tail === 'api/store' && method === 'GET') {
      const s = store.load(cfg.storeDir);
      json(res, 200, s);
      return true;
    }
    if (tail === 'api/queue' && method === 'GET') {
      if (!tokenOk(bearer(req), cfg.token)) {
        json(res, 401, { error: 'unauthorized' });
        return true;
      }
      const s = store.load(cfg.storeDir);
      json(res, 200, s.runs.filter((x) => x.status === 'queued'));
      return true;
    }
    if (/^api\/queue\/run_[a-f0-9]{12}\/claim$/.test(tail) && method === 'POST') {
      if (!tokenOk(bearer(req), cfg.token)) {
        json(res, 401, { error: 'unauthorized' });
        return true;
      }
      const runId = tail.split('/')[2];
      const s = store.load(cfg.storeDir);
      const run = store.claimRun(s, runId);
      store.save(cfg.storeDir, s);
      json(res, run ? 200 : 404, run || { error: 'run_not_found' });
      return true;
    }
    if (tail === 'api/ingest' && method === 'POST') {
      if (!tokenOk(bearer(req), cfg.token)) {
        json(res, 401, { error: 'unauthorized' });
        return true;
      }
      const body = parseBody(req, await readBody(req));
      if (body.run_id && !/^run_[a-f0-9]{12}$/.test(String(body.run_id))) {
        json(res, 400, { error: 'bad_run_id' });
        return true;
      }
      const opts = {
        runId: body.run_id || undefined,
        dir: cfg.storeDir,
        evidence: decodeEvidence(body.evidence),
      };
      let out;
      const s = store.load(cfg.storeDir);
      if (body.study) out = store.ingestStudy(s, body.study, opts);
      else if (body.report) out = store.ingestPreflight(s, body.report, opts);
      else {
        json(res, 400, { error: 'report_or_study_required' });
        return true;
      }
      store.save(cfg.storeDir, s);
      json(res, 200, { touched: out.touched, run: { id: out.run.id, status: out.run.status, summary: out.run.summary } });
      return true;
    }

    /* ---- browser routes (behind the host's admin auth) ---- */
    if (/^evidence\/fnd_[a-f0-9]{12}\/[A-Za-z0-9._-]+$/.test(tail) && method === 'GET') {
      const [, fndId, file] = tail.split('/');
      const full = store.evidencePath(cfg.storeDir, fndId, file);
      if (!full) {
        json(res, 400, { error: 'bad_evidence_path' });
        return true;
      }
      let data;
      try {
        data = fs.readFileSync(full);
      } catch {
        json(res, 404, { error: 'not_found' });
        return true;
      }
      const mime = MIME[file.slice(file.lastIndexOf('.')).toLowerCase()] || 'application/octet-stream';
      res.writeHead(200, { 'content-type': mime, 'content-length': data.length, 'cache-control': 'private, max-age=60' });
      res.end(data);
      return true;
    }
    if (tail === 'run' && method === 'POST') {
      const body = parseBody(req, await readBody(req, 64 * 1024));
      if (cfg.csrf && body.csrf !== cfg.csrf) {
        json(res, 403, { error: 'bad_csrf' });
        return true;
      }
      const s = store.load(cfg.storeDir);
      const run = store.enqueueRun(s, 'preflight', cfg.requestedBy || 'panel');
      store.save(cfg.storeDir, s);
      if (cfg.runner && canRunHere()) {
        store.claimRun(s, run.id);
        store.save(cfg.storeDir, s);
        setImmediate(() => { executeRun(cfg, run).catch(() => {}); });
      }
      res.writeHead(303, { location: backTo(req, base) });
      res.end();
      return true;
    }

    return false;
    } catch (err) {
      /* A kit error never kills the host's socket. */
      if (!res.headersSent) json(res, err && err.statusCode ? err.statusCode : 500, { error: (err && err.code) || 'internal' });
      else res.end();
      return true;
    }
  };
}

/* page({store, storeDir, base, ...}) — the Node host's one-call panel:
 * render() over a store object or a store directory. */
function page(opts = {}) {
  const s = opts.store || (opts.storeDir && store.load(opts.storeDir)) || store.emptyStore();
  return render(s, {
    base: opts.base,
    lang: opts.lang,
    canRun: opts.canRun !== undefined ? opts.canRun : true,
    csrf: opts.csrf,
    status: opts.status,
    now: opts.now,
  });
}

module.exports = { createHandler, page, summaryOf, tokenOk, MIME };
