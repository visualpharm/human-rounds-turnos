'use strict';
/* Check registry. A check module exports { id, wire?, scan?, finishPage?,
 * finish?, finishGlobal? } and is called by the runner:
 *  - wire(collector)      once per page, before the viewport loop (event wiring)
 *  - scan(visit)          per page × viewport, after load settles
 *  - finishPage(collector) once per page, after the last viewport
 *  - finish(site)         once per role, after all its pages (has the browser context)
 *  - finishGlobal(site)   once at the very end (lighthouse, walkthrough) */

const ORDER = ['links', 'console', 'requests', 'images', 'overflow', 'axe', 'contrast',
  'text_density', 'tap_targets', 'overlays', 'menu_style', 'lighthouse', 'html', 'walkthrough'];

const modules = {
  links: require('./links'),
  console: require('./console'),
  requests: require('./requests'),
  images: require('./images'),
  overflow: require('./overflow'),
  axe: require('./axe'),
  contrast: require('./contrast'),
  text_density: require('./text_density'),
  tap_targets: require('./tap_targets'),
  overlays: require('./overlays'),
  menu_style: require('./menu_style'),
  lighthouse: require('./lighthouse'),
  html: require('./html'),
  walkthrough: require('./walkthrough'),
};

function allChecks() {
  return ORDER.map((id) => modules[id]);
}

module.exports = { CHECK_ORDER: ORDER, allChecks, modules };
