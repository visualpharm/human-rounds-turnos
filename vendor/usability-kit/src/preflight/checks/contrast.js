'use strict';
/* contrast — rendered text contrast against the first opaque background
 * behind it, composited through translucent layers and ancestor opacity, in
 * light AND dark color scheme. Port of ~/.claude/bin/contrast-check (product owner,
 * 2026-09-22): ratio < 3 is "invisible text" (critical), < 4.5 (< 3 large) is
 * low contrast (moderate). Gradient/image backgrounds are skipped (solid
 * colors only); SVG text judged by fill, skipped when a shape sits under it.
 *
 * One fidelity fix over the original: the dark-scheme canvas fallback
 * (#181818) only applies when the page declares color-scheme dark — a page
 * without a background renders a white canvas in both schemes until it opts
 * into dark. */

/* Runs inside the page (serialized by Playwright; must be self-contained). */
function scanContrast() {
  document.querySelectorAll('details').forEach((d) => { d.open = true; });
  const parse = (c) => { const m = c && c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (top, bottom) => {
    const a = top.a + bottom.a * (1 - top.a);
    if (!a) return { r: 0, g: 0, b: 0, a: 0 };
    return {
      r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / a,
      g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / a,
      b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / a,
      a,
    };
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => { const l1 = lum(a); const l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
  const hex = (c) => `#${[c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')}`;
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const canvasColor = parse(getComputedStyle(document.documentElement).backgroundColor);
  const declared = String(getComputedStyle(document.documentElement).colorScheme || 'normal').toLowerCase();
  const canvas = canvasColor && canvasColor.a > 0
    ? canvasColor
    : (dark && declared.includes('dark') ? { r: 18, g: 18, b: 18, a: 1 } : { r: 255, g: 255, b: 255, a: 1 });
  const selectorOf = (el) => {
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
  const vw = innerWidth;
  const vh = Math.max(innerHeight, document.documentElement.scrollHeight);
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) continue;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    if (rect.right <= 0 || rect.bottom <= 0 || rect.left >= vw || rect.top >= vh) continue;
    if (cs.fontSize && parseFloat(cs.fontSize) < 1) continue;
    const fontSize = parseFloat(cs.fontSize);
    const bold = Number(cs.fontWeight) >= 700;
    const large = fontSize >= 24 || (bold && fontSize >= 18.66);
    const svgRoot = el.ownerSVGElement;
    let fg = parse(svgRoot ? cs.fill : cs.color);
    if (!fg) continue;
    if (svgRoot) {
      const shapes = svgRoot.querySelectorAll('path,rect,circle,ellipse,polygon');
      let painted = false;
      for (const sh of shapes) {
        const ss = getComputedStyle(sh);
        const f = parse(ss.fill);
        if (ss.fill === 'none' || !f || f.a === 0 || Number(ss.fillOpacity) === 0) continue;
        const b = sh.getBoundingClientRect();
        if (b.left < rect.right && b.right > rect.left && b.top < rect.bottom && b.bottom > rect.top) { painted = true; break; }
      }
      if (painted) continue;
    }
    let bg = { r: 0, g: 0, b: 0, a: 0 };
    let unknown = false;
    let alphaScale = 1;
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = n === el ? cs : getComputedStyle(n);
      const op = Number(s.opacity);
      if (n !== el && op < 1) alphaScale *= op;
      const c = parse(s.backgroundColor);
      if (s.backgroundImage && s.backgroundImage !== 'none') { unknown = true; break; }
      if (c && c.a > 0) layers.push(c);
      if (c && c.a >= 1) break;
      if (n === document.documentElement) break;
    }
    if (unknown || alphaScale === 0) continue; // unknown bg, or an ancestor at opacity 0 hides the text
    for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
    bg = over(bg, canvas);
    fg = over({ ...fg, a: fg.a * alphaScale }, bg);
    const r = ratio(fg, bg);
    const need = large ? 3 : 4.5;
    if (r < need) out.push({
      text: text.slice(0, 60), selector: selectorOf(el), fg: hex(fg), bg: hex(bg),
      ratio: Math.round(r * 100) / 100, level: r < 3 ? 'FAIL' : 'WARN',
      fontSize: Math.round(fontSize * 10) / 10,
    });
  }
  return out;
}

module.exports = {
  id: 'contrast',
  async scan(v) {
    for (const scheme of ['light', 'dark']) {
      await v.page.emulateMedia({ colorScheme: scheme });
      const issues = await v.page.evaluate(scanContrast);
      const suffix = scheme === 'dark' ? ', dark scheme' : '';
      for (const i of issues) {
        v.add({
          check: 'contrast',
          severity: i.level === 'FAIL' ? 'critical' : 'moderate',
          title: i.level === 'FAIL' ? `Invisible or near-invisible text (${i.ratio}:1${suffix})` : `Low text contrast (${i.ratio}:1${suffix})`,
          detail: `${i.fg} on ${i.bg} at ${i.fontSize}px — “${i.text}”`,
          selector: i.selector,
          viewport: v.viewport,
        });
      }
    }
    await v.page.emulateMedia({ colorScheme: null });
  },
};
