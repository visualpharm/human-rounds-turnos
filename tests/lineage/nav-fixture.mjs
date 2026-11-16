// DOM fixture from tests/nav-native-lifecycle.test.mjs.
import { mount } from '../../static/platform/nav.mjs';
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
  const values = new Set(['external-owner']);
  return {
    add(...names) { names.forEach(n => values.add(n)); },
    remove(...names) { names.forEach(n => values.delete(n)); },
    contains(name) { return values.has(name); },
    toggle(name, force) {
      const on = force ?? !values.has(name);
      if (on) values.add(name); else values.delete(name);
      return on;
    },
    clear() { values.clear(); },
  };
}
export function fixture(t, loggedIn = false, hostless = false) {
  const previous = new Map();
  const install = (key, value) => {
    previous.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  };
  const win = events(), doc = events();
  const requests = [], writes = [], observers = [], avatars = [];
  function avatar() {
    const img = { src: '', removeAttribute() { this.src = ''; } };
    const av = { img, classList: classes(), querySelector() { return img; } };
    avatars.push(av);
    return av;
  }
  const host = {
    isConnected: true, markup: '', classList: classes(),
    get innerHTML() { return this.markup; },
    set innerHTML(value) { this.markup = value; this.avatar = value.includes('stznav-cuenta-av') ? avatar() : null; },
    getAttribute() { return ''; },
    querySelector(selector) { return selector === '.stznav-cuenta-av' ? this.avatar : null; },
    querySelectorAll() { return []; }, contains() { return false; },
    replaceChildren() { this.innerHTML = ''; },
    set className(value) { this.classList.clear(); },
  };
  doc.body = {};
  doc.getElementById = id => id === 'stznav-styles' ? {} : (hostless ? null : host);
  doc.documentElement = { getAttribute() { return 'humanrounds'; }, style: {
    removeProperty() {}, setProperty() {},
  } };
  install('window', win); install('document', doc);
  install('location', { pathname: '/', hash: '', href: '/' });
  install('localStorage', { getItem(key) { return key === 'st_nav' && loggedIn ? JSON.stringify({loggedIn: true, hasSolicitudes: true, iniciales: "AP"}) : null; }, setItem(...args) { writes.push(args); } });
  install('fetch', (url, options = {}) => new Promise(resolve => requests.push({ url, options, resolve })));
  install('MutationObserver', class {
    constructor(callback) { this.callback = callback; this.active = false; observers.push(this); }
    observe() { this.active = true; }
    disconnect() { this.active = false; }
  });
  const controllers = [];
  const start = () => { const c = mount(hostless ? undefined : host); controllers.push(c); return c; };
  t.after(() => {
    controllers.forEach(c => c.dispose());
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return { host, win, doc, requests, writes, observers, avatars, start };
}
