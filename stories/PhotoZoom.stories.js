import { mount } from "../static/platform/photo-zoom.mjs";

export default {
  title: "Public site/Photo zoom",
  parameters: { layout: "padded" },
  beforeEach: () => {
    const controller = mount(document);
    controller.close();
    return () => controller.close();
  },
};

// These portraits belong to the fictional example-town pack already exposed
// through Storybook's narrow /demo-fotos static directory.
const photos = [
  { src: "/demo-fotos/paciente-demo.jpg", alt: "Retrato de paciente de demostración" },
  { src: "/demo-fotos/arias-paula.jpg", alt: "Retrato de profesional de demostración" },
];

export const Galeria = () => {
  mount(document);
  const host = document.createElement("div");
  for (const photo of photos) {
    const link = document.createElement("a");
    link.href = photo.src;
    link.dataset.photozoom = "";
    link.dataset.photozoomGroup = "demo";
    link.dataset.photozoomAlt = photo.alt;
    const img = document.createElement("img");
    img.src = photo.src;
    img.alt = photo.alt;
    img.width = 160;
    link.appendChild(img);
    host.appendChild(link);
  }
  return host;
};

export const VisorAbierto = () => {
  const host = Galeria();
  const controller = mount(document);
  controller.open(photos, { trigger: host.querySelector("a") });
  return host;
};
