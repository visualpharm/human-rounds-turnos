'use strict';
/* text_density — the product owner's zero-small-print gate (vow 2026-09-02, skill
 * ui-text-density): visible text below 13px (serious), eyebrows/kickers —
 * short uppercase letter-spaced text directly above a heading (moderate),
 * a subtitle right under a title repeating it (moderate), and
 * "Fuente:/Source:/Nota:" small-print lines (moderate). */

module.exports = {
  id: 'text_density',
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
      const visible = (el) => {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      const ownText = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').replace(/\s+/g, ' ').trim();
      // previous/next VISIBLE element sibling, skipping hidden ones
      const sibling = (el, dir) => {
        let n = el;
        while ((n = dir === 'prev' ? n.previousElementSibling : n.nextElementSibling)) {
          if (visible(n)) return n;
        }
        return null;
      };
      const out = { tiny: [], eyebrow: [], subtitle: [], source: [] };
      for (const el of document.querySelectorAll('body *')) {
        const text = ownText(el);
        if (!text || !visible(el)) continue;
        const cs = getComputedStyle(el);
        const fs = parseFloat(cs.fontSize);
        if (fs < 13) out.tiny.push({ text: text.slice(0, 40), size: Math.round(fs * 10) / 10, selector: selOf(el) });
        if (/^(fuente|source|nota|note)\s*[:：]/i.test(text)) out.source.push({ text: text.slice(0, 50), selector: selOf(el) });
      }
      for (const h of document.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
        if (!visible(h)) continue;
        const title = h.textContent.replace(/\s+/g, ' ').trim();
        if (!title) continue;
        const prev = sibling(h, 'prev');
        if (prev && !prev.matches('h1,h2,h3,h4,h5,h6')) {
          const t = prev.textContent.replace(/\s+/g, ' ').trim();
          const pcs = getComputedStyle(prev);
          const spaced = parseFloat(pcs.letterSpacing) > 0.5;
          const hasLetters = /[A-ZÁÉÍÓÚÑÜa-záéíóúñü]/.test(t);
          const upper = t.length > 1 && t.length <= 40
            && (t === t.toLocaleUpperCase('es') || pcs.textTransform === 'uppercase')
            && hasLetters;
          if (upper && spaced && ownText(prev)) out.eyebrow.push({ text: t.slice(0, 40), title: title.slice(0, 40), selector: selOf(prev) });
        }
        const next = sibling(h, 'next');
        if (next && !next.matches('h1,h2,h3,h4,h5,h6') && title.length >= 10) {
          const t = next.textContent.replace(/\s+/g, ' ').trim();
          if (t && t !== title && (t.includes(title) || title.includes(t))) {
            out.subtitle.push({ title: title.slice(0, 40), subtitle: t.slice(0, 60), selector: selOf(next) });
          }
        }
      }
      return out;
    }).then((res) => {
      for (const t of res.tiny) {
        v.add({
          check: 'text_density',
          severity: 'serious',
          title: 'Text below 13px',
          detail: `${t.size}px — “${t.text}”`,
          selector: t.selector,
          viewport: v.viewport,
        });
      }
      for (const e of res.eyebrow) {
        v.add({
          check: 'text_density',
          severity: 'moderate',
          title: 'Eyebrow text above a heading',
          detail: `“${e.text}” sits above “${e.title}”`,
          selector: e.selector,
          viewport: v.viewport,
        });
      }
      for (const s of res.subtitle) {
        v.add({
          check: 'text_density',
          severity: 'moderate',
          title: 'Subtitle repeats the title',
          detail: `“${s.subtitle}” under “${s.title}”`,
          selector: s.selector,
          viewport: v.viewport,
        });
      }
      for (const s of res.source) {
        v.add({
          check: 'text_density',
          severity: 'moderate',
          title: 'Small-print source line',
          detail: `“${s.text}”`,
          selector: s.selector,
          viewport: v.viewport,
        });
      }
    });
  },
};
