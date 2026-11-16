'use strict';
/* requests — failed network requests and same-origin 4xx/5xx responses.
 * A failing document request is critical (the page itself is broken); image
 * responses are left to the images check so one broken asset is one problem. */

module.exports = {
  id: 'requests',
  wire(c) {
    const seen = new Set();
    const baseUrl = c.baseUrl;
    c.page.on('requestfailed', (req) => {
      const failure = req.failure() || {};
      if (/ERR_ABORTED/i.test(failure.errorText || '')) return; // cancelled loads, not breakage
      const url = req.url();
      if (/^(about|data|chrome):/i.test(url)) return;
      const key = `fail:${url}`;
      if (seen.has(key)) return;
      seen.add(key);
      c.add({
        check: 'requests',
        severity: 'serious',
        title: 'Request failed',
        detail: `${req.method()} ${short(url, baseUrl)}: ${failure.errorText || 'network error'}`,
        selector: short(url, baseUrl),
        viewport: c.currentViewport,
      });
    });
    c.page.on('response', (resp) => {
      const status = resp.status();
      if (status < 400) return;
      const url = resp.url();
      if (/^(about|data|chrome):/i.test(url)) return;
      if (!url.startsWith(baseUrl)) return; // external assets: not ours to flag
      const type = resp.request().resourceType();
      if (type === 'image' || /^image\//i.test(resp.headers()['content-type'] || '')) return; // images check owns these
      const isDocument = type === 'document';
      const key = `resp:${status}:${url}`;
      if (seen.has(key)) return;
      seen.add(key);
      c.add({
        check: 'requests',
        severity: isDocument ? 'critical' : 'serious',
        title: isDocument ? 'The page itself answered with an error' : 'Page asset answers with an error',
        detail: `${status} ${short(url, baseUrl)}`,
        selector: short(url, baseUrl),
        viewport: c.currentViewport,
      });
    });
  },
};

function short(url, baseUrl) {
  return url.startsWith(baseUrl) ? url.slice(baseUrl.length) || '/' : url;
}
