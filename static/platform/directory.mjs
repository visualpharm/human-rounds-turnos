/* directory.mjs — directorio de especialidades compartido
   entre la home (index.html) y estados vacíos (my-requests.js).
   import { render } from the fingerprinted module → fetches /api/directory y pinta:
     · "Más buscadas": cards (título linkeado = todo el tile, una línea
       oscura, fila de retratos reales o el ícono del rubro) → /d/<cluster>
     · "Más especialidades": columnas de links de TEXTO con su ícono
       (ui-rules: un directorio es una lista de links, no pills).
   opts: {compact:true} = sólo las cards (para estados vacíos). */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // Interface strings go through the catalog (i18n.js, loaded before this
  // file on every page). The third argument is the Spanish this call site
  // replaced: with no catalog — a single-language installation, a story in
  // Storybook, a page that never loaded the shim — it renders exactly what
  // it rendered before, so translating a string can never make a page worse.
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

  var CSS =
    ".dir-sec{margin:30px 0 0;}" +
    // Título de sección compartido (tokens.css --section-h-*): el mismo que
    // «Centros de salud» y «Guías y trámites» en la portada.
    ".dir-h{margin:0 0 12px;font-size:var(--section-h-size,18px);line-height:1.3;font-weight:var(--section-h-weight,650);letter-spacing:-.01em;color:var(--text);}" +
    ".dir-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px;}" +
    ".dir-card{position:relative;display:block;background:var(--panel);border:1px solid var(--border);border-radius:13px;" +
      "padding:16px 18px 15px;text-decoration:none;color:var(--text);transition:box-shadow .14s,border-color .14s;}" +
    ".dir-card:hover{box-shadow:0 10px 26px -16px rgba(40,30,20,.26);border-color:var(--hover-bd);}" +
    /* Los nombres largos ("Endocrinología, diabetes y nutrición") envuelven a
       2 líneas — nunca elipsis: el contenido tiene que verse entero. */
    ".dir-card h3{margin:0 3px 3px 0;font-size:16px;font-weight:700;letter-spacing:-.015em;}" +
    ".dir-card:hover h3{color:var(--link,var(--accent));}" +
    /* Flecha de "esto lleva a algo" en la esquina — mismo criterio de opacidad
       que los otros íconos sueltos del directorio (.dr-chip img, .dc-av--ico
       img): no compite con el título, sólo confirma que la card entera es un
       link (mockup Claude Design). */
    ".dc-arrow{position:absolute;top:16px;right:16px;width:16px;height:16px;flex-shrink:0;}" +
    ".dc-arrow img{width:100%;height:100%;aspect-ratio:1/1;opacity:.4;display:block;}" +
    /* `min-height` reserva el renglón aunque el subtítulo venga vacío (un rubro
       sin médicos no dice "N médicos"): sin eso la banda de caras/ícono arranca
       ~17px más arriba que en las cards de al lado, y el ícono queda fuera de
       la línea en la que están los retratos vecinos. */
    /* Demo review 2026-11-15: the line under the title is a fact («7 médicos»,
       «Sin turno, por llegada») — dark and readable, not grey print. */
    ".dir-card .dc-sub{font-size:15px;line-height:1.35;min-height:1.35em;color:var(--text);}" +
    ".dc-avs{display:flex;align-items:center;margin-top:13px;height:44px;}" +
    ".dc-av{width:44px;height:44px;border-radius:50%;border:2px solid var(--panel);margin-left:-10px;" +
      "display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;}" +
    ".dc-av:first-child{margin-left:0;}" +
    ".dc-av--ph{background-color:#efece6;background-repeat:no-repeat;}" +
    /* Los glifos ios-glyphs de Icons8 están dibujados en grilla de 30x30 — se
       muestran a su tamaño nativo (30px), no reescalados. */
    ".dc-av--ico{background:var(--bg-2);}.dc-av--ico img{width:30px;height:30px;opacity:.62;}" +
    /* Ícono del rubro en la fila del listado: el glifo pelado, en tinta, en
       los dos temas. La rotación de cuatro fondos de color que tenía en
       «pinamar» no decía nada (el color no distinguía un rubro de otro) y
       hacía de la lista dos estilos de ícono (demo review 2026-11-15). */
    ".dr-chip{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}" +
    ".dr-chip img{width:22px;height:22px;aspect-ratio:1/1;opacity:.72;}" +
    /* Título de card en TINTA (responsable del producto, 2026-09-15): mismo criterio que
       .tile-name en tiles.mjs — el título es lo más oscuro de la card, un
       escalón arriba del subtítulo (.dc-sub, --text-3). El peso 600 se
       queda (Linear: semibold). Tinta aclarada a --title-ink el 2026-09-16
       (responsable del producto: "a little bit lighter"): el --text pleno competía con el
       cuerpo; el token define el paso intermedio. */
    ":root[data-theme=humanrounds] .dir-card h3{font-weight:600;color:var(--title-ink,#1a1714);}" +
    /* Columnas de lista, no grilla: la lista corre de arriba abajo y la
       última columna es apenas más corta, en vez de una fila final con
       celdas vacías (demo review 2026-11-15). */
    ".dir-all{columns:280px;column-gap:28px;}" +
    ".dir-row{display:flex;align-items:center;gap:14px;padding:11px 4px;text-decoration:none;border-radius:6px;break-inside:avoid;}" +
    ".dir-row .dr-label{min-width:0;font-size:16px;line-height:1.4;color:var(--text);}" +
    ".dir-row .dr-name{color:inherit;}" +
    ".dir-row:hover .dr-name{color:var(--link,var(--accent));}" +
    /* Esqueleto mientras viaja /api/directory. Son las MISMAS clases de card y
       de fila, con barras grises en lugar del texto, así que el alto y las
       columnas ya son los definitivos: cuando llegan los datos se reemplaza el
       contenido sin que salte nada. No es el "frame intermedio" que prohíben
       las ui-rules (eso es contenido real -> cargando -> contenido real): acá
       abajo no hay nada todavía, y un hueco en blanco durante medio segundo se
       lee como que la página se colgó. */
    ".dir-sk{display:block;background:var(--bg-2);border-radius:6px;}" +
    ".dir-card--sk{pointer-events:none;}" +
    ".dir-card--sk .sk-t{height:16px;width:62%;margin:3px 0 9px;}" +
    ".dir-card--sk .sk-s{height:11px;width:34%;}" +
    ".dir-card--sk .dc-av{background:var(--bg-2);border-radius:50%;}" +
    ".dir-row--sk{pointer-events:none;}" +
    ".dir-row--sk .dr-chip{background:var(--bg-2);}" +
    /* `.dr-label` es un flex item sin contenido acá, así que sin este `flex:1`
       colapsa a 0 y la barra de adentro (un %) se vuelve invisible. */
    ".dir-row--sk .dr-label{flex:1;}" +
    ".dir-row--sk .sk-t{height:13px;width:56%;}" +
    "@media(prefers-reduced-motion:no-preference){" +
      ".dir-sk,.dir-card--sk .dc-av,.dir-row--sk .dr-chip{animation:dir-sk-pulse 1.4s ease-in-out infinite;}" +
      "@keyframes dir-sk-pulse{0%,100%{opacity:1}50%{opacity:.5}}}";

  function injectStyles() {
    if (document.getElementById("sana-dir-styles")) return;
    var st = document.createElement("style");
    st.id = "sana-dir-styles";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function subOf(cl) {
    if (!cl.n_medicos) return "";
    return cl.n_medicos + " " + Tn("directory.doctor", cl.n_medicos, null, "médico", "médicos");
  }

  // Cuántas cards destacadas entran arriba. Son POCAS a propósito: la grilla
  // da 3 por fila en desktop, así que 6 son dos filas prolijas y el resto del
  // directorio queda en el listado de abajo, que lee mucho más rápido.
  var DESTACADAS = 6;

  function iconImg(cl) {
    return cl.icon ? '<img src="' + esc(cl.icon) + '?v=23" alt="">' : "";
  }

  // Every card has the same three parts: the title, ONE dark line, and a band
  // of 44px circles (demo review 2026-11-15). The band holds the real
  // portraits the server sends (up to 6, sprite `webdata/medico-photo-sprite`);
  // the doctors without one are counted in «N médicos», never in a «+N»
  // badge — the badge read as a number with no meaning next to that line.
  // A rubro with no portrait at all never becomes a card (`render` lists it
  // below), except the walk-in one, whose band carries the rubro's icon.
  function card(cl) {
    var avs = "";
    (cl.avatares || []).slice(0, 6).forEach(function (a) {
      avs += '<span class="dc-av dc-av--ph" style="' + a.ph + '"></span>';
    });
    var sub = subOf(cl);
    // «Demanda espontánea» promises no faces: its line says how it works and
    // its band shows the rubro's icon. The pack's long `desc` paragraph made it
    // the one card of a different shape; it stays on the cluster page.
    if (cl.walkin) {
      sub = T("directory.walkin_sub", null, "Sin turno, por llegada");
      if (!avs && cl.icon) avs = '<span class="dc-av dc-av--ico">' + iconImg(cl) + "</span>";
    }
    var avsHtml = avs ? '<div class="dc-avs">' + avs + "</div>" : "";
    return '<a class="dir-card" href="/d/' + esc(cl.id) + '">' +
      '<span class="dc-arrow"><img src="/static/icons/dir-card-arrow-sf-regular.svg?v=1" alt=""></span>' +
      "<h3>" + esc(cl.nombre) + "</h3>" +
      '<div class="dc-sub">' + esc(sub) + "</div>" + avsHtml + "</a>";
  }

  // One icon style on both themes: the bare glyph in ink. The row carries no
  // number — a sum of services and doctors meant nothing to a patient.
  function allRow(cl) {
    return '<a class="dir-row" href="/d/' + esc(cl.id) + '">' +
      '<span class="dr-chip">' + iconImg(cl) + "</span>" +
      '<span class="dr-label"><span class="dr-name">' + esc(cl.nombre) + "</span></span></a>";
  }

  function rep(n, fn) {
    var out = "";
    for (var i = 0; i < n; i++) out += fn(i);
    return out;
  }

  /* Placeholder con la forma exacta del directorio: DESTACADAS cards y (fuera
     de `compact`) 8 filas. Los títulos de sección van con su texto definitivo —
     ya se sabe qué va a haber ahí, y así son lo único que no cambia cuando
     entran los datos. */
  function skeleton(opts) {
    var html = '<div class="dir-sec"><h2 class="dir-h">'
      + esc(T("directory.most_searched", null, "Más buscadas")) + "</h2>" +
      '<div class="dir-cards">' + rep(DESTACADAS, function () {
        return '<span class="dir-card dir-card--sk" aria-hidden="true">' +
          '<span class="dir-sk sk-t"></span><span class="dir-sk sk-s"></span>' +
          '<span class="dc-avs">' + rep(4, function () {
            return '<span class="dc-av"></span>';
          }) + "</span></span>";
      }) + "</div></div>";
    if (!opts.compact) {
      html += '<div class="dir-sec"><h2 class="dir-h">'
        + esc(T("directory.more_specialties", null, "Más especialidades")) + "</h2>" +
        '<div class="dir-all">' + rep(8, function () {
          return '<span class="dir-row dir-row--sk" aria-hidden="true">' +
            '<span class="dr-chip"></span>' +
            '<span class="dr-label"><span class="dir-sk sk-t"></span></span></span>';
        }) + "</div></div>";
    }
    return html;
  }

  export function render(host, opts) {
    opts = opts || {};
    injectStyles();
    // El esqueleto se pinta ANTES del fetch: el directorio cuelga de
    // DOMContentLoaded, después de que corran los 11 <script defer> de la home,
    // y hasta que contestaba /api/directory el lugar quedaba literalmente
    // vacío. El hueco se lee como "no cargó", no como "está cargando".
    host.innerHTML = skeleton(opts);
    return fetch("/api/directory").then(function (r) { return r.json(); }).then(function (d) {
      var clusters = (d && d.clusters) || [];
      // Una card destacada es una card CON CARAS: es lo único que la hace
      // distinta de una fila del listado. Un rubro sin ningún retrato
      // ("Vacunas y prevención", "Grupos y talleres" — los atiende el
      // vacunatorio o un equipo, no un médico con ficha) se pintaba como un
      // rectángulo blanco enorme con un ícono suelto adentro; va abajo, en el
      // listado, que es exactamente la forma que le corresponde.
      var tops = clusters.filter(function (c) {
        return (c.top || c.walkin) && ((c.avatares || []).length || c.desc);
      }).sort(function (a, b) {
        // La «Demanda espontánea» cierra la fila SIEMPRE, sea cual sea su
        // `top` en el pack (responsable del producto, 2026-10-27 y 2026-11-13).
        var wa = a.walkin ? 1 : 0, wb = b.walkin ? 1 : 0;
        return wa !== wb ? wa - wb : a.top - b.top;
      });
      // El recorte a DESTACADAS no puede dejarla afuera.
      var wk = tops.filter(function (c) { return c.walkin; });
      // (con 7+ walk-in, slice(0, -n) devolvería casi todo: el tope no baja de 0).
      tops = tops.filter(function (c) { return !c.walkin; }).slice(0, Math.max(0, DESTACADAS - wk.length)).concat(wk.slice(0, DESTACADAS));
      // Lo destacado NO se repite abajo: el pack marca `top` en casi todos los
      // rubros, así que la home mostraba la misma lista dos veces, una en
      // cards y otra en filas (responsable del producto, 2026-08-05: "si está en más buscadas, no
      // se repite en todas las especialidades").
      var destacada = {};
      tops.forEach(function (c) { destacada[c.id] = true; });
      var html = "";
      if (tops.length) {
        html += '<div class="dir-sec"><h2 class="dir-h">'
          + esc(T("directory.most_searched", null, "Más buscadas")) + "</h2>" +
          '<div class="dir-cards">' + tops.map(card).join("") + "</div></div>";
      }
      if (!opts.compact) {
        // Orden del listado: primero los que el pack fija (`pinned`, de menor
        // a mayor — Pinamar pone «Vacunas y prevención» arriba), después por
        // tamaño, y el rubro comodín (`catch_all`, «Otras especialidades»)
        // siempre al final: arriba, pegado al título «Más especialidades», se
        // leía como una repetición (oficina de prensa de Pinamar, 2026-10-22).
        function grupo(c) { return c.catch_all ? 2 : (c.pinned != null ? 0 : 1); }
        var resto = clusters.filter(function (c) { return !destacada[c.id]; })
          .sort(function (a, b) {
            var ga = grupo(a), gb = grupo(b);
            if (ga !== gb) return ga - gb;
            if (ga === 0 && a.pinned !== b.pinned) return a.pinned - b.pinned;
            return ((b.n_servicios || 0) + (b.n_medicos || 0)) - ((a.n_servicios || 0) + (a.n_medicos || 0));
          });
        if (resto.length) {
          html += '<div class="dir-sec"><h2 class="dir-h">'
            + esc(T(tops.length ? "directory.more_specialties" : "directory.all_specialties", null,
                    tops.length ? "Más especialidades" : "Todas las especialidades")) + "</h2>" +
            '<div class="dir-all">' + resto.map(allRow).join("") + "</div></div>";
        }
      }
      host.innerHTML = html;
    }).catch(function () {
      host.innerHTML = "";
    });
  }
