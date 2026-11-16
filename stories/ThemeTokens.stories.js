// Not a component — a reference sheet of the CSS custom properties that
// theme the whole site (human-rounds.css, [data-theme] on <html>). Switch
// the Theme toolbar to compare pinamar vs humanrounds side by side.
export default {
  title: "Foundations/Theme tokens",
  parameters: { layout: "fullscreen" },
};

const GROUPS = [
  { label: "Surface", tokens: ["--bg", "--panel", "--border", "--border-strong"] },
  { label: "Text", tokens: ["--text", "--text-2", "--text-3", "--text-4"] },
  { label: "Accent", tokens: ["--accent", "--accent-2"] },
  { label: "State", tokens: ["--done", "--warn", "--danger", "--state-done-bg", "--warn-soft", "--danger-soft"] },
];

export const Tokens = () => {
  const root = document.createElement("div");
  root.style.padding = "24px";
  root.style.background = "var(--bg)";
  root.style.color = "var(--text)";
  root.style.fontFamily = "system-ui, sans-serif";

  const computed = getComputedStyle(document.documentElement);
  root.innerHTML = GROUPS.map((group) => `
    <h3 style="font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-3);margin:20px 0 8px;">${group.label}</h3>
    <div style="display:flex;flex-wrap:wrap;gap:12px;">
      ${group.tokens.map((name) => {
        const value = computed.getPropertyValue(name).trim() || "(unset)";
        return `
          <div style="width:160px;border:1px solid var(--border);border-radius:8px;overflow:hidden;">
            <div style="height:48px;background:var(${name});"></div>
            <div style="padding:8px 10px;font-size:12px;">
              <div style="font-weight:600;">${name}</div>
              <div style="color:var(--text-3);">${value}</div>
            </div>
          </div>`;
      }).join("")}
    </div>
  `).join("");
  return root;
};
