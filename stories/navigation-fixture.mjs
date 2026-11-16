// Scoped synthetic responses for the native navigation stories. Each owner
// snapshots its predecessor so out-of-order Storybook cleanup skips owners
// that were already disposed rather than reviving their globals.
const fixtureOwner = Symbol("navigation story fixture");

function json(body, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

export function installNavigationFixture() {
  const style = document.documentElement.style;
  const owner = {
    previousFetch: window.fetch,
    previousCache: localStorage.getItem("st_nav"),
    previousHeight: style.getPropertyValue("--stznav-h"),
    previousPriority: style.getPropertyPriority("--stznav-h"),
    previousTrace: window.__HRNavFixture,
    disposed: false,
  };
  const trace = { calls: [], mode: null };
  window.__HRNavFixture = trace;

  const fixtureFetch = (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, window.location.href);
    const method = (init.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    const key = method + " " + url.pathname;
    if (url.origin !== window.location.origin) throw new Error("External navigation story request blocked: " + key);
    if (!url.pathname.startsWith("/api/")) return owner.previousFetch(input, init);
    const signal = init.signal || (input instanceof Request ? input.signal : null);
    if (signal?.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
    trace.calls.push(key);
    if (key === "GET /api/account/me") {
      return trace.mode === "authenticated"
        ? json({ usuario: { nombre: "Marina", apellido: "Ejemplo" } })
        : json({ error: "not_authenticated" }, 401);
    }
    if (key === "GET /api/ai-status") return json({ disponible: true });
    if (key === "GET /api/account/config") return json({ auth_mode: "password" });
    if (key === "GET /api/account/photo") return json({ source: "none" });
    throw new Error("Unexpected navigation story API request: " + key);
  };
  fixtureFetch[fixtureOwner] = owner;
  window.fetch = fixtureFetch;

  return () => {
    if (owner.disposed) return;
    owner.disposed = true;
    if (window.fetch !== fixtureFetch || window.__HRNavFixture !== trace) return;
    let boundary = owner;
    while (boundary.previousFetch?.[fixtureOwner]?.disposed) {
      boundary = boundary.previousFetch[fixtureOwner];
    }
    window.fetch = boundary.previousFetch;
    if (boundary.previousTrace === undefined) delete window.__HRNavFixture;
    else window.__HRNavFixture = boundary.previousTrace;
    if (boundary.previousCache === null) localStorage.removeItem("st_nav");
    else localStorage.setItem("st_nav", boundary.previousCache);
    if (boundary.previousHeight) {
      style.setProperty("--stznav-h", boundary.previousHeight, boundary.previousPriority);
    } else {
      style.removeProperty("--stznav-h");
    }
  };
}
