'use strict';
/* Findings store v1 (schema/findings-store.v1.json) — one record per usability
 * problem across runs, keyed by `check|page|signature` so the same problem
 * merges whatever the wording (a folded keyword signature, English and Spanish
 * stopwords stripped, accents folded). Bruno's research-findings.js is the
 * idea's origin; this is the standalone store for the four consuming projects.
 *
 * State machine, computed on ingest:
 *   new key → open
 *   markFixed(id, note) → fixed
 *   a later run that covered the finding's page (viewport too, for preflight
 *   runs) and did NOT see the key → confirmed (also from open: the problem
 *   went away by itself), fixed_at recorded if empty
 *   a later run that DID see the key while fixed → failing
 *   failing seen again → open · confirmed seen again → failing
 * Ingest is idempotent: a run already ingested (runs[].ingested_at) is a no-op.
 *
 * The Python mirror python/usability_kit/host.py keeps the exact same machine;
 * test/fixtures/store-cases.json runs both and they must agree. The stopword
 * list is shared from src/store/stopwords.json so the two cannot drift. */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const rank = require('./rank');

const SCHEMA = 'usability-kit/findings-store';
const SEVERITIES = ['critical', 'serious', 'moderate', 'minor'];
const RATINGS = [0, 1, 2, 3];
const ID_RE = { fnd: /^fnd_[a-f0-9]{12}$/, run: /^run_[a-f0-9]{12}$/, ses: /^ses_[a-f0-9]{12}$/ };
const EVIDENCE_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$/;
const STOPWORDS = new Set(require('./stopwords.json').en.concat(require('./stopwords.json').es));

/* Bug words: a finding whose words say something broke is a bug, not a design
 * candidate. Mirrored character-for-character in python/usability_kit/host.py. */
const BUG_WORDS = /\b(error|errors|broken|breaks|crash|crashes|fails|failed|fails to load|did not load|does not load|does not work|doesn'?t work|not clickable|overflow|cut off|clipped|blank|404|500|no carga|no funciona|no anda|roto|se rompio)\b/i;

/* Simulated-participant severities (result.json) fold onto the store's scale;
 * preflight already speaks the store's vocabulary. */
const SEV_FROM_STUDY = {
  blocker: 'critical', critical: 'critical',
  major: 'serious', serious: 'serious',
  minor: 'moderate', moderate: 'moderate',
  cosmetic: 'minor',
};
const SEV_FROM_PREFLIGHT = {
  critical: 'critical', serious: 'serious', moderate: 'moderate', minor: 'minor',
  major: 'serious', blocker: 'critical', cosmetic: 'minor',
};

const nowIso = (now) => {
  if (now instanceof Date) return now.toISOString();
  if (typeof now === 'string' && now) return new Date(now).toISOString();
  return new Date().toISOString();
};
const newId = (prefix) => `${prefix}_${crypto.randomBytes(6).toString('hex')}`;
const clip = (s, n) => String(s == null ? '' : s).trim().slice(0, n) || null;

/* ---- keyword signature (the dedup key's text half) ---- */

function deaccent(s) {
  return String(s || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

/* The meaningful words of a title: deaccented, alphanumerics, len >= 2,
 * stopwords (English + Spanish) dropped. */
function tokens(text) {
  const out = new Set();
  for (const w of deaccent(text).split(/[^a-z0-9]+/)) {
    if (w.length >= 2 && !STOPWORDS.has(w)) out.add(w);
  }
  return out;
}

function signature(text) {
  return [...tokens(text)].sort().join('-');
}

/* A same-site path only: "/store/", never "//host" or "https://…". */
function normPage(p) {
  const s = String(p == null ? '' : p).trim().split(/[?#]/)[0];
  if (!s || !/^\/(?![/\\])/.test(s)) return /^\/(?![/\\])/.test(s) ? s : null;
  return s;
}

function keyOf({ check, page, pages, title }) {
  const p = normPage(page != null ? page : (pages || [])[0]);
  return `${String(check || '')}|${p || ''}|${signature(title)}`;
}

/* ---- store shape ---- */

function emptyStore(project) {
  return {
    schema: SCHEMA, version: 1, project: project || null, updated_at: null,
    findings: [], runs: [], sessions: [],
  };
}

function hydrate(s) {
  const store = emptyStore(s && s.project);
  if (!s || typeof s !== 'object') return store;
  Object.assign(store, s);
  store.schema = SCHEMA;
  store.version = 1;
  store.findings = Array.isArray(store.findings) ? store.findings : [];
  store.runs = Array.isArray(store.runs) ? store.runs : [];
  store.sessions = Array.isArray(store.sessions) ? store.sessions : [];
  return store;
}

function load(dir) {
  let raw;
  try {
    raw = fs.readFileSync(path.join(dir, 'store.json'), 'utf8');
  } catch (err) {
    if (err && err.code === 'ENOENT') return emptyStore();
    throw err;
  }
  return hydrate(JSON.parse(raw));
}

/* Atomic save: temp file in the same directory, then rename. */
function save(dir, store) {
  store.updated_at = new Date().toISOString();
  fs.mkdirSync(dir, { recursive: true });
  const final = path.join(dir, 'store.json');
  const tmp = path.join(dir, `.store-${process.pid}-${crypto.randomBytes(4).toString('hex')}.tmp`);
  fs.writeFileSync(tmp, `${JSON.stringify(store, null, 2)}\n`);
  fs.renameSync(tmp, final);
  return store;
}

/* ---- evidence files ---- */

/* One image per slot per occurrence: before.png / after.png for the first,
 * before-2.png … afterwards — never an overwrite. */
function evidenceName(slot, index, srcKey) {
  const ext = (/\.[a-z0-9]{2,5}$/i.test(String(srcKey || '')) && `.${String(srcKey).split('.').pop().toLowerCase()}`) || '.png';
  return index <= 1 ? `${slot}${ext}` : `${slot}-${index}${ext}`;
}

function writeEvidence(dir, fndId, name, bytes) {
  const edir = path.join(dir, 'evidence', fndId);
  fs.mkdirSync(edir, { recursive: true });
  const final = path.join(edir, name);
  const tmp = path.join(edir, `.${name}.tmp`);
  fs.writeFileSync(tmp, bytes);
  fs.renameSync(tmp, final);
  return `evidence/${fndId}/${name}`;
}

/* Resolve an evidence request to a safe absolute path, or null. Exported for
 * the panel API: <fnd_id>/<file> with both parts validated and the resolved
 * path pinned under evidence/ — traversal never escapes. */
function evidencePath(dir, fndId, file) {
  if (!ID_RE.fnd.test(String(fndId || '')) || !EVIDENCE_RE.test(String(file || ''))) return null;
  if (file.includes('..')) return null;
  const root = path.resolve(dir, 'evidence');
  const full = path.resolve(root, fndId, file);
  if (full !== root && !full.startsWith(`${root}${path.sep}`)) return null;
  return full;
}

/* ---- ingest ---- */

const seenTransition = (status) =>
  (status === 'fixed' || status === 'confirmed') ? 'failing'
    : status === 'failing' ? 'open'
      : 'open';

function itemPages(item) {
  const pages = [];
  for (const p of item.pages || []) {
    const n = normPage(p);
    if (n && !pages.includes(n)) pages.push(n);
  }
  return pages;
}

function countSummary(items) {
  const summary = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  for (const i of items) summary[i.severity] += 1;
  return summary;
}

/* The occurrence's evidence slot: recorded only when bytes for the report's
 * path were supplied; written when a store dir was supplied. */
function occurrenceEvidence(f, item, index, opts) {
  const out = { before: null, after: null };
  const bytes = opts.evidence || {};
  for (const slot of ['before', 'after']) {
    const src = item.evidence && item.evidence[slot];
    if (!src || !(src in bytes)) continue;
    const name = evidenceName(slot, index, src);
    out[slot] = opts.dir
      ? writeEvidence(opts.dir, f.id, name, bytes[src])
      : `evidence/${f.id}/${name}`;
  }
  return out;
}

function distinctParticipants(store) {
  return new Set(store.sessions.map((s) => s.id)).size;
}

function refreshParticipants(f) {
  const hit = new Set();
  for (const o of f.occurrences || []) if (o.participant) hit.add(o.participant);
  f.participants_hit = hit.size;
}

/* One run applied to the store: seen keys transition and gain an occurrence;
 * covered-but-unseen findings confirm. `run` fields are already normalized. */
function applyRun(store, run, opts) {
  const touched = [];
  const seen = new Set();

  /* Idempotent: this run already landed. */
  const existing = store.runs.find((r) => r.id === run.id);
  if (existing && existing.ingested_at) return { touched: [], run: existing };

  /* Findings the run saw. */
  for (const item of run.items) {
    const key = keyOf(item);
    const at = item.at || run.at;
    seen.add(key);
    let f = store.findings.find((x) => x.key === key);
    if (!f) {
      f = {
        id: newId('fnd'), key,
        kind: item.kind, scope: item.scope, source: run.source, check: item.check || null,
        severity: item.severity, title: item.title, detail: item.detail,
        pages: itemPages(item), viewports: [...item.viewports],
        rating: null, status: 'open', fixed_at: null, fix_note: null,
        participants_hit: 0, participants_ran: 0, suggested_fix: item.suggested_fix,
        first_seen: at, last_seen: at,
        occurrences: [],
      };
      store.findings.push(f);
    } else {
      f.status = seenTransition(f.status);
      /* The worst wording and the widest footprint survive merges. */
      if (rank.SEV_ORDER[item.severity] < rank.SEV_ORDER[f.severity]) {
        f.severity = item.severity;
      }
      if (!f.detail && item.detail) f.detail = item.detail;
      if (!f.suggested_fix && item.suggested_fix) f.suggested_fix = item.suggested_fix;
      for (const p of itemPages(item)) if (!f.pages.includes(p)) f.pages.push(p);
      for (const v of item.viewports) if (!f.viewports.includes(v)) f.viewports.push(v);
      if (!f.last_seen || at > f.last_seen) f.last_seen = at;
    }
    const index = f.occurrences.length + 1;
    f.occurrences.push({
      run_id: run.id, at,
      page: normPage(item.pages[0]) || null,
      viewport: item.viewports[0] ?? null,
      participant: item.participant || null,
      quote: item.quote || null,
      evidence: occurrenceEvidence(f, item, index, opts),
    });
    refreshParticipants(f);
    touched.push(f.id);
  }

  /* Findings the run covered but did not see: the problem is gone. */
  for (const f of store.findings) {
    if (seen.has(f.key)) continue;
    if (!runCovered(run, f)) continue;
    const since = f.fixed_at || f.last_seen || f.first_seen;
    if (since && run.at < since) continue; /* an older run confirms nothing */
    if (f.status !== 'confirmed') f.status = 'confirmed';
    if (!f.fixed_at) f.fixed_at = run.at;
  }

  /* Sessions and the participants denominator. */
  for (const ses of run.sessions) store.sessions.push(ses);
  if (run.sessions.length) {
    const ran = distinctParticipants(store);
    for (const f of store.findings) {
      if ((f.occurrences || []).some((o) => o.participant)) f.participants_ran = ran;
    }
  }

  /* The run record: upserted, marked ingested, closed when nothing else will. */
  const record = existing || {
    id: run.id, kind: run.kind, status: 'queued',
    requested_by: run.requestedBy || 'ingest', requested_at: run.at,
    started_at: run.at, finished_at: null, base_url: run.baseUrl || null,
    summary: null, error: null,
  };
  if (!existing) store.runs.push(record);
  record.kind = record.kind || run.kind;
  record.base_url = record.base_url || run.baseUrl || null;
  record.ingested_at = nowIso(opts.now);
  if (record.status === 'queued' || record.status === 'running') {
    record.status = 'done';
    record.finished_at = record.finished_at || run.at;
  }
  if (!record.summary) record.summary = countSummary(run.items);

  store.updated_at = nowIso(opts.now);
  return { touched, run: record };
}

/* Preflight coverage is page×viewport; study coverage is the pages the
 * participant visited. A finding without viewports ignores them; one without
 * pages (a pageless participant complaint) is covered by any later run of the
 * same source, never by the other source. */
function runCovered(run, f) {
  const pages = f.pages || [];
  if (!pages.length) return run.source === f.source;
  if (run.kind === 'preflight' && (f.viewports || []).length) {
    return pages.some((p) => f.viewports.some((v) => run.pairs.has(`${p}@${v}`)));
  }
  return pages.some((p) => run.pages.has(p));
}

function normalizeViewports(v) {
  const list = Array.isArray(v) ? v : v != null ? [v] : [];
  return [...new Set(list.map(Number).filter((n) => Number.isFinite(n) && n > 0))];
}

/* ---- preflight ingest ---- */

function coverageFromReport(report, items) {
  const pages = new Set();
  const pairs = new Set();
  const add = (p, v) => {
    const n = normPage(p);
    if (!n) return;
    pages.add(n);
    if (v) pairs.add(`${n}@${v}`);
  };
  const viewports = normalizeViewports(report.viewports);
  const raw = report.coverage || report.pages || [];
  for (const entry of raw) {
    if (typeof entry === 'string') {
      if (viewports.length) viewports.forEach((v) => add(entry, v));
      else add(entry, null);
    } else if (entry && typeof entry === 'object') {
      const p = entry.page || entry.path || entry.url;
      const vs = normalizeViewports(entry.viewports || entry.viewport);
      if (vs.length) vs.forEach((v) => add(p, v));
      else if (viewports.length) viewports.forEach((v) => add(p, v));
      else add(p, null);
    }
  }
  /* Without declared coverage, the report stands only for its findings. */
  if (!pages.size) {
    for (const item of items) {
      for (const p of itemPages(item)) {
        for (const v of item.viewports.length ? item.viewports : [null]) add(p, v);
      }
    }
  }
  return { pages, pairs };
}

/* A preflight report (schema preflight-report.v1): findings with check,
 * severity, page, viewport, evidence as a shots/ path or {before, after}. */
function ingestPreflight(store, report, opts = {}) {
  const items = [];
  for (const p of (report && (report.findings || report.problems)) || []) {
    if (!p || !p.title) continue;
    items.push({
      check: p.check || null,
      severity: SEV_FROM_PREFLIGHT[String(p.severity || '').toLowerCase()] || 'minor',
      title: clip(p.title, 200),
      detail: clip(p.detail || p.body, 1000),
      pages: [p.page || p.path || (p.pages || [])[0]].filter(Boolean),
      viewports: normalizeViewports(p.viewport || p.viewports),
      kind: p.kind === 'design' ? 'design' : 'bug',
      scope: p.scope === 'lab' ? 'lab' : 'product',
      suggested_fix: clip(p.suggested_fix || p.fix, 500),
      participant: null,
      quote: null,
      evidence: typeof p.evidence === 'string' ? { before: p.evidence, after: null } : (p.evidence || {}),
    });
  }
  const at = nowIso(report && (report.at || report.generated_at) || opts.now);
  const coverage = coverageFromReport(report || {}, items);
  return applyRun(store, {
    id: opts.runId || (report && report.run_id) || newId('run'),
    kind: 'preflight', source: 'preflight', at,
    baseUrl: report && report.base_url, requestedBy: opts.requestedBy,
    items, sessions: [], ...coveragePaths(coverage),
  }, opts);
}

/* ---- study ingest ---- */

const OUTCOME = {
  done: 'success', success: 'success', ok: 'success',
  giveup: 'fail', fail: 'fail', failed: 'fail',
  abandon: 'abandon', 'tool-failure': 'abandon', error: 'abandon',
};

/* The simulated participant's result.json: persona, task, verdict, steps,
 * findings (title, severity blocker|major|minor|cosmetic, category, quote,
 * suggested_fix). One result or an array of them. */
function ingestStudy(store, studyResults, opts = {}) {
  const results = Array.isArray(studyResults) ? studyResults : [studyResults];
  const items = [];
  const sessions = [];
  const pages = new Set();
  const at0 = nowIso(opts.now);

  for (const result of results) {
    if (!result || typeof result !== 'object') continue;
    const sesId = result.session_id || newId('ses');
    const at = nowIso(result.at || result.finished_at || opts.now);
    sessions.push({
      id: sesId, run_id: opts.runId || result.run_id || null,
      persona: clip(result.persona || result.participant, 80) || 'participant',
      task: clip(result.task || result.case, 200),
      outcome: OUTCOME[String(result.outcome || result.verdict || '').toLowerCase()] || 'abandon',
      steps: Number(result.steps) || 0,
      at,
      summary: clip(result.answer || result.summary || result.note, 300),
    });
    for (const p of result.pages || [result.path]) {
      const n = normPage(p);
      if (n) pages.add(n);
    }
    for (const f of result.findings || []) {
      if (!f || !f.title) continue;
      const kind = f.kind === 'bug' || f.kind === 'design' ? f.kind
        : f.category === 'bug' ? 'bug'
          : BUG_WORDS.test(String(f.title)) ? 'bug' : 'design';
      const sev = SEV_FROM_STUDY[String(f.severity || '').toLowerCase()] || 'minor';
      items.push({
        check: null,
        severity: sev,
        title: clip(f.title, 200),
        detail: clip(f.detail, 1000),
        pages: [f.page].filter(Boolean),
        viewports: normalizeViewports(f.viewport),
        kind, scope: f.scope === 'lab' ? 'lab' : 'product',
        suggested_fix: clip(f.suggested_fix, 500),
        participant: sesId,
        quote: clip(f.quote, 300),
        evidence: typeof f.evidence === 'string' ? { before: f.evidence, after: null } : (f.evidence || {}),
        at,
      });
    }
  }

  /* Session-level coverage: pages the participants visited declare it; else
   * the run stands only for what it reported. */
  const pairs = new Set();
  if (!pages.size) for (const item of items) {
    const p = normPage(item.pages[0]);
    if (p) pages.add(p);
  }
  return applyRun(store, {
    id: opts.runId || newId('run'),
    kind: 'study', source: 'simulated', at: at0,
    baseUrl: opts.baseUrl, requestedBy: opts.requestedBy,
    items, sessions, pages, pairs,
  }, opts);
}

function coveragePaths({ pages, pairs }) {
  return { pages, pairs };
}

/* ---- owner actions ---- */

function finding(store, idOrKey) {
  return (store.findings || []).find((f) => f.id === idOrKey || f.key === idOrKey) || null;
}

function markFixed(store, id, note, opts = {}) {
  const f = finding(store, id);
  if (!f) return null;
  f.status = 'fixed';
  f.fix_note = clip(note, 500);
  f.fixed_at = nowIso(opts.now);
  store.updated_at = nowIso(opts.now);
  return f;
}

function setRating(store, id, rating, opts = {}) {
  const f = finding(store, id);
  if (!f) return null;
  const value = rating == null || rating === '' ? null : Number(rating);
  if (value !== null && !RATINGS.includes(value)) throw new Error('bad_rating');
  f.rating = value;
  store.updated_at = nowIso(opts.now);
  return f;
}

/* ---- runs ---- */

function enqueueRun(store, kind, requestedBy, opts = {}) {
  const run = {
    id: newId('run'), kind: kind === 'study' ? 'study' : 'preflight', status: 'queued',
    requested_by: clip(requestedBy, 120) || 'panel', requested_at: nowIso(opts.now),
    started_at: null, finished_at: null, base_url: opts.baseUrl || null,
    summary: null, error: null,
  };
  store.runs.push(run);
  store.updated_at = run.requested_at;
  return run;
}

function claimRun(store, runId, opts = {}) {
  const run = store.runs.find((r) => r.id === runId);
  if (!run) return null;
  run.status = 'running';
  run.started_at = run.started_at || nowIso(opts.now);
  store.updated_at = run.started_at;
  return run;
}

function finishRun(store, runId, summary, error, opts = {}) {
  const run = store.runs.find((r) => r.id === runId);
  if (!run) return null;
  run.status = error ? 'failed' : 'done';
  run.finished_at = nowIso(opts.now);
  run.error = error ? clip(error, 500) : null;
  if (summary) run.summary = summary;
  store.updated_at = run.finished_at;
  return run;
}

module.exports = {
  load, save, emptyStore, hydrate,
  signature, tokens, deaccent, normPage, keyOf,
  ingestPreflight, ingestStudy, applyRun,
  markFixed, setRating, finding,
  enqueueRun, claimRun, finishRun,
  worst: rank.worst, bySeverity: rank.bySeverity, statusFilter: rank.statusFilter, counts: rank.counts,
  evidencePath, writeEvidence,
  SEVERITIES, RATINGS, BUG_WORDS, ID_RE,
};
