'use strict';
/* links — internal anchor targets answering >= 400 or unreachable (HEAD,
 * confirmed with GET whenever HEAD fails or answers >= 400, because many
 * servers answer HEAD wrongly while serving the page fine; shares the role's
 * logged-in cookies via the browser context's request API). External links
 * are only checked with --external. Excluded paths (crawl.exclude, e.g.
 * /logout) are never probed: a GET there has side effects. */

async function probe(request, url) {
  let resp = null;
  try {
    resp = await request.fetch(url, { method: 'HEAD', maxRedirects: 8, timeout: 15000 });
  } catch (e) { /* network error or refused method — try GET below */ }
  if (!resp || resp.status() >= 400) {
    try {
      resp = await request.fetch(url, { method: 'GET', maxRedirects: 8, timeout: 15000 });
    } catch (e) {
      return { error: e };
    }
  }
  return { status: resp ? resp.status() : 0 };
}

module.exports = {
  id: 'links',
  async finish(f) {
    const { crawl, context, opts, baseUrl } = f;
    const origin = new URL(baseUrl).origin;
    const crawledOk = new Set(crawl.pages);
    for (const link of crawl.links) {
      let u;
      try { u = new URL(link.url); } catch { continue; }
      const key = u.pathname + u.search;
      if (crawledOk.has(key)) continue; // already rendered fine by the crawler
      const external = u.origin !== origin;
      if (external && !opts.external) continue;
      const { status, error } = await probe(context.request, link.url);
      if (error) {
        f.add({
          check: 'links', severity: 'serious',
          title: 'Internal link is unreachable',
          detail: `link to ${key || '/'} (“${link.text || 'no text'}”) from ${link.from}: ${error.message || error}`,
          selector: key, viewport: null, page: link.from, role: f.role.id,
        });
      } else if (status >= 400) {
        f.add({
          check: 'links', severity: 'serious',
          title: `Internal link returns ${status}`,
          detail: `link to ${key || '/'} (“${link.text || 'no text'}”) from ${link.from}`,
          selector: key, viewport: null, page: link.from, role: f.role.id,
        });
      }
    }
  },
};
