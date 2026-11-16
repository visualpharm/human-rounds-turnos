await globalThis.__entrevistasFormatGate.promise;
// Load the real formatter only after the gate opens. A static
// `export * from` would start that import in parallel with the hold.
const format = await import("../../static/platform/format.mjs");
export const nombre = format.nombre;
export default format.default;
