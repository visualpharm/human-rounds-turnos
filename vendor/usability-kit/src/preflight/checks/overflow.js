'use strict';
/* overflow — document scrollWidth > clientWidth + 1 at a viewport; lists the
 * widest offending elements (selector, right edge) so the culprit is visible
 * without opening the page. */

module.exports = {
  id: 'overflow',
  scan(v) {
    return v.page.evaluate(() => {
      const doc = document.documentElement;
      const client = doc.clientWidth;
      if (doc.scrollWidth <= client + 1) return null;
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
      const offenders = [];
      for (const el of document.querySelectorAll('body *')) {
        if (el.tagName === 'HTML' || el.tagName === 'BODY') continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (r.right > client + 1) offenders.push({ selector: selOf(el), right: Math.round(r.right), width: Math.round(r.width) });
      }
      offenders.sort((a, b) => b.right - a.right);
      return { scrollWidth: doc.scrollWidth, clientWidth: client, offenders: offenders.slice(0, 5) };
    }).then((res) => {
      if (!res) return;
      const worst = res.offenders[0];
      v.add({
        check: 'overflow',
        severity: 'serious',
        title: 'Page overflows horizontally',
        detail: `scrollWidth ${res.scrollWidth} > viewport ${res.clientWidth}; widest: ${res.offenders.map((o) => `${o.selector} (right edge ${o.right}px)`).join(', ')}`,
        selector: worst ? worst.selector : 'body',
        viewport: v.viewport,
      });
    });
  },
};
