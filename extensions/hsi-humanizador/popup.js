/*
 * popup.js — the recent loads, newest first, at most twenty. Only the reduced
 * form ever reached the disk: first name + surname initial, last three digits
 * of the DNI, the hour. The whole DNI lives in session memory alone, so the
 * «Abrir en HSI» of a failed load copies it while the browser still holds it —
 * and says so when it no longer does.
 */
(function () {
  "use strict";

  var COMO = { guardado: "Guardado", error: "No se guardó", manual: "DNI copiado" };

  chrome.storage.local.get({ recientes: [] }).then(function (r) { pintar(r.recientes || []); });
  chrome.storage.onChanged.addListener(function (c) {
    if (c.recientes) pintar(c.recientes.newValue || []);
  });

  function pintar(lista) {
    var ol = document.getElementById("cargas");
    ol.textContent = "";
    // The three steps and the video are always one touch away. With nothing
    // loaded yet the steps open by themselves instead of an empty list; once
    // there are loads they fold under «Cómo se usa», however the operator
    // left them.
    var vacio = lista.length === 0;
    document.getElementById("recientes").hidden = vacio;
    var ayuda = document.getElementById("ayuda");
    if (vacio) ayuda.open = true;
    else if (!ayuda.dataset.tocado) ayuda.open = false;
    var video = document.getElementById("video");
    var url = self.HRShared.VIDEO_DEMO;
    video.hidden = !url;
    if (url) video.href = url;
    lista.slice(0, 20).forEach(function (fila) {
      var li = document.createElement("li");
      var quien = document.createElement("span");
      quien.className = "quien";
      quien.textContent = [fila.quien, fila.dni].filter(Boolean).join(" · ");
      var hora = document.createElement("span");
      hora.className = "hora";
      hora.textContent = fila.hora || "";
      var como = document.createElement("span");
      como.className = "como";
      como.dataset.tono = fila.estado;
      como.textContent = COMO[fila.estado] || fila.estado;
      li.appendChild(quien);
      li.appendChild(hora);
      li.appendChild(como);
      if (fila.estado === "error") {
        if (fila.detalle) {
          var detalle = document.createElement("p");
          detalle.className = "detalle";
          detalle.textContent = fila.detalle;
          li.appendChild(detalle);
        }
        var abrir = document.createElement("button");
        abrir.type = "button";
        abrir.className = "abrir";
        abrir.textContent = "Abrir en HSI";
        abrir.addEventListener("click", function () { abrirHsi(li, fila.id); });
        li.appendChild(abrir);
        var nota = document.createElement("span");
        nota.className = "nota";
        li.appendChild(nota);
      }
      ol.appendChild(li);
    });
  }

  function abrirHsi(li, id) {
    var nota = li.querySelector(".nota");
    nota.textContent = "";
    chrome.storage.session.get({ dnis: {} }).then(function (s) {
      var dni = (s.dnis || {})[id] || null;
      var copia = dni ? navigator.clipboard.writeText(dni) : Promise.reject(new Error("sin dni"));
      return chrome.runtime.sendMessage({ tipo: "abrir-hsi" }).then(function () {
        return copia;
      }).then(function () {
        nota.textContent = "DNI copiado";
      }, function () {
        nota.textContent = "El DNI ya no está disponible";
      });
    });
  }

  // Once the operator opens or folds the steps, a list refresh leaves them be.
  document.querySelector("#ayuda summary").addEventListener("click", function () {
    document.getElementById("ayuda").dataset.tocado = "1";
  });

  // A Human Rounds site the manifest does not name and Chrome does not allow
  // yet: one button that asks Chrome for it, gone once allowed. A site the
  // operator unticked in the settings is not offered again here.
  var H = self.HRShared;
  chrome.storage.local.get({ turnosApagados: [] }).then(function (r) {
    var apagados = r.turnosApagados || [];
    H.TURNOS_OPCIONALES.forEach(function (sitio) {
      if (apagados.indexOf(sitio.id) !== -1) return;
      var origenes = [H.patronDeOrigen(sitio.url)];
      chrome.permissions.contains({ origins: origenes }).then(function (tiene) {
        if (tiene) return;
        var boton = document.createElement("button");
        boton.type = "button";
        boton.className = "sitio";
        boton.textContent = "Activar en " + sitio.nombre;
        boton.addEventListener("click", function () {
          // Inside the click: Chrome only shows its prompt on a gesture.
          chrome.permissions.request({ origins: origenes }).then(function (ok) {
            if (ok) boton.remove();
          });
        });
        document.getElementById("sitios").appendChild(boton);
      });
    });
  });

  document.getElementById("ajustes").addEventListener("click", function () {
    chrome.runtime.openOptionsPage();
  });
})();
