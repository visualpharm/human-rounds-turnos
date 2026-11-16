// directory.mjs -> named render(): the ONE renderer of the specialties
// directory (home + my-requests empty states). Every story mounts through the
// REAL render(), which paints its skeleton and then fetches /api/directory —
// and Storybook has no app server behind that route. The HTML renderer calls a
// story synchronously and accepts only a Node or string, so every export must
// return its host immediately. `render()` calls fetch synchronously before it
// returns its promise; therefore the mock lives only around that one call,
// then is restored before any other asynchronous work (or another story) can
// observe it. This is narrower and safer than keeping a global fetch wrapper
// installed while the response is being painted.
import { render } from "../static/platform/directory.mjs";
import {
  DIRECTORY_PAYLOAD,
  DIRECTORY_PAYLOAD_SIN_DESTACADAS,
} from "./directory-fixture.mjs";

export default {
  title: "Public site/Directory",
  parameters: { layout: "padded" },
};

function mount(host, payload, opts) {
  const prior = window.fetch;
  const wrapper = (input, init) => {
    const url = typeof input === "string" ? input : (input && input.url);
    if (url === "/api/directory") {
      return Promise.resolve(new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    }
    return prior(input, init);
  };
  window.fetch = wrapper;
  try {
    // `render()` starts its only fetch before returning. Do not await it here:
    // Storybook needs the host Node synchronously and the renderer updates it
    // in place when this already-created fixture response resolves.
    void render(host, opts);
  } finally {
    window.fetch = prior;
  }
}

function story(payload, opts) {
  return () => {
    const host = document.createElement("div");
    mount(host, payload, opts);
    return host;
  };
}

// La home: destacadas con caras (incluido el nombre largo a 2 líneas y la
// fila "+2") + los rubros sin retratos abajo, como filas con chip.
export const Completo = story(DIRECTORY_PAYLOAD, {});

// Los estados vacíos de my-requests.js montan compact:true — sólo cards.
export const Compacto = story(DIRECTORY_PAYLOAD, { compact: true });

// Sin ningún rubro con retratos no hay destacadas: todo el directorio es el
// listado bajo "Todas las especialidades".
export const TodasSinDestacadas = story(DIRECTORY_PAYLOAD_SIN_DESTACADAS, {});
