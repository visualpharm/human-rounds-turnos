'use strict';
/* overlays — more than one visible modal/overlay at the same time (role=dialog,
 * aria-modal, or a fixed element covering > 50% of the viewport with a
 * background). Checked at load and after each walkthrough step — the
 * "photo viewer opened twice" class of bug. The scan is exported because the
 * walkthrough runner re-runs it after every step. */

function scanOverlaysInPage(page) {
  return page.evaluate(() => {
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
    const visible = (el) => {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const vw = innerWidth;
    const vh = innerHeight;
    const found = [];
    for (const el of document.querySelectorAll('[role="dialog"], [aria-modal="true"], dialog')) {
      if (!visible(el)) continue;
      found.push({ selector: selOf(el), kind: 'dialog' });
    }
    for (const el of document.querySelectorAll('body *')) {
      if (el.closest('[role="dialog"], [aria-modal="true"], dialog')) continue;
      /* A modal's backdrop surrounds its dialog; both are the same
       * overlay. Two visible dialogs still count independently above. */
      if ([...el.querySelectorAll('[role="dialog"], [aria-modal="true"], dialog')].some(visible)) continue;
      const cs = getComputedStyle(el);
      if (cs.position !== 'fixed') continue;
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if ((r.width * r.height) / (vw * vh) < 0.5) continue;
      const bg = cs.backgroundColor;
      const opaque = bg && !/rgba?\(\s*0,\s*0,\s*0,\s*0\s*\)/.test(bg) && bg !== 'transparent';
      if (!opaque) continue; // a transparent fixed layer is not a backdrop
      found.push({ selector: selOf(el), kind: 'backdrop' });
    }
    return found.slice(0, 5);
  });
}

module.exports = {
  id: 'overlays',
  scanOverlaysInPage,
  scan(v) {
    return scanOverlaysInPage(v.page).then((found) => {
      if (found.length <= 1) return;
      v.add({
        check: 'overlays',
        severity: 'serious',
        title: found.length === 2 ? 'Two overlays open at the same time' : `${found.length} overlays open at the same time`,
        detail: found.map((f) => f.selector).join(' + '),
        selector: found[0].selector,
        viewport: v.viewport,
      });
    });
  },
};
