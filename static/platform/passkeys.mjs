// passkeys.mjs — passkeys del panel en el navegador (WebAuthn, 2026-07-27).
//
// Módulo ES canónico: antes era el UMD passkeys.js con global de navegador
// (window.SanaPasskeys); ese archivo se borró y éste es el dueño único del
// cliente WebAuthn. Los consumidores (static/platform/admin-login.mjs en el
// gate, admin-account.js en Mi cuenta) lo importan por su namespace desde
// window.SANA_STATIC — no publica ningún contrato global.
//
// La verificación de verdad vive en el server (passkeys.py); acá sólo se
// traduce entre el JSON del endpoint y los ArrayBuffer que pide la API del
// navegador. El desafío viaja SIEMPRE en base64url: WebAuthn lo maneja como
// bytes y lo devuelve re-codificado dentro de clientData, así que las dos
// puntas terminan comparando el mismo string.
//
//   soportado()                    ¿el navegador tiene WebAuthn?
//   disponibleEnEsteEquipo()       Promise<bool> ¿hay autenticador de
//                                  plataforma (Touch ID, Windows Hello)?
//   registrar(nombre?)             alta: options → create → register
//   ingresar()                     login: options → get → login
//   cancelado(err)                 ¿la persona cerró el diálogo del sistema?
//   nombreDelEquipo()              etiqueta por defecto ("MacBook", "iPhone")
//   mensaje(clave)                 clave corta del server → frase legible
//
// Cero acceso a `window`/`document` a nivel de módulo — sólo dentro de las
// funciones — así el módulo se puede importar y testear en Node sin más.

var I18n = await (
    (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"])
      : import(new URL("./i18n.mjs", import.meta.url))
  );
  function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}

function b64uToBuf(s) {
  s = String(s || "").replace(/-/g, "+").replace(/_/g, "/");
  s += "=".repeat((4 - (s.length % 4)) % 4);
  var bin = atob(s);
  var out = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function bufToB64u(buf) {
  var bytes = new Uint8Array(buf), bin = "";
  for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function post(path, body) {
  return fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  }).then(function (res) {
    return res.json().catch(function () { return {}; }).then(function (j) {
      if (!res.ok) throw new Error(mensaje(j.error));
      return j;
    });
  });
}

// Los errores del server son claves cortas; acá se vuelven la frase que lee
// una persona en la pantalla. Un if por clave (no una tabla de datos) para
// que cada literal viva DENTRO de su propio T(...), donde el audit y el
// test de fallbacks lo pueden encontrar.
export function mensaje(clave) {
  if (clave === "desafio_invalido") return T("account.mine.passkeys.err_challenge_expired", null, "El intento venció. Probá de nuevo.");
  if (clave === "llave_desconocida") return T("account.mine.passkeys.err_unknown_key", null, "Esa llave no está registrada en este panel.");
  if (clave === "firma_invalida") return T("account.mine.passkeys.err_bad_signature", null, "No pudimos validar la llave.");
  if (clave === "llave_duplicada") return T("account.mine.passkeys.err_duplicate_key", null, "Esa llave parece duplicada. Registrá una nueva.");
  if (clave === "cuenta_inactiva") return T("account.mine.passkeys.err_inactive_account", null, "La cuenta está desactivada.");
  if (clave === "esa_llave_ya_estaba") return T("account.mine.passkeys.err_already_registered", null, "Esa llave ya estaba registrada.");
  if (clave === "dominio_inesperado") return T("account.mine.passkeys.err_wrong_domain", null, "La llave es de otro sitio.");
  if (clave === "base_no_disponible") return T("account.mine.passkeys.err_db_unavailable", null, "La base de datos no está disponible.");
  if (clave === "demasiados_intentos") return T("account.mine.passkeys.err_too_many_attempts", null, "Demasiados intentos. Esperá unos minutos.");
  if (clave === "la_llave_no_confirmo_presencia") return T("account.mine.passkeys.err_no_presence", null, "La llave no confirmó tu identidad.");
  if (clave === "no_encontrada") return T("account.mine.passkeys.err_not_found", null, "Esa passkey ya no está.");
  // Clave no reconocida: una frase del server pasa tal cual; un código
  // (demo_solo_lectura y cualquier otro) lo traduce apiError, nunca crudo.
  var generico = T("account.mine.passkeys.err_generic", null, "No se pudo usar la passkey.");
  return I18n && I18n.apiError ? I18n.apiError(clave, generico) : (clave || generico);
}

export function soportado() {
  return !!(window.PublicKeyCredential && navigator.credentials &&
            navigator.credentials.create);
}

/* ¿Este dispositivo puede crear una passkey él mismo (Touch ID, Windows
   Hello, el desbloqueo del teléfono)? Sirve para no ofrecer el botón donde
   el único camino sería una llave física que la persona no tiene. */
export function disponibleEnEsteEquipo() {
  if (!soportado() || !window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) {
    return Promise.resolve(false);
  }
  return window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
    .catch(function () { return false; });
}

export function registrar(nombre) {
  return post("/api/admin/passkeys/register-options").then(function (o) {
    return navigator.credentials.create({
      publicKey: {
        challenge: b64uToBuf(o.challenge),
        rp: o.rp,
        user: {
          id: b64uToBuf(o.user.id),
          name: o.user.name,
          displayName: o.user.displayName
        },
        pubKeyCredParams: o.pubKeyCredParams,
        excludeCredentials: (o.excludeCredentials || []).map(function (c) {
          return { type: "public-key", id: b64uToBuf(c.id) };
        }),
        authenticatorSelection: o.authenticatorSelection,
        timeout: o.timeout,
        attestation: o.attestation
      }
    }).then(function (cred) {
      if (!cred) throw new Error(T("account.mine.passkeys.err_not_created", null, "No se creó la passkey."));
      return post("/api/admin/passkeys/register", {
        challenge: o.challenge,
        id: bufToB64u(cred.rawId),
        nombre: nombre || nombreDelEquipo(),
        response: {
          clientDataJSON: bufToB64u(cred.response.clientDataJSON),
          attestationObject: bufToB64u(cred.response.attestationObject)
        }
      });
    });
  });
}

export function ingresar() {
  return post("/api/admin/passkeys/login-options").then(function (o) {
    return navigator.credentials.get({
      publicKey: {
        challenge: b64uToBuf(o.challenge),
        rpId: o.rpId,
        allowCredentials: [],
        userVerification: o.userVerification,
        timeout: o.timeout
      }
    }).then(function (cred) {
      if (!cred) throw new Error(T("account.mine.passkeys.err_none_chosen", null, "No se eligió ninguna passkey."));
      return post("/api/admin/passkeys/login", {
        challenge: o.challenge,
        id: bufToB64u(cred.rawId),
        response: {
          clientDataJSON: bufToB64u(cred.response.clientDataJSON),
          authenticatorData: bufToB64u(cred.response.authenticatorData),
          signature: bufToB64u(cred.response.signature)
        }
      });
    });
  });
}

/* Nombre por defecto de la llave: lo que la persona reconocería en una
   lista ("MacBook", "iPhone"). Es sólo una etiqueta, se puede editar. */
export function nombreDelEquipo() {
  var ua = navigator.userAgent || "";
  // iPhone/iPad/Mac/Windows son nombres de marca — no se traducen.
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return T("account.mine.passkeys.device_android", null, "Teléfono Android");
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows";
  return T("account.mine.passkeys.device_generic", null, "Este dispositivo");
}

/* Una cancelación del usuario (cerró el diálogo del sistema) no es un
   error que haya que gritar: se distingue para poder callarla. */
export function cancelado(err) {
  return !!err && (err.name === "NotAllowedError" || err.name === "AbortError");
}
