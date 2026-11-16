/* prep.mjs — canonical browser preparation model: which sections a
   preparation has and what they are called.

   Espejo exacto de prep_modelo.py (mismos títulos, mismo orden, mismas
   reglas). Lo consumen service.js (wizard), upload-order.js y my-requests.js:
   antes cada uno tenía su propia versión y ninguna sabía de las secciones
   nuevas. Este módulo no dibuja nada — devuelve el MODELO y cada superficie
   pone sus clases, que son distintas a propósito (wz-prep-* en el wizard,
   ms-prep-* en Mis solicitudes, prep-* en las páginas).

   test/prep-modelo.test.mjs imports this file and runs the Python twin against
   the same inputs: if one changes, the other must change too. */

  var I18n = await (
      (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
        ? import(window.SANA_STATIC["platform/i18n.mjs"])
        : import(new URL("./i18n.mjs", import.meta.url))
    );
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
    // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
    function Tn(key, n, vars, one, other) {
      if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
      var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
      return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
    }

  // Las claves ("antes", "traer"...) son el contrato con prep_model.py y con
  // el JSON del paquete — quedan iguales. Los TÍTULOS son idioma de interfaz,
  // así que pasan por T(): el mirror de Python (prep_model.py) no traduce
  // (test/prep-modelo.test.mjs lo corre sin ventana ni SanaI18n, así que T()
  // devuelve el mismo castellano de siempre y la paridad no se rompe).
  var SECCIONES = [
    ["antes", T("upload.prep.section.antes", null, "Antes de venir"), "normal"],
    ["traer", T("upload.prep.section.traer", null, "Qué llevar"), "normal"],
    ["avisar", T("upload.prep.section.avisar", null, "Avisá al llegar si"), "aviso"],
    ["no_hace_falta", T("upload.prep.section.no_hace_falta", null, "No hace falta"), "no"],
    ["despues", T("upload.prep.section.despues", null, "Después"), "normal"]
  ];

  var SIN_NADA = T("upload.prep.sin_nada", null, "No necesita preparación especial.");

  function norm(s) {
    return String(s == null ? "" : s).normalize("NFD")
      .replace(/[̀-ͯ]/g, "").toLowerCase().split(/\s+/).join(" ").trim();
  }

  function lista(e, clave) {
    var v = e && e[clave];
    if (!Array.isArray(v)) return [];
    return v.map(function (x) { return String(x == null ? "" : x); })
            .filter(function (x) { return x.trim(); });
  }

  /* `antes` se llamó `preparacion` hasta 2026-07-29 y ese nombre sigue vivo
     dentro del ai_json de las solicitudes viejas. */
  function antes(e) {
    var a = lista(e, "antes");
    return a.length ? a : lista(e, "preparacion");
  }

  function resumen(e) {
    return String((e && e.resumen) || "").trim();
  }

  /* El ayuno se anuncia sólo cuando ES un requisito: si no hace falta, el dato
     ya viaja como ítem de `no_hace_falta`. La nota se conserva siempre. */
  function ayuno(e) {
    var ay = (e && e.ayuno) || {};
    var nota = String(ay.nota || "").trim();
    if (!ay.required) return { hay: false, texto: "", nota: nota };
    var texto = ay.horas
      ? Tn("upload.ayuno.hours", ay.horas, { horas: ay.horas }, "Ayuno de {horas} hora.", "Ayuno de {horas} horas.")
      : T("upload.ayuno.fasting", null, "Se hace en ayunas.");
    var mencionaAgua = antes(e).some(function (p) { return norm(p).indexOf("agua") >= 0; });
    if (!mencionaAgua) {
      if (ay.agua_permitida === true) texto += " " + T("upload.ayuno.water_ok", null, "Podés tomar agua.");
      else if (ay.agua_permitida === false) texto += " " + T("upload.ayuno.water_not", null, "Tampoco tomes agua.");
    }
    return { hay: true, texto: texto, nota: nota };
  }

  function secciones(e) {
    if (!e || typeof e !== "object") return [];
    var out = [];
    SECCIONES.forEach(function (s) {
      var items = s[0] === "antes" ? antes(e) : lista(e, s[0]);
      if (items.length) out.push({ clave: s[0], titulo: s[1], items: items, tono: s[2] });
    });
    return out;
  }

  function vacia(e) {
    if (!e || typeof e !== "object") return true;
    var ay = ayuno(e);
    return !(secciones(e).length || ay.hay || ay.nota || resumen(e));
  }

  // Python's html.escape(quote=True), byte for byte: the parity test compares
  // this markup with prep_model.cuerpo_html.
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
  }

  /* How a whole preparation is DRAWN — the summary, the fasting, then each
     section as a bold heading over its list (one item = a sentence). The same
     markup as the service page (prep_model.cuerpo_html, pinned by
     test/prep-modelo.test.mjs); the `prep-*` classes are styled once, in the
     page CSS. The product owner 26.10: the wizard's own lighter variant made «Antes de
     venir» and «Qué llevar» read like body text. */
  function cuerpoHtml(e) {
    if (!e) return "";
    var partes = [];
    var res = resumen(e);
    if (res) partes.push('<p class="prep-resumen">' + esc(res) + "</p>");
    var ay = ayuno(e);
    if (ay.hay) partes.push('<div class="prep-key">' + esc(ay.texto) + "</div>");
    if (ay.nota) partes.push('<p class="prep-nota">' + esc(ay.nota) + "</p>");
    var secs = secciones(e).map(function (s) {
      var items = s.items.length === 1
        ? '<p class="prep-single prep-single--' + s.tono + '">' + esc(s.items[0]) + "</p>"
        : '<ul class="prep-list">' + s.items.map(function (i) { return "<li>" + esc(i) + "</li>"; }).join("") + "</ul>";
      return '<section class="prep-sec prep-sec--' + s.tono + '"><h3 class="prep-sec-h">' + esc(s.titulo) + "</h3>" + items + "</section>";
    });
    if (secs.length) partes.push('<div class="prep-cols">' + secs.join("") + "</div>");
    return partes.join("");
  }

export { SECCIONES, SIN_NADA, antes, resumen, ayuno, secciones, vacia, cuerpoHtml };

export default {
  SECCIONES: SECCIONES,
  SIN_NADA: SIN_NADA,
  antes: antes,
  resumen: resumen,
  ayuno: ayuno,
  secciones: secciones,
  vacia: vacia,
  cuerpoHtml: cuerpoHtml
};
