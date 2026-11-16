#!/usr/bin/env python3
"""A fallible simulated usability participant with eye-tracking replay.

    python3 -m usability_kit.fallible_participant STUDY.json [--only CASE_ID] [--no-video] [--lite] [--render-only]

The persona does not get the page. It gets what a person would have taken in:
a research-grounded gaze model (see references/human-fallacies.md in this
package) decides where the eyes land on the real rendered screen and how many
words of each fixated block are read; everything else is invisible to the model,
except a few large shapes seen "de reojo". GLM Flash plays the person on top of
that perception, thinking aloud before each action, with satisficing,
information scent in its own vocabulary, Jakob's-law expectations and
impatience. Phone personas occasionally hit a neighbouring small target (motor
slip).

Per case the run folder gets: every screen, the scanpath, the think-aloud
audio (OpenAI TTS), per-screen heatmaps, an eye-tracking replay video with
narration and soft subtitles, and report.md with what was read and missed.
This is simulation evidence, never a user's finding.

Lite mode (--lite, or "mode": "lite" in STUDY.json) is the same fallible
persona with no media at all: the gaze model still runs internally (it is
what makes the persona miss things) but nothing pixel-shaped is emitted.
Per case: trace.jsonl (one coordinate-free line per step), result.json
(verdict, seq, action_count, probes, findings from one researcher-role GLM
call over the trace), one screenshot at task end and one per error. Cases
may declare "login" (env-var credentials, the persona never sees the login
screen), "storage_state", a "setup" step list, and "probes" (file regex /
HTTP jsonpath side-channel checks). dashboard.py renders result.json files
from many runs as one self-contained HTML page.

STUDY.json:
{"site": "http://demo.localhost:6500/", "out": "~/…/run-x",
 "personas": {"marta": {…see PERSONA_DEFAULTS…}},
 "cases": [{"id": "marta-battery", "persona": "marta", "task": "…", "start": "/"}],
 "llm": {"openrouter_op_item": "OpenRouter API Key - bruno", "model": "z-ai/glm-5.3-flash"}}
"""
import argparse, colorsys, concurrent.futures as cf, json, math, os, random, re, shutil
import subprocess, sys, time, unicodedata, urllib.request
from pathlib import Path

from .openrouter import GLM

REFERENCES_DIR = Path(__file__).resolve().parent / "references"
PERSONA_DEFAULTS = {
    "name": "Participante", "description": "", "device": "desktop",
    "reading": "scanner",          # scanner | plower (lower literacy: word by word, narrow field)
    "words_per_block": 7,          # words of body text read per fixated block
    "words_per_second": 4.5,
    "screen_budget_s": 8,          # eyes on one screen before acting (NN/g: 10-20 s per page)
    "satisfice": 0.5,              # chance to stop scanning at the first scent-matching control
    "slip_rate": 0.0,              # phone: chance to hit an adjacent small target
    "max_actions": 18, "max_minutes": 5,
    "voice": "coral", "voice_style": "",
    "unknown_terms": [],           # jargon this person does not understand
}
DEVICES = {
    "desktop": {"viewport": {"width": 1280, "height": 800}, "device_scale_factor": 1},
    "phone": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True,
              "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"},
}
TTS_MODEL = "gpt-4o-mini-tts-2025-12-15"   # newest OpenAI speech snapshot in the live catalog, 2026-09-26


def log(*a):
    print(f"[fp {time.strftime('%H:%M:%S')}]", *a, flush=True)


# ── perception: what is physically on screen ────────────────────────────────
LOOK_JS = r"""() => {
  const W = innerWidth, H = innerHeight;
  const vis = (r) => r.width > 1 && r.height > 1 && r.bottom > 0 && r.right > 0 && r.top < H && r.left < W;
  const shown = (el) => { const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.05; };
  const modal = [...document.querySelectorAll('dialog[open]')].filter((d) => { try { return d.matches(':modal'); } catch { return false; } }).pop();
  const scope = modal || document.body;
  const CTRL = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab], [role=checkbox], [role=radio], summary';
  document.querySelectorAll('[data-fp-i]').forEach((el) => el.removeAttribute('data-fp-i'));
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; if (c && c !== 'transparent' && !/rgba\([^)]*,\s*0\)$/.test(c)) return c; } return 'rgb(255, 255, 255)'; };
  const adLike = (el) => { for (let e = el, k = 0; e && k < 5; e = e.parentElement, k++) { const s = `${e.id} ${typeof e.className === 'string' ? e.className : ''}`; if (/(^|[\s_-])(ad|ads|advert|banner|promo|sponsor(ed)?)([\s_-]|$)/i.test(s)) return true; } return false; };
  const pinned = (el) => { for (let e = el; e && e !== document.body; e = e.parentElement) { const p = getComputedStyle(e).position; if (p === 'fixed' || p === 'sticky') return true; } return false; };
  // Seen only if it is the topmost thing at one of a few sample points: overlays hide what is under them.
  const onTop = (el, r) => {
    const ys = [r.top + Math.min(r.height / 2, 12), r.top + r.height / 2];
    const xs = [r.left + Math.min(r.width / 2, 16), r.left + r.width / 2, r.right - Math.min(r.width / 2, 16)];
    for (const y of ys) for (const x of xs) {
      if (x < 0 || y < 0 || x >= W || y >= H) continue;
      const t = document.elementFromPoint(x, y);
      if (t && (t === el || el.contains(t) || t.contains(el))) return true;
    }
    return false;
  };
  const clip = (r) => { const x = Math.max(0, r.left), y = Math.max(0, r.top); return { x, y, w: Math.min(W, r.right) - x, h: Math.min(H, r.bottom) - y }; };
  // Stable selector for the lite-mode trace: id, name, data-testid, aria-label, else a short CSS path.
  const domId = (el) => {
    if (el.id) return el.id;
    const t = el.tagName.toLowerCase();
    const nm = el.getAttribute('name'); if (nm) return `${t}[name="${nm}"]`;
    const tid = el.getAttribute('data-testid'); if (tid) return `[data-testid="${tid}"]`;
    const al = el.getAttribute('aria-label'); if (al) return `${t}[aria-label="${al}"]`;
    const segs = []; let e = el;
    while (e && e !== document.body && segs.length < 3) {
      let seg = e.tagName.toLowerCase();
      if (e.id) { segs.unshift('#' + e.id); break; }
      if (e.parentElement) { const sib = [...e.parentElement.children].filter((c) => c.tagName === e.tagName); if (sib.length > 1) seg += `:nth-of-type(${sib.indexOf(e) + 1})`; }
      segs.unshift(seg); e = e.parentElement;
    }
    return segs.join('>') || t;
  };
  const blocks = [];
  const base = (el, r) => { const s = getComputedStyle(el); return { ...clip(r), fs: parseFloat(s.fontSize) || 16, fw: parseInt(s.fontWeight, 10) || 400, color: s.color, bg: bgOf(el), ownBg: s.backgroundColor, tag: el.tagName.toLowerCase(), ad: adLike(el), pinned: pinned(el), chrome: !!el.closest('nav,header,aside,footer,[role=navigation],[role=banner]'), heading: !!el.closest('h1,h2,h3,h4,h5,h6') }; };
  let ci = 0;
  for (const el of scope.querySelectorAll(CTRL)) {
    const r = el.getBoundingClientRect();
    if (!vis(r) || !shown(el) || el.closest('[inert]') || !onTop(el, r)) continue;
    if (el.parentElement && el.parentElement.closest(CTRL) && el.parentElement.closest(CTRL) !== el) continue;
    const i = ci++;
    el.setAttribute('data-fp-i', String(i));
    let label = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
    if (!label) { const img = el.querySelector('img[alt]'); if (img) label = img.alt; }
    const t = el.tagName.toLowerCase();
    const field = ['input', 'select', 'textarea'].includes(t);
    const b = { ...base(el, r), kind: 'control', i, dom_id: domId(el), label: label.replace(/\s+/g, ' ').slice(0, 300), role: el.getAttribute('role') || (t === 'a' ? 'link' : t === 'summary' ? 'disclosure' : t) };
    if (field) {
      b.type = el.type || t;
      b.fieldLabel = ((el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 120);
      b.placeholder = el.placeholder || '';
      b.value = t === 'select' ? (el.options[el.selectedIndex] || {}).text || '' : (el.type === 'password' ? '' : el.value || '');
      if (t === 'select') b.options = [...el.options].map((o) => o.text.trim()).slice(0, 15);
      if (el.type === 'checkbox' || el.type === 'radio') b.checked = el.checked;
    }
    const trs = []; const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = tw.nextNode(); n && trs.length < 6; n = tw.nextNode()) { if (!n.textContent.trim()) continue; const rg = document.createRange(); rg.selectNodeContents(n); trs.push(...[...rg.getClientRects()].filter(vis)); }
    if (trs.length) { const u = trs.reduce((a, r) => ({ l: Math.min(a.l, r.left), t: Math.min(a.t, r.top), r: Math.max(a.r, r.right), b: Math.max(a.b, r.bottom) }), { l: 1e9, t: 1e9, r: -1e9, b: -1e9 }); b.text_box = [u.l, u.t, u.r - u.l, u.b - u.t]; }
    b.buttonish = b.ownBg !== 'rgba(0, 0, 0, 0)' && b.ownBg !== b.bg ? true : (b.ownBg !== 'rgba(0, 0, 0, 0)' && t !== 'a');
    blocks.push(b);
  }
  for (const el of scope.querySelectorAll('img, svg, picture, video, [style*="background-image"]')) {
    const r = el.getBoundingClientRect();
    if (!vis(r) || !shown(el) || r.width < 48 || r.height < 48 || !onTop(el, r)) continue;
    if (el.closest('svg') && el.closest('svg') !== el) continue;
    const ctrl = el.closest(CTRL);
    blocks.push({ ...base(el, r), kind: 'image', alt: (el.getAttribute('alt') || el.getAttribute('aria-label') || (ctrl && ctrl.getAttribute('data-fp-i') !== null ? '' : '')).trim().slice(0, 120), inControl: ctrl ? Number(ctrl.getAttribute('data-fp-i')) : null });
  }
  const groups = new Map();
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n.textContent.replace(/\s+/g, ' ').trim();
    const p = n.parentElement;
    if (!text || !p || p.closest('script,style,noscript,[inert]') || p.closest(CTRL) || !shown(p)) continue;
    let blk = p;
    while (blk && blk !== scope && ['inline', 'contents'].includes(getComputedStyle(blk).display)) blk = blk.parentElement;
    const range = document.createRange(); range.selectNodeContents(n);
    const rects = [...range.getClientRects()].filter(vis);
    if (!rects.length) continue;
    const g = groups.get(blk) || { el: blk, parts: [], rects: [], first: p };
    g.parts.push(text); g.rects.push(...rects); groups.set(blk, g);
  }
  for (const g of groups.values()) {
    if (!g.rects.some((r) => onTop(g.el, r))) continue;
    const u = g.rects.reduce((a, r) => ({ left: Math.min(a.left, r.left), top: Math.min(a.top, r.top), right: Math.max(a.right, r.right), bottom: Math.max(a.bottom, r.bottom) }), { left: 1e9, top: 1e9, right: -1e9, bottom: -1e9 });
    const b = { ...base(g.first, u), kind: 'text', text: g.parts.join(' ').slice(0, 900), cut: u.top < H - 8 && u.bottom > H + 4 };
    b.heading = b.heading || /^h[1-6]$/.test(g.el.tagName.toLowerCase());
    blocks.push(b);
  }
  const max = Math.max(0, document.documentElement.scrollHeight - H);
  return { W, H, url: location.pathname + location.search, title: document.title, scrollY: Math.round(scrollY), maxScroll: max,
           modal: !!modal, cutAtFold: blocks.some((b) => b.kind === 'text' && b.cut) || blocks.some((b) => b.y + b.h >= H - 1 && b.kind !== 'text' && !b.pinned), blocks };
}"""

# Cheap post-action state probe for the lite trace: what changed on screen, no pixels.
POST_JS = r"""() => ({ url: location.pathname + location.search, title: document.title,
  modal: !!document.querySelector('dialog[open]'),
  toast: !!document.querySelector('[role=status], [role=alert], .toast, [class*="toast" i]'),
  scrollY: Math.round(scrollY), H: innerHeight,
  maxScroll: Math.max(0, document.documentElement.scrollHeight - innerHeight) })"""


# ── gaze model ──────────────────────────────────────────────────────────────
def norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


STOP = set("para como donde cuando quiero queres querés tenes tenés sobre desde hasta entre tiene este esta esto estos estas porque pero algo alguna alguno mucho muchos poco todo todos cual cuál que qué una uno unos unas los las del con sin por mas más muy".split())


def stems(text):
    return {w[:5] for w in re.findall(r"[a-z0-9]{4,}", norm(text)) if w not in STOP}


NAV_WORDS = {"menu", "categ", "secci", "catal", "tiend"}


def rgb(css):
    m = re.findall(r"[\d.]+", css or "")
    return tuple(float(x) for x in m[:3]) if len(m) >= 3 else (255.0, 255.0, 255.0)


def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((lum(rgb(a)), lum(rgb(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def color_name(css):
    r, g, b = (v / 255 for v in rgb(css))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.2: return "negro"
    if s < 0.15: return "blanco" if v > 0.85 else "gris"
    for lim, name in ((0.04, "rojo"), (0.11, "naranja"), (0.18, "amarillo"), (0.45, "verde"), (0.55, "celeste"), (0.72, "azul"), (0.85, "violeta"), (0.95, "rosa"), (1.01, "rojo")):
        if h < lim: return name
    return "de color"


def where(b, W, H):
    cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    v = "arriba" if cy < H / 3 else "abajo" if cy > 2 * H / 3 else "en el medio"
    h = "a la izquierda" if cx < W / 3 else "a la derecha" if cx > 2 * W / 3 else ""
    return f"{v} {h}".strip()


def block_words(b):
    if b["kind"] == "control":
        if b.get("type"):
            return (b.get("fieldLabel") or "").split() or (b.get("placeholder") or "").split()
        return b.get("label", "").split()
    if b["kind"] == "text":
        return b["text"].split()
    return (b.get("alt") or "").split()


def salience(b, W, H, persona, goal):
    area = math.sqrt(max(1.0, b["w"] * b["h"])) / math.sqrt(W * H)
    s = min(1.0, area * 2.2)
    s += 0.35 * max(0.0, (b["fs"] / 16) ** 1.2 - 0.6) * (1.3 if b["fw"] >= 600 else 1)
    if b.get("heading") or b["fs"] >= 22: s += 0.6
    if b["kind"] == "control":
        s += 0.55 if b.get("buttonish") else 0.2
        if b.get("type"): s += 0.35
    if b["kind"] == "image":
        s += 0.45 if b["w"] * b["h"] > 0.04 * W * H else 0.05
    if b["kind"] != "image":
        s += 0.12 * min(4.0, contrast(b["color"], b["bg"]) / 3)
    cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    if persona["device"] == "desktop":
        s *= 1.0 if cx < W / 2 else 0.6                       # attention leans left (80/20)
        if b["x"] > 0.7 * W and b["w"] < 0.3 * W: s *= 0.5    # right-rail blindness
    s *= 1.25 - 0.45 * (cy / H)                               # top of screen first
    if b["ad"]: s *= 0.15                                     # banner blindness
    if b["pinned"] and b["kind"] == "text": s *= 0.6
    words = block_words(b)
    peek = " ".join(words[: 8 if b["kind"] == "text" and not b.get("heading") else 14])
    hit = len(stems(peek) & goal)
    if hit: s += 0.9 * min(2, hit)                            # information scent, literal words only
    elif b.get("chrome"):
        # Jakob's law: hunting for navigation sends the eyes to where menus usually are; otherwise nav is skipped
        s *= 2.6 if goal & NAV_WORDS else 0.6
    return max(0.01, s), hit


def read_depth(b, persona, text_blocks_read):
    words = block_words(b)
    n = len(words)
    if b["kind"] == "image": return 0
    if b["kind"] == "control" and n <= 12: return n
    if (b.get("heading") or b["fs"] >= 20) and n <= 14: return n
    k = persona["words_per_block"]
    if persona["reading"] == "plower": k = max(k, 16)
    elif text_blocks_read < 2: k = int(k * 1.9)               # the F's two horizontal bars
    return min(n, max(2, k))


def fixation_points(b, nwords, rng):
    """Fixations along the first lines of a block, left to right, as in reading."""
    if nwords <= 0:
        return [(b["x"] + b["w"] / 2 + rng.uniform(-8, 8), b["y"] + b["h"] / 2 + rng.uniform(-8, 8))]
    if b.get("text_box"):
        b = {**b, "x": b["text_box"][0], "y": b["text_box"][1], "w": max(8, b["text_box"][2]), "h": max(8, b["text_box"][3])}
    fs = b["fs"]; lh = fs * 1.35; cw = fs * 0.52
    per_fix = max(1, round(1.3 + rng.uniform(-0.3, 0.4)))
    pts, x, y = [], b["x"] + min(b["w"] * 0.1, cw * 3), b["y"] + min(lh / 2, b["h"] / 2)
    avg_word = 6.2
    for j in range(max(1, math.ceil(nwords / per_fix))):
        pts.append((min(x, b["x"] + b["w"] - 4) + rng.uniform(-3, 3), y + rng.uniform(-2, 2)))
        x += per_fix * avg_word * cw
        if x > b["x"] + b["w"] - cw * 2:
            x = b["x"] + cw * 2; y += lh
            if y > b["y"] + b["h"]: y = b["y"] + b["h"] - lh / 2
    return pts


def scan(view, persona, goal, rng, budget_s, focus=None):
    """Scanpath over one screen: list of fixations and what was taken in."""
    W, H = view["W"], view["H"]
    blocks = [b for b in view["blocks"] if b["w"] > 2 and b["h"] > 2 and (b["kind"] != "image" or b.get("inControl") is None or True)]
    scored = []
    for idx, b in enumerate(blocks):
        s, hit = salience(b, W, H, persona, goal)
        if focus:
            cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            fx, fy = focus
            s *= 1 + 3 * math.exp(-math.hypot(cx - fx, cy - fy) / (0.25 * math.hypot(W, H)))
        scored.append((idx, s, hit))
    diag = math.hypot(W, H)
    decay = 0.28 if persona["reading"] == "plower" else 0.55
    budget = budget_s * 1000 * rng.uniform(0.75, 1.2)
    t, fixes, read, visited, pos = 0.0, [], [], set(), None
    text_read = 0
    while t < budget and len(visited) < len(blocks):
        cands = []
        for idx, s, hit in scored:
            if idx in visited: continue
            b = blocks[idx]
            cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            if pos is None:
                # entry point: the big picture or headline, then the top-left (UEyes; NN/g photos as content)
                entry = 1.8 if (b.get("heading") or b["fs"] >= 26 or (b["kind"] == "image" and b["w"] * b["h"] > 0.06 * W * H)) else 1.0
                w = s * entry * (1.2 - 0.4 * cx / W) * (1.3 - 0.6 * cy / H) if not focus else s
            else:
                d = math.hypot(cx - pos[0], cy - pos[1])
                order = 1.25 if cy >= pos[1] - 10 else 0.55
                w = s * math.exp(-d / (decay * diag)) * order
            cands.append((idx, max(1e-6, w) ** 2.2))
        if not cands: break
        tot = sum(w for _, w in cands)
        r, acc = rng.uniform(0, tot), 0.0
        pick = cands[-1][0]
        for idx, w in cands:
            acc += w
            if acc >= r: pick = idx; break
        visited.add(pick)
        b = blocks[pick]
        hit = scored[pick][2]
        n = read_depth(b, persona, text_read)
        if b["kind"] == "text" and not b.get("heading"): text_read += 1
        pts = fixation_points(b, n, rng)
        per_word = 1000 / persona["words_per_second"]
        dwell = [rng.uniform(200, 320) for _ in pts]
        extra = max(0.0, n * per_word - sum(dwell))
        dwell = [d + extra / len(pts) for d in dwell]
        for (x, y), d in zip(pts, dwell):
            fixes.append({"x": round(x, 1), "y": round(y, 1), "ms": round(d), "block": pick})
        t += sum(dwell) + 40 * len(pts)
        pos = pts[-1]
        read.append({"block": pick, "words": n})
        if b["kind"] == "control" and hit and rng.random() < persona["satisfice"]:
            break                                             # satisficing: good enough, stop looking
    return fixes, read, t


def describe(view, read, persona):
    """The persona's perception, in Spanish, and the controls it can act on."""
    W, H = view["W"], view["H"]
    blocks = view["blocks"]
    lines, actionable = [], {}
    for k, r in enumerate(read, 1):
        b = blocks[r["block"]]
        words = block_words(b)
        frag = " ".join(words[: r["words"]]) + ("…" if r["words"] < len(words) else "")
        if b["kind"] == "image":
            lines.append(f"{k}. una imagen" + (f" (parece: {b['alt']})" if b.get("alt") else ""))
        elif b["kind"] == "control":
            actionable[b["i"]] = b
            if b.get("type"):
                what = {"select": "una lista para elegir", "checkbox": "una casilla", "radio": "una opción redonda", "textarea": "un cuadro para escribir"}.get(b["type"], "un campo para escribir")
                extra = f" que dice «{frag}»" if frag else ""
                if b.get("placeholder"): extra += f", adentro en gris: «{b['placeholder']}»"
                if b.get("value"): extra += f", ya tiene escrito: «{b['value']}»"
                if b.get("options"): extra += f"; opciones: {', '.join(b['options'][:8])}"
                lines.append(f"{k}. [{b['i']}] {what}{extra}")
            else:
                kind = "botón" if b.get("buttonish") else "desplegable" if b["role"] == "disclosure" else "pestaña" if b["role"] == "tab" else "enlace"
                lines.append(f"{k}. [{b['i']}] {kind}: «{frag}»" if frag else f"{k}. [{b['i']}] un ícono sin texto {where(b, W, H)}")
        else:
            kind = "título grande" if (b.get("heading") or b["fs"] >= 22) else "texto"
            lines.append(f"{k}. {kind}: «{frag}»")
    seen = {r["block"] for r in read}
    periph = []
    for idx, b in enumerate(blocks):
        if idx in seen or b["ad"]: continue
        area = b["w"] * b["h"] / (W * H)
        weight = area * (2.5 if b.get("buttonish") else 1.6 if b["kind"] == "image" else 1.0) * (1 + (b["fs"] >= 24))
        if area > 0.015 or (b.get("buttonish") and area > 0.004):
            periph.append((weight, idx, b))
    periph.sort(key=lambda x: -x[0])
    side, letters = [], {}
    for letter, (_, idx, b) in zip("ABC", periph[:3]):
        if b.get("buttonish"): what = f"un botón {color_name(b['ownBg'])}"
        elif b["kind"] == "image": what = "una imagen grande"
        elif b.get("type"): what = "un campo de formulario"
        elif b["fs"] >= 24: what = "un título grande"
        else: what = "un bloque de texto"
        side.append(f"{letter}) {what} {where(b, W, H)}")
        letters[letter] = b
    return lines, side, actionable, letters


# ── the person (GLM Flash over OpenRouter: see openrouter.py) ───────────────
RULES = """You are a real person taking part in a usability test of a Spanish-language website, thinking aloud. You are NOT an assistant and NOT an expert tester.

HOW YOU PERCEIVE (this is enforced; obey it):
- Each turn you get only what your eyes actually took in on this screen, in the order you looked: fragments of text and a few controls with [numbers]. A fragment ending in "…" is where you stopped reading; you do not know the rest. Everything not listed you did NOT see.
- "De reojo" lists big shapes you noticed without reading them. To read one, use {"do":"look","at":"A"}. To look at an area of THIS screen: {"do":"look","at":"abajo"|"arriba"|"izquierda"|"derecha"}. Looking only moves your eyes; it never moves the page. To get back to the top of the page you must scroll up (negative dy). Looking costs time.
- You can only click or type into controls that have a [number] in the list.

HOW YOU BEHAVE (research on real users; be this person, flaws included):
- Satisfice: take the first option that looks good enough; do not survey everything first.
- Follow your own words: you look for what you would call the thing, not the site's jargon. Words you do not understand stay meaningless to you and you might skip them or misread them.
- Expect the site to work like the sites you already use (MercadoLibre, your bank, Google, WhatsApp).
- You skip long paragraphs and instructions unless stuck. You trust big, colorful, obvious things; you ignore things that look like ads.
- You make honest mistakes and wrong guesses, and act on them until the screen shows you were wrong. Do not recover unrealistically well.
- You are impatient in proportion to your persona. When lost or annoyed enough, a real person gives up; so do you.

EVERY REPLY is exactly ONE JSON object, nothing else:
{"say":"...","looking_for":["...","..."],"expect":"...","do":"click","i":12}
- "say": what you say out loud BEFORE acting: 1-2 short sentences in natural Argentine Spanish (voseo), never English, first person, like "A ver... dice tienda, capaz está ahí." Mention what caught your eye, your guess, your feeling. Never mention numbers in brackets, JSON, "elemento", "control" or "fragmento".
- "looking_for": 1-4 words in YOUR vocabulary for what your eyes will hunt for next.
- "expect": what you expect to see after this action.
Actions: click {"i":N} · type {"i":N,"text":"...","enter":false} · select {"i":N,"value":"option text"} · scroll {"dy":600} (negative = up) · back {} · look {"at":"..."} · finish {"verdict":"done"|"giveup","seq":1-7,"answer":"...","note":"..."}
finish: "seq" answers "¿Qué tan fácil o difícil fue?" 1 (muy difícil) to 7 (muy fácil), honest for this person; "note" = one or two Spanish sentences on what got in the way."""


def first_json(text):
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m: return None
    try: return json.loads(m.group(0))
    except json.JSONDecodeError:
        try: return json.loads(re.sub(r",\s*}", "}", m.group(0)))
        except json.JSONDecodeError: return None


def settle(page):
    """Wait until on-screen images have loaded, as a person would see them."""
    try:
        page.wait_for_function("""() => [...document.images].filter((i) => { const r = i.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight && r.width > 0; }).every((i) => i.complete)""", timeout=4000)
    except Exception:  # noqa: BLE001 — a broken image is part of what the person sees
        pass
    page.wait_for_timeout(250)


def banner_html(banner, landing):
    """A social-feed post carrying the ad. The ad's lettering is baked into the image, so each
    line is laid over its own spot as transparent text: the gaze model reads it where it is."""
    import base64, html as H
    img = Path(os.path.expanduser(banner["image"]))
    uri = f"data:image/{'webp' if img.suffix == '.webp' else 'jpeg'};base64," + base64.b64encode(img.read_bytes()).decode()
    width = banner.get("width", 460)
    ratio = banner.get("ratio", 1350 / 1080)
    spans = "".join(
        f'<span class="ov" style="left:{b["x"]}%;top:{b["y"]}%;width:{b["w"]}%;height:{b["h"]}%;font-size:{b["fs"]}px">{H.escape(b["text"])}</span>'
        for b in banner.get("boxes", []))
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Facebook</title>
<style>body{{margin:0;background:#f0f2f5;font:15px/1.3 Helvetica,Arial,sans-serif;color:#050505}}
.post{{width:{width}px;max-width:calc(100% - 24px);margin:24px auto;background:#fff;border-radius:10px;box-shadow:0 1px 2px rgba(0,0,0,.2)}}
.hd{{display:flex;gap:10px;align-items:center;padding:12px 14px}} .av{{width:40px;height:40px;border-radius:50%;background:#111;color:#fff;display:grid;place-items:center;font-weight:700}}
.nm{{font-weight:700}} .sp{{color:#65676b;font-size:13px}} .tx{{padding:0 14px 10px}}
.media{{position:relative;display:block;aspect-ratio:1/{ratio};overflow:hidden}} .media img{{width:100%;height:100%;object-fit:cover;display:block}}
.ov{{position:absolute;color:rgba(255,255,255,0);font-weight:800;line-height:1;white-space:nowrap;overflow:hidden;cursor:pointer}}
.ft{{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 14px;background:#f7f8fa;border-radius:0 0 10px 10px}}
.dm{{color:#65676b;font-size:13px;text-transform:uppercase}} .hl{{font-weight:700}} .cta{{background:#e4e6eb;color:#050505;border-radius:6px;padding:8px 14px;font-weight:700;text-decoration:none}}</style></head>
<body><article class="post"><div class="hd"><div class="av">{H.escape(banner.get("brand", "B")[:1])}</div><div><div class="nm">{H.escape(banner.get("brand", "Bruno"))}</div><div class="sp">Publicidad</div></div></div>
<div class="tx">{H.escape(banner.get("primary", ""))}</div>
<div class="media"><a href="{landing}"><img src="{uri}" alt="{H.escape(banner.get("alt", ""))}"></a>{spans}</div>
<div class="ft"><div><div class="dm">{H.escape(banner.get("domain", ""))}</div><div class="hl">{H.escape(banner.get("headline", ""))}</div></div><a class="cta" href="{landing}">{H.escape(banner.get("cta", "Más información"))}</a></div>
</article><script>document.querySelectorAll('.ov').forEach((e) => e.addEventListener('click', () => {{ location.href = {json.dumps(landing)}; }}));</script></body></html>"""


# ── lite mode: coordinate-free trace, auth, setup, probes, findings ─────────
def _trace_seen(lines):
    """describe() output → plain trace strings ('botón: Agendar visita'), no [i], no «»."""
    out = []
    for ln in lines:
        s = re.sub(r"^\d+\.\s*(\[\d+\]\s*)?", "", ln).replace("«", "").replace("»", "").strip()
        if s:
            out.append(s)
    return out


def _trace_glanced(side):
    return [re.sub(r"^[A-C]\)\s*", "", s).strip() for s in side]


def _lite_action(do, target=None, at=None, text=None):
    """Action record for the lite trace: labels and selectors only, never coordinates."""
    label = None
    if target is not None:
        label = ((target.get("label") or "").strip() or (target.get("fieldLabel") or "").strip())[:100] or None
    elif at:
        label = at
    return {"do": do, "target_label": label, "target_dom_id": (target or {}).get("dom_id"), "text_typed": text}


def _screen_position(post):
    h = max(1, post.get("H") or 1)
    total = max(1, round((post.get("maxScroll") or 0) / h) + 1)
    cur = min(total, (post.get("scrollY") or 0) // h + 1)
    return f"pantalla {cur} de {total}"


def _outcome_note(view, post):
    if post.get("url") != view.get("url"):
        return f"new URL: {post['url']}"
    if post.get("modal") and not view.get("modal"):
        return "dialog opened"
    if post.get("toast"):
        return "toast appeared"
    return "nothing"


def _trace_append(out, line):
    with (out / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _abs_url(study, path):
    return path if str(path).startswith("http") else study["site"].rstrip("/") + str(path)


def _setup_value(v):
    # {"env": "VAR"} indirection keeps secrets out of the study JSON
    if isinstance(v, dict) and "env" in v:
        return os.environ.get(v["env"], "")
    return v


def _authenticate(page, study, login):
    """Log the session in before the persona starts, from env vars; the persona never
    sees the login screen and the credentials never reach logs, traces or results."""
    user = os.environ.get(login.get("user_env", ""), "")
    pwd = os.environ.get(login.get("pass_env", ""), "")
    if not user or not pwd:
        raise RuntimeError(f"login: set ${login.get('user_env')} and ${login.get('pass_env')}")
    page.goto(_abs_url(study, login["url"]), wait_until="networkidle", timeout=45000)
    page.wait_for_timeout(400)
    page.locator("input[type=email], input[name*='user' i], input[autocomplete*='username' i], input[type=text]").first.fill(user)
    page.locator("input[type=password]").first.fill(pwd)
    page.locator("button[type=submit], input[type=submit], button").first.click()
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:  # noqa: BLE001 — a busy page is still a page
        pass
    page.wait_for_timeout(400)
    if page.locator("input[type=password]").count():
        raise RuntimeError("login: still on a password screen after submitting")
    log(f"auth ok via {login.get('user_env')} (value never logged)")


def _run_setup(ctx, page, study, setup):
    """Pre-task steps: {"http": {method,url,json|body}}, {"goto"}, {"click"}, {"fill": {selector, value}}."""
    for step in setup:
        if "http" in step:
            r = step["http"]
            kw = {"method": r.get("method", "GET")}
            if "json" in r:
                kw["data"] = json.dumps(r["json"])
                kw["headers"] = {"content-type": "application/json"}
            elif "body" in r:
                kw["data"] = str(_setup_value(r["body"]))
            resp = ctx.request.fetch(_abs_url(study, r["url"]), **kw)
            log(f"setup {kw['method']} {r['url']} -> {resp.status}")
        elif "goto" in step:
            page.goto(_abs_url(study, step["goto"]), wait_until="domcontentloaded", timeout=30000)
        elif "click" in step:
            page.click(step["click"], timeout=10000)
        elif "fill" in step:
            page.fill(step["fill"]["selector"], str(_setup_value(step["fill"]["value"])))


def _jsonpath(data, path):
    """Minimal jsonpath for probe counts: $.a.b and $.a[2].b."""
    cur = data
    for m in re.finditer(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]", str(path).lstrip("$")):
        if cur is None:
            break
        if m.group(1):
            cur = cur.get(m.group(1)) if isinstance(cur, dict) else None
        else:
            i = int(m.group(2))
            cur = cur[i] if isinstance(cur, list) and i < len(cur) else None
    return cur


def _run_probes(probes):
    """Side-channel checks after the task: file+regex or http+jsonpath → [{name, passed, count, message}]."""
    out = []
    for p in probes or []:
        row = {"name": p.get("name", "probe"), "passed": False, "count": 0, "message": ""}
        try:
            if p.get("method") == "file":
                text = Path(os.path.expanduser(p["path"])).read_text(errors="replace")
            elif p.get("method") == "http":
                text = urllib.request.urlopen(p["url"], timeout=15).read().decode("utf-8", "replace")
            else:
                raise ValueError(f"unknown probe method {p.get('method')!r}")
            if p.get("check") == "regex":
                hits = re.findall(p.get("pattern", ""), text)
                row.update(passed=bool(hits), count=len(hits), message=f"{len(hits)} match(es) of /{p.get('pattern')}/")
            elif p.get("check") == "jsonpath":
                val = _jsonpath(json.loads(text), p.get("path", "$"))
                if isinstance(val, bool):
                    n = 1 if val else 0
                elif isinstance(val, (int, float)):
                    n = val
                elif isinstance(val, list):
                    n = len(val)
                else:
                    n = 1 if val else 0
                row.update(passed=bool(val), count=n, message=f"{p.get('path')} = {val!r}")
            else:
                raise ValueError(f"unknown probe check {p.get('check')!r}")
        except Exception as err:  # noqa: BLE001 — a failed probe is a result, not a crash
            row["message"] = str(err).split("\n")[0][:160]
        out.append(row)
    return out


SEVERITIES = ("blocker", "major", "minor", "cosmetic")
CATEGORIES = ("layout", "copy", "navigation", "missing-feature", "bug", "notification", "performance")
RESEARCHER_RULES = """You are a usability RESEARCHER analyzing one simulated session; you are NOT the persona and NOT an assistant. The trace is one JSON object per step: what a fallible persona looked at, said, did, expected and what actually happened.

Report interface problems only, grounded in this trace:
- Every finding must cite step numbers and quote the persona's own Spanish words when possible.
- The persona's own mistakes count only if the interface caused or failed to catch them.
- Do not invent elements: only what appears in looked_at / glanced / action / outcome_note exists.
- severity: blocker = the task became impossible; major = completed via a wrong or costly path; minor = friction; cosmetic = polish.
- category: one of layout | copy | navigation | missing-feature | bug | notification | performance.
- 0 to 8 findings, most severe first; an empty list is a valid answer.

Reply with exactly ONE JSON object, nothing else:
{"findings": [{"title": "...", "severity": "blocker|major|minor|cosmetic", "category": "...", "evidence_steps": [2, 5], "quote": "the persona's Spanish words", "suggested_fix": "concrete change"}]}"""


def _extract_findings(glm, case, trace):
    """One researcher-role GLM call over the coordinate-free trace only; best-effort."""
    trace_text = "\n".join(json.dumps(t, ensure_ascii=False) for t in trace)
    user = f"TASK given to the persona: «{case['task']}»\n\nTRACE ({len(trace)} steps, one JSON object per line):\n{trace_text}"
    try:
        reply = glm(RESEARCHER_RULES, [{"role": "user", "content": user}], max_tokens=8000)   # Flash reasons before answering; 2500 truncated the JSON
        j = first_json(reply)
        rows = j.get("findings") if isinstance(j, dict) else j
    except Exception as err:  # noqa: BLE001 — findings never crash the case
        log(f"findings extraction failed: {err}")
        return []
    out = []
    for f in rows if isinstance(rows, list) else []:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "")).lower()
        cat = str(f.get("category", "")).lower()
        steps = []
        for x in (f.get("evidence_steps") or [])[:12]:
            try:
                steps.append(int(x))
            except (TypeError, ValueError):
                continue
        out.append({"title": str(f.get("title", "")).strip()[:120] or "Untitled finding",
                    "severity": sev if sev in SEVERITIES else "minor",
                    "category": cat if cat in CATEGORIES else "bug",
                    "evidence_steps": steps,
                    "quote": str(f.get("quote", ""))[:300],
                    "suggested_fix": str(f.get("suggested_fix", ""))[:300]})
    return out


# ── one case ────────────────────────────────────────────────────────────────
def run_case(pw, study, case, persona, out, glm, lite=False):
    rng = random.Random(case.get("seed", 7))
    dev = DEVICES[persona["device"]]
    dpr = dev["device_scale_factor"]
    browser = pw.chromium.launch()
    ctx_kwargs = {"locale": "es-AR", **dev}
    if case.get("storage_state"):
        ctx_kwargs["storage_state"] = os.path.expanduser(case["storage_state"])
    ctx = browser.new_context(**ctx_kwargs)
    page = ctx.new_page()
    openers = []   # tabs a target=_blank link came from
    if case.get("login"):
        _authenticate(page, study, case["login"])   # antes de la tarea: el usuario ya está logueado
    if case.get("setup"):
        _run_setup(ctx, page, study, case["setup"])
    url = study["site"].rstrip("/") + case.get("start", "/")
    if case.get("banner"):
        page.set_content(banner_html(case["banner"], url), wait_until="load")
    else:
        page.goto(url, wait_until="networkidle", timeout=45000)
    page.wait_for_timeout(800)
    shots = out / "screens"; shots.mkdir(parents=True, exist_ok=True)
    unknown = ", ".join(persona["unknown_terms"]) or "nada en especial"
    system = (f"{RULES}\n\nYOUR PERSONA:\n{persona['description']}\nWords you do not understand: {unknown}.\n\n"
              f"YOUR TASK (the moderator read it to you once):\n«{case['task']}»\n\nYou have at most {persona['max_actions']} actions.")
    goal = stems(case["task"])
    task_goal = set(goal)
    history, steps, used, expect, focus, looks = [], [], 0, "", None, 0
    sim_ms = 0.0
    result = None
    view = None
    fresh = True
    trace = []
    while True:
        if fresh:
            settle(page)
            view = page.evaluate(LOOK_JS)
            looks = 0
        n = len(steps) + 1
        shot = None
        if not lite:
            shot = shots / f"step-{n:02d}.png"
            page.screenshot(path=str(shot))
        budget = persona["screen_budget_s"] * (1.0 if fresh else 0.45)
        fixes, read, gaze_ms = scan(view, persona, goal, rng, budget, focus)
        sim_ms += gaze_ms
        lines, side, actionable, letters = describe(view, read, persona)
        seen_idx = {r["block"] for r in read}
        missed = [b["label"][:60] for i, b in enumerate(view["blocks"]) if b["kind"] == "control" and i not in seen_idx and not b.get("type") and stems(b["label"]) & task_goal]
        more = ""
        if view["maxScroll"] - view["scrollY"] > 4:
            if persona["device"] == "desktop": more = "La barra de desplazamiento muestra que la página sigue más abajo."
            elif view["cutAtFold"]: more = "Abajo de todo se ve algo cortado: parece que sigue."
        down = view["scrollY"] / max(1, view["H"])
        where_page = "Estás al principio de la página." if down < 0.05 else f"Bajaste unas {max(1, round(down))} pantallas desde el principio de la página."
        msg = [f"Pantalla {n}. {where_page} Te quedan {persona['max_actions'] - used} acciones. Llevás {round(sim_ms / 1000)} segundos."]
        if expect: msg.append(f"(Antes de tocar esperabas: «{expect}».)")
        msg.append("Lo que leíste, en el orden en que lo miraste:" if lines else "No alcanzaste a leer nada en esta pantalla.")
        msg += lines
        if side: msg.append("De reojo, sin leer: " + "; ".join(side) + ".")
        if more: msg.append(more)
        user = "\n".join(msg)
        history.append({"role": "user", "content": user})
        compact = [m if (m["role"] == "assistant" or k >= len(history) - 1) else {"role": "user", "content": m["content"].split("\n")[0] + " …(pantalla anterior)"} for k, m in enumerate(history)]
        a, fails = None, 0
        while a is None:
            reply = glm(system, compact)
            a = first_json(reply)
            if not a or "do" not in a:
                fails += 1
                a = None
                if fails > 2:
                    result = {"verdict": "tool-failure", "note": "the model did not reply with one JSON action"}
                    break
                compact = compact + [{"role": "assistant", "content": reply or "{}"}, {"role": "user", "content": "Respondé con UN solo objeto JSON."}]
        if result: break
        history.append({"role": "assistant", "content": json.dumps(a, ensure_ascii=False)})
        step = {"n": n, "url": view["url"], "scrollY": view["scrollY"], "screenshot": shot.name if shot else None, "dpr": dpr, "viewport": [view["W"], view["H"]],
                "fixations": fixes, "perceived": lines, "peripheral": side, "missed_task_controls": missed, "reply": a, "sim_ms": round(sim_ms)}
        steps.append(step)
        line = None
        if lite:
            line = {"step": n, "url_path": view["url"], "page_title": view.get("title") or "", "thinking": a.get("say", ""),
                    "looked_at": _trace_seen(lines), "glanced": _trace_glanced(side), "action": None, "scroll": None,
                    "expected": a.get("expect", ""), "outcome_note": ""}
            trace.append(line)
        log(f"{case['id']} #{n} {a.get('do')} {a.get('i', a.get('at', ''))} — {a.get('say', '')[:90]}")
        focus, fresh = None, True
        goal = stems(" ".join(a.get("looking_for") or [])) or goal
        expect = a.get("expect", "")
        do = a.get("do")
        if do == "finish":
            result = {"verdict": "done" if a.get("verdict") == "done" else "giveup", "seq": a.get("seq"), "answer": a.get("answer", ""), "note": a.get("note", "")}
            if lite:
                line["action"] = _lite_action("finish")
                line["outcome_note"] = f"task finished: {result['verdict']}"
                _trace_append(out, line)
            break
        if used >= persona["max_actions"] or sim_ms > persona["max_minutes"] * 60000:
            result = {"verdict": "giveup", "note": "se le acabó la paciencia (límite de acciones o de tiempo)"}
            if lite:
                t = actionable.get(int(a["i"])) if str(a.get("i", "")).isdigit() else None
                line["action"] = _lite_action(do, target=t, text=None if a.get("do") != "type" else str(a.get("text", "")))
                line["outcome_note"] = "limit reached; this action was never performed"
                _trace_append(out, line)
            break
        try:
            if do == "look":
                looks += 1
                at = str(a.get("at", "")).strip()
                W, H = view["W"], view["H"]
                if at.upper() in letters:
                    b = letters[at.upper()]; focus = (b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
                else:
                    focus = {"arriba": (W / 2, H * 0.15), "abajo": (W / 2, H * 0.85), "izquierda": (W * 0.2, H / 2), "derecha": (W * 0.8, H / 2)}.get(norm(at), (W / 2, H / 2))
                step["action"] = {"do": "look", "focus": focus}
                if lite:
                    line["action"] = _lite_action("look", at=at)
                    line["outcome_note"] = "nothing (only looked)"
                    _trace_append(out, line)
                fresh = False
                if looks > 3: used += 1
                continue
            used += 1
            i = a.get("i")
            if do in ("click", "type", "select"):
                i = int(i)
                if i not in actionable:
                    raise ValueError("no ves ese elemento en la pantalla")
                target = actionable[i]
                if do == "click" and persona["slip_rate"] and target["h"] < 40 and rng.random() < persona["slip_rate"]:
                    near = [b for b in view["blocks"] if b["kind"] == "control" and b["i"] != i and not b.get("type")
                            and abs((b["y"] + b["h"] / 2) - (target["y"] + target["h"] / 2)) < 48 and abs((b["x"] + b["w"] / 2) - (target["x"] + target["w"] / 2)) < 160]
                    if near:
                        target = min(near, key=lambda b: abs(b["y"] - target["y"]) + abs(b["x"] - target["x"]))
                        step["slip"] = {"intended": i, "hit": target["i"]}
                        log(f"  motor slip: meant [{i}], hit [{target['i']}]")
                cx, cy = target["x"] + target["w"] / 2, target["y"] + min(target["h"] / 2, 22)
                step["action"] = {"do": do, "i": target["i"], "xy": [cx, cy], "box": [target["x"], target["y"], target["w"], target["h"]]}
                if lite:
                    if do == "type":
                        typed = None if target.get("type") == "password" else str(a.get("text", ""))
                    elif do == "select":
                        typed = str(a.get("value", ""))
                    else:
                        typed = None
                    line["action"] = _lite_action(do, target=target, text=typed)
                el = page.locator(f"[data-fp-i='{target['i']}']").first
                if do == "click":
                    if persona["device"] == "phone": page.touchscreen.tap(cx, cy)
                    else: page.mouse.click(cx, cy)
                    sim_ms += 900
                elif do == "type":
                    el.click()
                    text = str(a.get("text", ""))
                    el.fill("")
                    el.type(text, delay=30)
                    sim_ms += len(text) * (380 if persona["reading"] == "plower" else 220)
                    if a.get("enter"): page.keyboard.press("Enter")
                else:
                    el.select_option(label=str(a.get("value", "")))
                    sim_ms += 1500
            elif do == "scroll":
                dy = int(a.get("dy", 600))
                step["action"] = {"do": "scroll", "dy": dy}
                if lite:
                    line["action"] = _lite_action("scroll")
                page.mouse.wheel(0, dy) if persona["device"] == "desktop" else page.evaluate(f"window.scrollBy(0,{dy})")
                sim_ms += 700
            elif do == "back":
                step["action"] = {"do": "back"}
                if lite:
                    line["action"] = _lite_action("back")
                went = page.go_back(wait_until="domcontentloaded", timeout=20000)
                if went is None and openers and page.evaluate("history.length") <= 1:
                    page.close(); page = openers.pop(); page.bring_to_front()   # phone back in a fresh tab closes it
                sim_ms += 1200
            else:
                raise ValueError(f"acción desconocida: {do}")
            t0 = time.time()
            try: page.wait_for_load_state("networkidle", timeout=6000)
            except Exception: pass  # noqa: BLE001 — a busy page is still a page
            page.wait_for_timeout(500)
            wait = time.time() - t0
            if wait > 4: sim_ms += wait * 1000
            opened_note = None
            extra = [p for p in ctx.pages if p is not page] if do == "click" else []
            if extra:   # a target=_blank link: a phone opens it in a new tab or in another app
                newp = extra[-1]
                try: newp.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception: pass  # noqa: BLE001 — an external app URL may never load
                nu = newp.url or ""
                site_host = re.sub(r"^https?://", "", study["site"]).split("/")[0]
                if site_host and site_host in nu.split("/")[2:3]:
                    openers.append(page); page = newp  # same site: the new tab is now the screen; back returns to the opener
                    page.wait_for_timeout(500)
                else:
                    host = (nu.split("/")[2] if "//" in nu else nu)[:60]
                    wa = re.search(r"(?:wa\.me/|phone=)(\d{6,})", nu)
                    opened_note = f"opened outside the site: {host}" + (f" (WhatsApp chat to {wa.group(1)})" if wa else "")
                    history.append({"role": "user", "content": (f"(Se abrió WhatsApp con un chat nuevo al número {wa.group(1)}; volviste a la página.)" if wa
                                                                else f"(Se abrió otra aplicación o sitio: {host}; volviste a la página.)")})
                    newp.close()
            if lite:
                post = page.evaluate(POST_JS)
                if do == "scroll":
                    line["scroll"] = {"dy": int(a.get("dy", 600)), "position": _screen_position(post)}
                line["outcome_note"] = opened_note or _outcome_note(view, post)
                _trace_append(out, line)
        except Exception as err:  # noqa: BLE001 — told to the persona, not a crash
            step["action_error"] = str(err).split("\n")[0][:160]
            history.append({"role": "user", "content": f"(Eso no se pudo hacer: {step['action_error']})"})
            if lite:
                try:
                    page.screenshot(path=str(shots / f"error-{n:02d}.png"))   # one screenshot per error
                except Exception:  # noqa: BLE001 — the page may be gone
                    pass
                if line["action"] is None:
                    line["action"] = _lite_action(do)
                line["outcome_note"] = f"error: {step['action_error']}"
                _trace_append(out, line)
            fresh = False
    if lite:
        try:
            page.screenshot(path=str(shots / "final.png"))   # one screenshot at task end
        except Exception:  # noqa: BLE001 — the page may be gone
            pass
    ctx.close(); browser.close()
    if lite:
        sname = study.get("name") or Path(os.path.expanduser(study["out"])).name
        probes = _run_probes(case.get("probes"))
        findings = _extract_findings(glm, case, trace) if (glm and trace) else []
        actions = sum(1 for t in trace if (t.get("action") or {}).get("do") in ("click", "type", "select", "scroll", "back"))
        lite_result = {"study": sname, "case": case["id"], "persona": persona["name"], "device": persona["device"], "task": case["task"],
                       "verdict": (result or {}).get("verdict"), "seq": (result or {}).get("seq"),
                       "simulated_seconds": round(sim_ms / 1000), "action_count": actions, "steps": len(trace),
                       "answer": (result or {}).get("answer", ""), "note": (result or {}).get("note", ""),
                       "findings": findings, "probes": probes, "model": getattr(glm, "model", None)}
        (out / "result.json").write_text(json.dumps(lite_result, ensure_ascii=False, indent=1))
    return {"case": case, "persona": persona, "steps": steps, "result": result, "sim_seconds": round(sim_ms / 1000)}


# ── voice ───────────────────────────────────────────────────────────────────
def openai_key():
    """TTS key: $OPENAI_API_KEY only (full mode). Lite mode never calls this."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("no OpenAI key for TTS: $OPENAI_API_KEY is not set")
    return key


def tts(key, text, voice, style, path):
    body = json.dumps({"model": TTS_MODEL, "voice": voice, "input": text, "response_format": "mp3",
                       "instructions": "Español rioplatense de Argentina, con voseo. Es una persona común pensando en voz alta frente a la pantalla durante una prueba de usabilidad: natural, sin tono de locutor, con pausas y dudas. " + style}).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            path.write_bytes(urllib.request.urlopen(req, timeout=90).read())
            return
        except Exception as err:  # noqa: BLE001
            log(f"TTS retry {attempt + 1}: {err}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("TTS failed")


def duration(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], text=True).strip())


def voice_steps(run, out):
    key = openai_key()
    audio = out / "audio"; audio.mkdir(exist_ok=True)
    persona = run["persona"]
    jobs = []
    with cf.ThreadPoolExecutor(4) as pool:
        for s in run["steps"]:
            say = (s["reply"].get("say") or "").strip()
            if not say: continue
            p = audio / f"step-{s['n']:02d}.mp3"
            s["audio"] = str(p)
            jobs.append(pool.submit(tts, key, say, persona["voice"], persona["voice_style"], p))
        for j in jobs: j.result()
    for s in run["steps"]:
        if s.get("audio"): s["audio_s"] = duration(s["audio"])


# ── eye-tracking replay ─────────────────────────────────────────────────────
def render_video(run, out, fps=25):
    from PIL import Image, ImageDraw, ImageFilter
    steps = run["steps"]
    first = Image.open(out / "screens" / steps[0]["screenshot"])
    Wp, Hp = first.size
    Wp -= Wp % 2; Hp -= Hp % 2
    dpr = steps[0]["dpr"]
    phone = run["persona"]["device"] == "phone"
    frames_path = out / "replay-video.mp4"
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{Wp}x{Hp}", "-r", str(fps), "-i", "-",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-preset", "medium", str(frames_path)], stdin=subprocess.PIPE)
    ring = 2 * dpr

    def base_img(s):
        return Image.open(out / "screens" / s["screenshot"]).convert("RGB").crop((0, 0, Wp, Hp))

    def draw_frame(img, gaze, trail, cursor=None, ripple=None):
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        pts = trail[-5:]
        for k in range(1, len(pts)):
            a = int(60 + 30 * k)
            d.line([pts[k - 1][:2], pts[k][:2]], fill=(255, 150, 0, a), width=max(2, int(1.5 * dpr)))
        for k, (x, y, r) in enumerate(pts[:-1]):
            a = int(40 + 25 * k)
            d.ellipse([x - r * 0.6, y - r * 0.6, x + r * 0.6, y + r * 0.6], fill=(255, 190, 0, a), outline=(255, 140, 0, a + 40), width=max(1, dpr))
        if gaze:
            x, y, r = gaze
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 196, 0, 105), outline=(255, 130, 0, 230), width=ring)
        if ripple:
            x, y, p = ripple
            rr = (10 + 30 * p) * dpr
            d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=(20, 110, 255, int(255 * (1 - p))), width=3 * dpr)
        if cursor:
            x, y = cursor
            if phone:
                d.ellipse([x - 16 * dpr, y - 16 * dpr, x + 16 * dpr, y + 16 * dpr], fill=(60, 60, 60, 90), outline=(255, 255, 255, 200), width=dpr)
            else:
                arrow = [(0, 0), (0, 17), (4.5, 13), (7.5, 20), (10, 19), (7, 12.5), (12.5, 12.5)]
                poly = [(x + px * dpr * 1.2, y + py * dpr * 1.2) for px, py in arrow]
                d.polygon(poly, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
        return Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")

    def emit(img):
        ff.stdin.write(img.tobytes())

    ease = lambda p: p * p * (3 - 2 * p)
    t = 0.0
    starts = []
    cursor = (Wp * 0.6, Hp * 0.55)
    gaze, trail = None, []
    heat_dir = out / "heatmaps"; heat_dir.mkdir(exist_ok=True)
    for si, s in enumerate(steps):
        img = base_img(s)
        starts.append(t)
        fixes = [(f["x"] * dpr, f["y"] * dpr, f["ms"]) for f in s["fixations"]]
        heat(img, fixes, heat_dir / f"step-{s['n']:02d}.png", dpr)
        seg = sum(ms for *_, ms in fixes) / 1000 + 0.05 * len(fixes)
        need = max(seg, s.get("audio_s", 0) + 0.35, 1.2)
        # fixations
        frames = int(need * fps)
        timeline = []
        acc = 0.0
        for (x, y, ms) in fixes:
            timeline.append((acc, acc + 0.05, x, y, ms))
            acc += 0.05 + ms / 1000
        trail = []
        for k in range(frames):
            now = k / fps
            cur = None
            for j, (a0, a1, x, y, ms) in enumerate(timeline):
                if now >= a0: cur = j
            if cur is None:
                g = gaze
            else:
                a0, a1, x, y, ms = timeline[cur]
                if now < a1 and gaze:
                    p = ease((now - a0) / 0.05)
                    g = (gaze[0] + (x - gaze[0]) * p, gaze[1] + (y - gaze[1]) * p, gaze[2])
                else:
                    dwell = max(0.0, min(ms / 1000, now - a1))
                    g = (x, y, (12 + 11 * math.sqrt(dwell / 0.3)) * dpr)
                trail = [(tx, ty, (12 + 11 * math.sqrt(tms / 300)) * dpr) for (_, _, tx, ty, tms) in timeline[max(0, cur - 4):cur]] + [g]
            emit(draw_frame(img, g, trail, None if phone else cursor))
        if timeline:
            _, _, x, y, ms = timeline[-1]
            gaze = (x, y, (12 + 11 * math.sqrt(ms / 300)) * dpr)
        t += frames / fps
        act = s.get("action") or {}
        nxt = base_img(steps[si + 1]) if si + 1 < len(steps) else None
        if act.get("do") in ("click", "type", "select") and act.get("xy"):
            tx, ty = act["xy"][0] * dpr, act["xy"][1] * dpr
            n = int(0.7 * fps)
            g0 = gaze or (tx, ty, 14 * dpr)
            for k in range(n):
                p = ease(k / (n - 1))
                g = (g0[0] + (tx - g0[0]) * min(1, p * 2.5), g0[1] + (ty - g0[1]) * min(1, p * 2.5), 16 * dpr)
                c = (cursor[0] + (tx - cursor[0]) * p, cursor[1] + (ty - cursor[1]) * p)
                emit(draw_frame(img, g, [g], None if phone else c))
            cursor = (tx, ty)
            n2 = int(0.35 * fps)
            for k in range(n2):
                emit(draw_frame(img, (tx, ty, 16 * dpr), [(tx, ty, 16 * dpr)], cursor, (tx, ty, k / n2)))
            gaze = (tx, ty, 16 * dpr)
            t += (n + n2) / fps
        if nxt is not None:
            n = int((0.5 if act.get("do") == "scroll" else 0.3) * fps)
            dy = int(act.get("dy", 0)) * dpr if act.get("do") == "scroll" else 0
            for k in range(n):
                p = ease(k / (n - 1))
                if dy:
                    frame = Image.new("RGB", (Wp, Hp), (255, 255, 255))
                    off = int(min(abs(dy), Hp) * p) * (1 if dy > 0 else -1)
                    frame.paste(img, (0, -off))
                    frame.paste(nxt, (0, Hp - off if dy > 0 else -Hp - off))
                else:
                    frame = Image.blend(img, nxt, p)
                emit(draw_frame(frame, None, [], None if phone else cursor))
            t += n / fps
    tail = int(1.0 * fps)
    last = base_img(steps[-1])
    for _ in range(tail): emit(draw_frame(last, None, [], None if phone else cursor))
    t += tail / fps
    ff.stdin.close(); ff.wait()
    # narration track + soft subtitles
    clips = [(starts[k], s["audio"]) for k, s in enumerate(steps) if s.get("audio")]
    srt = out / "narration.srt"
    with srt.open("w") as f:
        for k, (st, s) in enumerate([(starts[j], x) for j, x in enumerate(steps) if x.get("audio")], 1):
            en = st + s["audio_s"]
            ts = lambda v: f"{int(v // 3600):02d}:{int(v % 3600 // 60):02d}:{int(v % 60):02d},{int(v * 1000 % 1000):03d}"
            f.write(f"{k}\n{ts(st)} --> {ts(en)}\n{s['reply']['say']}\n\n")
    final = out / "replay.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(frames_path)]
    for _, p in clips: cmd += ["-i", p]
    cmd += ["-i", str(srt)]
    if clips:
        parts = "".join(f"[{k + 1}:a]adelay={int(st * 1000)}|{int(st * 1000)}[a{k}];" for k, (st, _) in enumerate(clips))
        mix = "".join(f"[a{k}]" for k in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0,apad[aout]"
        cmd += ["-filter_complex", parts + mix, "-map", "0:v", "-map", "[aout]", "-map", f"{len(clips) + 1}:s", "-shortest",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-c:s", "mov_text", "-metadata:s:s:0", "language=spa"]
    else:
        cmd += ["-map", "0:v", "-map", "1:s", "-c:v", "copy", "-c:s", "mov_text"]
    subprocess.run(cmd + [str(final)], check=True)
    frames_path.unlink()
    return final


def heat(img, fixes, path, dpr):
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    if not fixes: return
    w, h = img.size
    small = 4
    acc = Image.new("L", (w // small, h // small), 0)
    for x, y, ms in fixes:
        r = 26 * dpr / small
        layer = Image.new("L", acc.size, 0)
        ImageDraw.Draw(layer).ellipse([x / small - r, y / small - r, x / small + r, y / small + r], fill=min(255, int(40 + ms / 6)))
        acc = ImageChops.add(acc, layer)
    acc = acc.filter(ImageFilter.GaussianBlur(10 * dpr / small)).resize((w, h))
    mx = max(1, acc.getextrema()[1])
    lut_r, lut_g, lut_b, lut_a = [], [], [], []
    for v in range(256):
        p = v / mx
        if p < 0.08: c = (0, 0, 0, 0)
        elif p < 0.4: c = (40, 200, 60, int(120 * p / 0.4))
        elif p < 0.7: c = (255, 220, 0, 150)
        else: c = (240, 40, 20, 170)
        lut_r.append(c[0]); lut_g.append(c[1]); lut_b.append(c[2]); lut_a.append(c[3])
    color = Image.merge("RGBA", (acc.point(lut_r), acc.point(lut_g), acc.point(lut_b), acc.point(lut_a)))
    Image.alpha_composite(img.convert("RGBA"), color).convert("RGB").save(path)


# ── report ──────────────────────────────────────────────────────────────────
def report(run, out):
    c, p, r = run["case"], run["persona"], run["result"] or {}
    lines = [f"# {p['name']} — {c['id']}", "", f"**Task:** {c['task']}", "",
             f"**Outcome:** {r.get('verdict')} · SEQ {r.get('seq', '—')} · {len(run['steps'])} screens · simulated time {run['sim_seconds']} s",
             f"**Answer:** {r.get('answer', '')}" if r.get("answer") else "", f"**Note:** {r.get('note', '')}", "",
             "Simulation evidence (GLM Flash persona behind a gaze model), not a user finding.", ""]
    for s in run["steps"]:
        a = s["reply"]
        lines += [f"## Screen {s['n']} — `{s['url']}` (scroll {s['scrollY']})", "", f"> {a.get('say', '')}", "",
                  f"Action: `{a.get('do')}` {a.get('i', a.get('at', a.get('dy', '')))}" + (f" · **motor slip** meant [{s['slip']['intended']}], hit [{s['slip']['hit']}]" if s.get("slip") else "")
                  + (f" · error: {s['action_error']}" if s.get("action_error") else ""), "", "Read:", *[f"- {x}" for x in s["perceived"]]]
        if s["peripheral"]: lines.append(f"- de reojo: {'; '.join(s['peripheral'])}")
        if s["missed_task_controls"]: lines.append(f"- **Never looked at (task words):** {'; '.join(s['missed_task_controls'])}")
        lines += ["", f"![screen {s['n']} heatmap](heatmaps/step-{s['n']:02d}.png)", ""]
    (out / "report.md").write_text("\n".join(x for x in lines if x is not None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("study")
    ap.add_argument("--only")
    ap.add_argument("--lite", action="store_true", help="lite mode: no media, per-case trace.jsonl + result.json")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--render-only", action="store_true", help="re-render voice/video/report from a saved run.json")
    args = ap.parse_args()
    study = json.loads(Path(args.study).read_text())
    lite = args.lite or study.get("mode") == "lite"
    if lite and args.render_only:
        log("--render-only re-renders full-mode run.json files; lite runs have nothing to re-render")
        return
    root = Path(os.path.expanduser(study["out"]))
    root.mkdir(parents=True, exist_ok=True)
    glm = None if args.render_only else GLM(op_item=(study.get("llm") or {}).get("openrouter_op_item"))
    from playwright.sync_api import sync_playwright
    summary = []
    with sync_playwright() as pw:
        for case in study["cases"]:
            if args.only and case["id"] not in args.only.split(","): continue
            persona = {**PERSONA_DEFAULTS, **study["personas"][case["persona"]]}
            out = root / case["id"]
            log(f"case {case['id']} — {persona['name']} on {persona['device']}" + (" [lite]" if lite else ""))
            if lite:
                if out.exists(): shutil.rmtree(out)
                out.mkdir(parents=True)
                run_case(pw, study, case, persona, out, glm, lite=True)
                r = json.loads((out / "result.json").read_text())
                summary.append({"case": case["id"], "verdict": r["verdict"], "actions": r["action_count"],
                                "sim_s": r["simulated_seconds"], "findings": len(r.get("findings", [])),
                                "probes": f"{sum(1 for p in r.get('probes', []) if p.get('passed'))}/{len(r.get('probes', []))}"})
                continue
            saved = out / "run.json"
            if args.render_only and saved.exists():
                run = json.loads(saved.read_text())
            else:
                if out.exists(): shutil.rmtree(out)
                out.mkdir(parents=True)
                run = run_case(pw, study, case, persona, out, glm)
                run["model"] = {"cognition": glm.model, "reported": sorted({c.get("reported") for c in glm.calls if c.get("reported")}), "calls": len(glm.calls), "tts": TTS_MODEL}
                saved.write_text(json.dumps(run, ensure_ascii=False, indent=1))
            if not args.no_video and run["steps"]:
                if not all(Path(s.get("audio", "/nonexistent")).exists() for s in run["steps"] if (s["reply"].get("say") or "").strip()):
                    voice_steps(run, out)
                video = render_video(run, out)
                log(f"video {video} ({duration(video):.0f} s)")
                saved.write_text(json.dumps(run, ensure_ascii=False, indent=1))
            report(run, out)
            summary.append({"case": case["id"], "verdict": (run["result"] or {}).get("verdict"), "screens": len(run["steps"]), "sim_s": run["sim_seconds"]})
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
