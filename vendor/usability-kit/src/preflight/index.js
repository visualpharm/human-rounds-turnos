'use strict';
/* runPreflight(config, opts) -> report (schema/preflight-report.v1.json).
 *
 * For the public surface and each configured role (logged in once, cookies
 * kept in the context) it crawls same-origin pages from the seed pages, then
 * visits every page at every viewport and runs the check modules. Writes
 * DIR/report.json + DIR/shots/*.png (one screenshot per page×viewport that
 * has a problem) when opts.out is set. */

const fs = require('node:fs');
const path = require('node:path');
const { resolveCredentials } = require('../config');
const { crawlPages } = require('./crawl');
const { allChecks } = require('./checks');
const { buildReport, writeReport, shotName } = require('./report');

const HEIGHTS = { 360: 780, 390: 844, 834: 1112, 1440: 900 };

class PreflightError extends Error {
  constructor(message) {
    super(message);
    this.name = 'PreflightError';
  }
}

function trimBase(u) { return String(u).replace(/\/+$/, ''); }

async function performLogin(page, baseUrl, login, creds) {
  await page.goto(baseUrl + login.url, { waitUntil: 'load', timeout: 30000 });
  for (const step of login.steps || []) {
    if (step.fill !== undefined) {
      let value = step.value != null ? step.value : '';
      if (step.secret) {
        if (creds[step.secret] === undefined) throw new Error(`no credential "${step.secret}" resolved`);
        value = creds[step.secret];
      }
      await page.fill(step.fill, String(value), { timeout: 10000 });
    } else if (step.click !== undefined) {
      await page.click(step.click, { timeout: 10000 });
    } else if (step.wait_url !== undefined) {
      await page.waitForURL((u) => String(u).includes(step.wait_url), { timeout: 15000 });
    } else {
      throw new Error('unknown login step');
    }
  }
}

async function runPreflight(config, opts = {}) {
  const startedAt = Date.now();
  const baseUrl = trimBase(opts.baseUrl || config.local_url || config.base_url);
  const viewports = (opts.viewports && opts.viewports.length ? opts.viewports : config.viewports);
  const maxPages = opts.maxPages || config.crawl.max_pages;
  const disabled = new Set(config.checks.disable || []);
  let checks = allChecks().filter((c) => !disabled.has(c.id));
  if (opts.only && opts.only.length) {
    const only = new Set(opts.only);
    checks = checks.filter((c) => only.has(c.id));
  }

  let shotsDir = null;
  let outDir = null;
  if (opts.out) {
    outDir = path.resolve(opts.out);
    shotsDir = path.join(outDir, 'shots');
    fs.mkdirSync(shotsDir, { recursive: true });
  }

  let chromium;
  try {
    chromium = require('playwright').chromium;
  } catch (e) {
    throw new PreflightError('playwright is not installed (npm install)');
  }

  const problems = [];
  const skipped = [];
  const walkthroughs = [];
  const pagesByRole = {};

  const siteAdd = (p) => {
    problems.push({
      check: p.check,
      severity: p.severity,
      page: p.page || '/',
      role: p.role || 'public',
      viewport: p.viewport == null ? null : p.viewport,
      title: p.title,
      detail: p.detail || '',
      selector: p.selector || '',
      evidence: p.evidence || null,
    });
  };

  const browser = await chromium.launch();
  try {
    const roles = [
      { id: 'public', pages: config.public_pages, login: null },
      ...Object.entries(config.roles || {}).map(([id, r]) => ({ id, pages: r.pages, login: r.login })),
    ];

    for (const role of roles) {
      let creds = null;
      if (role.login) {
        const secrets = [...new Set((role.login.steps || []).map((s) => s.secret).filter(Boolean))];
        const res = await resolveCredentials(role.login, secrets);
        if (!res.ok) {
          skipped.push({ kind: 'role', name: role.id, role: role.id, reason: res.reason });
          continue;
        }
        creds = res.creds;
      }

      const context = await browser.newContext({ ignoreHTTPSErrors: true });

      if (role.login) {
        const loginPage = await context.newPage();
        try {
          await performLogin(loginPage, baseUrl, role.login, creds);
        } catch (e) {
          skipped.push({
            kind: 'role', name: role.id, role: role.id,
            reason: `login failed: ${String(e.message || e).split('\n')[0].slice(0, 120)}`,
          });
          await loginPage.close().catch(() => {});
          await context.close().catch(() => {});
          continue;
        }
        await loginPage.close().catch(() => {});
      }

      const crawlPage = await context.newPage();
      const crawl = await crawlPages(crawlPage, baseUrl, role.pages, {
        maxPages,
        exclude: config.crawl.exclude,
      });
      await crawlPage.close().catch(() => {});
      pagesByRole[role.id] = crawl.pages.length;

      for (const pagePath of crawl.pages) {
        const ctx = {
          page: await context.newPage(),
          path: pagePath,
          role: role.id,
          baseUrl,
          viewports,
          state: {},
          problems: [],
          currentViewport: viewports[0],
          viewport: viewports[0],
          skip(checkId, reason) {
            skipped.push({ kind: 'check', name: checkId, check: checkId, reason });
          },
        };
        ctx.add = (p) => {
          ctx.problems.push({
            check: p.check,
            severity: p.severity,
            page: p.page || ctx.path,
            role: p.role || ctx.role,
            viewport: p.viewport === undefined ? ctx.viewport : p.viewport,
            title: p.title,
            detail: p.detail || '',
            selector: p.selector || '',
            evidence: null,
          });
        };

        for (const c of checks) if (c.wire) c.wire(ctx);

        for (const vp of viewports) {
          ctx.currentViewport = vp;
          ctx.viewport = vp;
          await ctx.page.setViewportSize({ width: vp, height: HEIGHTS[vp] || 900 });
          try {
            await ctx.page.goto(baseUrl + pagePath, { waitUntil: 'load', timeout: 30000 });
          } catch (e) {
            ctx.add({
              check: 'requests',
              severity: 'critical',
              title: 'The page itself failed to load',
              detail: String(e.message || e).split('\n')[0].slice(0, 140),
              selector: pagePath,
            });
            continue;
          }
          await ctx.page.waitForLoadState('networkidle', { timeout: 1500 }).catch(() => {});
          await ctx.page.waitForTimeout(150);

          for (const c of checks) {
            if (!c.scan) continue;
            try {
              await c.scan(ctx);
            } catch (e) {
              skipped.push({
                kind: 'check', name: c.id, check: c.id,
                reason: `${c.id} failed on ${pagePath}: ${String(e.message || e).split('\n')[0].slice(0, 100)}`,
              });
            }
          }

          const vpProblems = ctx.problems.filter((p) => p.viewport === vp);
          if (vpProblems.length && shotsDir) {
            const file = shotName(role.id, pagePath, vp);
            await ctx.page.screenshot({ path: path.join(shotsDir, file) }).catch(() => {});
            for (const p of vpProblems) p.evidence = `shots/${file}`;
          }
        }

        for (const c of checks) {
          if (!c.finishPage) continue;
          try {
            await c.finishPage(ctx);
          } catch (e) { /* per-page finish is best effort */ }
        }
        await ctx.page.close().catch(() => {});
        problems.push(...ctx.problems);
      }

      for (const c of checks) {
        if (!c.finish) continue;
        try {
          await c.finish({ config, opts, baseUrl, add: siteAdd, skipped, role, context, crawl });
        } catch (e) {
          skipped.push({
            kind: 'check', name: c.id, check: c.id,
            reason: `${c.id} failed for role ${role.id}: ${String(e.message || e).split('\n')[0].slice(0, 100)}`,
          });
        }
      }
      await context.close().catch(() => {});
    }

    for (const c of checks) {
      if (!c.finishGlobal) continue;
      try {
        await c.finishGlobal({ config, opts, baseUrl, add: siteAdd, skipped, shotsDir, walkthroughs });
      } catch (e) {
        skipped.push({
          kind: 'check', name: c.id, check: c.id,
          reason: `${c.id} failed: ${String(e.message || e).split('\n')[0].slice(0, 100)}`,
        });
      }
    }
  } finally {
    await browser.close().catch(() => {});
  }

  const report = buildReport({ config, baseUrl, startedAt, viewports, pages: pagesByRole, problems, skipped, checksRun: checks.map((c) => c.id), walkthroughs });
  if (outDir) writeReport(outDir, report);
  return report;
}

module.exports = { runPreflight, PreflightError, HEIGHTS };
