import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// The home's map of the centers (static/platform/centers-map.mjs): the pure
// parts — which centers share a dot, and where the names go.
const Map_ = await import("../static/platform/centers-map.mjs");

const URL = "https://www.google.com/maps/dir/?api=1&destination=x";

test("a center is drawn on the map only with a point and a route", () => {
  assert.equal(Map_.hasPoint({ map_x: 0.1, map_y: 0.2, directions_url: URL }), true);
  assert.equal(Map_.hasPoint({ map_x: 0.1, map_y: 0.2 }), false);
  assert.equal(Map_.hasPoint({ map_y: 0.2, directions_url: URL }), false);
  assert.equal(Map_.hasPoint({ map_x: "0.1", map_y: 0.2, directions_url: URL }), false);
  assert.equal(Map_.hasPoint(null), false);
});

test("centers at one street address share a group, the main one first; the rest stay off the map", () => {
  const groups = Map_.placeGroups([
    { slug: "sangre", place: "av. 1 255", map_x: 0.5, map_y: 0.5, directions_url: URL },
    { slug: "hospital", place: "av. 1 255", place_main: true, map_x: 0.51, map_y: 0.49, directions_url: URL },
    { slug: "caps-norte", map_x: 0.2, map_y: 0.3, directions_url: URL },
    { slug: "movil", directions_url: URL },
  ]);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].items.map((c) => c.slug), ["hospital", "sangre"]);
  assert.deepEqual([groups[0].fx, groups[0].fy], [0.51, 0.49]);
  assert.deepEqual(groups[1].items.map((c) => c.slug), ["caps-norte"]);
});

test("no layout without places or without a frame", () => {
  const g = [{ fx: 0.5, fy: 0.5, size: { w: 60, h: 20 } }];
  assert.equal(Map_.layoutMap({ W: 1000, H: 1000, cw: 400, ch: 400, groups: [] }), null);
  assert.equal(Map_.layoutMap({ W: 1000, H: 1000, cw: 0, ch: 400, groups: g }), null);
});

test("a place between two others shares the dot of the nearer one, whatever the order of the list", () => {
  // At the closest zoom (2.5 px per unit) C sits at 0, B at 20 px, A at 28 px:
  // B is 8 px from A (their dots would overlap) and 20 from C (over the 16 px merge).
  const at = (dx) => ({ fx: 0.5 + dx / 2500, fy: 0.5, size: { w: 40, h: 20 } });
  const lay = Map_.layoutMap({ W: 1000, H: 1000, cw: 400, ch: 400, groups: [at(0), at(20), at(28)] });
  assert.equal(lay.s, 2.5);
  assert.deepEqual(lay.clusters.map((c) => c.ids), [[0], [1, 2]]);
});

// A crowded small town, as the home measured it on a phone (390 px, the map
// 356×558) and a desktop (1440 px, 1070×518): nine places, g0 a building with
// two services under its center (three names, one block), g2 and g5 two
// addresses 40 m apart (one dot at any zoom), g3 and g6 two addresses 300 m
// apart (never one dot: the product owner's hub, 2026-11-05), each name's own box and,
// for a crowded spot, the block wrapped narrower (on the phone g0 too: its
// center's name on two lines, the services as they are).
const TOWN = JSON.parse(readFileSync(fileURLToPath(new globalThis.URL("./fixtures/centers-map-town.json", import.meta.url)), "utf8"));

// Each name's box as the module stacks them (independent of its own math):
// the places of a dot one under the other 8 px apart, a narrow or brief
// block where the layout chose it, the names aligned in their block.
function nameBoxes(c, groups) {
  let y = 0;
  const out = [];
  c.ids.forEach((gi, k) => {
    const g0 = groups[gi], g = c.narrow && g0.narrow ? g0.narrow : g0;
    let names = g.names, h = g.size.h;
    if (c.brief && names.length > 1) { names = names.slice(0, 1); h = Math.ceil(names[0].y + names[0].h); }
    if (k) y += 8;
    names.forEach((n) => {
      const x = c.align === "r" ? c.box.w - n.w : c.align === "c" ? (c.box.w - n.w) / 2 : n.x;
      out.push({ gi, x: c.box.x + x, y: c.box.y + y + n.y, w: n.w, h: n.h });
    });
    y += h;
  });
  return out;
}
// The free space between a box and a round dot of radius 8 at p.
function gap(b, p) {
  const dx = Math.max(b.x - p.x, 0, p.x - (b.x + b.w)), dy = Math.max(b.y - p.y, 0, p.y - (b.y + b.h));
  return Math.max(0, Math.hypot(dx, dy) - 8);
}

function cross(a, b, c, d) {
  const side = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  return (side(c, d, a) > 0) !== (side(c, d, b) > 0) && (side(a, b, c) > 0) !== (side(a, b, d) > 0);
}
function hits(l, b) {
  // Sample the segment: enough at these sizes, and independent of the module's own math.
  for (let t = 0; t <= 1; t += 0.01) {
    const x = l[0] + (l[2] - l[0]) * t, y = l[1] + (l[3] - l[1]) * t;
    if (x > b.x + 0.5 && x < b.x + b.w - 0.5 && y > b.y + 0.5 && y < b.y + b.h - 0.5) return true;
  }
  return false;
}
function apart(a, b) {
  return a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
}
// Two places' names read as two (ui-expert, 2026-11-06: «Hospital de Pinamar»
// 6 px over «Coordinación de Discapacidad…» read as one name): one above the
// other, 20 px between them; side by side, 8 px.
function readApart(a, b) {
  const dx = Math.max(b.x - (a.x + a.w), a.x - (b.x + b.w), 0);
  const dy = Math.max(b.y - (a.y + a.h), a.y - (b.y + b.h), 0);
  return !((dx < 5 && dy < 20) || (dy < 5 && dx < 8));
}

for (const [frame, reversed] of [["phone", false], ["phone", true], ["desktop", false], ["desktop", true]]) {
  const listed = reversed ? "listed backwards" : "listed in order";
  test(`every name of a crowded town on a ${frame}, ${listed}: nearer its own dot than any other, no overlap, short lines`, () => {
    const t = TOWN[frame];
    const groups = reversed ? t.groups.slice().reverse() : t.groups;
    const key = (c) => c.ids.map((i) => groups[i].key).sort().join("+");
    const { cw, ch } = t, attr = t.fixed[0], maxLine = frame === "phone" ? 36 : 60;
    const lay = Map_.layoutMap({ W: t.W, H: t.H, cw, ch, groups, fixed: t.fixed, maxLine });
    assert.equal(lay.misses, 0);
    assert.deepEqual(lay.clusters.flatMap((c) => c.ids).sort((a, b) => a - b), groups.map((g, i) => i));
    // One dot for the two addresses 40 m apart, never one for the two 300 m apart.
    const keys = lay.clusters.map(key).sort();
    assert.deepEqual(keys, ["g0", "g1", "g2+g5", "g3", "g4", "g6", "g7", "g8"]);
    // The building's services stay under its center where they fit (the
    // desktop); on the phone only the center's name fits by its dot, and the
    // services go to the rows under the map.
    const brief = lay.clusters.filter((c) => c.brief).map(key);
    assert.deepEqual(brief, frame === "phone" ? ["g0"] : []);
    const boxes = lay.clusters.map((c) => nameBoxes(c, groups));
    lay.clusters.forEach((c, i) => {
      const b = c.box;
      assert.ok(b.x >= 0 && b.y >= 0 && b.x + b.w <= cw && b.y + b.h <= ch, `name ${key(c)} inside the frame`);
      assert.ok(apart(b, attr), `name ${key(c)} clear of the attribution`);
      lay.clusters.forEach((o, j) => {
        if (j <= i) return;
        assert.ok(readApart(b, o.box), `names ${key(c)} and ${key(o)} far enough apart to read as two`);
      });
      // No name nearer another dot than its own (the product owner's hub, 2026-11-05: on a
      // phone a name sat 11 px from the neighbour's dot, another beside the
      // hospital's): each one 10 px clear of every other dot and 8 px further
      // from it than its place's nearest name is from its own dot.
      boxes[i].forEach((n) => {
        const own = Math.min(...boxes[i].filter((m) => m.gi === n.gi).map((m) => gap(m, c.dot)));
        lay.clusters.forEach((o, j) => {
          if (j === i) return;
          const g = gap(n, o.dot);
          assert.ok(g >= 10 && g >= own + 8, `a name of ${groups[n.gi].key} ${g.toFixed(1)} px from the dot of ${key(o)}, ${own.toFixed(1)} from its own`);
        });
      });
      // every place within the merge distance of the dot it is named at
      c.ids.forEach((gi) => {
        const g = groups[gi];
        const x = lay.tx + lay.s * g.fx * t.W, y = lay.ty + lay.s * g.fy * t.H;
        assert.ok(Math.hypot(x - c.dot.x, y - c.dot.y) < 16, `place ${g.key} near its dot`);
      });
    });
    const leaders = lay.clusters.map((c, i) => [c.leader, i]).filter(([l]) => l);
    // Every name beside its own dot: a line, where one is needed, stays short
    // (a 92 px one read as a road on the phone, 2026-11-05).
    leaders.forEach(([l]) => assert.ok(Math.hypot(l[2] - l[0], l[3] - l[1]) <= maxLine + 8, "a short line"));
    assert.ok(leaders.length <= 1, `${leaders.length} names at the end of a line`);
    leaders.forEach(([l, i]) => {
      lay.clusters.forEach((o, j) => { if (j !== i) assert.ok(!hits(l, o.box), `line of ${i} clear of name ${j}`); });
      leaders.forEach(([m, j]) => {
        if (j <= i) return;
        assert.ok(!cross({ x: l[0], y: l[1] }, { x: l[2], y: l[3] }, { x: m[0], y: m[1] }, { x: m[2], y: m[3] }), `lines ${i} and ${j} do not cross`);
      });
    });
  });
}

test("a place whose name fits nowhere clean leaves the map, dot and all, and the rest keep the rule", () => {
  // Three places in a row 20 px apart on a phone (the town fills the frame, no
  // closer zoom): the middle one's three-line name is nearer a neighbour's dot
  // wherever it goes, so it is not drawn and goes under the map (the product owner's hub,
  // 2026-11-05: a name never nearer another dot than its own).
  const block = (w, h) => ({ size: { w, h }, names: [{ x: 0, y: 0, w, h }] });
  const at = (fx, fy, w, h, narrow) => ({ fx, fy, ...block(w, h), narrow: narrow ? block(115, h) : null });
  const groups = [
    at(0.336, 0.05, 100, 22), at(0.336, 0.95, 100, 22),
    at(0.3, 0.5, 160, 64, true), at(0.336, 0.5, 160, 64, true), at(0.372, 0.505, 160, 64, true),
  ];
  const lay = Map_.layoutMap({ W: 1000, H: 1000, cw: 356, ch: 558, groups, fixed: [], maxLine: 36 });
  assert.equal(lay.misses, 1);
  assert.deepEqual(lay.clusters.filter((c) => c.off).map((c) => c.ids), [[3]]);
  assert.equal(lay.clusters.find((c) => c.off).box, undefined);
  const shown = lay.clusters.filter((c) => !c.off);
  assert.deepEqual(shown.flatMap((c) => c.ids).sort(), [0, 1, 2, 4]);
  for (const c of shown) {
    const own = gap(c.box, c.dot);
    for (const o of shown) {
      if (o === c) continue;
      const g = gap(c.box, o.dot);
      assert.ok(g >= 10 && g >= own + 8, `the name of ${c.ids} ${g.toFixed(1)} px from the dot of ${o.ids}, ${own.toFixed(1)} from its own`);
    }
  }
});

test("a name that fits on either side of its dot takes the side away from the neighbour's dot", () => {
  // Two dots 94 px apart: the first name to the right of its dot would end
  // 12 px from the second dot (allowed, but the eye may pair them), to the
  // left it is clear (the phone showed one 12 px off, 2026-11-05).
  const groups = [
    { fx: 0.5, fy: 0.5, size: { w: 60, h: 20 } },
    { fx: 0.5376, fy: 0.5, size: { w: 60, h: 20 } },
  ];
  const lay = Map_.layoutMap({ W: 1000, H: 1000, cw: 400, ch: 200, groups, fixed: [] });
  assert.equal(lay.misses, 0);
  assert.equal(lay.clusters.length, 2);
  const [a, b] = lay.clusters;
  assert.ok(Math.abs(b.dot.x - a.dot.x - 94) < 1, "the dots 94 px apart");
  const gap = Math.max(b.dot.x - 8 - (a.box.x + a.box.w), a.box.x - (b.dot.x + 8), b.dot.y - 8 - (a.box.y + a.box.h), a.box.y - (b.dot.y + 8));
  assert.ok(gap >= 22, `the first name ${Math.round(gap)} px from the second dot`);
  assert.ok(lay.clusters.every((c) => !c.leader), "no line");
});
