/* nav-search-shortcut.test.mjs — Cmd-K/Ctrl-K abre la búsqueda pública de la
   página y Escape devuelve el foco (static/platform/nav.mjs, responsable del producto 2026-10-21:
   «make this Command-K shortcut so that it also opens search»). DOM sintético
   (sin navegador): el atajo enfoca el primer input[type=search] VISIBLE,
   cede ante un diálogo/modal, el chat abierto, un editor o una composición
   IME, y sin caja navega a la portada. Los listeners viven y mueren con el
   controlador (dispose/idempotencia).

   Correr:  node --test tests/nav-search-shortcut.test.mjs   (desde la raíz) */
import test from 'node:test';
import assert from 'node:assert/strict';
// Import the actual module before exposing the browser fixture, so auto-boot
// does not create an extra owner (same contract as nav-native-lifecycle).
import { mount } from '../static/platform/nav.mjs';

function events() {
  const handlers = new Map();
  return {
    addEventListener(type, fn) {
      if (!handlers.has(type)) handlers.set(type, new Set());
      handlers.get(type).add(fn);
    },
    removeEventListener(type, fn) { handlers.get(type)?.delete(fn); },
    fire(type, value = {}) { for (const fn of [...(handlers.get(type) || [])]) fn(value); },
    count(type) { return handlers.get(type)?.size || 0; },
  };
}
function classes() {
  const values = new Set();
  return {
    add(...names) { names.forEach(n => values.add(n)); },
    remove(...names) { names.forEach(n => values.delete(n)); },
    contains(name) { return values.has(name); },
    toggle(name, force) {
      const on = force ?? !values.has(name);
      if (on) values.add(name); else values.delete(name);
      return on;
    },
  };
}

// A visible public search input (the home's #q / the listings' .lst-search).
function searchInput(doc, extra = {}) {
  const el = {
    tagName: 'INPUT', type: 'search', disabled: false, readOnly: false,
    isConnected: true, focusCount: 0, selectCount: 0,
    getClientRects() { return [{}]; },
    focus() { this.focusCount++; doc.activeElement = this; },
    select() { this.selectCount++; },
    closest() { return null; },
    ...extra,
  };
  return el;
}
function focusable(doc, extra = {}) {
  const el = {
    tagName: 'A', isConnected: true, focusCount: 0,
    focus() { this.focusCount++; doc.activeElement = this; },
    closest() { return null; },
    ...extra,
  };
  return el;
}

function fixture(t, opts = {}) {
  const previous = new Map();
  const install = (key, value) => {
    previous.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  };
  const win = events(), doc = events();
  const requests = [];
  doc.activeElement = null;
  doc.body = {};
  const searchBoxes = opts.searchBoxes === undefined ? [searchInput(doc)] : opts.searchBoxes;
  doc.querySelectorAll = sel => (sel === 'input[type="search"]' ? searchBoxes : []);
  const chatPanel = opts.chatPanel || null;
  doc.getElementById = id => (id === 'stznav-styles' ? {} : (id === 'stz-chat-panel' ? chatPanel : null));
  doc.documentElement = { getAttribute() { return 'humanrounds'; }, style: { removeProperty() {}, setProperty() {} } };
  const host = {
    isConnected: true, markup: '', classList: classes(),
    get innerHTML() { return this.markup; },
    set innerHTML(value) { this.markup = value; },
    getAttribute() { return ''; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    contains() { return false; },
    replaceChildren() { this.innerHTML = ''; },
  };
  const location = {
    pathname: '/', hash: '', href: '/', assigns: [],
    assign(url) { this.assigns.push(url); },
  };
  install('window', win); install('document', doc); install('location', location);
  install('navigator', { platform: opts.platform || 'MacIntel' });
  install('localStorage', { getItem() { return null; }, setItem() {} });
  install('fetch', (url, options = {}) => new Promise(resolve => requests.push({ url, options, resolve })));
  install('MutationObserver', class {
    constructor(callback) { this.callback = callback; this.active = false; }
    observe() { this.active = true; }
    disconnect() { this.active = false; }
  });
  const controllers = [];
  const start = () => { const c = mount(host); controllers.push(c); return c; };
  t.after(() => {
    controllers.forEach(c => c.dispose());
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return { host, win, doc, location, searchBoxes, requests, start };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

const cmdK = (over = {}) => ({
  key: 'k', code: 'KeyK', keyCode: 75, metaKey: true, ctrlKey: false,
  altKey: false, shiftKey: false, isComposing: false, defaultPrevented: false,
  preventDefault() { this.defaultPrevented = true; },
  ...over,
});
const esc = () => ({ key: 'Escape', keyCode: 27, isComposing: false, preventDefault() {} });

test('Cmd-K focuses the visible public search and selects its text', async t => {
  const f = fixture(t);
  const link = focusable(f.doc);
  f.doc.activeElement = link;
  f.start(); await settle();
  const ev = cmdK();
  f.doc.fire('keydown', ev);
  assert.equal(f.searchBoxes[0].focusCount, 1);
  assert.equal(f.searchBoxes[0].selectCount, 1);
  assert.equal(f.doc.activeElement, f.searchBoxes[0]);
  assert.equal(ev.defaultPrevented, true, 'keeps Cmd-K away from the browser bar');
});

test('Ctrl-K works too (non-Mac), and the physical key decides on a Cyrillic layout', async t => {
  const f = fixture(t, { platform: 'Win32' });
  f.start(); await settle();
  f.doc.fire('keydown', cmdK({ metaKey: false, ctrlKey: true }));
  assert.equal(f.searchBoxes[0].focusCount, 1);
  // A Russian layout reports the Cyrillic letter U+043A as key but the same physical key.
  f.doc.fire('keydown', cmdK({ ctrlKey: true, metaKey: false, key: '\u043a', code: 'KeyK' }));
  assert.equal(f.searchBoxes[0].focusCount, 2);
});

test('Escape gives focus back to the invoker, but only while the shortcut owns it', async t => {
  const f = fixture(t);
  const link = focusable(f.doc);
  f.doc.activeElement = link;
  f.start(); await settle();
  f.doc.fire('keydown', cmdK());
  f.doc.fire('keydown', esc());
  assert.equal(link.focusCount, 1);
  assert.equal(f.doc.activeElement, link);
  // Focus reached the search by other means (a click/tab): Escape must NOT
  // teleport focus anywhere — the shortcut never owned this entry.
  f.doc.activeElement = f.searchBoxes[0];
  f.doc.fire('keydown', esc());
  assert.equal(link.focusCount, 1);
  assert.equal(f.doc.activeElement, f.searchBoxes[0]);
});

test('a focus change after the shortcut cancels the Escape restore', async t => {
  const f = fixture(t);
  const link = focusable(f.doc);
  f.doc.activeElement = link;
  f.start(); await settle();
  f.doc.fire('keydown', cmdK());
  const other = focusable(f.doc);
  other.focus();
  f.doc.fire('focusin', { target: other });
  f.doc.activeElement = f.searchBoxes[0];
  f.doc.fire('keydown', esc());
  assert.equal(link.focusCount, 0, 'invoker is stale, nothing to restore');
});

test('ignores: IME composition, open dialog/modal, chat panel, editor focus, Shift/Alt combos', async t => {
  const f = fixture(t);
  const editor = { tagName: 'TEXTAREA', isConnected: true, isContentEditable: false, focus() {}, closest() { return null; } };
  f.doc.activeElement = editor;
  f.start(); await settle();

  f.doc.fire('keydown', cmdK({ isComposing: true }));          // IME composing
  assert.equal(f.searchBoxes[0].focusCount, 0);
  const ev229 = cmdK({ keyCode: 229 });
  f.doc.fire('keydown', ev229);                                 // Safari's composition keyCode
  assert.equal(f.searchBoxes[0].focusCount, 0);

  const evDialog = cmdK();
  evDialog.defaultPrevented = true;                             // another component handled it
  f.doc.fire('keydown', evDialog);
  assert.equal(f.searchBoxes[0].focusCount, 0);

  f.doc.fire('keydown', cmdK());                                // textarea owns the keyboard
  assert.equal(f.searchBoxes[0].focusCount, 0);
  f.doc.fire('keydown', cmdK({ shiftKey: true }));              // Cmd-Shift-K: not ours
  assert.equal(f.searchBoxes[0].focusCount, 0);
  f.doc.fire('keydown', cmdK({ altKey: true }));
  assert.equal(f.searchBoxes[0].focusCount, 0);

  // Inside an open dialog: not ours either.
  const inDialog = { ...focusable(f.doc), closest: sel => (sel === 'dialog[open]' ? {} : null) };
  f.doc.activeElement = inDialog;
  f.doc.fire('keydown', cmdK());
  assert.equal(f.searchBoxes[0].focusCount, 0);
});

test('the chat panel open keeps the keyboard (it is the modal of the moment)', async t => {
  const f = fixture(t, { chatPanel: { hasAttribute: name => name === 'hidden' ? false : true } });
  f.start(); await settle();
  f.doc.fire('keydown', cmdK());
  assert.equal(f.searchBoxes[0].focusCount, 0);
});

test('a page without a visible search opens the home (which IS the search)', async t => {
  const f = fixture(t, { searchBoxes: [] });
  f.start(); await settle();
  f.doc.fire('keydown', cmdK());
  assert.deepEqual(f.location.assigns, ['/#q']);

  const hidden = fixture(t, { searchBoxes: [searchInput(f.doc, { getClientRects() { return []; } })] });
  hidden.start(); await settle();
  hidden.doc.fire('keydown', cmdK());
  assert.deepEqual(hidden.location.assigns, ['/#q']);
});

test('a search input inside a dialog is never the target', async t => {
  const dialogDoc = { activeElement: null };
  const dialogBox = searchInput(dialogDoc, { closest: sel => (sel.includes('dialog,') ? {} : null) });
  const f = fixture(t, { searchBoxes: [dialogBox] });
  const second = searchInput(f.doc);
  f.searchBoxes.push(second);
  f.start(); await settle();
  f.doc.activeElement = null;
  f.doc.fire('keydown', cmdK());
  assert.equal(second.focusCount, 1, 'falls through to the first public box');
  assert.equal(dialogBox.focusCount, 0);
});

test('dispose removes the handlers; a second mount does not double-handle', async t => {
  const f = fixture(t);
  const first = f.start(); await settle();
  f.doc.fire('keydown', cmdK());
  assert.equal(f.searchBoxes[0].focusCount, 1);

  first.dispose();
  f.doc.activeElement = null;
  f.doc.fire('keydown', cmdK());
  assert.equal(f.searchBoxes[0].focusCount, 1, 'disposed controller no longer listens');

  f.start(); await settle();
  f.doc.activeElement = null;
  f.doc.fire('keydown', cmdK());
  assert.equal(f.searchBoxes[0].focusCount, 2, 'exactly one focus per event after re-mount');
  assert.equal(f.doc.count('keydown'), 1, 'one live keydown listener, not two');
});

test('focus already inside a search box: Cmd-K re-focuses without an invoker', async t => {
  const f = fixture(t);
  f.doc.activeElement = f.searchBoxes[0];
  f.start(); await settle();
  f.doc.fire('keydown', cmdK());
  assert.equal(f.searchBoxes[0].selectCount, 1);
  f.doc.fire('keydown', esc());
  assert.equal(f.doc.activeElement, f.searchBoxes[0], 'nothing to restore — stays put');
});


test('repeated shortcut retains the original focus return target', async t => {
  const f = fixture(t);
  const link = focusable(f.doc);
  f.doc.activeElement = link;
  f.start(); await settle();
  f.doc.fire('keydown', cmdK());
  f.doc.fire('keydown', cmdK());
  f.doc.fire('keydown', esc());
  assert.equal(f.doc.activeElement, link);
});

test('uses only the platform modifier and yields to an open modal before focus moves', async t => {
  const f = fixture(t);
  f.start(); await settle();
  f.doc.fire('keydown', cmdK({metaKey:false,ctrlKey:true}));
  assert.equal(f.searchBoxes[0].focusCount, 0);
  const original = f.doc.querySelectorAll;
  f.doc.querySelectorAll = selector => selector.startsWith('dialog[open]')
    ? [{getClientRects:()=>[{}],closest:()=>null}] : original(selector);
  f.doc.fire('keydown', cmdK());
  assert.equal(f.searchBoxes[0].focusCount, 0);
});
