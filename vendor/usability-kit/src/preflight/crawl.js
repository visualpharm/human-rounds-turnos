'use strict';
/* Same-origin crawl from seed pages, rendered in the real browser so
 * script-built navigation is followed too. Pages answering >= 400 are not
 * kept for the check pass — the links check owns reporting them.
 *
 * Returns { pages: [path+search…], links: [{url, from, text}] } where `links`
 * holds every internal anchor discovered (unique by URL, first sighting). */

function isExcluded(pathname, patterns) {
  return patterns.some((p) => pathname === p || pathname.startsWith(p));
}

function normalizeKey(u) { return u.pathname + u.search; }

async function crawlPages(page, baseUrl, seeds, { maxPages = 40, exclude = [] } = {}) {
  const origin = new URL(baseUrl).origin;
  const seen = new Set();
  const queued = new Set();
  const pages = [];
  const links = new Map();

  const queue = seeds.map((s) => {
    const u = new URL(s, origin);
    return normalizeKey(u);
  });

  while (queue.length && pages.length < maxPages) {
    const key = queue.shift();
    if (seen.has(key)) continue;
    seen.add(key);
    if (isExcluded(new URL(key, origin).pathname, exclude)) continue;

    let resp;
    try {
      resp = await page.goto(origin + key, { waitUntil: 'load', timeout: 30000 });
    } catch (e) {
      continue;
    }
    if (resp && resp.status() >= 400) continue;
    await page.waitForLoadState('networkidle', { timeout: 1500 }).catch(() => {});
    pages.push(key);

    let anchors = [];
    try {
      anchors = await page.$$eval('a[href]', (as) => as.map((a) => ({
        href: a.href,
        text: (a.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60),
      })));
    } catch (e) { /* page navigated away mid-eval; skip */ }

    for (const a of anchors) {
      let u;
      try { u = new URL(a.href); } catch { continue; }
      if (u.origin !== origin || !/^https?:$/.test(u.protocol)) continue;
      if (/^(mailto|tel|javascript|data):/i.test(a.href)) continue;
      const anchorKey = normalizeKey(u);
      if (!anchorKey || anchorKey === '#') continue;
      if (isExcluded(u.pathname, exclude)) continue;
      if (!links.has(anchorKey)) links.set(anchorKey, { url: u.href, from: key, text: a.text });
      if (!seen.has(anchorKey) && !queued.has(anchorKey)) {
        queued.add(anchorKey);
        queue.push(anchorKey);
      }
    }
  }
  return { pages, links: [...links.values()] };
}

module.exports = { crawlPages, isExcluded };
