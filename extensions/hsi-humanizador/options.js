/*
 * options.js — the operator's settings. Chrome itself asks for permission on
 * the HSI address chosen here; the extension can touch no other site.
 *
 * The first card is one question in three parts — which HSI, which of its
 * institutions, where the turnos are seen — because together they say where a
 * turno goes from and to.
 */
(function () {
  "use strict";

  var S = self.HRShared;
  var $ = function (id) { return document.getElementById(id); };
  var POR_CENTRO = "", OTRA = "otra";

  function decir(txt, tono) {
    $("estado").textContent = txt;
    $("estado").dataset.tono = tono || "";
  }

  function direccionElegida() {
    var marcado = document.querySelector('input[name="hsi"]:checked');
    if (!marcado) return "";
    var valor = marcado.value === "otra" ? $("otra").value.trim() : marcado.value;
    return S.origen(valor);
  }

  function pintarPresets(actual) {
    var caja = $("presets");
    var esPreset = false;
    S.PRESETS.forEach(function (p) {
      var fila = document.createElement("label");
      fila.className = "opcion";
      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "hsi";
      radio.value = p.url;
      if (p.url === actual) { radio.checked = true; esPreset = true; }
      var nombre = document.createElement("span");
      nombre.textContent = p.nombre;
      fila.appendChild(radio);
      fila.appendChild(nombre);
      caja.appendChild(fila);
    });
    if (!esPreset) {
      document.querySelector('input[name="hsi"][value="otra"]').checked = true;
      $("otra").value = actual;
    }
    mostrarOtra();
  }

  function mostrarOtra() {
    var otra = document.querySelector('input[name="hsi"][value="otra"]').checked;
    $("otra").hidden = !otra;
  }

  // ---- the institution -----------------------------------------------------
  // «Según el centro de cada turno» is the default: a row of a center with a
  // known number goes to that number, any other center is asked once in HSI.
  // The known institutions of the chosen HSI follow, then «Otra» with the
  // number typed by hand.

  function pintarInstituciones(elegida) {
    var sel = $("institucion");
    var lista = S.institucionesDe(direccionElegida());
    sel.textContent = "";
    function opcion(valor, texto) {
      var o = document.createElement("option");
      o.value = valor;
      o.textContent = texto;
      sel.appendChild(o);
    }
    opcion(POR_CENTRO, "Según el centro de cada turno");
    lista.forEach(function (i) { opcion(i.id, i.nombre + " (" + i.id + ")"); });
    opcion(OTRA, "Otra, escribo el número");
    var numero = S.soloDigitos(elegida);
    var conocida = lista.some(function (i) { return i.id === numero; });
    if (elegida === OTRA || (numero && !conocida)) {
      sel.value = OTRA;
      if (numero) $("institucionId").value = numero;
    } else {
      sel.value = numero || POR_CENTRO;
    }
    mostrarNumero();
  }

  function mostrarNumero() {
    var otra = $("institucion").value === OTRA;
    $("fila-numero").hidden = !otra;
    if (!otra) mostrarAyuda(false);
  }

  function mostrarAyuda(abierta) {
    $("ayuda-numero").hidden = !abierta;
    $("info").setAttribute("aria-expanded", abierta ? "true" : "false");
  }

  function institucionElegida() {
    var v = $("institucion").value;
    return v === OTRA ? S.soloDigitos($("institucionId").value) : v;
  }

  // An HSI tab already inside an institution says its number in the address
  // (…/institucion/1086/…): «Otra» starts with it. Only readable once Chrome
  // allows that HSI; without it the box stays empty for the operator.
  function numeroDeHsiAbierto() {
    var patron = S.patronDeOrigen(direccionElegida());
    if (!patron || !chrome.tabs) return Promise.resolve("");
    return chrome.tabs.query({ url: patron }).then(function (tabs) {
      for (var i = 0; i < tabs.length; i++) {
        var n = S.institucionDeUrl(tabs[i].url);
        if (n) return n;
      }
      return "";
    }).catch(function () { return ""; });
  }

  // ---- where the turnos are seen ---------------------------------------------

  // ONE place, like the HSI block: a radio per Human Rounds site the manifest
  // does not name, then «Otra dirección» with the operator's own address. The
  // next «Guardar» asks Chrome for the chosen one in the same prompt as HSI.
  function pintarSitios(elegido) {
    var caja = $("sitios-lista");
    S.TURNOS_OPCIONALES.forEach(function (sitio) {
      var fila = document.createElement("label");
      fila.className = "opcion";
      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "turnos";
      radio.value = sitio.id;
      var nombre = document.createElement("span");
      nombre.textContent = sitio.nombre;
      fila.appendChild(radio);
      fila.appendChild(nombre);
      caja.appendChild(fila);
    });
    var marcar = document.querySelector('input[name="turnos"][value="' + elegido + '"]');
    if (marcar) marcar.checked = true;
    mostrarTurnosPropio();
  }

  function turnosMarcado() {
    var r = document.querySelector('input[name="turnos"]:checked');
    return r ? r.value : "";
  }

  function sitiosMarcados() {
    var id = turnosMarcado();
    return S.TURNOS_OPCIONALES.filter(function (s) { return s.id === id; });
  }

  // Copying the DNI needs the operator's own click: in «Solo copiar el DNI»
  // nothing can go to HSI by itself, so the choice is not offered there.
  function mostrarAlAprobar() {
    var marcado = document.querySelector('input[name="modo"]:checked');
    $("fila-al-aprobar").hidden = !!marcado && marcado.value === "manual";
  }

  function mostrarTurnosPropio() {
    $("turnosPropio").hidden = turnosMarcado() !== "otra";
  }

  // ---- load ------------------------------------------------------------------

  // Which version this computer runs: the desk and support read it off here.
  $("version").textContent = "Versión " + chrome.runtime.getManifest().version;

  chrome.storage.local.get(S.DEFAULTS).then(function (a) {
    pintarPresets(String(a.hsiUrl || "").replace(/\/+$/, ""));
    pintarInstituciones(a.institucionId || "");
    $("turnosPropio").value = a.turnosPropio || "";
    pintarSitios(S.turnosElegido(a));
    var modo = S.DEFAULTS.modo === a.modo || ["manual", "boton", "auto"].indexOf(a.modo) !== -1 ? a.modo : S.DEFAULTS.modo;
    var radio = document.querySelector('input[name="modo"][value="' + modo + '"]');
    if (radio) radio.checked = true;
    $("mostrarPasos").checked = !!a.mostrarPasos;
    $("cargarAlAprobar").checked = !!a.cargarAlAprobar;
    mostrarAlAprobar();
    $("guardarSolo").checked = !!a.guardarSolo;
    // «Guardar solo» is earned at the desk: it shows up here once HSI has
    // confirmed at least one turno the operator saved by hand.
    $("fila-guardar-solo").hidden = !(a.guardarSolo || a.guardados > 0);
  });

  document.addEventListener("change", function (e) {
    if (e.target.name === "hsi") mostrarOtra();
    if (e.target.name === "hsi" || e.target.id === "otra") {
      // Another HSI has other institutions: a known one of the old HSI does
      // not carry over; «Otra» and its number, typed by the operator, do.
      var antes = $("institucion").value;
      var sigue = S.institucionesDe(direccionElegida()).some(function (i) { return i.id === antes; });
      pintarInstituciones(antes === OTRA ? OTRA : (sigue ? antes : POR_CENTRO));
    }
    if (e.target.id === "institucion") {
      mostrarNumero();
      if (e.target.value === OTRA && !$("institucionId").value) {
        numeroDeHsiAbierto().then(function (n) { if (n && !$("institucionId").value) $("institucionId").value = n; });
      }
    }
    if (e.target.name === "turnos") mostrarTurnosPropio();
    if (e.target.name === "modo") mostrarAlAprobar();
  });

  $("info").addEventListener("click", function () {
    mostrarAyuda($("ayuda-numero").hidden);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("ayuda-numero").hidden) { mostrarAyuda(false); $("info").focus(); }
  });

  // ---- save ------------------------------------------------------------------

  $("form").addEventListener("submit", function (e) {
    e.preventDefault();
    var url = direccionElegida();
    if (!url) return decir("Escribí la dirección de HSI completa, con https://", "error");
    if ($("institucion").value === OTRA && !institucionElegida()) {
      return decir("Escribí el número de la institución, o elegí una de la lista.", "error");
    }
    var otra = turnosMarcado() === "otra";
    var propio = otra ? S.origen($("turnosPropio").value.trim()) : "";
    if (otra && !propio) return decir("Escribí la dirección donde ves los turnos completa, con https://", "error");
    var sitios = sitiosMarcados();
    var apagados = S.TURNOS_OPCIONALES.filter(function (s) { return sitios.indexOf(s) === -1; });
    // Must run inside this click: Chrome only shows its prompt on a gesture.
    // One prompt for HSI, every ticked site and the operator's own address; a
    // site already allowed adds nothing to it.
    var origenes = [S.patronDeOrigen(url)]
      .concat(sitios.map(function (s) { return S.patronDeOrigen(s.url); }))
      .concat(propio ? [S.patronDeOrigen(propio)] : []);
    chrome.permissions.request({ origins: origenes }).then(function (ok) {
      if (!ok) return decir("Sin ese permiso no puedo cargar turnos en HSI.", "error");
      return chrome.storage.local.get(S.DEFAULTS).then(function (antes) {
        // What the operator unticked or replaced gives its permission back —
        // never one that is still in use.
        var soltar = apagados.map(function (s) { return S.patronDeOrigen(s.url); });
        if (antes.turnosPropio && S.origen(antes.turnosPropio) !== propio) soltar.push(S.patronDeOrigen(antes.turnosPropio));
        soltar = soltar.filter(function (p) { return p && origenes.indexOf(p) === -1; });
        return (soltar.length ? chrome.permissions.remove({ origins: soltar }) : Promise.resolve()).then(function () {
          return antes;
        });
      }).then(function (antes) {
        var cambioDeHsi = String(antes.hsiUrl).replace(/\/+$/, "") !== url;
        var modoMarcado = document.querySelector('input[name="modo"]:checked');
        return chrome.storage.local.set({
          hsiUrl: url,
          modo: ["manual", "boton", "auto"].indexOf(modoMarcado && modoMarcado.value) !== -1 ? modoMarcado.value : S.DEFAULTS.modo,
          mostrarPasos: $("mostrarPasos").checked,
          cargarAlAprobar: !$("fila-al-aprobar").hidden && $("cargarAlAprobar").checked,
          turnosApagados: apagados.map(function (s) { return s.id; }),
          turnosPropio: propio,
          guardarSolo: !$("fila-guardar-solo").hidden && $("guardarSolo").checked && !cambioDeHsi,
          // Another HSI is another institution and a fresh start: «guardar
          // solo» has to be earned there too, and the numbers picked per
          // center belong to the old one.
          guardados: cambioDeHsi ? 0 : antes.guardados,
          institucionPorCentro: cambioDeHsi ? {} : antes.institucionPorCentro,
          // What the form shows is what is saved, HSI changed or not.
          institucionId: institucionElegida()
        });
      }).then(function () {
        return chrome.runtime.sendMessage({ tipo: "ajustes-cambiados" });
      }).then(function () {
        decir("Listo, quedó guardado.", "bien");
        return chrome.storage.local.get(S.DEFAULTS);
      }).then(function (a) {
        $("guardarSolo").checked = !!a.guardarSolo;
        $("fila-guardar-solo").hidden = !(a.guardarSolo || a.guardados > 0);
        pintarInstituciones(a.institucionId || "");
      });
    }).catch(function () {
      decir("No se pudo guardar. Fijate las direcciones.", "error");
    });
  });
})();
