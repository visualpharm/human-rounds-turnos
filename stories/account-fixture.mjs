// Fictional account data. The wrapper is installed only while one of these
// stories is active and refuses unknown account API calls.
export const accountHolder = {
  dni: "40123456",
  nombre: "Marina",
  apellido: "Ejemplo",
  nacimiento: "1988-05-14",
  sexo: "F",
  contacto: "2254550000",
  email: "marina@example.test",
  domicilio: { calle: "Calle Ficticia 123", localidad: "Pinamar", cp: "7167" },
  cobertura: { obras_sociales: [] },
  avisos: { whatsapp: true, email: true },
};

export const dependents = [
  { dni: "50123456", nombre: "Lucía", apellido: "Ejemplo", nacimiento: "2017-04-12", sexo: "F", vinculo: "hijo" },
  { dni: "51123456", nombre: "Tomás", apellido: "Ejemplo", nacimiento: "2019-08-03", sexo: "M", vinculo: "hijo" },
];

function json(body, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

const fixtureOwner = Symbol("account story fixture owner");

export function installAccountFixture() {
  const previousFetch = window.fetch;
  const previousIdentity = localStorage.getItem("st_identity");
  const previousTrace = window.__HRAccountFixture;
  const previousHash = window.location.hash;
  const previousHistoryState = window.history?.state;
  const trace = { calls: [] };
  const owner = {
    previousFetch, previousTrace, previousIdentity, previousHash, previousHistoryState,
    disposed: false, mounts: new Set(), pendingMounts: new Set(),
    account: { labs: { informes: [], series: [], analisis_pendiente: 0 }, requests: [] },
  };
  window.__HRAccountFixture = trace;
  localStorage.removeItem("st_identity");
  let people = dependents.map((person) => ({ ...person }));

  const fixtureFetch = async (input, init = {}) => {
    if (owner.disposed) throw new Error("Disposed account story fixture cannot fetch");
    const url = new URL(input instanceof Request ? input.url : input, window.location.href);
    const method = (init.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    const key = method + " " + url.pathname;
    if (url.protocol === "http:" || url.protocol === "https:") {
      if (url.origin !== window.location.origin) throw new Error("External account story request blocked: " + key);
    }
    if (!url.pathname.startsWith("/api/")) {
      let forward = previousFetch;
      while (forward?.[fixtureOwner]?.disposed) forward = forward[fixtureOwner].previousFetch;
      return forward(input, init);
    }
    if (url.search) throw new Error("Unexpected account story API query: " + key);
    const signal = init.signal || (input instanceof Request ? input.signal : null);
    if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
    if (owner.disposed) throw new Error("Disposed account story fixture cannot fetch");
    const rawBody = init.body == null ? (input instanceof Request ? await input.clone().formData().catch(() => input.clone().text()) : "") : init.body;
    if (owner.disposed) throw new Error("Disposed account story fixture cannot fetch");
    if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
    // The lab upload is multipart. Never stringify or JSON-parse its FormData.
    const body = rawBody instanceof FormData ? rawBody : (rawBody ? JSON.parse(String(rawBody)) : null);
    trace.calls.push({ method, path: url.pathname, body });
    if (key === "GET /api/account/lab-results") {
      if (owner.account.labsReply) {
        const reply = await owner.account.labsReply();
        if (owner.disposed) throw new Error("Disposed account story fixture cannot resolve");
        return reply;
      }
      return json(owner.account.labsStatus === 401 ? { error: "unauthorized" } : owner.account.labs,
        owner.account.labsStatus || 200);
    }
    if (key === "POST /api/account/lab-results/upload") {
      if (!(body instanceof FormData) || !(body.get("archivo") instanceof File)) {
        throw new Error("Account lab upload must include a file in FormData");
      }
      owner.account.labs = {
        ...owner.account.labs,
        informes: [owner.account.uploadedLab, ...owner.account.labs.informes].filter(Boolean),
      };
      return json({ ok: true });
    }
    if (key === "GET /api/services") return json({ servicios: owner.account.servicios || [] });
    if (key === "GET /api/centers") return json({ centros: owner.account.centros || [] });
    if (key === "GET /api/calendar/feed") return json({ url: "/api/calendar/feed.ics" });
    if (key === "GET /api/requests") {
      return json(owner.account.requestsStatus === 401 ? { error: "unauthorized" } : { solicitudes: owner.account.requests },
        owner.account.requestsStatus || 200);
    }
    if (key === "POST /api/requests/story-approved/waitlist") {
      if (!body || typeof body.activo !== "boolean") throw new Error("Waitlist fixture expects an activo boolean");
      return json({ ok: true });
    }
    if (key === "POST /api/requests/story-approved/cancel" ||
        key === "POST /api/requests/story-pending/cancel") {
      const id = url.pathname.split("/")[3];
      owner.account.requests = owner.account.requests.map((row) =>
        row.id === id ? { ...row, estado: "cancelada", cancelada_por: "paciente" } : row);
      return json({ ok: true });
    }
    if (key === "GET /api/account/config") return json({
      auth_mode: "password", renaper_provider: "real", test_identity_provider: false,
      turnstile_sitekey: null, otp_registro_requerido: false,
    });
    if (key === "GET /api/ai-status") return json({ disponible: false });
    if (key === "GET /api/account/dependents") return json({ dependientes: people });
    if (key === "POST /api/account/dependents") {
      people.push(body);
      return json({ dependiente: body });
    }
    if (key === "POST /api/account/dependents/delete") {
      const { dni } = body;
      people = people.filter((person) => person.dni !== dni);
      return json({ ok: true });
    }
    if (key === "POST /api/account/update") {
      return json({ usuario: { ...accountHolder, ...body } });
    }
    if (key === "POST /api/account/login") return json({ error: "credenciales" }, 401);
    if (key === "POST /api/account/reset") return json({ error: "token_invalido" }, 400);
    throw new Error("Unexpected account story API request: " + key);
  };
  fixtureFetch[fixtureOwner] = owner;
  window.fetch = fixtureFetch;
  return () => {
    if (owner.disposed) return;
    owner.disposed = true;
    for (const cancel of owner.pendingMounts) cancel();
    owner.pendingMounts.clear();
    for (const dispose of owner.mounts) dispose();
    owner.mounts.clear();
    if (window.fetch !== fixtureFetch || window.__HRAccountFixture !== trace) return;
    // A story may finish after its successor mounted. Skip any disposed
    // fixture underneath so its fetch wrapper and identity never return.
    let boundary = owner;
    while (boundary.previousFetch?.[fixtureOwner]?.disposed) {
      boundary = boundary.previousFetch[fixtureOwner];
    }
    window.fetch = boundary.previousFetch;
    if (boundary.previousTrace === undefined) delete window.__HRAccountFixture;
    else window.__HRAccountFixture = boundary.previousTrace;
    if (boundary.previousIdentity === null) localStorage.removeItem("st_identity");
    else localStorage.setItem("st_identity", boundary.previousIdentity);
    if ((window.location.hash !== boundary.previousHash ||
         window.history.state !== boundary.previousHistoryState) && window.history?.replaceState) {
      window.history.replaceState(boundary.previousHistoryState, "",
        window.location.pathname + window.location.search + boundary.previousHash);
    }
  };
}

function currentOwner() {
  const owner = window.fetch?.[fixtureOwner];
  if (!owner || owner.disposed) throw new Error("Account story fixture is not active");
  return owner;
}

export function configureAccountFixture(account) {
  Object.assign(currentOwner().account, account);
}

export function setAccountStoryHash(hash) {
  const owner = currentOwner();
  if (!/^#labs(?:&r=\d+)?$/.test(hash)) throw new Error("Invalid account story hash");
  if (window.fetch?.[fixtureOwner] !== owner) return;
  window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search + hash);
}

// The HTML renderer appends the synchronously returned element after the
// story function finishes. Mount only once that element and its sibling IDs
// are in the document, and cancel this scheduled work on story teardown.
export function mountAccountStoryWhenAttached(node, mount) {
  const owner = currentOwner();
  let frame = null;
  const cancel = () => { if (frame != null) cancelAnimationFrame(frame); };
  owner.pendingMounts.add(cancel);
  const tryMount = () => {
    frame = null;
    if (owner.disposed) return;
    if (!node.isConnected) { frame = requestAnimationFrame(tryMount); return; }
    owner.pendingMounts.delete(cancel);
    const dispose = mount();
    if (typeof dispose === "function") owner.mounts.add(dispose);
  };
  frame = requestAnimationFrame(tryMount);
}
