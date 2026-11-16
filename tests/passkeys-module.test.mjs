import test from "node:test";
import assert from "node:assert/strict";

async function fresh(name) {
  return import(`../static/platform/passkeys.mjs?test=${name}-${Date.now()}-${Math.random()}`);
}

function setBrowser({ credentials, userAgent = "Macintosh" } = {}) {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    writable: true,
    value: { PublicKeyCredential: function PublicKeyCredential() {} },
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    writable: true,
    value: { credentials: credentials || {}, userAgent },
  });
}

test("passkeys.mjs exposes the real WebAuthn client only through named exports", async () => {
  setBrowser({ credentials: { create() {} } });
  const mod = await fresh("exports");
  for (const name of ["soportado", "disponibleEnEsteEquipo", "registrar", "ingresar", "cancelado", "nombreDelEquipo", "mensaje"]) {
    assert.equal(typeof mod[name], "function", name);
  }
  assert.equal(window.SanaPasskeys, undefined);
  assert.equal(mod.soportado(), true);
  assert.equal(mod.nombreDelEquipo(), "Mac");
});

test("passkey login translates the server challenge and credential bytes", async () => {
  const calls = [];
  setBrowser({
    credentials: {
      async get(options) {
        assert.deepEqual([...new Uint8Array(options.publicKey.challenge)], [1, 2, 3]);
        assert.equal(options.publicKey.rpId, "localhost");
        return {
          rawId: Uint8Array.from([9, 8]).buffer,
          response: {
            clientDataJSON: Uint8Array.from([4]).buffer,
            authenticatorData: Uint8Array.from([5]).buffer,
            signature: Uint8Array.from([6]).buffer,
          },
        };
      },
      create() {},
    },
  });
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("login-options")) {
      return { ok: true, async json() { return { challenge: "AQID", rpId: "localhost", userVerification: "required", timeout: 60000 }; } };
    }
    return { ok: true, async json() { return { ok: true, via: "passkey" }; } };
  };
  const mod = await fresh("login");
  assert.deepEqual(await mod.ingresar(), { ok: true, via: "passkey" });
  assert.deepEqual(calls.map((call) => call.path), [
    "/api/admin/passkeys/login-options",
    "/api/admin/passkeys/login",
  ]);
  const body = JSON.parse(calls[1].options.body);
  assert.equal(body.challenge, "AQID");
  assert.equal(body.id, "CQg");
  assert.deepEqual(body.response, {
    clientDataJSON: "BA",
    authenticatorData: "BQ",
    signature: "Bg",
  });
});

test("registration posts attestation bytes and keeps cancellation quiet", async () => {
  const calls = [];
  setBrowser({
    userAgent: "Android",
    credentials: {
      async create(options) {
        assert.deepEqual([...new Uint8Array(options.publicKey.user.id)], [7]);
        return {
          rawId: Uint8Array.from([3]).buffer,
          response: {
            clientDataJSON: Uint8Array.from([4]).buffer,
            attestationObject: Uint8Array.from([5]).buffer,
          },
        };
      },
    },
  });
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("register-options")) {
      return { ok: true, async json() { return {
        challenge: "AQID", rp: { id: "localhost", name: "Human Rounds" },
        user: { id: "Bw", name: "staff", displayName: "Staff" },
        pubKeyCredParams: [{ type: "public-key", alg: -7 }],
        excludeCredentials: [], authenticatorSelection: {}, timeout: 60000,
        attestation: "none",
      }; } };
    }
    return { ok: true, async json() { return { ok: true }; } };
  };
  const mod = await fresh("register");
  assert.deepEqual(await mod.registrar(), { ok: true });
  const body = JSON.parse(calls[1].options.body);
  assert.equal(body.nombre, "Teléfono Android");
  assert.equal(body.id, "Aw");
  assert.deepEqual(body.response, { clientDataJSON: "BA", attestationObject: "BQ" });
  assert.equal(mod.cancelado({ name: "NotAllowedError" }), true);
  assert.equal(mod.cancelado({ name: "SecurityError" }), false);
});
