// Shared piece of the template bootstrap harnesses (2026-11-16): a screen's
// module script imports the one loader, static/platform/screen-boot.mjs,
// through its server-fingerprinted URL. A harness that executes the script
// verbatim as a data: module re-points that one specifier at the real file,
// so the template's wiring runs against the real loader, never a stub.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

export const SCREEN_BOOT_IMPORT = `"{{ static('platform/screen-boot.mjs') }}"`;
export const SCREEN_BOOT_URL = new URL('../static/platform/screen-boot.mjs', import.meta.url).href;
export const SLOW_CLASS = 'screen-load-slow';

export function withScreenBoot(script) {
  assert.ok(script.includes(SCREEN_BOOT_IMPORT), 'the screen imports the shared loader');
  return script.replace(SCREEN_BOOT_IMPORT, JSON.stringify(SCREEN_BOOT_URL));
}

// The same for a script a browser runs from a page with no origin of its own
// (setContent): the loader travels inline as a data: module (it imports
// nothing itself).
export function withScreenBootInline(script) {
  assert.ok(script.includes(SCREEN_BOOT_IMPORT), 'the screen imports the shared loader');
  const source = readFileSync(new URL(SCREEN_BOOT_URL));
  const url = 'data:text/javascript;base64,' + source.toString('base64');
  return script.replace(SCREEN_BOOT_IMPORT, JSON.stringify(url));
}

// The late copy is a sibling placed right before the screen's host: a fake
// element that may receive it needs a parent and before()/remove().
export function withSiblings(ElementClass) {
  return class extends ElementClass {
    before(node) {
      const parent = this.parent;
      assert.ok(parent, 'the host sits inside a parent');
      parent.children.splice(parent.children.indexOf(this), 0, node);
      node.parent = parent;
    }
    remove() {
      const parent = this.parent;
      if (!parent) return;
      parent.children.splice(parent.children.indexOf(this), 1);
      this.parent = null;
    }
    append(...nodes) {
      const add = nodes.flatMap(node => (node && node.fragment ? node.children : [node]));
      for (const node of add) if (node && typeof node === 'object') node.parent = this;
      this.children.push(...add);
    }
  };
}

// The wrapper of a host and the late copy, if any, right before it.
export function placeHost(Element, host) {
  const wrap = new Element('div');
  wrap.children = [host];
  host.parent = wrap;
  return {
    wrap,
    slowCopy() { return wrap.children.find(node => node.className === SLOW_CLASS) ?? null; },
  };
}
