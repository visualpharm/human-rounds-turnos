/* Synthetic /api/directory payloads for the Directory story — shaped exactly
   like site_listing._directory_data() builds it (patient_web/site_*.py): cluster
   ids, nombres and icon paths mirror packs/example-town/clusters.json, and
   the faces are the demo pack's synthetic portraits (a story never shows a
   real doctor's photo). Shared with tests/directory-story.test.mjs so the
   assertions and the rendered data cannot drift apart.

   The payload deliberately carries the four shapes the renderer branches on:
   · a long specialty name (the 2-line wrap case the card CSS is written for),
   · icon-only rubros — `top` set, no portraits — which must land in the list
     below, never as an empty card,
   · a 7-doctor cluster with 6 portraits, so the avatar row shows 5 faces and
     the "+2" overflow (one portrait-less doctor is counted, not drawn),
   · a 1-doctor cluster, for the singular "médico" subtitle branch. */

const foto = (id) =>
  "background-image:url('/demo-fotos/" + id + ".jpg');background-size:cover;background-position:center";
const avs = (ids) => ids.map((id) => ({ ph: foto(id) }));

export const DIRECTORY_PAYLOAD = {
  total_medicos: 12,
  clusters: [
    {
      id: "clinica", nombre: "Clínica y medicina general", top: 1,
      n_medicos: 7, n_servicios: 3,
      avatares: avs(["garcia-jorge", "rojas-ana", "katz-laura", "wu-carlos", "torres-claudia", "kim-daniel"]),
    },
    {
      id: "endocrino", nombre: "Endocrinología, diabetes y nutrición", top: 2,
      n_medicos: 1, n_servicios: 0, avatares: avs(["sharma-fernanda"]),
    },
    {
      id: "trauma-kine", nombre: "Traumatología y kinesiología", top: 3,
      n_medicos: 2, n_servicios: 1, avatares: avs(["nowak-marcelo", "diallo-luis"]),
    },
    // Rubros sin médicos con ficha: los atiende el vacunatorio o un equipo —
    // con `top` igual van al listado de abajo, como fila con chip.
    {
      id: "vacunas", nombre: "Vacunas y prevención", top: 10,
      n_medicos: 0, n_servicios: 2, avatares: [], icon: "/static/icons/vacunas-prevencion.png",
    },
    {
      id: "grupos", nombre: "Grupos y talleres", top: 12,
      n_medicos: 0, n_servicios: 1, avatares: [], icon: "/static/icons/grupos.png",
    },
    // Con médicos pero sin retratos: también fila, no card.
    {
      id: "otorrino", nombre: "Otorrinolaringología y fonoaudiología", top: 11,
      n_medicos: 2, n_servicios: 2, avatares: [], icon: "/static/icons/otorrino-fono.png",
    },
  ],
};

// Ningún rubro con retratos: sin destacadas, el directorio entero es el
// listado bajo "Todas las especialidades" (la rama directory.all_specialties).
export const DIRECTORY_PAYLOAD_SIN_DESTACADAS = {
  total_medicos: 4,
  clusters: [
    {
      id: "otorrino", nombre: "Otorrinolaringología y fonoaudiología", top: 11,
      n_medicos: 2, n_servicios: 2, avatares: [], icon: "/static/icons/otorrino-fono.png",
    },
    {
      id: "vacunas", nombre: "Vacunas y prevención", top: 10,
      n_medicos: 0, n_servicios: 2, avatares: [], icon: "/static/icons/vacunas-prevencion.png",
    },
    {
      id: "grupos", nombre: "Grupos y talleres", top: 12,
      n_medicos: 0, n_servicios: 1, avatares: [], icon: "/static/icons/grupos.png",
    },
  ],
};
