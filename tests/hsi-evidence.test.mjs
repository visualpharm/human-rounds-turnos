// Runtime proofs for the HSI-off evidence helper (tests/e2e/lib/hsi-evidence.mjs).
// Every allocation here targets an isolated throwaway root, so the durable
// ~/.claude/artifacts tree is never touched from the unit suite.
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { allocateEvidenceRunDir } from "./e2e/lib/hsi-evidence.mjs";

const HELPER_URL = pathToFileURL(
  join(dirname(fileURLToPath(import.meta.url)), "e2e", "lib", "hsi-evidence.mjs"),
).href;

function tempRoot(prefix) {
  return mkdtempSync(join(tmpdir(), prefix));
}

test("two allocations at the same instant produce different directories", () => {
  const root = tempRoot("hsi-evidence-same-time-");
  try {
    const now = new Date("2026-09-08T12:00:00.000Z");
    const first = allocateEvidenceRunDir({ root, label: "run", now });
    const second = allocateEvidenceRunDir({ root, label: "run", now });
    assert.notEqual(first, second);
    assert.ok(statSync(first).isDirectory());
    assert.ok(statSync(second).isDirectory());
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("an allocation stays contained under the requested root", () => {
  const root = tempRoot("hsi-evidence-containment-");
  try {
    const dir = allocateEvidenceRunDir({ root, label: "run" });
    assert.ok(dir.startsWith(root + sep), `${dir} must live below ${root}`);
    assert.equal(dirname(dir), root, "the run directory must be a direct child of the root");
    assert.deepEqual(
      readdirSync(root).map((name) => statSync(join(root, name)).isDirectory()),
      [true],
      "the root must hold only allocated directories",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a later allocation retains earlier evidence untouched", () => {
  const root = tempRoot("hsi-evidence-retention-");
  try {
    const first = allocateEvidenceRunDir({ root, label: "run" });
    const earlier = join(first, "connectors-off-phone-390.png");
    writeFileSync(earlier, "earlier run evidence");
    const second = allocateEvidenceRunDir({ root, label: "run" });
    assert.notEqual(second, first);
    assert.equal(readFileSync(earlier, "utf8"), "earlier run evidence");
    assert.deepEqual(readdirSync(first), ["connectors-off-phone-390.png"]);
    assert.equal(existsSync(join(second, basename(earlier))), false,
      "the new run must not inherit or shadow the earlier run's files");
    assert.ok(readdirSync(root).includes(basename(first)),
      "the earlier run's directory must still be present in the root");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("importing the module has no startup effects on disk", () => {
  // The child processes below run the production helper with HOME pointed at
  // an isolated directory, so the default durable root resolves inside the
  // throwaway tree and any import-time filesystem effect is observable here.
  const fakeHome = tempRoot("hsi-evidence-import-home-");
  const defaultRoot = join(fakeHome, ".claude", "artifacts", "human-rounds", "hsi-disabled");
  const env = { ...process.env, HOME: fakeHome };
  try {
    execFileSync(process.execPath,
      ["--input-type=module", "-e", `await import(${JSON.stringify(HELPER_URL)})`],
      { env, stdio: ["ignore", "pipe", "pipe"] });
    assert.deepEqual(readdirSync(fakeHome), [],
      "importing the helper alone must not create anything");

    const allocated = execFileSync(process.execPath,
      ["--input-type=module", "-e", [
        `const mod = await import(${JSON.stringify(HELPER_URL)});`,
        "process.stdout.write(mod.allocateEvidenceRunDir({ label: \"child\" }));",
      ].join("\n")],
      { env, stdio: ["ignore", "pipe", "pipe"] }).toString();
    assert.ok(allocated.startsWith(defaultRoot + sep),
      `allocation must resolve under the durable root, got ${allocated}`);
    assert.ok(statSync(allocated).isDirectory());
    assert.deepEqual(readdirSync(fakeHome), [".claude"],
      "only the allocation itself may touch the disk");
  } finally {
    rmSync(fakeHome, { recursive: true, force: true });
  }
});
