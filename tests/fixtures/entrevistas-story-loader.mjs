import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export async function load(url, context, nextLoad) {
  const clean = url.split("?")[0];
  if (url.includes("?raw") || clean.endsWith(".css")) {
    return { format: "module", source: "export default '';", shortCircuit: true };
  }
  if (clean.endsWith(".json")) {
    const data = JSON.parse(readFileSync(fileURLToPath(clean), "utf8"));
    return {
      format: "module",
      source: `export default ${JSON.stringify(data)};`,
      shortCircuit: true,
    };
  }
  return nextLoad(url, context);
}
