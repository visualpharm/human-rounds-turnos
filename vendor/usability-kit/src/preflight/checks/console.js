'use strict';
/* console — console.error messages and uncaught exceptions (pageerror).
 * Deduped per page across viewport revisits (a page loaded four times
 * repeats the same error). */

module.exports = {
  id: 'console',
  wire(c) {
    const seen = new Set();
    c.page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      const text = msg.text().replace(/\s+/g, ' ').trim();
      if (!text) return;
      if (/^failed to load resource/i.test(text)) return; // network failures: requests/images checks own them
      const key = text.slice(0, 200);
      if (seen.has(key)) return;
      seen.add(key);
      c.add({
        check: 'console',
        severity: 'serious',
        title: 'Console error on the page',
        detail: `“${text.slice(0, 120)}”`,
        selector: '',
        viewport: c.currentViewport,
      });
    });
    c.page.on('pageerror', (err) => {
      const text = String(err && err.message ? err.message : err).replace(/\s+/g, ' ').trim();
      if (!text) return;
      const key = `pageerror:${text.slice(0, 200)}`;
      if (seen.has(key)) return;
      seen.add(key);
      c.add({
        check: 'console',
        severity: 'serious',
        title: 'Uncaught JavaScript exception',
        detail: `“${text.slice(0, 120)}”`,
        selector: '',
        viewport: c.currentViewport,
      });
    });
  },
};
