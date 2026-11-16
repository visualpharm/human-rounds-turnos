import { accountFormGate } from "./dependents-account-form-gate.mjs";
await accountFormGate.promise;
// Load the real Account module only after the gate opens. A static
// `export * from` would start that import in parallel with the hold, so a
// rejected gate would still leave Account evaluating after the test ended.
const account = await import("../../static/platform/account-form.mjs");
export const renderAuth = account.renderAuth;
export const renderEditar = account.renderEditar;
export const renderReset = account.renderReset;
export const parseFlexibleDate = account.parseFlexibleDate;
export const isoToArg = account.isoToArg;
export const ageFromIso = account.ageFromIso;
export const dobField = account.dobField;
export const fileToJpeg = account.fileToJpeg;
