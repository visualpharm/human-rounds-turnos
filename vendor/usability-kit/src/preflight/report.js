'use strict';
/* Report assembly: stable problem ids, deterministic ordering, atomic write.
 * Problem id = first 16 hex of sha1(check|page|viewport|selector|title) so the
 * same bug across runs (and across projects) keeps its identity. */

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const SEVERITY_RANK = { critical: 0, serious: 1, moderate: 2, minor: 3 };

function problemId(p) {
  return crypto.createHash('sha1')
    .update([p.check, p.page, p.viewport == null ? '' : p.viewport, p.selector || '', p.title].join('|'))
    .digest('hex')
    .slice(0, 16);
}

function finalizeProblems(problems, severityOverrides = {}) {
  const seen = new Set();
  const out = [];
  for (const p of problems) {
    if (severityOverrides[p.check]) p.severity = severityOverrides[p.check];
    p.id = problemId(p);
    if (seen.has(p.id)) continue;
    seen.add(p.id);
    out.push(p);
  }
  out.sort((a, b) => (SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity])
    || String(a.check).localeCompare(b.check)
    || String(a.page).localeCompare(b.page)
    || (a.viewport || 0) - (b.viewport || 0)
    || String(a.id).localeCompare(b.id));
  return out;
}

function summarize(problems) {
  const s = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  for (const p of problems) s[p.severity] += 1;
  return s;
}

function finalizeSkipped(skipped) {
  const seen = new Set();
  const out = [];
  for (const s of skipped) {
    const key = `${s.kind}|${s.name}|${s.reason}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(s);
  }
  out.sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
  return out;
}

/* '/store/product/' -> 'store-product', '/' -> 'home'. */
function pageSlug(pagePath) {
  let s = String(pagePath).replace(/\.html?$/i, '').replace(/\/+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');
  s = s.replace(/^-+|-+$/g, '');
  return s || 'home';
}

function shotName(role, pagePath, viewport) {
  const base = `${pageSlug(pagePath)}-${viewport}.png`;
  return role === 'public' ? base : `${role}-${base}`;
}

function buildReport({ config, baseUrl, startedAt, viewports, pages, problems, skipped, checksRun = [], walkthroughs = [] }) {
  const finalProblems = finalizeProblems(problems, (config.checks && config.checks.severity_overrides) || {});
  return {
    schema: 'usability-kit/preflight-report',
    version: 1,
    project: config.project,
    base_url: baseUrl,
    generated_at: new Date().toISOString(),
    duration_ms: Date.now() - startedAt,
    viewports,
    pages,
    summary: summarize(finalProblems),
    problems: finalProblems,
    skipped: finalizeSkipped(skipped),
    checks_run: [...checksRun],
    walkthroughs: walkthroughs.map((w) => ({ name: w.name, start: w.start, steps: w.steps, ran: Boolean(w.ran), ok: Boolean(w.ok) })),
  };
}

function writeReport(dir, report) {
  fs.mkdirSync(path.join(dir, 'shots'), { recursive: true });
  const file = path.join(dir, 'report.json');
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(report, null, 2)}\n`);
  fs.renameSync(tmp, file);
  return file;
}

function exitCodeFor(report) {
  return report.summary.critical > 0 ? 1 : 0;
}

module.exports = {
  SEVERITY_RANK,
  problemId,
  finalizeProblems,
  finalizeSkipped,
  summarize,
  pageSlug,
  shotName,
  buildReport,
  writeReport,
  exitCodeFor,
};
