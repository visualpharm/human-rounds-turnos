// tests/story-static-assets.test.mjs — every picture a story asks for is
// published by Storybook (2026-11-09).
//
// The «Médicos por especialidad» listing story asked for three demo portraits
// that the demo pack renamed when it was diversified (475ae1541): three 404s
// in the Storybook console and three empty circles. Portraits come from the
// synthetic demo pack (/demo-fotos → packs/example-town/fotos-medicos) and the
// rubro icons from /static/icons; a name that is not there is red here.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const STORIES = path.join(ROOT, 'stories');
const PHOTOS = path.join(ROOT, 'packs/example-town/fotos-medicos');
const ICONS = path.join(ROOT, 'static/icons');

function wanted() {
  const out = [];
  for (const name of fs.readdirSync(STORIES)) {
    if (!/\.(js|mjs)$/.test(name)) continue;
    const src = fs.readFileSync(path.join(STORIES, name), 'utf8');
    // foto("garcia-jorge") and avs(["garcia-jorge", …]) build /demo-fotos/<id>.jpg
    for (const m of src.matchAll(/\bfoto\(\s*["']([a-z0-9-]+)["']\s*\)/g)) out.push([name, 'photo', m[1] + '.jpg']);
    for (const m of src.matchAll(/\bavs\(\s*\[([^\]]*)\]\s*\)/g)) {
      for (const id of m[1].matchAll(/["']([a-z0-9-]+)["']/g)) out.push([name, 'photo', id[1] + '.jpg']);
    }
    for (const m of src.matchAll(/\/demo-fotos\/([a-z0-9-]+\.jpg)/g)) out.push([name, 'photo', m[1]]);
    for (const m of src.matchAll(/\/static\/icons\/([A-Za-z0-9_.-]+\.(?:png|svg))/g)) out.push([name, 'icon', m[1]]);
  }
  return out;
}

test('the stories name their pictures (the scan finds them)', () => {
  const all = wanted();
  assert.ok(all.filter((w) => w[1] === 'photo').length >= 10, 'demo portraits found in stories');
  assert.ok(all.some((w) => w[0] === 'Listing.stories.js'), 'the listing story is scanned');
});

test('every demo portrait and icon a story asks for exists, so Storybook serves it', () => {
  const missing = wanted().filter(([, kind, file]) => !fs.existsSync(path.join(kind === 'photo' ? PHOTOS : ICONS, file)));
  assert.deepEqual(missing, [], 'pick a portrait that exists in packs/example-town/fotos-medicos');
});
