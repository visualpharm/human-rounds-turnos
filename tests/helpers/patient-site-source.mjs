// The code of the public patient site is split by domain: `patient_web/site_*.py` hold the pages and the
// data views (`site_base` is the shared plumbing). A source guard that pins a detail of "the patient
// site" reads all of it, whichever module the detail lives in today (a function that moves between
// modules must not turn the guard red).
import { readFileSync, readdirSync } from "node:fs";

const PKG = new URL("../../app/patient_web/", import.meta.url);

export function patientSiteFiles() {
  const names = readdirSync(PKG).filter((n) => /^site_.*\.py$/.test(n)).sort();
  return names;
}

export function patientSiteSource() {
  return patientSiteFiles().map((n) => readFileSync(new URL(n, PKG), "utf8")).join("\n");
}
