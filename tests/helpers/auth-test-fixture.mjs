// Complete native controller in a narrow DOM double. Browser gates separately
// prove actual rendering, focus, form validation and gesture reachability.
import { mount } from '../../static/platform/auth-test.mjs';
import { readFileSync } from 'node:fs';

// Import the actual canonical modules with the same pack as the real renderer.
// Restore the global after evaluation; format.mjs owns its captured config.
const previousPhone = Object.getOwnPropertyDescriptor(globalThis, 'SANA_COUNTRY_PHONE');
globalThis.SANA_COUNTRY_PHONE = JSON.parse(readFileSync(new URL('../../packs/ar/country.json', import.meta.url), 'utf8')).phone;
export const Format = await import('../../static/platform/format.mjs');
export const Dates = await import('../../static/platform/dates.mjs');
if (previousPhone) Object.defineProperty(globalThis, 'SANA_COUNTRY_PHONE', previousPhone);
else delete globalThis.SANA_COUNTRY_PHONE;

export const flush = () => new Promise(resolve => setImmediate(resolve));
export function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
export const response = (json, ok = true) => ({ ok, json: async () => json });
export const identity = (dni, nombre = 'Synthetic') => ({ dni, nombre, apellido: 'Fixture', nacimiento: '1990-01-01' });

export async function fixture({ returnTo = '/account', embedded = false, popup = false,
  holdLists = false, loadI18n = async () => null,
  loadFormat = async () => Format, loadDates = async () => Dates } = {}) {
  function node() {
    return {
      value: '', textContent: '', innerHTML: '', disabled: false, open: false,
      valid: true, focused: false, listeners: new Map(), attributes: new Map(),
      addEventListener(type, handler) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type).add(handler);
      },
      removeEventListener(type, handler) { this.listeners.get(type)?.delete(handler); },
      dispatch(type, event = {}) {
        for (const listener of [...(this.listeners.get(type) || [])])
          listener.call(this, { preventDefault() {}, ...event });
      },
      setAttribute(key, value) { this.attributes.set(key, value); },
      reportValidity() { return this.valid; }, focus() { this.focused = true; },
      showModal() { this.open = true; }, close() { this.open = false; },
    };
  }
  const elements = new Map(), people = [], calls = [], lists = [], messages = [], timers = [];
  const pendingTimers = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, node());
    return elements.get(id);
  };
  const location = { origin: 'https://example.invalid', search: '?provider=renaper&return_to=' + encodeURIComponent(returnTo), href: '' };
  const window = Object.assign(node(), {
    location,
    setTimeout(callback) { const id = timers.push(callback); pendingTimers.set(id, callback); return id; },
    clearTimeout(id) { pendingTimers.delete(id); },
    close() { window.closedByController = true; },
    fetch(url, options) {
      if (!options?.method) {
        const request = { url, options, ...deferred() };
        lists.push(request);
        if (!holdLists) request.resolve(response({ identidades: [] }));
        return request.promise;
      }
      const request = { url, options, ...deferred() };
      calls.push(request);
      return request.promise;
    },
  });
  window.parent = embedded ? { postMessage(...args) { messages.push(args); } } : window;
  if (popup) window.opener = { closed: false, postMessage(...args) { messages.push(args); } };
  const document = {
    defaultView: window, getElementById: element,
    querySelectorAll(selector) {
      return selector === '.tid-edit, #tid-new' ? [element('tid-new')]
        : selector === '.tid-person' ? people.map(person => person.row)
        : [...people.map(person => person.button), element('tid-random'), element('tid-save')];
    },
  };
  const controller = mount(document, { loadI18n, loadFormat, loadDates });
  controller.ready.catch(() => {});
  await flush();
  function person(dni) {
    const button = Object.assign(node(), { getAttribute: () => dni,
      closest: selector => selector === '.tid-login' || selector === 'button' ? button : null });
    const row = Object.assign(node(), { getAttribute: () => dni, querySelector: () => button,
      closest: selector => selector === '.tid-person' ? row : null });
    const person = { row, button }; people.push(person); return person;
  }
  const click = id => element(id).dispatch('click');
  const submit = () => element('tid-form').dispatch('submit');
  return { element, calls, lists, location, window, document, messages, timers, person,
    controller, pendingTimers, click, submit,
    dispatch: (type, target, key) => element('tid-rows').dispatch(type, { target, key }),
    remount: () => mount(document, { loadI18n, loadFormat, loadDates }),
    openDraft(dni, name) {
      click('tid-new'); element('tid-dni').value = dni; element('tid-name').value = name;
      element('tid-surname').value = 'Synthetic'; element('tid-birth').value = '1990-01-01';
    },
  };
}
