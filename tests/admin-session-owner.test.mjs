// Independent whole-module ownership proof; no application logic is copied.
import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL, fileURLToPath } from 'node:url';
const source = fileURLToPath(new URL('../static/platform/admin-session.mjs', import.meta.url));
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
const expired = () => ({ status: 401, clone: () => ({ json: async () => ({ error: 'no_autenticado' }) }) });
for (const remainsCurrent of [false, true]) {
  test(`confirmation waiting for whoami respects current owner=${remainsCurrent}`, async t => {
    const originals = Object.fromEntries(['window', 'location', 'fetch'].map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
    t.after(() => { for (const [key, descriptor] of Object.entries(originals)) descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key]; });
    const requested = deferred(), pending = deferred();
    let current = true;
    globalThis.location = { href: '', pathname: '/admin/settings', search: '' };
    globalThis.window = { location: globalThis.location, SANA_ADMIN_SCREEN: 'settings' };
    globalThis.fetch = (url) => { assert.equal(url, '/api/admin/whoami'); requested.resolve(); return pending.promise; };
    const session = await import(pathToFileURL(source).href + `?owner-repro=${remainsCurrent}`);
    const result = session.confirmar(expired(), { error: 'no_autenticado' }, () => current);
    await requested.promise;
    current = remainsCurrent;
    pending.resolve(expired());
    const transitioned = await result;
    assert.equal(transitioned, remainsCurrent, 'obsolete ownership cannot authorize a navigation');
    if (remainsCurrent) assert.match(location.href, /^\/admin\/login\?next=/);
    else assert.equal(location.href, '', 'stale whoami completion must leave the fresh page alone');
  });
}
