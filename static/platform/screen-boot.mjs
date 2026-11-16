// One loader for every screen whose controller arrives after the page: the
// panel screens under templates/admin/, the intake consultation, the test
// identity page and the study pages. It owns the controller's lifetime
// (generation guard, pagehide disposal, persisted-pageshow remount) and the
// eight-second deadline.
//
// The deadline only SAYS the load is late; it never abandons it (2026-11-16,
// after templates/account.html of 2026-11-15). At eight seconds, while the
// controller has neither loaded its dependencies nor become ready, the
// shared late copy («La carga tarda más de lo normal.» and «Recargar»,
// templates/_screen_load_late.html; a page without it falls back to the
// screen's recovery copy) appears beside the screen — never inside it, so a
// skeleton the controller already drew stays where the controller left it —
// and the load goes on. A healthy late answer removes the copy. A real
// failure — the import or the mount throws, `ready` rejects, the controller
// calls `onError` — still stops the load and shows the copy where the screen
// always showed it.
//
//   bootScreen({
//     host,          // the screen's root element
//     recovery,      // id of a <template> (cloned; [data-retry] reloads) or of
//                    // an element shown and hidden through `hidden`
//     load,          // () => import(...) — the controller module
//     mount,         // (module, onError, { first }) => controller: a dispose
//                    // function or an object with destroy(), carrying `ready`
//                    // and optionally `loaded` (resolves true once its
//                    // dependencies are in)
//     failedIn,      // where a failure's copy goes (default: host's children)
//     slowIn,        // where the late copy goes (default: its own node right
//                    // before host)
//     late,          // id of the late copy's <template> (default
//                    // "screen-load-late")
//     onStart, onStop, onReady, onFailed,  // per-screen extras (aria-busy,
//                    // gating, a status line hidden on failure)
//     pageshow,      // "start" (default) remounts on a persisted pageshow,
//                    // "reload" reloads the page instead
//   })
//
// Returns { start, stop } (the listeners are already installed and the first
// start already ran).

export const SLOW_AFTER_MS = 8000;

function disposeController(controller) {
  if (typeof controller === "function") controller();
  else if (controller && typeof controller.destroy === "function") controller.destroy();
}

export function bootScreen(options) {
  const {
    host,
    recovery,
    load,
    mount,
    failedIn = host,
    slowIn = null,
    late = "screen-load-late",
    onStart = null,
    onStop = null,
    onReady = null,
    onFailed = null,
    pageshow = "start",
  } = options;
  let generation = 0, timer = null, controller = null, slowNode = null, slowHidden = false;
  // Where a failure's copy went outside the host, so a restart takes it down
  // (a copy in the host is replaced by the controller's own mount).
  let failedNode = null, failedHidden = false;

  function recoveryElement() { return document.getElementById(recovery); }

  // A template's copy as a fragment for this owner; a retry from a copy left
  // over from an older generation does nothing.
  function copy(owner, source = recoveryElement()) {
    if (!source || !source.content) return null;
    const fragment = source.content.cloneNode(true);
    const retry = fragment.querySelector("[data-retry]");
    if (retry) retry.onclick = function () { if (owner === generation) location.reload(); };
    return fragment;
  }

  function hideSlow() {
    if (!slowNode) return;
    if (slowNode === "element") recoveryElement().hidden = true;
    else if (slowNode === slowIn) { slowIn.replaceChildren(); slowIn.hidden = slowHidden; }
    else slowNode.remove();
    slowNode = null;
  }

  // The late copy is the page's shared one (templates/_screen_load_late.html:
  // the load is late, not failed); a page without it shows the recovery copy.
  function showSlow(owner) {
    let fragment = copy(owner, document.getElementById(late));
    if (!fragment) {
      const source = recoveryElement();
      if (source && !source.content) { source.hidden = false; slowNode = "element"; return; }
      fragment = copy(owner);
    }
    if (!fragment) return;
    if (slowIn) {
      slowHidden = slowIn.hidden;
      slowIn.replaceChildren(fragment);
      slowIn.hidden = false;
      slowNode = slowIn;
      return;
    }
    const node = document.createElement("div");
    node.className = "screen-load-slow";
    node.append(fragment);
    host.before(node);
    slowNode = node;
  }

  function showFailed() {
    const source = recoveryElement();
    if (source && !source.content) { source.hidden = false; failedNode = "element"; return; }
    const fragment = copy(generation);
    if (!fragment) return;
    if (failedIn !== host && failedNode !== failedIn) { failedHidden = failedIn.hidden; failedNode = failedIn; }
    failedIn.replaceChildren(fragment);
    failedIn.hidden = false;
  }

  function hideFailed() {
    if (!failedNode) return;
    if (failedNode === "element") recoveryElement().hidden = true;
    else { failedNode.replaceChildren(); failedNode.hidden = failedHidden; }
    failedNode = null;
  }

  function stop() {
    generation++;
    clearTimeout(timer);
    timer = null;
    const current = controller;
    controller = null;
    disposeController(current);
    hideSlow();
    if (onStop) onStop();
  }

  function start(first) {
    stop();
    hideFailed();
    const owner = generation;
    let settled = false;
    if (onStart) onStart();
    function failed() {
      if (owner !== generation) return;
      stop();
      if (onFailed) onFailed();
      showFailed();
    }
    // Dependencies in or the screen ready: the late copy, if it is up, goes.
    function settle() {
      if (owner !== generation || settled) return;
      settled = true;
      clearTimeout(timer);
      timer = null;
      hideSlow();
    }
    function slow() {
      timer = null;
      if (owner !== generation || settled) return;
      showSlow(owner);
    }
    timer = setTimeout(slow, SLOW_AFTER_MS);
    Promise.resolve().then(load).then(function (module) {
      if (owner !== generation) return;
      const mounted = mount(module, failed, { first: first === true });
      // onError called inside mount() already stopped this owner: the
      // controller it returned is not kept beside the failure copy.
      if (owner !== generation) { disposeController(mounted); return; }
      controller = mounted;
      if (controller && controller.loaded) {
        controller.loaded.then(function (ok) { if (ok) settle(); }, function () {});
      }
      return controller && controller.ready;
    }).then(function () {
      if (owner !== generation) return;
      settle();
      if (onReady) onReady();
    }).catch(failed);
  }

  window.addEventListener("pagehide", function () { stop(); });
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    if (pageshow === "reload") location.reload();
    else start(false);
  });
  start(true);
  return { start, stop };
}
