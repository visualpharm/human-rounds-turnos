// Fictional records and exact admin routes for the three native editorial
// screens. One owner lives for one Storybook story and rejects unknown APIs.
const fixtureOwner = Symbol("admin editorial story fixture owner");
const copy = (value) => JSON.parse(JSON.stringify(value));
const reply = (body) => new Response(JSON.stringify(body), {
  headers: { "Content-Type": "application/json" },
});

const initial = {
  preparations: [{
    id: "story-ecografia", nombre: "Ecografía abdominal", resumen: "Preparación para el estudio",
    match: ["ecografia", "abdomen"],
    ayuno: { required: true, horas: 6, agua_permitida: true, nota: "Agua permitida" },
    antes: ["Seguí las indicaciones del centro"], traer: ["Orden del estudio"],
    avisar: [], no_hace_falta: [], despues: [], _origen: "json+db", _borrado: false,
  }],
  legal: [
    { id: "terminos", titulo: "Términos de uso", pagina: "/terms", cuerpo: "## Términos\nTexto de ejemplo.",
      original: "## Términos\nTexto de ejemplo.", actualizado: null, actualizado_por: null, editado: false },
    { id: "privacidad", titulo: "Privacidad", pagina: "/privacy", cuerpo: "## Privacidad\nTexto de ejemplo.",
      original: "## Privacidad\nTexto de ejemplo.", actualizado: null, actualizado_por: null, editado: false },
  ],
  prompts: [{
    clave: "story-appointment", titulo: "Appointment assistance",
    que_hace: "Helps explain appointment steps.", donde: "Appointment assistant",
    variables: [{ nombre: "sitio", que_es: "Current installation" }],
    editado: false, texto_en: "Explain the next appointment step clearly.",
    texto_es: "Explicá claramente el próximo paso del turno.", tecnico_en: "Return plain text.",
    original_en: "Explain the next appointment step clearly.", alineado: true,
    actualizado: null, actualizado_por: null,
  }],
  system: {
    estado: "error", ok: 1, total: 4,
    clusters: [
      { clave: "app", titulo: "Aplicación de ejemplo", estado: "ok", resumen: "Todo listo", items: [
        { label: "Servidor", valor: "Disponible", estado: "ok" },
      ] },
      { clave: "dominio", titulo: "Dominio de ejemplo", estado: "warn", resumen: "Falta configurar el dominio", items: [
        { label: "Dominio definitivo", valor: "Pendiente", estado: "warn", campo: "TURNOS_DOMINIO", detalle: "Dirección de prueba" },
      ] },
      { clave: "email", titulo: "Correo de ejemplo", estado: "error", resumen: "Servicio sin configurar", items: [
        { label: "Dominio de correo", valor: "Ausente", estado: "error", campo: "MAILGUN_DOMAIN" },
      ] },
      { clave: "renaper", titulo: "Identidad opcional", estado: "na", resumen: "Opcional", items: [
        { label: "Proveedor", valor: "No conectado", estado: "na" },
      ] },
    ],
  },
};

function activeOwner() {
  const owner = window.fetch?.[fixtureOwner];
  if (!owner || owner.disposed) throw new Error("Admin editorial story fixture is not active");
  return owner;
}

function retireOwner(owner) {
  if (!owner || owner.disposed) return;
  for (const cancel of owner.pending) cancel();
  owner.pending.clear();
  for (const reject of owner.systemHolds) reject(new DOMException("The operation was aborted", "AbortError"));
  owner.systemHolds.clear();
  for (const dispose of owner.mounts) dispose();
  owner.mounts.clear();
  owner.disposed = true;
}

export function installEditorialFixture() {
  const priorFetch = window.fetch;
  // Storybook may install the successor before calling the previous cleanup.
  // Singleton editors must release the predecessor before the new story mounts.
  retireOwner(priorFetch?.[fixtureOwner]);
  const priorTrace = window.__HREditorialFixture;
  const priorHref = window.location.pathname + window.location.search + window.location.hash;
  const priorHistoryState = window.history.state;
  const state = copy(initial);
  const trace = { calls: [], errors: [] };
  const owner = {
    priorFetch, priorTrace, priorHref, priorHistoryState,
    state, trace, disposed: false, mounts: new Set(), pending: new Set(), systemHolds: new Set(),
  };

  const fixtureFetch = async (input, init = {}) => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request ? request.url : input, window.location.href);
    const method = String(init.method || request?.method || "GET").toUpperCase();
    const key = method + " " + url.pathname;
    if (owner.disposed) {
      // admin-session captures fetch at module evaluation. A successor's
      // exact synthetic whoami is still routed through its current boundary.
      if (key === "GET /api/admin/whoami" && window.fetch !== fixtureFetch &&
          window.fetch?.[fixtureOwner] && !window.fetch[fixtureOwner].disposed) {
        return window.fetch(input, init);
      }
      throw new Error("Disposed admin editorial fixture cannot fetch");
    }
    if (url.origin !== window.location.origin) throw new Error("External admin editorial request blocked: " + key);
    if (!url.pathname.startsWith("/api/")) {
      let forward = priorFetch;
      while (forward?.[fixtureOwner]?.disposed) forward = forward[fixtureOwner].priorFetch;
      return forward(input, init);
    }
    if (url.search || url.hash) throw new Error("Unexpected admin editorial API query or fragment: " + key);
    const signal = init.signal || request?.signal;
    if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
    const raw = init.body == null ? (request ? await request.clone().text() : "") : init.body;
    if (owner.disposed) throw new Error("Disposed admin editorial fixture cannot resolve");
    if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
    const body = raw ? JSON.parse(String(raw)) : null;
    if (method === "GET" && body !== null) throw new Error("Admin editorial GET cannot carry a body: " + key);
    trace.calls.push({ method, path: url.pathname, body });

    if (key === "GET /api/admin/content/preparations") return reply({ items: copy(state.preparations) });
    if (key === "POST /api/admin/content/preparations") {
      if (!body || !body.nombre || !Array.isArray(body.match) || !body.ayuno) {
        throw new Error("Preparation creation requires the native editor payload");
      }
      const item = { ...copy(body), id: "story-created-preparation", _origen: "db", _borrado: false };
      state.preparations.push(item);
      return reply({ ok: true, item: copy(item) });
    }
    const prep = /^\/api\/admin\/content\/preparations\/([^/]+)(\/restaurar)?$/.exec(url.pathname);
    if (prep) {
      const item = state.preparations.find((candidate) => candidate.id === decodeURIComponent(prep[1]));
      if (!item) throw new Error("Unknown synthetic preparation: " + key);
      if (method === "PUT" && !prep[2]) {
        if (!body || !body.nombre || !Array.isArray(body.match) || !body.ayuno) {
          throw new Error("Preparation update requires the native editor payload");
        }
        Object.assign(item, copy(body), { id: item.id });
        return reply({ ok: true, item: copy(item) });
      }
      if (method === "DELETE" && !prep[2]) {
        if (body !== null) throw new Error("Preparation delete cannot carry a body");
        item._borrado = true;
        return reply({ ok: true, turnos: 0, conservado: true });
      }
      if (method === "POST" && prep[2]) {
        if (body !== null) throw new Error("Preparation restore cannot carry a body");
        item._borrado = false;
        return reply({ ok: true, item: copy(item) });
      }
    }

    if (key === "GET /api/admin/legal") return reply({ items: copy(state.legal) });
    const legal = /^\/api\/admin\/legal\/(terminos|privacidad)$/.exec(url.pathname);
    if (legal) {
      const item = state.legal.find((candidate) => candidate.id === legal[1]);
      if (method === "PUT") {
        if (!body || Object.keys(body).length !== 1 || typeof body.cuerpo !== "string" || !body.cuerpo.trim()) {
          throw new Error("Legal save requires nonempty cuerpo only");
        }
        Object.assign(item, { cuerpo: body.cuerpo, editado: true,
          actualizado: "2026-09-25T12:00:00-03:00", actualizado_por: "Story editor" });
        return reply({ ok: true, item: copy(item) });
      }
      if (method === "DELETE") {
        if (body !== null) throw new Error("Legal restore cannot carry a body");
        Object.assign(item, { cuerpo: item.original, editado: false,
          actualizado: null, actualizado_por: null });
        return reply({ ok: true, item: copy(item) });
      }
    }

    if (key === "GET /api/admin/prompts") return reply({ items: copy(state.prompts) });
    if (key === "GET /api/admin/system") {
      if (body !== null) throw new Error("System read cannot carry a body");
      if (owner.nextSystemHold) {
        const hold = owner.nextSystemHold;
        owner.nextSystemHold = null;
        return new Promise((resolve, reject) => {
          const abort = () => {
            owner.systemHolds.delete(abort);
            signal?.removeEventListener("abort", abort);
            reject(new DOMException("The operation was aborted", "AbortError"));
          };
          owner.systemHolds.add(abort);
          signal?.addEventListener("abort", abort, { once: true });
          hold.release = (status = 200) => {
            if (!owner.systemHolds.has(abort)) return false;
            owner.systemHolds.delete(abort);
            signal?.removeEventListener("abort", abort);
            resolve(new Response(JSON.stringify(status === 200 ? copy(state.system) :
              { error: "synthetic_status_" + status }), {
              status, headers: { "Content-Type": "application/json" },
            }));
            return true;
          };
        });
      }
      const status = owner.systemStatus || 200;
      return new Response(JSON.stringify(status === 200 ? copy(state.system) :
        { error: status === 401 ? "no_autenticado" : "synthetic_status_" + status }), {
        status, headers: { "Content-Type": "application/json" },
      });
    }
    if (key === "GET /api/admin/whoami") {
      return new Response(JSON.stringify({ username: "story-admin", role: "admin" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    const prompt = /^\/api\/admin\/prompts\/([^/]+)(?:\/(guardar|historial|traduccion))?$/.exec(url.pathname);
    if (prompt) {
      const item = state.prompts.find((candidate) => candidate.clave === decodeURIComponent(prompt[1]));
      if (!item) throw new Error("Unknown synthetic prompt: " + key);
      if (method === "POST" && prompt[2] === "guardar") {
        if (!body || Object.keys(body).length !== 1 ||
            !(typeof body.texto_en === "string" || typeof body.texto_es === "string")) {
          throw new Error("Prompt save requires one native language field");
        }
        Object.assign(item, copy(body), { editado: true, alineado: true,
          actualizado: "2026-09-25T12:00:00-03:00", actualizado_por: "Story editor" });
        return reply({ ok: true, item: copy(item), cambios: [] });
      }
      if (method === "DELETE" && !prompt[2]) {
        if (body !== null) throw new Error("Prompt restore cannot carry a body");
        Object.assign(item, { texto_en: item.original_en, editado: false,
          actualizado: null, actualizado_por: null });
        return reply({ ok: true, item: copy(item) });
      }
      if (method === "GET" && prompt[2] === "historial") return reply({ items: [] });
      if (method === "GET" && prompt[2] === "traduccion") return reply(copy(item));
    }
    throw new Error("Unexpected admin editorial story API request: " + key);
  };
  fixtureFetch[fixtureOwner] = owner;
  window.fetch = fixtureFetch;
  window.__HREditorialFixture = trace;
  trace.setSystemStatus = (status) => {
    if (activeOwner() !== owner) throw new Error("Retired System trace cannot set status");
    setEditorialSystemStatus(status);
  };
  trace.holdSystemRead = () => {
    if (activeOwner() !== owner) throw new Error("Retired System trace cannot hold reads");
    return holdEditorialSystemRead();
  };

  return () => {
    if (owner.disposed) return;
    // Production disposers run while their exact fetch boundary still exists.
    if (window.fetch === fixtureFetch) retireOwner(owner);
    else { owner.disposed = true; owner.pending.forEach((cancel) => cancel()); owner.pending.clear(); }
    if (window.fetch !== fixtureFetch || window.__HREditorialFixture !== trace) return;
    let boundary = owner;
    while (boundary.priorFetch?.[fixtureOwner]?.disposed) boundary = boundary.priorFetch[fixtureOwner];
    window.fetch = boundary.priorFetch;
    if (boundary.priorTrace === undefined) delete window.__HREditorialFixture;
    else window.__HREditorialFixture = boundary.priorTrace;
    // Storybook can select the next iframe URL before invoking cleanup. Only
    // replace the full route while the editorial controller still owns it;
    // otherwise retain Storybook's new story ID/state and restore our fragment.
    const editorialRoute = /^\/admin\/(preparations|legal|prompts|system|plan)$/.test(window.location.pathname);
    const priorUrl = new URL(boundary.priorHref, window.location.href);
    const restoredUrl = editorialRoute ? priorUrl : new URL(window.location.href);
    // Manager navigation can select the successor before the preview updates
    // its URL. The same-origin manager is authoritative in that ordering too.
    try {
      if (window.parent && window.parent !== window) {
        const manager = new URL(window.parent.location.href);
        const selected = /^\/story\/([a-z0-9-]+)$/.exec(manager.searchParams.get("path") || "");
        if (manager.origin === window.location.origin && selected && restoredUrl.pathname.endsWith("/iframe.html")) {
          restoredUrl.searchParams.set("id", selected[1]);
          restoredUrl.searchParams.set("viewMode", "story");
        }
      }
    } catch { /* Standalone or cross-origin previews use their own route. */ }
    const restoredHref = restoredUrl.pathname + restoredUrl.search + priorUrl.hash;
    const restoredState = editorialRoute ? boundary.priorHistoryState : window.history.state;
    if (window.history.replaceState &&
        (window.location.pathname + window.location.search + window.location.hash !== restoredHref ||
         window.history.state !== restoredState)) {
      window.history.replaceState(restoredState, "", restoredHref);
    }
  };
}

export function reportEditorialMountError(error, status, trace) {
  if (!trace || window.__HREditorialFixture !== trace) return;
  const message = error?.message || String(error);
  trace.errors.push(message);
  if (status) {
    status.hidden = false;
    status.setAttribute("role", "alert");
    status.textContent = message;
  }
}

export function setEditorialStoryRoute(path) {
  activeOwner();
  if (!/^\/admin\/(?:preparations(?:\?item=[a-z0-9-]+)?|legal(?:\?doc=(?:terminos|privacidad))?|prompts(?:\?prompt=[a-z0-9-]+)?|system|plan(?:\?view=technical)?)$/.test(path)) {
    throw new Error("Invalid admin editorial story route");
  }
  window.history.replaceState(window.history.state, "", path);
}

export function setEditorialSystemStatus(status) {
  const owner = activeOwner();
  if (![200, 401, 403, 500].includes(status)) throw new Error("Invalid synthetic System status");
  owner.systemStatus = status;
}

export function holdEditorialSystemRead() {
  const owner = activeOwner();
  if (owner.nextSystemHold) throw new Error("A synthetic System read is already held");
  const hold = { release: () => false };
  owner.nextSystemHold = hold;
  return hold;
}

// Storybook inserts the returned element after render finishes. Start the real
// production controller only when its exact host is connected to the document.
export function mountEditorialStoryWhenAttached(node, mount) {
  const owner = activeOwner();
  let frame = null;
  const cancel = () => { if (frame != null) cancelAnimationFrame(frame); };
  owner.pending.add(cancel);
  const tryMount = () => {
    frame = null;
    if (owner.disposed) return;
    if (!node.isConnected) { frame = requestAnimationFrame(tryMount); return; }
    owner.pending.delete(cancel);
    const dispose = mount();
    if (typeof dispose === "function") owner.mounts.add(dispose);
  };
  frame = requestAnimationFrame(tryMount);
}
