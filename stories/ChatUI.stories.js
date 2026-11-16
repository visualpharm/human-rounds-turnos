// static/platform/chat-ui.mjs -> named crearLista(): the message list shared
// by BOTH chat surfaces (the booking assistant and the pre-consult interview).
// Stories use the interview's prefix ("iv") because entrevista.css is a real
// stylesheet we can import; the widget's own chrome ("stzc") is injected by
// static/platform/chat-widget.mjs, which also boots a floating launcher —
// not what a story wants. The rendering logic underneath is the same export
// either way.
import { crearLista } from "../static/platform/chat-ui.mjs";
import "../app/modules/intake/entrevista.css";

export default {
  title: "Chat/Message list",
  parameters: { layout: "padded" },
};

function panel(build) {
  // TODA regla de entrevista.css cuelga de `#iv-host` (la pantalla real es una
  // sola con ese id). Sin ese ancestro no matchea NADA y las burbujas salen
  // como texto pelado, que es como se veían estas stories.
  const scope = document.createElement("div");
  scope.id = "iv-host";
  const host = document.createElement("div");
  host.className = "iv-msgs";
  host.style.height = "420px";
  host.style.overflowY = "auto";
  host.style.background = "var(--panel)";
  host.style.border = "1px solid var(--border)";
  host.style.borderRadius = "12px";
  host.style.padding = "14px";
  scope.appendChild(host);
  const lista = crearLista(host, { prefix: "iv" });
  build(lista);
  return scope;
}

export const ConversacionDeTurno = () =>
  panel((l) => {
    l.addBubble("assistant", "Hola. ¿Para qué necesitás el turno?");
    l.addBubble("user", "Me duele el pecho cuando camino rápido");
    l.addBubble(
      "assistant",
      "Eso lo ve **Cardiología**. Hay lugar el jueves a las 09:40 en el Hospital de Pinamar.",
      { enlaces: [{ label: "Cardiología", href: "/d/cardiologia" }] }
    );
  });

export const Escribiendo = () =>
  panel((l) => {
    l.addBubble("user", "¿Tengo que llevar algo?");
    l.setTyping(true);
  });

export const ConOpcionesParaTocar = () =>
  panel((l) => {
    l.addBubble("assistant", "¿Cuándo te queda mejor?");
    l.addOpciones(["Mañana temprano", "Mañana a la tarde", "Cualquier día"], () => {});
  });

// markdown-lite: **negrita**, listas con "* " y links internos, todo sobre
// texto ya escapado — sin librería.
export const ListaYNegrita = () =>
  panel((l) => {
    l.addBubble(
      "assistant",
      "Para la **ecografía abdominal** necesitás:\n* Ayuno de 8 horas\n* La orden médica\n* Llegar 15 minutos antes"
    );
  });

// Sólo se cita una fuente OFICIAL: addFuentes descarta cualquier entrada sin
// título o cuya URL no sea https — un link relativo o http se ignora en
// silencio, y si no queda ninguna el bloque no aparece.
export const ConFuentes = () =>
  panel((l) => {
    l.addBubble("assistant", "El laboratorio atiende de 7 a 10, por orden de llegada.");
    l.addFuentes([
      { titulo: "Hospital de Pinamar", url: "https://pinamar.humanrounds.org/center/hospital-pinamar" },
      { titulo: "Municipalidad de Pinamar", url: "https://www.pinamar.gob.ar/salud" },
      { titulo: "Se descarta: no es https", url: "http://ejemplo.test" },
    ]);
  });

// Un turno que se responde en varias pasadas: la MISMA burbuja se re-renderiza
// mientras llega el texto, en vez de apilar burbujas nuevas.
export const RevelacionProgresiva = () =>
  panel((l) => {
    l.addBubble("user", "¿Dónde queda el CAPS Ostende?");
    const b = l.addBubble("assistant", "Está en");
    l.updateBubble(b, "assistant", "Está en **Constitución y Del Tuyú**, Ostende. Atiende de lunes a viernes de 8 a 14.");
  });
