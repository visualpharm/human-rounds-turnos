/* tiles.mjs — componente ÚNICO de mosaico con foto de Human Rounds (centros de
   salud y también los talleres de /d/grupos). Reemplaza a las viejas .ccard.
   Toda página que muestra tiles carga ESTE archivo y le pasa la lista; cada
   item puede traer `href` explícito (talleres → /service/<id>); sin `href`
   se asume un centro y linkea a /center/<slug>.

   Reglas (skills accent-is-link + patient-flow-design + ui-consistency):
   - una tile = UN solo <a> que envuelve foto + nombre + dirección + jtbd; toda
     la tile es el único link (patrón whole-tile). El título queda en TINTA; el
     hover (borde/sombra + título al acento) revela que se puede tocar.
   - SIN links anidados ni un segundo link adentro: la dirección es texto en
     tinta (revisión de la demo 2026-11-15, P35; antes, desde el 2026-10-29,
     linkeaba a Maps por encima del link de la tile). Maps vive en la página
     del centro, junto al teléfono.
   - cada tile a SU altura (align-items:start): estiradas a la más alta de la
     fila quedaban con el fondo vacío.
   - foto opcional: sin `foto` la tile se dibuja sin bloque de imagen (degradación
     limpia, sin img rota ni caja vacía).
   - legibilidad: nombre 18px, dirección/jtbd/notas 15px en tinta plena (piso
     patient-flow-design; nada en gris clarito para un público mayor).

   Uso servidor: <div data-tiles><script type="application/json" class="tiles-data">
     {"centros":[…]}</script></div>  +  <script type="module" src="{static_fingerprint.url('platform/tiles.mjs')}"></script>
   Uso cliente: import { render } from the fingerprinted tiles.mjs URL. */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  export function tileHtml(c, i) {
    var foto = c && c.foto;
    // Las primeras tiles entran arriba del fold: `loading="lazy"` en la que
    // termina siendo el LCP hace que el browser ni siquiera la pida hasta
    // después del layout (medido en /centros: 2,6 s de "load delay" sobre un
    // LCP de 6 s). Las de arriba van eager y la primera además con prioridad;
    // el resto sigue lazy, que es donde lazy realmente sirve.
    var arriba = i < 2;
    var carga = arriba
      ? ' loading="eager"' + (i === 0 ? ' fetchpriority="high"' : "")
      : ' loading="lazy"';
    var photo = foto
      ? '<span class="tile-photo"><img src="' + esc(foto) + '" alt=""' + carga + '></span>'
      : "";
    // direccion_nota: until when the address holds («Hasta el 30 de noviembre»),
    // plain text under it — never inside the Maps link.
    var addrNota = c.direccion_nota ? '<span class="tile-addr">' + esc(c.direccion_nota) + "</span>" : "";
    var addr = c.direccion ? '<span class="tile-addr">' + esc(c.direccion) + "</span>" + addrNota : "";
    // lede: jtbd/descripcion; con jtbd_href el lugar LINKEA a la página del
    // centro (tiles informativas de talleres — link visible como link) y
    // jtbd_suffix es texto plano detrás (", consultorio 9").
    var ledeTxt = c.jtbd || c.descripcion || "";
    var lede = "";
    if (ledeTxt && c.jtbd_href) {
      lede = '<a class="tile-lede-a" href="' + esc(c.jtbd_href) + '">' + esc(ledeTxt) + "</a>" +
             esc(c.jtbd_suffix || "");
    } else if (ledeTxt) {
      lede = esc(ledeTxt + (c.jtbd_suffix || ""));
    }
    // notas: líneas extra de información (edad, cómo participar) — texto plano.
    var notas = (c.notas || []).map(function (n) {
      return '<span class="tile-nota">' + esc(n) + "</span>";
    }).join("");
    // nombre_sub: dato clave que viaja EN el título ("· Para mayores de 60") —
    // misma línea y tamaño, peso apenas menor (responsable del producto, 2026-07-16: la edad va en
    // el título, no perdida abajo en gris).
    var name = esc(c.nombre) + (c.nombre_sub
      ? ' <span class="tile-name-sub">&middot; ' + esc(c.nombre_sub) + "</span>"
      : "");
    var body =
      '<span class="tile-body">' +
        '<span class="tile-name">' + name + "</span>" +
        addr +
        (lede ? '<span class="tile-lede">' + lede + "</span>" : "") +
        notas +
      "</span>";
    // href explícito, o el clásico de centros por slug. href:"" (talleres) =
    // tile INFORMATIVA: toda la info vive acá, no hay página de detalle, así
    // que se dibuja como <div> sin hover ni cursor (accent-is-link: nada debe
    // parecer clickeable si no navega).
    var href = c.href === undefined ? "/center/" + c.slug : c.href;
    if (!href) {
      return '<div class="tile tile--static' + (foto ? "" : " tile--nophoto") + '">' + photo + body + "</div>";
    }
    return '<a class="tile' + (foto ? "" : " tile--nophoto") + '" href="' + esc(href) + '">' +
      photo + body + "</a>";
  }

  export function render(host, centros) {
    injectStyle();
    host.classList.add("tiles-grid");
    host.innerHTML = (centros || []).map(function (c, i) { return tileHtml(c, i); }).join("");
  }

  /* LISTA COMPACTA de centros (la portada; responsable del producto, 2026-10-25: «solo el nombre,
     a la derecha un botón con solo el ícono, sin relleno»). Una fila por LUGAR:
     los centros con la misma dirección (`place`: Hospital, Hemoterapia y
     Oncología en Av. Shaw 255) comparten fila y UN botón de ruta; adentro,
     los nombres linkean a sus páginas, el principal (`place_main`, o el
     primero) arriba y en negrita. Sin dirección fija (`directions_url` vacío:
     CAPS Móvil) la fila no lleva botón. La dirección no se escribe: la
     resuelve el mapa. `label(key, vars, fallback)` es el T() de la página. */
  export function listRows(centros) {
    var rows = [], byPlace = {};
    (centros || []).forEach(function (c) {
      var k = c.place || "";
      var row = k ? byPlace[k] : null;
      if (!row) {
        row = { items: [] };
        rows.push(row);
        if (k) byPlace[k] = row;
      }
      if (c.place_main) row.items.unshift(c); else row.items.push(c);
    });
    return rows;
  }

  // Por ZONA (`locality`: Pinamar, Ostende, Valeria del Mar…), en el orden en
  // que aparece cada una; los que no tienen zona (CAPS Móvil, o una zona que el
  // panel todavía no cargó) van al final, en una tarjeta aparte sin título.
  export function listSections(centros) {
    var sections = [], byLoc = {}, rest = [];
    (centros || []).forEach(function (c) {
      var loc = c.locality || "";
      if (!loc) { rest.push(c); return; }
      if (!byLoc[loc]) { byLoc[loc] = { title: loc, centros: [] }; sections.push(byLoc[loc]); }
      byLoc[loc].centros.push(c);
    });
    if (rest.length) sections.push({ title: "", centros: rest });
    return sections.map(function (s) {
      return { title: s.title, rows: listRows(s.centros) };
    });
  }

  export function listHtml(centros, label) {
    var T = label || function (k, v, f) { return f; };
    return listSections(centros).map(function (sec) {
      return '<ul class="cl">' +
        (sec.title ? '<li class="cl-h">' + esc(sec.title) + "</li>" : "") +
        sec.rows.map(function (row) { return rowHtml(row, T); }).join("") + "</ul>";
    }).join("");
  }

  // Dentro de un grupo (la dirección del hospital) los servicios cuelgan del
  // principal: sin «Servicio de» (es obvio bajo el hospital), el guion que
  // separaba el subtítulo pasa a coma (una línea que arranca con «- » quedaba
  // colgada) y las palabras cortas se pegan a la siguiente con un espacio
  // duro, para que ninguna línea termine en «de».
  export function subName(name) {
    var n = String(name || "").replace(/^\s*servicio\s+de\s+/i, "").replace(/\s+[-–—]\s+/g, ", ").trim();
    n = n.charAt(0).toUpperCase() + n.slice(1);
    var short = /^(de|del|la|el|los|las|y|e|en|a|con)$/i, words = n.split(/\s+/);
    return words.map(function (w, i) {
      return i === words.length - 1 ? w : w + (short.test(w) ? " " : " ");
    }).join("");
  }

  function rowHtml(row, T) {
    var first = row.items[0];
    var names = row.items.map(function (c, i) {
      var href = c.href === undefined ? "/center/" + c.slug : c.href;
      return '<a class="cl-name' + (i === 0 ? " cl-name--main" : " cl-name--sub") + '" href="' + esc(href) + '">' +
        esc(i === 0 ? c.name : subName(c.name)) + "</a>";
    }).join("");
    var url = first.directions_url || "";
    var go = "";
    // Sin ruta pero con dirección (CAPS Móvil: «Víctor Hugo y Anspach, hasta
    // el 30 de noviembre», responsable del producto 2026-11-05): ni mapa ni botón la dicen, así
    // que se escribe bajo el nombre.
    var addr = String(first.address || "").trim();
    if (!url && addr) names += '<span class="cl-addr">' + esc(addr) + "</span>";
    if (url) {
      var txt = T("home.centers.directions", { name: first.name }, "Cómo llegar a " + first.name);
      go = '<a class="cl-go" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" aria-label="' + esc(txt) + '" title="' + esc(txt) + '">' +
        '<img src="/static/icons/directions-sf-regular.png?v=1" srcset="/static/icons/directions-sf-regular-2x.png?v=1 2x" alt="" width="24" height="24"></a>';
    }
    return '<li class="cl-row"><span class="cl-names">' + names + "</span>" + go + "</li>";
  }

  export function renderList(host, centros, label) {
    injectStyle();
    host.classList.add("cl-grid");
    host.innerHTML = listHtml(centros, label);
  }

  export function injectStyle() {
    if (document.getElementById("tiles-style")) return;
    var css =
      // Lista compacta de la portada. Tinta plena y contorno OSCURO en el
      // botón (público mayor, nada de gris clarito); el botón es de contorno,
      // no relleno — acción secundaria al lado del nombre —; 44px de toque.
      ".cl{list-style:none;margin:14px 0 0;padding:0;max-width:640px;background:var(--panel);border:1px solid var(--border-strong);border-radius:14px;overflow:hidden;}" +
      // El botón de ruta va ARRIBA, a la altura del nombre principal (no al
      // centro de la fila): el 11px de arriba de .cl-names centra la primera
      // línea (22px) con el botón (44px).
      ".cl-row{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:8px 14px 8px 18px;border-top:1px solid var(--border);}" +
      ".cl-row:first-child{border-top:0;}" +
      ".cl+.cl{margin-top:12px;}" +
      // Título de zona: una banda en tinta plena sobre la primera fila.
      ".cl-h{padding:10px 18px;background:var(--bg-2);color:var(--text);font-size:17px;line-height:1.3;font-weight:700;letter-spacing:-.01em;}" +
      ".cl-h+.cl-row{border-top:0;}" +
      ".cl-names{display:flex;flex-direction:column;gap:2px;min-width:0;padding:11px 0;}" +
      ".cl-name{font-size:17px;line-height:1.3;font-weight:500;letter-spacing:-.01em;color:var(--text);text-decoration:none;text-wrap:balance;}" +
      ".cl-name--main{font-weight:700;}" +
      // Servicios del grupo: bajo el principal, con sangría, peso normal, tinta plena.
      ".cl-name--sub{font-weight:400;padding:2px 0 2px 18px;text-wrap:pretty;}" +
      ".cl-addr{font-size:16px;line-height:1.35;color:var(--text);text-wrap:pretty;}" +
      ":root[data-theme=humanrounds] .cl-name--main{font-weight:600;color:var(--title-ink,#1a1714);}" +
      ".cl-name:hover{color:var(--link-hover,var(--accent-hover));}" +
      ".cl-go{flex:0 0 auto;display:flex;align-items:center;justify-content:center;box-sizing:border-box;width:44px;height:44px;" +
        "border:1.5px solid var(--text-2);border-radius:var(--btn-radius,10px);background:transparent;transition:background .12s;}" +
      ".cl-go:hover{background:var(--bg-2);}" +
      ".cl-go:focus-visible,.cl-name:focus-visible{outline:3px solid var(--accent);outline-offset:2px;}" +
      ".cl-go img{display:block;width:24px;height:24px;}" +
      "@media(max-width:600px){.cl-row{gap:12px;padding:8px 10px 8px 14px;}.cl-h{padding:10px 14px;}.cl-name--sub{padding-left:14px;}}" +
      // Escritorio ancho: las zonas en 2 columnas (en el orden de lectura),
      // cada tarjeta a su altura. El teléfono y la tablet siguen en una.
      "@media(min-width:1280px){.cl-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 16px;align-items:start;margin-top:14px;}" +
        ".cl-grid>.cl,.cl-grid>.cl+.cl{margin:0;max-width:none;}}" +
      ".tiles-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-top:14px;align-items:start;}" +
      // Borde y elevación en REPOSO: --border sobre --bg da 1,12:1, así que la
      // tile sin foto se perdía contra la página — sólo se leía el texto suelto
      // (responsable del producto, 2026-08-04). Mismo tratamiento que las otras tarjetas del sitio
      // (.lst-searchbox en listing.js, .svc-card en service.js): --border-strong
      // + --shadow-card. El hover ya no cambia el borde (ahí no quedaba nada
      // que subir): levanta la sombra y pinta el título al acento.
      ".tile{display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--border-strong);" +
        "border-radius:14px;overflow:hidden;text-decoration:none;color:inherit;" +
        "box-shadow:var(--shadow-card);transition:box-shadow .14s,border-color .14s;}" +
      ".tile:hover{box-shadow:0 12px 28px -16px rgba(40,30,20,.30);}" +
      // --recessed no existía en human-rounds.css: el var() siempre caía al
      // fallback fijo (el bg-2 de "pinamar"), ignorando el tema. Ahora usa
      // --bg-2 de verdad (2026-08-05, sweep del design-loop; no cambia nada
      // en pinamar, su --bg-2 ya es este mismo hex).
      ".tile-photo{display:block;width:100%;aspect-ratio:4/3;background:var(--bg-2);overflow:hidden;}" +
      ".tile-photo img{width:100%;height:100%;object-fit:cover;display:block;}" +
      // Interlineado UNIFORME entre las líneas del cuerpo: el gap del flex es
      // el único espaciador — nada de margin-top por línea (se veía disparejo).
      // Legibilidad (público mayor): horario en tinta, notas en --text-2;
      // --text-3 no se usa para información clave.
      ".tile-body{display:flex;flex-direction:column;gap:6px;padding:15px 17px 17px;}" +
      ".tile-name{font-size:18px;font-weight:700;letter-spacing:-.01em;color:var(--text);line-height:1.25;}" +
      ".tile-name-sub{font-weight:500;}" +
      ".tile:hover .tile-name{color:var(--link,var(--accent));}" +
      /* Título en TINTA en humanrounds también (responsable del producto, 2026-09-15: "el título
         del hospital negro, la dirección también, la descripción grisísima").
         Hasta hoy llevaba #2a2521 — un paso MÁS CLARO que la dirección de
         abajo (#1a1714 = --text), o sea que la tile invertía su propia
         jerarquía: el dato menos importante (dirección) era el más oscuro.
         El peso 600 sí se queda (Linear: título semibold, resto regular).
         Tinta aclarada a --title-ink el 2026-09-16 (the product owner: "a little bit
         lighter"): paso intermedio entre --text y el #2a2521 original. */
      ":root[data-theme=humanrounds] .tile-name{font-weight:600;color:var(--title-ink,#1a1714);}" +
      ".tile-addr{font-size:15px;color:var(--text);line-height:1.45;}" +
      ".tile-lede{font-size:15px;color:var(--text);line-height:1.45;}" +
      ".tile-nota{font-size:15px;color:var(--text);line-height:1.45;}" +
      ".tile-lede-a{color:var(--accent);text-decoration:none;}" +
      ".tile-lede-a:hover{color:var(--accent-hover);}" +
      ".tile--static{cursor:default;}" +
      // La tile informativa no navega: al pasar el mouse queda EXACTAMENTE como
      // en reposo (no se levanta, no cambia el borde) — nada debe parecer
      // clickeable si no lleva a ningún lado (accent-is-link).
      ".tile--static:hover{box-shadow:var(--shadow-card);}" +
      ".tile--static:hover .tile-name{color:var(--text);}" +
      "@media(max-width:600px){.tiles-grid{grid-template-columns:1fr;gap:13px;}.tile-photo{aspect-ratio:16/9;}}";
    var st = document.createElement("style");
    st.id = "tiles-style";
    st.textContent = css;
    document.head.appendChild(st);
  }

  export function boot() {
    injectStyle();
    document.querySelectorAll("[data-tiles]").forEach(function (m) {
      if (m.__tiles) return; m.__tiles = 1;
      var raw = m.querySelector(".tiles-data");
      if (!raw) return;
      var p; try { p = JSON.parse(raw.textContent); } catch (e) { return; }
      render(m, p.centros || []);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
