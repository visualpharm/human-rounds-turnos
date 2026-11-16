// static/platform/islands.mjs — Tier 5 slice 3 islands bootstrap (docs/roadmap/
// tier5-frontend-design.md, "Interactive screens as Preact+HTM islands").
// Real ESM, no UMD — this module has no Node-`require()` consumer; its pure
// pieces are exercised by test/islands.test.mjs via `import`, and the real
// browser proof is the Playwright pass, not a jsdom stand-in.
//
// Loaded through the fingerprint manifest by admin-content.js, AFTER the
// page's `<script type="importmap">` and the `window.SANA_ISLAND_URLS`
// bootstrap object booking_requests.py's _serve_admin() injects the same
// way it already injects window.SANA_COUNTRY_PHONE (a per-request
// `{{static.*}}`-resolved name -> fingerprinted-URL map, one entry per
// island the page actually uses).
//
// Mechanism: scans `[data-island]` elements, resolves `data-island="X"` via
// window.SANA_ISLAND_URLS["X"], dynamic `import()`s that URL, and calls the
// module's exported `mount(el, props)` — `props` comes from the element's
// `data-props='{...}'` JSON attribute (empty object if absent/invalid),
// merged with an optional `options.props` a caller passes to mount()
// directly. The override exists because JSON can't carry a function: the
// Calendario-tab pilot island needs a live `onSelect` callback into legacy
// admin-content.js code, which data-props alone can't express — see
// admin-content.js's wireMedicoCalendario for the concrete call.
//
// admin.html is still the STILL-LEGACY hash-routed shell this slice (routing
// untouched, per the design's own slice-3 scope) — its tab panels are built
// by client-side `innerHTML` (admin-content.js), so a `[data-island]` element
// usually does NOT exist yet at this file's own DOMContentLoaded scan. Any
// call site that inserts island markup later calls the imported `scan(el)`
// itself right after insertion — ONE mount code path, used both by the
// automatic initial scan here and by that kind of explicit legacy call.
//
// Risk register (tier5-frontend-design.md): "import maps unsupported on an
// old hospital-desktop browser" — feature-detected below; a page that fails
// the check never attempts the dynamic import, so the legacy imperative
// code an island replaces is what a staff member on such a browser keeps
// seeing, unmodified, exactly the register's own rollback note.

const MOUNTED = new WeakSet();
const CONTROLLERS = new WeakMap();
// A legacy call site can call update(el, props) before mount()'s dynamic
// import resolves (mount() is async; the caller's own fetch can win the
// race). PENDING holds the LAST props passed while the element is claimed
// by an in-flight mount() whose controller hasn't arrived yet; mount()
// applies it the moment the controller is created, then clears it — "last
// known state wins", same semantics Preact's own re-render has.
const PENDING = new WeakMap();
// Ownership token per element. Every mount() run claims the element with a
// fresh token and unmount() invalidates it. A dynamic import — or an island
// whose own mount() is async — can settle long after the caller unmounted
// the element, possibly after a NEW mount() claimed it. The token check at
// each await boundary is what tells a late run "you no longer own this
// element": don't invoke the island's mount, don't register a controller
// over a fresh owner, don't release or consume state the new owner owns.
const OWNERS = new WeakMap();
let nextToken = 1;

function supportsImportMaps() {
  return typeof HTMLScriptElement !== "undefined"
    && typeof HTMLScriptElement.supports === "function"
    && HTMLScriptElement.supports("importmap");
}

function readProps(el) {
  const raw = el.getAttribute("data-props");
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (err) {
    console.error("[islands] data-props inválido en", el, err);
    return {};
  }
}

function resolveUrl(name, urlMap) {
  const map = urlMap !== undefined
    ? urlMap
    : (typeof window !== "undefined" ? window.SANA_ISLAND_URLS : null);
  return (map && map[name]) || null;
}

/** mount(el, options?) — options are for tests/advanced callers only
 * (urlMap, importFn, skipFeatureCheck); normal call sites just do
 * `mount(el)` or let scan()/the DOMContentLoaded listener do it. Returns
 * the island's controller (or null if it couldn't mount), so a caller that
 * mounts explicitly right after inserting markup can keep a handle on it
 * without a second WeakMap lookup. */
async function mount(el, options) {
  options = options || {};
  if (MOUNTED.has(el)) return CONTROLLERS.get(el) || null;
  const name = el.getAttribute("data-island");
  if (!name) return null;
  if (!options.skipFeatureCheck && !supportsImportMaps()) {
    console.warn("[islands] import maps no soportados por este navegador — se omite", name);
    return null;
  }
  const url = resolveUrl(name, options.urlMap);
  if (!url) {
    console.error("[islands] sin URL registrada para", name, "(ver window.SANA_ISLAND_URLS)");
    return null;
  }
  MOUNTED.add(el);
  const token = nextToken++;
  OWNERS.set(el, token);
  const importFn = options.importFn || ((u) => import(u));
  let mod;
  try {
    mod = await importFn(url);
  } catch (err) {
    // A failed load mounted nothing: release the claim so a retry mount()
    // doesn't short-circuit on MOUNTED forever. Generation-guarded — a run
    // whose element was unmounted and re-claimed meanwhile must not delete
    // the fresh owner's claim or its buffered PENDING update.
    if (OWNERS.get(el) === token) release(el);
    console.error("[islands] no se pudo cargar el módulo de", name, url, err);
    return null;
  }
  // unmount() ran while the import was in flight: this run lost the
  // element. The island's mount() must NOT be invoked — a disposed element
  // that later re-imports would paint into markup the caller already owns.
  if (OWNERS.get(el) !== token) return null;
  const mountFn = mod.mount || (mod.default && mod.default.mount);
  if (typeof mountFn !== "function") {
    // Same registry release as a failed load: a module without mount()
    // never touched the element, so a later retry (or a fixed module on a
    // future deploy) must be able to claim it again.
    if (OWNERS.get(el) === token) release(el);
    console.error("[islands]", name, "no exporta mount(el, props)");
    return null;
  }
  const props = Object.assign(readProps(el), options.props || {});
  let controller;
  try {
    controller = await mountFn(el, props);
  } catch (error) {
    if (OWNERS.get(el) === token) release(el);
    console.error("[islands] island mount failed", name, error);
    return null;
  }
  if (OWNERS.get(el) !== token) {
    // The island's own mount() was still in flight when unmount() (and
    // possibly a fresh mount()) took the element. This controller completed
    // after losing ownership: dispose it exactly once so it releases
    // whatever it rendered, and register nothing — the registry and any
    // buffered PENDING belong to whoever owns the element now.
    if (controller && typeof controller.dispose === "function") {
      try {
        controller.dispose();
      } catch (err) {
        console.error("[islands] error al disponer un controller tardío de", name, err);
      }
    }
    return null;
  }
  if (controller) {
    CONTROLLERS.set(el, controller);
    if (PENDING.has(el)) {
      const pending = PENDING.get(el);
      PENDING.delete(el);
      if (typeof controller.update === "function") controller.update(pending);
    }
  } else {
    release(el);
  }
  return controller || null;
}

/** release(el) — drops a mount claim together with the update buffered
 * under it. Only ever called while the caller's token still owns el. */
function release(el) {
  MOUNTED.delete(el);
  OWNERS.delete(el);
  PENDING.delete(el);
}

/** unmount(el) — explicit, idempotent teardown of the island mounted on
 * `el`: the ownership transfer a caller needs before replacing the markup
 * around (or inside) the element. Disposes the current controller — an
 * island SHOULD expose dispose(); one that doesn't is simply deregistered —
 * releases the mount claim so a later mount() on the same element starts
 * fresh, and drops the buffered PENDING update, which belonged to the
 * generation being torn down. In-flight ownership is respected: a mount()
 * whose import hasn't settled never invokes the island's mount() when it
 * does, and a controller completing after this call is disposed once and
 * never registered over a fresh owner. Returns true when this call released
 * an owned island (a controller or an in-flight claim), false when there
 * was nothing to release. */
function unmount(el) {
  if (!el) return false;
  const controller = CONTROLLERS.get(el);
  const released = !!controller || MOUNTED.has(el);
  // Release before calling external cleanup: it may synchronously claim the
  // same element again. The retiring owner cannot erase that fresh claim.
  CONTROLLERS.delete(el);
  release(el);
  if (controller) {
    if (typeof controller.dispose === "function") {
      try {
        controller.dispose();
      } catch (err) {
        console.error("[islands] error al disponer el island de", el, err);
      }
    }
  }
  return released;
}

/** scan(root?) — mounts every not-yet-mounted [data-island] element under
 * `root` (default: the whole document). Safe to call repeatedly: mount()
 * itself is idempotent per element via MOUNTED. */
function scan(root) {
  const scope = root || (typeof document !== "undefined" ? document : null);
  if (!scope || !scope.querySelectorAll) return;
  const found = scope.querySelectorAll("[data-island]");
  Array.prototype.forEach.call(found, (el) => { mount(el); });
}

/** update(el, props) — forwards to the mounted controller's own update(),
 * for a legacy call site that re-fetched data and wants the island to
 * reflect it without a full unmount/remount. Buffers `props` in PENDING
 * ONLY while a mount() has claimed the element and its controller hasn't
 * arrived yet — mount() applies the buffered value as soon as the
 * controller exists. With no controller and no in-flight mount (never
 * mounted, or unmounted), the update is dropped: buffering it would let a
 * stray post-dispose update() feed stale props to whichever mount() claims
 * the element next. */
function update(el, props) {
  const controller = CONTROLLERS.get(el);
  if (controller && typeof controller.update === "function") {
    controller.update(props);
    return controller;
  }
  if (MOUNTED.has(el)) PENDING.set(el, props);
  return null;
}

export const _internal = { readProps, resolveUrl, supportsImportMaps };
export { mount, scan, unmount, update };

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => scan(document));
  } else {
    scan(document);
  }
}
