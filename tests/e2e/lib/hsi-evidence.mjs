// Per-run evidence directories for the HSI-off browser acceptance run.
// Screenshots are operator evidence, so every run reserves its own directory
// below the durable root: a new run can never overwrite or prune the evidence
// of a previous one. Importing this module touches nothing; the filesystem is
// only mutated inside allocateEvidenceRunDir().
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { artifactPath } from "./artifacts.mjs";

const DEFAULT_ROOT = artifactPath("human-rounds", "hsi-disabled");
const MAX_ATTEMPTS = 8;

function candidateName(label, now) {
  const stamp = now.toISOString().replace(/[:.]/g, "-");
  return `${stamp}-${label}-${randomBytes(4).toString("hex")}`;
}

// The root is created idempotently (recursive mkdir never deletes anything);
// the run directory itself is reserved with a non-recursive mkdir, the one
// primitive that is atomic against a same-name race: a collision fails with
// EEXIST and the loop retries on a fresh random suffix. Two allocations can
// therefore never share a directory, and no allocation can land on -- let
// alone clear -- an existing evidence directory.
export function allocateEvidenceRunDir({ root = DEFAULT_ROOT, label = "run", now = new Date() } = {}) {
  mkdirSync(root, { recursive: true });
  for (let attempt = 1; ; attempt += 1) {
    const candidate = join(root, candidateName(label, now));
    try {
      mkdirSync(candidate);
      return candidate;
    } catch (error) {
      if (error.code !== "EEXIST" || attempt >= MAX_ATTEMPTS) throw error;
    }
  }
}
