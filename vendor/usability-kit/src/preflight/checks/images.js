'use strict';
/* images — <img> that finished with naturalWidth 0, image responses >= 400,
 * and CSS backgrounds whose request failed (they arrive as image responses
 * too). DOM findings win over the network finding for the same URL so a
 * broken img is one problem, not two. */

module.exports = {
  id: 'images',
  wire(c) {
    const badResponses = new Map(); // url -> status
    c.state.badResponses = badResponses;
    c.state.domBroken = new Set();
    c.page.on('response', (resp) => {
      const status = resp.status();
      if (status < 400) return;
      const url = resp.url();
      const type = resp.request().resourceType();
      if (type !== 'image' && !/^image\//i.test(resp.headers()['content-type'] || '')) return;
      badResponses.set(url, status);
    });
  },
  scan(v) {
    return v.page.evaluate(() => {
      const selOf = (el) => {
        const parts = [];
        for (let n = el, depth = 0; n && n.nodeType === 1 && depth < 4; n = n.parentElement, depth++) {
          let s = n.tagName.toLowerCase();
          if (n.id) { parts.unshift(`${s}#${n.id}`); break; }
          const cls = [...n.classList].slice(0, 2).join('.');
          if (cls) s += `.${cls}`;
          parts.unshift(s);
        }
        return parts.join('>');
      };
      const out = [];
      for (const img of document.images) {
        const cs = getComputedStyle(img);
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) continue;
        if (img.complete && img.naturalWidth === 0) {
          out.push({ src: img.currentSrc || img.src, selector: selOf(img) });
        }
      }
      return out;
    }).then((broken) => {
      for (const b of broken) {
        v.state.domBroken.add(b.src);
        v.add({
          check: 'images',
          severity: 'serious',
          title: 'Broken image on the page',
          detail: `${b.src} did not load (naturalWidth 0)`,
          selector: b.selector,
          viewport: v.viewport,
        });
      }
    });
  },
  finishPage(c) {
    for (const [url, status] of c.state.badResponses) {
      if (c.state.domBroken.has(url)) continue; // already reported from the DOM
      c.add({
        check: 'images',
        severity: 'serious',
        title: 'Image request answered with an error',
        detail: `${status} ${url}`,
        selector: url,
        viewport: c.currentViewport,
      });
    }
  },
};
