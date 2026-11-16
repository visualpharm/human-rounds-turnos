'use strict';
/* menu_style — header/nav controls using more than one font size (moderate).
 * Port of ~/.claude/bin/menu-style-check (product owner, 2026-09-22): outermost
 * header-like container wins (a nav inside a header is judged with the
 * header), brand marks are display type and stay out, sizes rounded to
 * 0.5px. The chevron rules of the original are a Bruno-shell concern and
 * stay out of this generic check. */

module.exports = {
  id: 'menu_style',
  scan(v) {
    return v.page.evaluate(() => {
      const CONTAINERS = 'header,nav,[role="navigation"]';
      const CONTROLS = 'a,button,summary,select,[role="tab"],[role="menuitem"]';
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
        return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.left < innerWidth;
      };
      const label = (el) => (el.tagName === 'SELECT' ? (el.selectedOptions[0] || {}).textContent || '' : el.textContent).replace(/\s+/g, ' ').trim();
      const containers = [...document.querySelectorAll(CONTAINERS)].filter((c) => visible(c) && !c.parentElement.closest(CONTAINERS));
      const out = [];
      for (const box of containers) {
        const controls = [...box.querySelectorAll(CONTROLS)].filter((el) => visible(el) && label(el));
        if (controls.length < 2) continue;
        // Brand marks are display type, not menu text.
        const menu = controls.filter((el) => !/logo|brand|mark|hmark|title/.test(String(el.className)) && !el.closest('[class*="logo"],[class*="brand"],h1'));
        const sizes = [...new Set(menu.map((el) => Math.round(parseFloat(getComputedStyle(el).fontSize) * 2) / 2))];
        if (sizes.length > 1) {
          out.push({
            container: selOf(box),
            sizes,
            items: menu.map((el) => `${selOf(el)} “${label(el).slice(0, 30)}” ${Math.round(parseFloat(getComputedStyle(el).fontSize) * 2) / 2}px`),
          });
        }
      }
      return out;
    }).then((issues) => {
      for (const i of issues) {
        v.add({
          check: 'menu_style',
          severity: 'moderate',
          title: 'Header menu mixes font sizes',
          detail: `${i.sizes.join(', ')}px among ${i.container} controls — ${i.items.slice(0, 3).join('; ')}`,
          selector: i.container,
          viewport: v.viewport,
        });
      }
    });
  },
};
