export async function installHomeQueryTrace(page) {
  const mainFrames = [];
  const onFrameNavigated = (frame) => {
    if (frame === page.mainFrame()) mainFrames.push({ href: frame.url(), time: Date.now() });
  };
  page.on("framenavigated", onFrameNavigated);
  await page.addInitScript(() => {
    const trace = [];
    const push = (kind, detail = {}) => {
      trace.push({ kind, href: location.href, time: Math.round(performance.now() * 10) / 10, ...detail });
      if (trace.length > 48) trace.splice(0, trace.length - 48);
    };
    const stack = () => String(new Error().stack || "").split("\n").slice(1, 4).join(" | ");
    let knownQuery = null;
    const inspectQuery = (reason) => {
      const query = document.getElementById("q");
      if (query !== knownQuery) {
        push("query-node", {
          reason,
          previousConnected: knownQuery ? knownQuery.isConnected : null,
          currentConnected: query ? query.isConnected : null,
          value: query ? query.value : null,
        });
        knownQuery = query;
      }
    };
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    if (descriptor && descriptor.get && descriptor.set) {
      Object.defineProperty(HTMLInputElement.prototype, "value", {
        configurable: descriptor.configurable,
        enumerable: descriptor.enumerable,
        get: descriptor.get,
        set(value) {
          if (this.id !== "q") return descriptor.set.call(this, value);
          const before = descriptor.get.call(this);
          descriptor.set.call(this, value);
          push("value-set", { before, assigned: String(value), after: descriptor.get.call(this), stack: stack() });
        },
      });
    }
    ["beforeinput", "input", "change", "reset"].forEach((type) => {
      document.addEventListener(type, (event) => {
        const target = event.target;
        if (type === "reset" || (target && target.id === "q")) {
          const query = document.getElementById("q");
          push(type, { target: target && target.id, value: query ? query.value : null, stack: stack() });
        }
      }, true);
    });
    ["focusin", "focusout"].forEach((type) => {
      document.addEventListener(type, (event) => {
        push(type, {
          target: event.target?.id || event.target?.tagName,
          active: document.activeElement?.id || document.activeElement?.tagName,
          queryValue: document.getElementById("q")?.value ?? null,
        });
      }, true);
    });
    new MutationObserver(() => inspectQuery("mutation")).observe(document, { childList: true, subtree: true });
    inspectQuery("init");
    window.__homeQueryTrace = trace;
  });
  return {
    async compact() {
      const browser = await page.evaluate(() => (window.__homeQueryTrace || []).slice(-24));
      return { browser, mainFrames: mainFrames.slice(-6) };
    },
    dispose() { page.off("framenavigated", onFrameNavigated); },
  };
}
