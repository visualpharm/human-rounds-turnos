// Not a component — a reference sheet of the brand-mark assets (fixed
// 2026-08-05: the doctor's coat used to render as a noisy grey smear instead
// of clean white, in every derived asset). Three distinct marks, each with a
// job:
//   - the logo (three figures in a ronda) — header/nav, any size
//   - the favicon (one flying doctor) — the three-figure mark reads as a
//     blob at 32px, so the browser tab gets its own simpler mark
//   - the admin favicon — same flying doctor, recolored brand blue
//     (#296eaa / #8fb8de light-dark pair) so the admin tab is visually
//     distinct from the black/white public one
export default {
  title: "Foundations/Brand mark",
  parameters: { layout: "fullscreen" },
};

const SWATCHES = [
  {
    label: "Logo — header/nav",
    file: "Human Rounds logo",
    src: "/logo.png",
    bg: "#f7f3eb",
  },
  {
    label: "Favicon — browser tab (light)",
    file: "Human Rounds favicon",
    src: "/human-rounds-mark-32.png",
    bg: "#ffffff",
  },
  {
    label: "Favicon — browser tab (dark)",
    file: "Human Rounds favicon",
    src: "/human-rounds-mark-32-dark.png",
    bg: "#111111",
  },
  {
    label: "Favicon — apple-touch",
    file: "Human Rounds favicon",
    src: "/human-rounds-mark-180.png",
    bg: "#f7f3eb",
  },
  {
    label: "Favicon — admin, browser tab (light)",
    file: "human-rounds-mark-32-admin",
    src: "/human-rounds-mark-32-admin.png",
    bg: "#ffffff",
  },
  {
    label: "Favicon — admin, browser tab (dark)",
    file: "human-rounds-mark-32-admin-dark",
    src: "/human-rounds-mark-32-admin-dark.png",
    bg: "#111111",
  },
];

export const Marks = () => {
  const root = document.createElement("div");
  root.style.padding = "24px";
  root.style.background = "var(--bg, #f7f3eb)";
  root.style.fontFamily = "system-ui, sans-serif";

  root.innerHTML = `
    <p style="max-width:640px;font-size:13px;line-height:1.5;color:var(--text-2,#555);margin:0 0 20px;">
      Three marks, three jobs — the logo is the full three-figure ronda; the
      favicon is a single flying doctor (legible at 32px, where three figures
      blur together); the admin favicon is the same doctor recolored brand
      blue to read as "this is the internal panel". Derived by
      <code>scripts/build_brand_marks.py</code> from the one master in
      <code>static/</code>.
    </p>
    <div style="display:flex;flex-wrap:wrap;gap:16px;">
      ${SWATCHES.map((s) => `
        <div style="width:180px;border:1px solid var(--border,#ddd);border-radius:10px;overflow:hidden;">
          <div style="height:120px;display:flex;align-items:center;justify-content:center;background:${s.bg};">
            <img src="${s.src}" alt="${s.label}" style="max-width:70%;max-height:70%;" />
          </div>
          <div style="padding:8px 10px;font-size:12px;">
            <div style="font-weight:600;">${s.label}</div>
            <div style="color:var(--text-3,#888);">${s.file}.svg / .png</div>
          </div>
        </div>`).join("")}
    </div>
  `;
  return root;
};
