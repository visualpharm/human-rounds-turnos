/* centers-map.mjs — the home's map of the health centers (the product owner, 2026-11-05:
   "let's make a map... a black-and-white one").

   The drawing is a picture the data pack carries, made once from
   OpenStreetMap (scripts/build_centers_map.py) and served by our own server:
   no tiles, no third party, one request. On top of it this module lays a dot
   per place and the names as real links, sized for the patient (17 px), each
   one opening the route in Google Maps — the same destination the old
   «cómo llegar» button had. The names are a plain list of links in the HTML,
   so a screen reader reads the centers as a list and nothing is duplicated on
   screen. Centers without a point on the map (CAPS Móvil, which moves; a
   center the pack has no point for yet) go in rows under it.

   Labels are placed by a small search: tight against the dot first, further
   out on a short line when the town is crowded, never over another name or
   dot, never out of the frame, and every name clearly nearer its own dot than
   any other (the product owner's hub, 2026-11-05). Places whose dots would overlap share
   one; a shared dot leads nowhere, its names do. The map zooms in as far as
   that still works; a place whose names fit nowhere by those rules goes, dot
   and all, to the rows under the map. Pure parts (`placeGroups`, `layoutMap`, `gapTo`) are
   exported for the tests.

   Uso: render(host, centros, mapa, T, tiles) — `mapa` is /api/centers'
   `mapa`, `T` the page's translator, `tiles` the loaded tiles.mjs (its list
   draws the rows under the map, and the whole list when there is no map). */

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}

export function hasPoint(c) {
  return !!c && typeof c.map_x === "number" && typeof c.map_y === "number" && !!c.directions_url;
}

// One group per PLACE (centers at the same street address share the dot: the
// hospital, Hemoterapia and Oncología), the main center first.
export function placeGroups(centros) {
  var groups = [], byPlace = {};
  (centros || []).forEach(function (c) {
    if (!hasPoint(c)) return;
    var k = c.place || "slug:" + c.slug;
    var g = byPlace[k];
    if (!g) {
      g = { fx: c.map_x, fy: c.map_y, items: [] };
      byPlace[k] = g;
      groups.push(g);
    }
    if (c.place_main) { g.items.unshift(c); g.fx = c.map_x; g.fy = c.map_y; }
    else g.items.push(c);
  });
  return groups;
}

var DIRS = ["E", "W", "NE", "SE", "NW", "SW", "N", "S"];
// Every place a name may take around its dot, in order of preference:
// [direction, slide (see boxAt), text alignment]. Centred beside the dot
// first, then sliding along it.
var ALIGN = { E: "l", NE: "l", SE: "l", W: "r", NW: "r", SW: "r", N: "c", S: "c" };
var CANDS = [];
[0, -1, 1].forEach(function (slide) {
  DIRS.forEach(function (dir) {
    var straight = dir.length === 1;
    if (slide && !straight) return;
    var align = ALIGN[dir];
    if (slide && (dir === "N" || dir === "S")) align = slide > 0 ? "l" : "r";
    CANDS.push([dir, slide, align]);
  });
});
var DISTS = [6, 20, 36, 60];  // how far a name may sit from its dot; past the first, on a line
var R = 8;          // dot radius, px
var GAP = 5;        // free space kept around a name, px
var STACK = 20;     // two places' names one above the other: about an empty line between them
var SIDE = 8;       // two places' names side by side: wider than a word space (12 sent a second place of a crowded town under the map)
var MERGE = 2 * R;  // places whose dots would overlap on screen share one dot
var EDGE = 6;       // names stay this far from the frame
var APART = 8;      // two places named at one dot: the space between them (.cmap-label's gap)
var CLEAR = 10;     // a name never comes closer than this to another dot (its own sits 6 px off)
var MARGIN = 8;     // every name this much nearer its own dot than any other dot, and every
                    // dot this much nearer one of its own names than any other name
var NEAR = 22;      // closer than this, the eye may take the neighbour's dot for the name's
                    // (only a tie-break: it never buys a line or a farther zoom)
var LINE_MAX = 60;  // the longest line from a dot to its name (draw() asks for 36 on a phone)
var SHARED = 1000;  // two places at one dot that a closer zoom would keep apart: dearer than
                    // anything else but a miss (the product owner's hub, 2026-11-05: two addresses 300 m
                    // apart are two dots); places too close for any zoom share one regardless
var NARROW = 12;    // a name wrapped narrower than usual: cheaper than a line, dearer than none
var NARROW_K = 0.72;  // how much narrower (draw() measures the names at this share of the width)
var BRIEF = 300;    // a place with services named on the map by its main name alone, its
                    // services in the rows under the map: dearer than a line or a zoom
var MISS = 1e5;     // a name with no free place at all

/* The box of a name `d` px from its dot in direction `dir`. `slide` moves a
   name beside the dot (E/W) or above/below it (N/S) off centre: -1 puts its
   top (or left) edge level with the dot's, +1 its bottom (or right) edge. */
function boxAt(p, sz, dir, d, slide) {
  var k = 0.7071, a = (R + d) * k, x, y;
  var sy = (slide || 0) * Math.max(0, sz.h / 2 - R), sx = (slide || 0) * Math.max(0, sz.w / 2 - R);
  switch (dir) {
    case "E": x = p.x + R + d; y = p.y - sz.h / 2 + sy; break;
    case "W": x = p.x - R - d - sz.w; y = p.y - sz.h / 2 + sy; break;
    case "N": x = p.x - sz.w / 2 + sx; y = p.y - R - d - sz.h; break;
    case "S": x = p.x - sz.w / 2 + sx; y = p.y + R + d; break;
    case "NE": x = p.x + a; y = p.y - a - sz.h; break;
    case "SE": x = p.x + a; y = p.y + a; break;
    case "NW": x = p.x - a - sz.w; y = p.y - a - sz.h; break;
    default: x = p.x - a - sz.w; y = p.y + a; break;  // SW
  }
  return { x: x, y: y, w: sz.w, h: sz.h };
}

function overlaps(a, b, gap) {
  return a.x < b.x + b.w + gap && b.x < a.x + a.w + gap &&
         a.y < b.y + b.h + gap && b.y < a.y + a.h + gap;
}

/* Two places' blocks too close to read as two (ui-expert, 2026-11-06: on a
   phone «Hospital de Pinamar» sat 6 px over «Coordinación de Discapacidad y
   Centro de Día», same column, colour and size, and read as one name): one
   above the other, about an empty line between them; side by side, more than
   a word space; corner to corner, GAP. */
function crowds(a, b) {
  var dx = Math.max(b.x - (a.x + a.w), a.x - (b.x + b.w), 0);
  var dy = Math.max(b.y - (a.y + a.h), a.y - (b.y + b.h), 0);
  return (dx < GAP && dy < STACK) || (dy < GAP && dx < SIDE);
}

function inside(b, cw, ch) {
  return b.x >= EDGE && b.y >= EDGE && b.x + b.w <= cw - EDGE && b.y + b.h <= ch - EDGE;
}

/* Candidate `cand` for a name `d` px from dot p, inside the frame: a name
   above or below its dot that would pass the frame's side slides back in, one
   beside its dot slides up or down, as long as the dot still faces it (the
   lines then align on the dot's side). {box, align} or null. */
function fitBox(p, sz, cand, d, cw, ch) {
  var dir = cand[0], b = boxAt(p, sz, dir, d, cand[1]);
  if (inside(b, cw, ch)) return { box: b, align: cand[2] };
  if (dir.length !== 1) return null;
  var align = cand[2];
  if (dir === "N" || dir === "S") {
    var x = Math.max(EDGE, Math.min(b.x, cw - EDGE - b.w));
    if (p.x < x + R || p.x > x + b.w - R) return null;
    b.x = x;
    var mid = x + b.w / 2;
    align = Math.abs(p.x - mid) < 1 ? "c" : p.x < mid ? "l" : "r";
  } else {
    var y = Math.max(EDGE, Math.min(b.y, ch - EDGE - b.h));
    if (p.y < y + R || p.y > y + b.h - R) return null;
    b.y = y;
  }
  return inside(b, cw, ch) ? { box: b, align: align } : null;
}

function closest(b, p) {
  return { x: Math.max(b.x, Math.min(p.x, b.x + b.w)), y: Math.max(b.y, Math.min(p.y, b.y + b.h)) };
}

// Does the segment p→q cross box b (Liang–Barsky)?
function segHits(p, q, b) {
  var t0 = 0, t1 = 1, dx = q.x - p.x, dy = q.y - p.y;
  var tests = [[-dx, p.x - b.x], [dx, b.x + b.w - p.x], [-dy, p.y - b.y], [dy, b.y + b.h - p.y]];
  for (var i = 0; i < 4; i++) {
    var pp = tests[i][0], qq = tests[i][1];
    if (pp === 0) { if (qq < 0) return false; continue; }
    var r = qq / pp;
    if (pp < 0) { if (r > t1) return false; if (r > t0) t0 = r; }
    else { if (r < t0) return false; if (r < t1) t1 = r; }
  }
  return true;
}

function dotBox(p) {
  return { x: p.x - R, y: p.y - R, w: 2 * R, h: 2 * R };
}

function grow(b, g) {
  return { x: b.x - g, y: b.y - g, w: b.w + 2 * g, h: b.h + 2 * g };
}

// The free space between box b and the round dot centred at p.
export function gapTo(b, p) {
  var dx = Math.max(b.x - p.x, 0, p.x - (b.x + b.w));
  var dy = Math.max(b.y - p.y, 0, p.y - (b.y + b.h));
  return Math.max(0, Math.hypot(dx, dy) - R);
}

function nearCost(gap) {
  return gap >= NEAR ? 0 : NEAR - gap;
}

// The boxes of a cluster's names when its block is `box` with text alignment
// `align` (the names hug their text: .cmap-label aligns them, it does not
// stretch them).
function nameRects(names, box, align) {
  return names.map(function (n) {
    var x = align === "r" ? box.w - n.w : align === "c" ? (box.w - n.w) / 2 : n.x;
    return { x: box.x + x, y: box.y + n.y, w: n.w, h: n.h, g: n.g };
  });
}

// Fewer misses, then less line, sharing and zoom, then names further from other dots.
function better(r, top) {
  if (!top) return true;
  if (r.misses !== top.misses) return r.misses < top.misses;
  if (Math.abs(r.cost - top.cost) > 0.01) return r.cost < top.cost;
  return r.near < top.near - 0.01;
}

// Do the segments a→b and c→d cross?
function segsCross(a, b, c, d) {
  function side(p, q, r) { return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x); }
  var d1 = side(c, d, a), d2 = side(c, d, b), d3 = side(a, b, c), d4 = side(a, b, d);
  return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0)) && d1 !== 0 && d2 !== 0 && d3 !== 0 && d4 !== 0;
}

/* Places whose dots would overlap on screen share one dot (the product owner's hub,
   2026-11-05: two addresses 300 m apart are two dots, never one). The closest
   pair goes first and two groups join only when EVERY place of one is that
   close to every place of the other, so a place between two others joins the
   nearer one whatever the order of the list. The dot goes to the middle of its
   places, so it does not depend on the order either; the names keep the
   list's order, one place under the other, APART px apart. */
function cluster(groups, at) {
  var cs = groups.map(function (g, i) {
    var p = at(g);
    return { ids: [i], dot: p, pts: [p] };
  });
  function far(a, b) {
    var m = 0;
    a.pts.forEach(function (p) {
      b.pts.forEach(function (q) { m = Math.max(m, Math.hypot(p.x - q.x, p.y - q.y)); });
    });
    return m;
  }
  for (;;) {
    var bi = -1, bj = -1, bd = MERGE;
    for (var i = 0; i < cs.length; i++) {
      for (var j = i + 1; j < cs.length; j++) {
        var d = far(cs[i], cs[j]);
        if (d < bd) { bd = d; bi = i; bj = j; }
      }
    }
    if (bi < 0) break;
    var a = cs[bi], b = cs[bj], pts = a.pts.concat(b.pts);
    cs[bi] = {
      ids: a.ids.concat(b.ids).sort(function (x, y) { return x - y; }),
      dot: {
        x: pts.reduce(function (t, p) { return t + p.x; }, 0) / pts.length,
        y: pts.reduce(function (t, p) { return t + p.y; }, 0) / pts.length,
      },
      pts: pts,
    };
    cs.splice(bj, 1);
  }
  cs.forEach(function (c) {
    delete c.pts;
    // The names as measured; wrapped narrower where a group has that too; and,
    // where a place with services fits nowhere whole, by its main name alone
    // (its services then go to the rows under the map, draw()).
    var narrow = c.ids.some(function (gi) { return groups[gi].narrow; });
    var brief = c.ids.some(function (gi) { return (groups[gi].names || []).length > 1; });
    c.variants = [];
    [false].concat(brief ? [true] : []).forEach(function (b) {
      var v0 = stack(c, false, b);
      c.variants.push(v0);
      if (narrow) {
        var v = stack(c, true, b);
        if (v.size.w < v0.size.w) c.variants.push(v);
      }
    });
  });
  return cs;

  function stack(c, narrow, brief) {
    var y = 0, w = 0, names = [], cut = 0;
    c.ids.forEach(function (gi, k) {
      var g = groups[gi], m = (narrow && g.narrow) || g;
      var ns = m.names && m.names.length ? m.names : [{ x: 0, y: 0, w: m.size.w, h: m.size.h }];
      var sz = m.size;
      if (brief && ns.length > 1) {
        ns = ns.slice(0, 1);
        sz = { w: Math.ceil(ns[0].x + ns[0].w), h: Math.ceil(ns[0].y + ns[0].h) };
        cut++;
      }
      if (k) y += APART;
      ns.forEach(function (n) {
        names.push({ x: n.x, y: y + n.y, w: n.w, h: n.h, g: k });
      });
      y += sz.h;
      w = Math.max(w, sz.w);
    });
    return { size: { w: w, h: y }, names: names, narrow: narrow, brief: cut > 0,
      cost: (narrow ? NARROW : 0) + BRIEF * cut };
  }
}

/* The layout for a frame of cw×ch px over a drawing of W×H units.
   o.groups: [{fx, fy, size: {w, h}, names: [{x, y, w, h}], narrow}] (fractions
   of the drawing; the size of the group's name block and, inside it, of each
   name, left-aligned; `narrow`, optional, the same {size, names} wrapped
   narrower, for a crowded spot); o.fixed: boxes already taken (the
   attribution); o.maxLine: the longest line from a dot to its name.
   Returns {s, tx, ty, clusters: [{ids, dot, box, dir, align, leader, gap,
   narrow, brief}], misses}: the drawing is shown at scale s, shifted by
   tx/ty; `ids` are the indexes of the groups sharing a dot; `leader` [x1, y1,
   x2, y2] or null; `gap` the space between the dot and its nearest name;
   `narrow` the names take their narrow wrap; `brief` each place with
   services shows only its first name (the services go under the map);
   `off` (with only ids and dot) no clean place for its names: not drawn,
   its places go under the map. Every off cluster is a miss. Once a place is
   off, the others are laid out again without its dot: a dot not drawn
   keeps no name away. */
export function layoutMap(o) {
  var lay = layoutOnce(o);
  var off = lay ? lay.clusters.filter(function (c) { return c.off; }) : [];
  if (!off.length || off.length === lay.clusters.length) return lay;
  var gone = {};
  off.forEach(function (c) { c.ids.forEach(function (i) { gone[i] = 1; }); });
  var keep = o.groups.map(function (g, i) { return i; }).filter(function (i) { return !gone[i]; });
  var sub = {};
  for (var k in o) sub[k] = o[k];
  sub.groups = keep.map(function (i) { return o.groups[i]; });
  var again = layoutMap(sub);
  again.clusters.forEach(function (c) { c.ids = c.ids.map(function (j) { return keep[j]; }); });
  off.forEach(function (c) {
    var pts = c.ids.map(function (i) { return { x: again.tx + again.s * o.groups[i].fx * o.W, y: again.ty + again.s * o.groups[i].fy * o.H }; });
    again.clusters.push({ ids: c.ids, off: true, dot: {
      x: pts.reduce(function (t, p) { return t + p.x; }, 0) / pts.length,
      y: pts.reduce(function (t, p) { return t + p.y; }, 0) / pts.length } });
  });
  again.misses += off.length;
  return again;
}

function layoutOnce(o) {
  var W = o.W, H = o.H, cw = o.cw, ch = o.ch, groups = o.groups || [];
  var fixed = o.fixed || [];
  if (!groups.length || !(cw > 0) || !(ch > 0)) return null;
  var lineMax = o.maxLine > 0 ? o.maxLine : LINE_MAX;
  var dists = DISTS.filter(function (d) { return d <= lineMax; });
  var xs = groups.map(function (g) { return g.fx * W; });
  var ys = groups.map(function (g) { return g.fy * H; });
  var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
  var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
  var spanX = Math.max(maxX - minX, 1), spanY = Math.max(maxY - minY, 1);
  var sCover = Math.max(cw / W, ch / H);
  var sTop = Math.max(Math.min(cw * 0.86 / spanX, ch * 0.8 / spanY, o.maxScale || 2.5), sCover);
  var scales = [];
  for (var s = sTop; s > sCover * 1.0001 && scales.length < 12; s *= 0.88) scales.push(s);
  scales.push(sCover);

  /* Every zoom from the closest down to the drawing filling the frame. The
     winner places every name, then has the least leader line (each line also
     costs a fixed 24 px) and the fewest places sharing a dot, with a mild
     preference for the closer zoom; between equals, its names keep further
     from the other dots. Deterministic: the same frame gives the same map. */
  var best = null;
  for (var si = 0; si < scales.length; si++) {
    var zoom = 120 * (1 - scales[si] / sTop);
    if (best && best.misses === 0 && zoom >= best.cost) break;  // only dearer from here on
    var res = attempt(scales[si]);
    res.cost += zoom;
    if (better(res, best)) best = res;
    if (best.misses === 0 && best.cost < 0.01) break;
  }
  delete best.cost;
  delete best.near;
  return best;

  function attempt(s) {
    var tx = Math.min(0, Math.max(cw - s * W, cw / 2 - s * (minX + maxX) / 2));
    var ty = Math.min(0, Math.max(ch - s * H, ch / 2 - s * (minY + maxY) / 2));
    var base = cluster(groups, function (g) { return { x: tx + s * g.fx * W, y: ty + s * g.fy * H }; });
    var dots = base.map(function (c) { return c.dot; });
    var misses = 0, cost = 0, near = 0;
    dots.forEach(function (p) {
      if (p.x - R < EDGE || p.y - R < EDGE || p.x + R > cw - EDGE || p.y + R > ch - EDGE) misses++;
    });
    var pick = search(base.map(function (c, ci) { return options(c, ci, dots); }));
    var clusters = base.map(function (c, ci) {
      var p = pick[ci];
      cost += SHARED * (c.ids.length - 1);
      // No clean place for its names (three places in a row closer than a
      // name is wide): the place leaves the map, dot and all, for the rows
      // under it — a name that might be the neighbour's is worse than none.
      if (!p) { misses++; return { ids: c.ids, dot: c.dot, off: true }; }
      cost += p.cost; near += p.near;
      return { ids: c.ids, dot: c.dot, box: p.box, dir: p.dir, align: p.align, leader: p.leader, gap: p.own,
        narrow: !!p.narrow, brief: !!p.brief };
    });
    return { s: s, tx: tx, ty: ty, clusters: clusters, misses: misses, cost: cost, near: near };
  }

  /* Every place a cluster's names may take on their own: inside the frame,
     clear of the credit, each place's names MARGIN nearer its own dot than
     any other dot (the product owner's hub, 2026-11-05: on a phone «Centro de Salud
     Integral de Ostende» sat 11 px from the Zoonosis dot), a line only up to
     lineMax and never across another dot, wrapped narrower only where the
     usual wrap fits nowhere near. Cheapest first. */
  function options(c, ci, dots) {
    var list = [];
    c.variants.forEach(function (v) {
     dists.forEach(function (d) {
      CANDS.forEach(function (cand) {
        var f = fitBox(c.dot, v.size, cand, d, cw, ch);
        if (!f) return;
        var b = f.box;
        if (fixed.some(function (t) { return overlaps(b, t, GAP); })) return;
        // A place's names read as one block (a center and the services
        // under it, 2 px apart): the block is as near its dot as its nearest
        // name, and each of its names must keep further from any other dot.
        var rects = nameRects(v.names, b, f.align);
        var mine = c.ids.map(function () { return Infinity; });
        rects.forEach(function (r) { mine[r.g] = Math.min(mine[r.g], gapTo(r, c.dot)); });
        var to = [], nearest = Infinity;
        for (var j = 0; j < dots.length; j++) {
          if (j === ci) { to.push(Infinity); continue; }
          if (overlaps(b, dotBox(dots[j]), 0)) return;
          var m = Infinity;
          for (var k = 0; k < rects.length; k++) {
            var g = gapTo(rects[k], dots[j]);
            if (g < CLEAR || g < mine[rects[k].g] + MARGIN) return;
            if (g < m) m = g;
          }
          to.push(m);
          if (m < nearest) nearest = m;
        }
        var leader = null, p0 = null, q = null, cost = v.cost;
        if (d > DISTS[0]) {
          q = closest(b, c.dot);
          var len = Math.hypot(q.x - c.dot.x, q.y - c.dot.y) || 1;
          p0 = { x: c.dot.x + (q.x - c.dot.x) * R / len, y: c.dot.y + (q.y - c.dot.y) * R / len };
          if (fixed.some(function (t) { return segHits(p0, q, t); }) ||
              dots.some(function (p, j) { return j !== ci && segHits(p0, q, grow(dotBox(p), 6)); })) return;
          leader = [p0.x, p0.y, q.x, q.y];
          cost += 24 + Math.hypot(q.x - p0.x, q.y - p0.y);
        }
        list.push({ box: b, wide: grow(b, GAP), dir: cand[0], align: f.align, leader: leader, p0: p0, q: q,
          own: Math.min.apply(null, mine), to: to, near: nearCost(nearest), cost: cost, narrow: v.narrow,
          brief: v.brief });
      });
     });
    });
    return list.sort(function (a, b) { return a.cost - b.cost || a.near - b.near; });  // stable
  }

  // Two clusters' places that cannot stand together: names over each other or
  // close enough to read as one (crowds), a dot nearer the other cluster's
  // name than its own by less than MARGIN, a line across a name or another line.
  function clash(a, ai, b, bi) {
    if (crowds(a.box, b.box)) return true;
    if (a.to[bi] < b.own + MARGIN || b.to[ai] < a.own + MARGIN) return true;
    if (a.leader && segHits(a.p0, a.q, b.wide)) return true;
    if (b.leader && segHits(b.p0, b.q, a.wide)) return true;
    return !!(a.leader && b.leader && segsCross(a.p0, a.q, b.p0, b.q));
  }

  /* The cheapest set of places, one per cluster, none clashing: a greedy pass
     first (an answer to beat, and the answer when the budget runs out), then
     branch and bound, the most constrained cluster first. A cluster with no
     place left is a miss. */
  function search(opts) {
    var n = opts.length;
    var order = opts.map(function (o, i) { return i; })
      .sort(function (a, b) { return opts[a].length - opts[b].length || a - b; });
    var pick = [], best, bestVal = 0, budget = 30000;
    function fits(o, ci, k) {
      for (var m = 0; m < k; m++) {
        var cj = order[m];
        if (pick[cj] && clash(o, ci, pick[cj], cj)) return false;
      }
      return true;
    }
    function value(o) { return o ? o.cost + o.near / 1000 : MISS; }
    order.forEach(function (ci, k) {
      var list = opts[ci], o = null;
      for (var i = 0; i < list.length && !o; i++) if (fits(list[i], ci, k)) o = list[i];
      pick[ci] = o;
      bestVal += value(o);
    });
    best = pick.slice();
    function go(k, val) {
      if (k === n) {
        if (val < bestVal - 1e-9) { best = pick.slice(); bestVal = val; }
        return;
      }
      var ci = order[k], list = opts[ci];
      for (var i = 0; i <= list.length && budget > 0; i++) {
        budget--;
        var o = i < list.length ? list[i] : null;
        if (val + value(o) >= bestVal - 1e-9) return;  // the list is cheapest first
        if (o && !fits(o, ci, k)) continue;
        pick[ci] = o;
        go(k + 1, val + value(o));
        if (bestVal < 1e-9) return;
      }
    }
    pick = [];
    go(0, 0);
    return best;
  }
}

function nameLink(c, main, T, subName) {
  var url = c.directions_url;
  var txt = main ? c.name : subName(c.name);
  var label = T("home.centers.directions", { name: c.name }, "Cómo llegar a " + c.name);
  return '<a class="cmap-name' + (main ? " cmap-name--main" : " cmap-name--sub") + '" href="' + esc(url) +
    '" target="_blank" rel="noopener noreferrer" aria-label="' + esc(label) + '" title="' + esc(label) + '">' +
    esc(txt) + "</a>";
}

// One place: its center and the services under it, 2 px apart (`brief`: the
// center alone).
function groupLines(g, T, subName, brief) {
  return '<span class="cmap-group">' +
    (brief ? g.items.slice(0, 1) : g.items).map(function (c, i) { return nameLink(c, i === 0, T, subName); }).join("") +
    "</span>";
}

// The rows under the map: centers it has no point for (CAPS Móvil), a place
// whose names fit nowhere clean on the map and, when the map names a place by
// its main center alone, that place with its services. One plain card each.
function restHtml(rest, moved, tiles, T) {
  var list = moved.concat(rest).map(function (c) {
    var o = {};
    for (var k in c) o[k] = c[k];
    o.locality = "";
    return o;
  });
  return list.length ? tiles.listHtml(list, T) : "";
}

export function render(host, centros, mapa, T, tiles) {
  T = T || function (k, v, f) { return f; };
  var groups = placeGroups(centros);
  if (!mapa || !mapa.svg || !groups.length || !(mapa.width > 0) || !(mapa.height > 0)) {
    tiles.renderList(host, centros, T);
    return;
  }
  injectStyle();
  tiles.injectStyle();
  host.classList.remove("cl-grid");
  var rest = (centros || []).filter(function (c) { return !hasPoint(c); });
  var subName = tiles.subName || function (n) { return n; };
  host.innerHTML =
    '<div class="cmap">' +
      '<img class="cmap-img" src="' + esc(mapa.svg) + '" alt="" decoding="async">' +
      '<svg class="cmap-lines" aria-hidden="true" focusable="false"></svg>' +
      (mapa.areas || []).map(function (a) {
        return '<span class="cmap-area" aria-hidden="true">' + esc(a.name) + "</span>";
      }).join("") +
      '<ul class="cmap-places"></ul>' +
      '<a class="cmap-attr" href="' + esc(mapa.attribution_url || "https://www.openstreetmap.org/copyright") +
        '" target="_blank" rel="noopener noreferrer">' + esc(mapa.attribution || "© OpenStreetMap") + "</a>" +
    "</div>" +
    '<div class="cmap-rest">' + restHtml(rest, [], tiles, T) + "</div>";
  var box = host.querySelector(".cmap");
  var under = host.querySelector(".cmap-rest");
  var lastW = -1, lastMoved = "";
  function relayout(force) {
    var cw = box.clientWidth, ch = box.clientHeight;
    if (!cw || !ch || (!force && cw === lastW)) return;
    lastW = cw;
    var moved = draw(box, groups, mapa, cw, ch, T, subName) || [];
    var key = moved.map(function (c) { return c.slug; }).join(" ");
    if (key !== lastMoved) {
      lastMoved = key;
      under.innerHTML = restHtml(rest, moved, tiles, T);
    }
  }
  relayout(true);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { relayout(true); });
  if (typeof ResizeObserver === "function") {
    var ro = new ResizeObserver(function () { relayout(false); });
    ro.observe(box);
  }
}

function draw(box, groups, mapa, cw, ch, T, subName) {
  // On a phone a name wraps at 160 px: wider, a long one fits neither beside
  // nor above a dot in the middle of a 358 px frame.
  var maxW = Math.round(Math.min(cw < 600 ? 160 : 270, cw * 0.56));
  var narrowW = Math.round(maxW * NARROW_K);
  // Measure each place's name block once, off-screen, at the width it gets
  // and at the narrower one a crowded spot may ask for: a center's name wraps
  // at that width, a service under it stays on one line (a break after its
  // comma reads as two places).
  var probe = document.createElement("div");
  probe.className = "cmap-probe";
  probe.setAttribute("aria-hidden", "true");
  function probeLabel(g, w) {
    return '<div class="cmap-label" style="--cmap-w:' + w + "px;max-width:" + (cw - 2 * EDGE) + 'px">' +
      groupLines(g, T, subName) + "</div>";
  }
  probe.innerHTML = groups.map(function (g) { return probeLabel(g, maxW); }).join("") +
    groups.map(function (g) { return probeLabel(g, narrowW); }).join("") +
    '<div class="cmap-areas">' + (mapa.areas || []).map(function (a) {
      return '<span class="cmap-area">' + esc(a.name) + "</span>";
    }).join("") + "</div>";
  box.appendChild(probe);
  // Each name's own box too, left-aligned in its block: a short name leaves
  // room beside it that a long one does not, and the ownership rule
  // (layoutMap) is about the words, not the block.
  function measure(el) {
    var r = el.getBoundingClientRect();
    return {
      size: { w: Math.ceil(r.width), h: Math.ceil(r.height) },
      names: Array.prototype.map.call(el.querySelectorAll(".cmap-name"), function (n) {
        var q = n.getBoundingClientRect();
        return { x: q.left - r.left, y: q.top - r.top, w: Math.ceil(q.width), h: Math.ceil(q.height) };
      }),
    };
  }
  // The narrow wrap is kept when the block or its center's name alone gets
  // narrower: a place whose services stay on one line still gets a two-line
  // center name for its brief form (on a phone «Hospital de Pinamar» on one
  // line sat over «Coordinación de Discapacidad…» in one column, ui-expert,
  // 2026-11-06).
  var sized = groups.map(function (g, i) {
    var m = measure(probe.children[i]), n = measure(probe.children[groups.length + i]);
    var thinner = n.size.w < m.size.w || (n.names.length && m.names.length && n.names[0].w < m.names[0].w);
    return { fx: g.fx, fy: g.fy, size: m.size, names: m.names, narrow: thinner ? n : null };
  });
  var areaSizes = Array.prototype.map.call(probe.querySelectorAll(".cmap-areas .cmap-area"), function (el) {
    var r = el.getBoundingClientRect();
    return { w: Math.ceil(r.width), h: Math.ceil(r.height) };
  });
  box.removeChild(probe);

  var attr = box.querySelector(".cmap-attr");
  var ar = attr.getBoundingClientRect(), br = box.getBoundingClientRect();
  var fixed = [{ x: ar.left - br.left, y: ar.top - br.top, w: ar.width, h: ar.height }];
  // A phone's line stays short: 92 px read as a road (the product owner's hub, 2026-11-05).
  var lay = layoutMap({ W: mapa.width, H: mapa.height, cw: cw, ch: ch, groups: sized, fixed: fixed,
    maxLine: cw < 600 ? 36 : 60 });
  if (!lay) return;

  var img = box.querySelector(".cmap-img");
  img.style.width = (lay.s * mapa.width) + "px";
  img.style.height = (lay.s * mapa.height) + "px";
  img.style.transform = "translate(" + lay.tx + "px," + lay.ty + "px)";

  var lines = [], moved = [];
  lay.clusters.forEach(function (c) {
    if (c.off) c.ids.forEach(function (i) { moved = moved.concat(groups[i].items); });
  });
  var shown = lay.clusters.filter(function (c) { return !c.off; });
  var html = shown.map(function (c) {
    var names = c.ids.map(function (i) {
      var g = groups[i];
      if (c.brief && g.items.length > 1) moved = moved.concat(g.items);
      return groupLines(g, T, subName, c.brief);
    }).join("");
    if (c.leader) lines.push(c.leader);
    var at = 'style="left:' + c.dot.x + "px;top:" + c.dot.y + 'px"';
    // A dot leads where all its names lead (the product owner's hub, 2026-11-05): the
    // route of its one place, or of places at one address. A dot two
    // addresses share (40 m apart, too close for any zoom) would take one of
    // them for both, so it is only a mark and the names are the links.
    var routes = {};
    c.ids.forEach(function (i) { routes[groups[i].items[0].directions_url] = 1; });
    var urls = Object.keys(routes);
    var dot = urls.length === 1
      ? '<a class="cmap-dot" href="' + esc(urls[0]) + '" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true" ' + at + "></a>"
      : '<span class="cmap-dot cmap-dot--shared" aria-hidden="true" ' + at + "></span>";
    return '<li class="cmap-place">' + dot +
      '<div class="cmap-label cmap-label--' + (c.align || ALIGN[c.dir]) + '" style="left:' + c.box.x + "px;top:" + c.box.y +
        "px;width:" + c.box.w + "px;--cmap-w:" + (c.narrow ? narrowW : maxW) + 'px">' + names + "</div></li>";
  }).join("");
  box.querySelector(".cmap-places").innerHTML = html;

  var svg = box.querySelector(".cmap-lines");
  svg.setAttribute("viewBox", "0 0 " + cw + " " + ch);
  svg.innerHTML = lines.map(function (l) {
    return '<line x1="' + l[0].toFixed(1) + '" y1="' + l[1].toFixed(1) + '" x2="' + l[2].toFixed(1) + '" y2="' + l[3].toFixed(1) + '"/>';
  }).join("");

  // Area names (Pinamar, Ostende…) only where they hide nothing and no dot
  // could take one for its own name.
  var taken = fixed.concat(shown.map(function (c) { return c.box; }),
    shown.map(function (c) { return dotBox(c.dot); }));
  function claims(b) {
    return shown.some(function (c) { return gapTo(b, c.dot) < c.gap + MARGIN; });
  }
  var areas = box.querySelectorAll(".cmap > .cmap-area");
  (mapa.areas || []).forEach(function (a, i) {
    var el = areas[i], sz = areaSizes[i];
    if (!el || !sz) return;
    var b = { x: lay.tx + lay.s * a.x * mapa.width - sz.w / 2, y: lay.ty + lay.s * a.y * mapa.height - sz.h / 2, w: sz.w, h: sz.h };
    var wide = grow(b, GAP);
    var ok = inside(b, cw, ch) && !taken.some(function (t) { return overlaps(b, t, GAP); }) && !claims(b) &&
      !lines.some(function (l) { return segHits({ x: l[0], y: l[1] }, { x: l[2], y: l[3] }, wide); });
    el.style.left = b.x + "px";
    el.style.top = b.y + "px";
    el.style.visibility = ok ? "visible" : "hidden";
    if (ok) taken.push(b);
  });
  return moved;
}

export function injectStyle() {
  if (document.getElementById("cmap-style")) return;
  // A solid white ring around every letter (3 px out, filled in at 1.5 px)
  // with a soft edge beyond it: a dark road under a name stops short of the
  // letters instead of running through them.
  var ring = [];
  for (var k = 0; k < 24; k++) {
    var r = k < 16 ? 3 : 1.5, a = (k < 16 ? k / 16 : (k - 16) / 8) * 2 * Math.PI;
    ring.push((r * Math.cos(a)).toFixed(2) + "px " + (r * Math.sin(a)).toFixed(2) + "px 0 #fff");
  }
  var halo = ring.join(",") + ",0 0 5px #fff";
  var css =
    // The frame: a fixed height (no jump while the page fills in), the card
    // border of the site, the drawing white like the land on it.
    ".cmap{position:relative;overflow:hidden;height:520px;margin-top:14px;background:#fff;" +
      "border:1px solid var(--border-strong);border-radius:14px;}" +
    "@media(max-width:600px){.cmap{height:560px;}}" +
    ".cmap-img{position:absolute;left:0;top:0;transform-origin:0 0;max-width:none;display:block;user-select:none;pointer-events:none;}" +
    ".cmap-lines{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;}" +
    ".cmap-lines line{stroke:#1f1f1f;stroke-width:1.25;}" +
    ".cmap-places{position:absolute;inset:0;margin:0;padding:0;list-style:none;}" +
    // The dot: black with a white ring; the link around it is a 44 px target.
    ".cmap-dot{position:absolute;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;}" +
    ".cmap-dot::before{content:\"\";position:absolute;left:50%;top:50%;width:16px;height:16px;margin:-8px 0 0 -8px;box-sizing:border-box;" +
      "border-radius:50%;background:#1f1f1f;border:3px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.35);}" +
    ".cmap-dot--shared{pointer-events:none;}" +
    // The names hug their words (align-items, never stretch): the layout
    // measured each one where it is drawn.
    ".cmap-label{position:absolute;display:flex;flex-direction:column;align-items:flex-start;gap:" + APART + "px;box-sizing:border-box;}" +
    ".cmap-group{display:flex;flex-direction:column;gap:2px;align-items:inherit;}" +
    ".cmap-label--r{align-items:flex-end;text-align:right;}" +
    ".cmap-label--c{align-items:center;text-align:center;}" +
    // The probe spans the frame: a name measured in a zero-wide box would break
    // after every word.
    ".cmap-probe{position:absolute;inset:0;visibility:hidden;pointer-events:none;}" +
    ".cmap-probe .cmap-label{position:absolute;left:0;top:0;}" +
    ".cmap-probe .cmap-areas{position:absolute;left:0;top:0;}" +
    ".cmap-probe .cmap-area{position:absolute;left:0;top:0;}" +
    ".cmap-name{display:block;font-size:17px;line-height:1.25;font-weight:600;letter-spacing:-.01em;" +
      "color:var(--link,var(--accent));text-shadow:" + halo + ";text-wrap:balance;}" +
    ".cmap-name--main{max-width:var(--cmap-w,270px);}" +
    ".cmap-name--sub{font-size:16px;font-weight:500;white-space:nowrap;}" +
    ":root[data-theme=humanrounds] .cmap-name{color:var(--title-ink,#1a1714);}" +
    ".cmap-name:hover{color:var(--link-hover,var(--accent-hover));}" +
    ".cmap-name:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:4px;}" +
    ".cmap-area{position:absolute;visibility:hidden;white-space:nowrap;font-size:14px;line-height:1.2;font-weight:600;" +
      "letter-spacing:.08em;text-transform:uppercase;color:#6a6a6a;text-shadow:" + halo + ";pointer-events:none;}" +
    ".cmap-attr{position:absolute;right:0;bottom:0;padding:3px 8px;font-size:13px;line-height:1.3;" +
      "color:var(--text-2);background:rgba(255,255,255,.88);border-top-left-radius:8px;}" +
    ".cmap-rest>.cl:first-child{margin-top:12px;}";
  var st = document.createElement("style");
  st.id = "cmap-style";
  st.textContent = css;
  document.head.appendChild(st);
}
