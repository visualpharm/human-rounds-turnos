// The story mounts the production upload control. HTML Storybook expects
// the node synchronously; css/zoneHtml/wire are the real named exports.
import { css, zoneHtml, wire } from "../app/modules/turnos/wz-upload.mjs";

export default {
  title: "Public site/Upload zone",
  parameters: { layout: "padded" },
};

export const Default = () => {
  if (!document.getElementById("wz-upload-story-css")) {
    const style = document.createElement("style");
    style.id = "wz-upload-story-css";
    style.textContent = css;
    document.head.appendChild(style);
  }
  const host = document.createElement("div");
  host.innerHTML = zoneHtml();
  wire(host, () => {});
  return host;
};
