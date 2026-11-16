// Where browser runs keep their evidence (screenshots, measurement reports).
// The default is the per-user ~/.claude/artifacts tree, so a run works on any
// machine; TURNOS_E2E_ARTIFACTS_DIR moves it somewhere else. Never hard-code a
// person's home directory here: the suite then fails everywhere but there.
import { homedir } from "node:os";
import { join } from "node:path";

export const ARTIFACTS_ROOT = process.env.TURNOS_E2E_ARTIFACTS_DIR || join(homedir(), ".claude", "artifacts");

export function artifactPath(...parts) {
  return join(ARTIFACTS_ROOT, ...parts);
}
