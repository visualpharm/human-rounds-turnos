'use strict';
/* Browser mount for hosts that prefer a client-side panel: the host page has
 * <div data-usability-kit data-api="/admin/usability"></div> and loads, in
 * order, store/rank.js, panel/render.js and this file as plain external
 * scripts (CSP-friendly: no inline script, no eval, no inline styles beyond
 * the one injected <style> — hosts with style-src 'unsafe-inline' bans should
 * server-render instead, via api.js#page).
 *
 * Fetches GET {data-api}/api/store once and renders the whole panel into the
 * element. No polling, no framework, ~1.5 kB. */
(function () {
  var PANEL = window.UsabilityKitPanel;
  if (!PANEL) return;

  var el = document.querySelector('[data-usability-kit]');
  if (!el) return;
  var base = (el.getAttribute('data-api') || '').replace(/\/+$/, '');
  var opts = {
    base: base,
    lang: el.getAttribute('data-lang') === 'es' ? 'es' : 'en',
    canRun: el.getAttribute('data-can-run') !== 'false',
    csrf: el.getAttribute('data-csrf') || null,
  };

  fetch(base + '/api/store', { credentials: 'same-origin' })
    .then(function (r) {
      if (!r.ok) throw new Error('store fetch failed: ' + r.status);
      return r.json();
    })
    .then(function (store) {
      var out = PANEL.render(store, opts);
      var style = document.createElement('style');
      style.id = 'usability-kit-css';
      var existing = document.getElementById(style.id);
      if (existing) existing.remove();
      document.head.appendChild(style);
      style.textContent = out.css;
      el.innerHTML = out.html;
    })
    .catch(function () {
      /* One quiet line, no chrome: the host page keeps its own error UI. */
      el.textContent = 'Usability panel unavailable.';
    });
})();
