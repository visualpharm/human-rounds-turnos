await globalThis.__serviceAgeGate.promise;
// Load the real age module only after the gate opens. A static
// `export * from` would start that import in parallel with the hold.
const age = await import("../../static/platform/age.mjs");
export const edadDesde = age.edadDesde;
export const aptoPorEdad = age.aptoPorEdad;
export const alternativaPorEdad = age.alternativaPorEdad;
