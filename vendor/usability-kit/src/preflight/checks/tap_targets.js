'use strict';
/* tap_targets — at phone viewports: interactive elements under 24×24 CSS px
 * (moderate; 44×44 is the goal, elements under it are minor), and two
 * interactive elements touching or overlapping with 0 px between them
 * (serious) — the "two buttons glued together" class of bug. Prose links
 * (display:inline inside text) are skipped: they ride the line box. */

const PHONE_MAX = 700;

module.exports = {
  id: 'tap_targets',
  scan(v) {
    if (v.viewport >= PHONE_MAX) return Promise.resolve();
    return v.page.evaluate(() => {
      const CONTROLS = 'a[href], button, input, select, summary, [role="button"], [role="tab"], [role="menuitem"], [role="switch"], [role="checkbox"], [role="option"]';
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
      const label = (el) => {
        const t = el.value || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '';
        return t.replace(/\s+/g, ' ').trim().slice(0, 30);
      };
      const els = [...document.querySelectorAll(CONTROLS)].filter(visible).slice(0, 300);
      const info = els.map((el) => ({
        el,
        r: el.getBoundingClientRect(),
        label: label(el),
        selector: selOf(el),
        inline: getComputedStyle(el).display === 'inline',
      }));
      const inDialog = (el) => !!el.closest('[role="dialog"], [aria-modal="true"], dialog');
      const out = { small: [], under44: [], glued: [] };
      for (const x of info) {
        if (x.inline) continue;
        const w = Math.round(x.r.width);
        const h = Math.round(x.r.height);
        if (w < 24 || h < 24) out.small.push({ label: x.label, selector: x.selector, w, h });
        else if (w < 44 || h < 44) out.under44.push({ label: x.label, selector: x.selector, w, h });
      }
      for (let a = 0; a < info.length; a++) {
        for (let b = a + 1; b < info.length; b++) {
          const A = info[a];
          const B = info[b];
          if (A.inline || B.inline) continue;
          if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
          if (inDialog(A.el) !== inDialog(B.el)) continue; // dialog over page content
          const hgap = Math.max(B.r.left - A.r.right, A.r.left - B.r.right);
          const vgap = Math.max(A.r.top - B.r.bottom, B.r.top - A.r.bottom);
          if (hgap > 0.5 || vgap > 0.5) continue; // a real gap somewhere (0.5px tolerance: subpixel edges)
          out.glued.push({ a: A.label, b: B.label, selectorA: A.selector, selectorB: B.selector, overlap: hgap < -0.5 && vgap < -0.5 });
        }
      }
      return out;
    }).then((res) => {
      for (const s of res.small) {
        v.add({
          check: 'tap_targets',
          severity: 'moderate',
          title: 'Tap target smaller than 24×24 px',
          detail: `“${s.label}” renders at ${s.w}×${s.h} px`,
          selector: s.selector,
          viewport: v.viewport,
        });
      }
      for (const s of res.under44) {
        v.add({
          check: 'tap_targets',
          severity: 'minor',
          title: 'Tap target under 44×44 px',
          detail: `“${s.label}” renders at ${s.w}×${s.h} px (44×44 is the goal)`,
          selector: s.selector,
          viewport: v.viewport,
        });
      }
      for (const g of res.glued) {
        v.add({
          check: 'tap_targets',
          severity: 'serious',
          title: 'Two buttons touch with 0 px between them',
          detail: `«${g.a}» and «${g.b}» ${g.overlap ? 'overlap' : 'share an edge'}`,
          selector: `${g.selectorA} + ${g.selectorB}`,
          viewport: v.viewport,
        });
      }
    });
  },
};
