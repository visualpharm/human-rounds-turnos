import gateCss from "../static/root/admin-gate.css?raw";
import shellCss from "../static/root/admin-shell.css?raw";
import directCss from "../static/root/admin-direct.css?raw";

// Match the actual admin/base.html stylesheet bytes and order. Root-owned
// nodes disappear with the story, so admin button rules do not leak into a
// later patient story. Raw CSS also preserves the browser's production
// parsing behavior instead of rewriting it through Vite's CSS minifier.
export function applyEditorialStyles(root) {
  for (const text of [gateCss, shellCss, directCss]) {
    const style = document.createElement("style");
    style.textContent = text;
    root.append(style);
  }
}
