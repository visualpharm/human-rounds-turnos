/* Shared photo viewer.  Anchors marked data-photozoom remain ordinary image
   links until this module has mounted, so a held or rejected module request
   never turns a patient-visible image into a dead control. */

const instances = new WeakMap();

export function normalizeItems(itemsOrSrc, options = {}) {
  const raw = typeof itemsOrSrc === "string" ? [itemsOrSrc] : (itemsOrSrc || []);
  return raw.map((item) => typeof item === "string"
    ? { src: item, alt: options.alt || "" }
    : { src: item && item.src, alt: (item && item.alt) || "" })
    .filter((item) => item.src);
}

export function itemFrom(node) {
  return {
    src: node.getAttribute("data-photozoom-src") || node.getAttribute("href") || "",
    alt: node.getAttribute("data-photozoom-alt") || node.getAttribute("aria-label") || "",
  };
}

export function groupedItems(doc, trigger) {
  const group = trigger.getAttribute("data-photozoom-group") || "";
  const nodes = group
    ? Array.from(doc.querySelectorAll("[data-photozoom]")).filter((node) =>
      (node.getAttribute("data-photozoom-group") || "") === group)
    : [trigger];
  const usable = nodes.filter((node) => itemFrom(node).src);
  return { items: usable.map(itemFrom), index: Math.max(0, usable.indexOf(trigger)) };
}

function style(doc) {
  if (doc.getElementById("photozoom-styles")) return;
  const node = doc.createElement("style");
  node.id = "photozoom-styles";
  node.textContent = ".pz-overlay{position:fixed;inset:0;background:rgba(20,16,12,.94);z-index:9999;display:none}.pz-overlay.pz-open{display:block}.pz-stage{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:56px 16px;cursor:zoom-out}.pz-img{max-width:100%;max-height:100%;object-fit:contain;border-radius:6px;box-shadow:0 20px 60px -20px rgba(0,0,0,.6);cursor:default;opacity:0;transition:opacity .15s ease}.pz-img.pz-ready{opacity:1}.pz-btn{position:absolute;border:0;border-radius:50%;background:rgba(255,255,255,.14);color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .12s;-webkit-tap-highlight-color:transparent}.pz-btn:hover{background:rgba(255,255,255,.26);color:#fff}.pz-btn:focus-visible{outline:2px solid #fff;outline-offset:2px}.pz-close{top:14px;right:14px;width:46px;height:46px;border-radius:50%}.pz-prev,.pz-next{top:50%;transform:translateY(-50%);width:52px;height:52px;font-size:0}.pz-prev::before,.pz-next::before{content:\"\";display:block;width:24px;height:24px;background:currentColor;-webkit-mask:url(/static/icons/dir-card-arrow-sf-regular.svg) center/contain no-repeat;mask:url(/static/icons/dir-card-arrow-sf-regular.svg) center/contain no-repeat}.pz-prev::before{transform:rotate(180deg)}.pz-prev{left:10px}.pz-next{right:10px}.pz-counter{position:absolute;left:0;right:0;bottom:14px;text-align:center;color:rgba(255,255,255,.8);font:500 13px/1 var(--font);pointer-events:none}@media(max-width:640px){.pz-close{top:8px;right:8px}.pz-prev{left:4px}.pz-next{right:4px}}";
  doc.head.appendChild(node);
}

function button(doc, className, text, label) {
  const node = doc.createElement("button");
  node.type = "button";
  node.className = `pz-btn ${className}`;
  node.textContent = text;
  node.setAttribute("aria-label", label);
  return node;
}

function build(doc, controller) {
  style(doc);
  const overlay = doc.createElement("div");
  overlay.className = "pz-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-hidden", "true");
  const stage = doc.createElement("div");
  stage.className = "pz-stage";
  const image = doc.createElement("img");
  image.className = "pz-img";
  image.alt = "";
  stage.appendChild(image);
  // The icon is the shared close button .hr-x (human-rounds.css); .pz-btn keeps the round backdrop.
  const close = button(doc, "pz-close hr-x", "", "Cerrar");
  // The arrows are the Icons8 arrow of the project pack as a mask (::before), never a typed glyph.
  const previous = button(doc, "pz-prev", "", "Foto anterior");
  const next = button(doc, "pz-next", "", "Foto siguiente");
  const counter = doc.createElement("div");
  counter.className = "pz-counter";
  overlay.append(stage, close, previous, next, counter);
  doc.body.appendChild(overlay);

  controller.applyLabels({ close, previous, next });

  close.addEventListener("click", controller.close);
  previous.addEventListener("click", (event) => { event.stopPropagation(); controller.previous(); });
  next.addEventListener("click", (event) => { event.stopPropagation(); controller.next(); });
  stage.addEventListener("click", (event) => { if (event.target === stage) controller.close(); });
  stage.addEventListener("touchstart", controller.touchStart, { passive: true });
  stage.addEventListener("touchend", controller.touchEnd);
  return { overlay, stage, image, close, previous, next, counter };
}

export function mount(doc = globalThis.document) {
  if (!doc || !doc.body) return null;
  if (instances.has(doc)) return instances.get(doc);
  const state = { items: [], index: 0, trigger: null, touchX: null, elements: null, priorOverflow: "", destroyed: false,
    labels: { close: "Cerrar", previous: "Foto anterior", next: "Foto siguiente" } };
  const controller = {
    applyLabels(elements = state.elements) {
      if (!elements) return;
      elements.close.setAttribute("aria-label", state.labels.close);
      elements.previous.setAttribute("aria-label", state.labels.previous);
      elements.next.setAttribute("aria-label", state.labels.next);
    },
    open(itemsOrSrc, options = {}) {
      if (state.destroyed) return false;
      const items = normalizeItems(itemsOrSrc, options);
      if (!items.length) return false;
      if (!state.elements) state.elements = build(doc, controller);
      state.items = items;
      state.index = Math.min(Math.max(options.index || 0, 0), items.length - 1);
      controller.render();
      const wasOpen = state.elements.overlay.classList.contains("pz-open");
      if (!wasOpen) {
        state.trigger = options.trigger || doc.activeElement;
        state.priorOverflow = doc.documentElement.style.overflow;
      }
      state.elements.overlay.classList.add("pz-open");
      state.elements.overlay.setAttribute("aria-hidden", "false");
      doc.documentElement.style.overflow = "hidden";
      state.elements.close.focus();
      return true;
    },
    close() {
      if (!state.elements || !state.elements.overlay.classList.contains("pz-open")) return;
      state.elements.overlay.classList.remove("pz-open");
      state.elements.overlay.setAttribute("aria-hidden", "true");
      state.elements.image.removeAttribute("src");
      doc.documentElement.style.overflow = state.priorOverflow;
      if (state.trigger && typeof state.trigger.focus === "function") state.trigger.focus();
    },
    render() {
      if (state.destroyed) return;
      const item = state.items[state.index] || {};
      const { image, previous, next, counter } = state.elements;
      image.classList.remove("pz-ready");
      image.onload = () => image.classList.add("pz-ready");
      image.alt = item.alt || "";
      image.src = item.src || "";
      const multiple = state.items.length > 1;
      previous.style.display = multiple ? "" : "none";
      next.style.display = multiple ? "" : "none";
      counter.style.display = multiple ? "" : "none";
      counter.textContent = multiple ? `${state.index + 1} / ${state.items.length}` : "";
    },
    next() { if (!state.destroyed && state.items.length > 1) { state.index = (state.index + 1) % state.items.length; controller.render(); } },
    previous() { if (!state.destroyed && state.items.length > 1) { state.index = (state.index - 1 + state.items.length) % state.items.length; controller.render(); } },
    onKey(event) {
      if (!state.elements || !state.elements.overlay.classList.contains("pz-open")) return;
      if (event.key === "Escape" || event.key === "Esc") controller.close();
      else if (event.key === "ArrowRight") controller.next();
      else if (event.key === "ArrowLeft") controller.previous();
    },
    handleClick(event) {
      if (state.destroyed) return;
      if (event.defaultPrevented || event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const trigger = event.target && event.target.closest && event.target.closest("[data-photozoom]");
      if (!trigger) return;
      const gallery = groupedItems(doc, trigger);
      if (!gallery.items.length) return;
      event.preventDefault();
      controller.open(gallery.items, { index: gallery.index, trigger });
    },
    touchStart(event) { state.touchX = event.touches && event.touches[0] ? event.touches[0].clientX : null; },
    touchEnd(event) {
      const end = event.changedTouches && event.changedTouches[0];
      if (state.touchX == null || !end || state.items.length < 2) { state.touchX = null; return; }
      const delta = end.clientX - state.touchX;
      state.touchX = null;
      if (Math.abs(delta) >= 40) (delta < 0 ? controller.next : controller.previous)();
    },
    destroy() {
      if (state.destroyed) return;
      controller.close();
      state.destroyed = true;
      doc.removeEventListener("click", controller.handleClick);
      doc.removeEventListener("keydown", controller.onKey);
      if (state.elements && state.elements.overlay.parentNode) state.elements.overlay.parentNode.removeChild(state.elements.overlay);
      state.elements = null;
      state.items = [];
      state.trigger = null;
      instances.delete(doc);
    },
    get state() { return { items: state.items.slice(), index: state.index, open: !!(state.elements && state.elements.overlay.classList.contains("pz-open")) }; },
  };
  instances.set(doc, controller);
  doc.addEventListener("click", controller.handleClick);
  doc.addEventListener("keydown", controller.onKey);
  // Translation is optional and must never delay mounting: every rendered
  // trigger keeps its native href until this module is ready, and a rejected
  // catalog simply leaves the same Spanish fallbacks that classic PhotoZoom used.
  const i18nUrl = globalThis.SANA_STATIC && globalThis.SANA_STATIC["platform/i18n.mjs"];
  if (i18nUrl) import(i18nUrl).then((i18n) => {
    if (state.destroyed) return;
    state.labels = {
      close: i18n.t("intake.common.close", null, "Cerrar"),
      previous: i18n.t("intake.photozoom.prev", null, "Foto anterior"),
      next: i18n.t("intake.photozoom.next", null, "Foto siguiente"),
    };
    controller.applyLabels();
  }).catch(() => {});
  return controller;
}

export function open(itemsOrSrc, options) { return mount()?.open(itemsOrSrc, options); }
export function close() { return mount()?.close(); }

if (typeof document !== "undefined") mount(document);
