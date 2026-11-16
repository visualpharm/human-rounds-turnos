import * as Requests from "../static/platform/account-requests.mjs";
import {
  configureAccountFixture, installAccountFixture, mountAccountStoryWhenAttached,
} from "./account-fixture.mjs";

export default {
  title: "Public site/Account requests",
  parameters: { layout: "padded" },
  beforeEach: installAccountFixture,
};

const services = [
  { id: "story-clinica", nombre: "Clínica general" },
  { id: "story-odontologia", nombre: "Odontología" },
];
const centers = [{ slug: "story-costa", name: "Centro Costa Ficticio" }];

function futureAppointment() {
  const date = new Date(Date.now() + 21 * 24 * 60 * 60 * 1000);
  const day = date.toISOString().slice(0, 10);
  return `${day}T10:30:00-03:00`;
}

function sampleRequests() {
  return [
    {
      id: "story-approved", estado: "aprobada", servicio_id: "story-clinica",
      centro_slug: "story-costa", medico_asignado: "Dra. Clara Torres",
      turno_dt: futureAppointment(), lista_espera_activo: false, preparacion: [],
    },
    {
      id: "story-pending", estado: "pendiente", servicio_id: "story-odontologia",
      centro_slug: "story-costa", preferencia_horario: "Por la mañana", preparacion: [],
    },
    {
      id: "story-rejected", estado: "rechazada", servicio_id: "story-clinica",
      centro_slug: "story-costa", motivo_rechazo: "Falta una orden vigente", preparacion: [],
    },
  ];
}

function mountRequests(host, requests) {
  return () => Requests.mount(host, {
    requests,
    onUnauthorized: () => {
      host.textContent = "Sign in required";
      host.setAttribute("role", "status");
      host.dataset.unauthorized = "true";
    },
  });
}

function show(requests, status) {
  configureAccountFixture({
    servicios: services, centros: centers,
    requests: requests || [], requestsStatus: status,
  });
  const host = document.createElement("div");
  host.id = "ms-host";
  host.setAttribute("data-embedded", "1");
  host.style.maxWidth = "800px";
  mountAccountStoryWhenAttached(host, mountRequests(host, requests));
  return host;
}

export const Appointments = () => show(sampleRequests());
export const Empty = () => show([]);
// Omitting preloaded rows makes the real embedded controller handle a 401.
export const Unauthorized = () => show(undefined, 401);
