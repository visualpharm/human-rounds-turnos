'use strict';
/* lighthouse — programmatic run of the lighthouse npm module against the same
 * Chromium playwright uses (via chrome-launcher with an explicit chromePath),
 * only for config.lighthouse.pages on the public surface. No upload, no
 * telemetry: scores are read from the in-memory result. Performance < 50
 * serious / < 80 moderate; SEO < 90 moderate; best-practices < 80 minor. */

module.exports = {
  id: 'lighthouse',
  async finishGlobal(f) {
    const { config, opts, baseUrl, add, skipped } = f;
    if (opts.noLighthouse) {
      skipped.push({ kind: 'check', name: 'lighthouse', check: 'lighthouse', reason: 'disabled by --no-lighthouse' });
      return;
    }
    const pages = (config.lighthouse && config.lighthouse.pages) || [];
    if (!pages.length) {
      skipped.push({ kind: 'check', name: 'lighthouse', check: 'lighthouse', reason: 'no lighthouse.pages in config' });
      return;
    }
    let lighthouseFn;
    let chromeLauncher;
    let chromium;
    try {
      const mod = require('lighthouse');
      lighthouseFn = mod.default || mod;
      chromeLauncher = require('chrome-launcher');
      chromium = require('playwright').chromium;
    } catch (e) {
      skipped.push({ kind: 'check', name: 'lighthouse', check: 'lighthouse', reason: 'lighthouse, chrome-launcher or playwright not installed' });
      return;
    }
    let chrome;
    try {
      chrome = await chromeLauncher.launch({ chromePath: chromium.executablePath(), chromeFlags: ['--headless=new'] });
    } catch (e) {
      skipped.push({ kind: 'check', name: 'lighthouse', check: 'lighthouse', reason: `could not launch Chromium: ${String(e.message || e).slice(0, 80)}` });
      return;
    }
    try {
      for (const pagePath of pages) {
        let result;
        try {
          result = await lighthouseFn(baseUrl + pagePath, {
            port: chrome.port,
            output: 'json',
            onlyCategories: ['performance', 'seo', 'best-practices'],
          });
        } catch (e) {
          add({
            check: 'lighthouse', severity: 'serious', page: pagePath, role: 'public', viewport: null,
            title: 'Lighthouse run failed',
            detail: String(e.message || e).slice(0, 160),
            selector: '',
          });
          continue;
        }
        const cats = (result && result.lhr && result.lhr.categories) || {};
        const problems = [];
        const perf = cats.performance && cats.performance.score != null ? Math.round(cats.performance.score * 100) : null;
        const seo = cats.seo && cats.seo.score != null ? Math.round(cats.seo.score * 100) : null;
        const bp = cats['best-practices'] && cats['best-practices'].score != null ? Math.round(cats['best-practices'].score * 100) : null;
        if (perf != null && perf < 50) problems.push(['serious', `Performance score ${perf}`, `Lighthouse performance ${perf}/100`]);
        else if (perf != null && perf < 80) problems.push(['moderate', `Performance score ${perf}`, `Lighthouse performance ${perf}/100`]);
        if (seo != null && seo < 90) problems.push(['moderate', `SEO score ${seo}`, `Lighthouse SEO ${seo}/100`]);
        if (bp != null && bp < 80) problems.push(['minor', `Best-practices score ${bp}`, `Lighthouse best-practices ${bp}/100`]);
        for (const [severity, title, detail] of problems) {
          add({ check: 'lighthouse', severity, page: pagePath, role: 'public', viewport: null, title, detail, selector: '' });
        }
      }
    } finally {
      try { await chrome.kill(); } catch (e) { /* already gone */ }
    }
  },
};
