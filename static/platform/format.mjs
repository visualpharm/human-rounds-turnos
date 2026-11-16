/* static/platform/format.mjs — canonical ES module for formatting shared Human Rounds data.

   Browser and Node consumers import the same named functions. There is no
   compatibility global and no CommonJS wrapper.

   Cómo se escribe un dato en TODO el sitio, una sola vez
   (the product owner, 2026-07-28: "normalize the phone numbers. Don't write +54. Start with
   the area code and link them to WhatsApp. Make the emails clickable" →
   "create the same format of the phone number everywhere on the website").

   Nació como `admin-format.js` porque las cuatro copias que unificó vivían en
   el panel: admin.html:contactoLink, la de admin-my-appointments.js, la de
   admin-content.js (medCalContacto, la única que separaba el área) y la de la
   cola. Cuatro maneras de escribir el mismo teléfono, y ninguna llevaba a
   WhatsApp — que es por donde el hospital realmente le escribe al paciente (el
   recordatorio del día previo ya sale por ahí, notifications.py). El wizard
   del paciente tenía una QUINTA (`service.js:formatPhone`), así que el módulo
   perdió el prefijo `admin-`: es del sitio, no del panel. Cualquier pantalla
   nueva que muestre un teléfono o un mail usa ESTE módulo — una segunda copia
   de la lógica de formato ES el bug.

   Teléfono, tres decisiones:

   1. UNA sola forma, la canónica, para mostrar Y para guardar (responsable del producto,
      2026-07-28 — revierte la forma nacional sin país que había ese mismo
      día):

        +54 9 11 3547-7302     celular del AMBA
        +54 11 4321-5678       fijo del AMBA
        +54 9 2254 12-3456     celular de Pinamar
        +54 2254 40-1234       fijo de Pinamar

      El abonado se corta dejando los últimos 4 dígitos después del guion, sea
      de 8, 7 o 6 dígitos: un solo criterio, no tres casos.
   2. Se NORMALIZA lo que haya cargado: el 0 de larga distancia, el 9 de
      celular y el 15 histórico son formas distintas de escribir el mismo
      número y llegan todas mezcladas (el paciente lo tipea en su cuenta, la
      mesa lo copia del DNI, el padrón lo trae de otra forma). Lo que SÍ se
      conserva es si es un celular — el 9 no se adivina: +54 11 4321-5678 y
      +54 9 11 4321-5678 son dos líneas distintas, así que un número que no
      viene marcado se muestra sin el 9.
   3. El link va a WhatsApp, no a `tel:`. El panel se usa en una computadora de
      escritorio: `tel:` no marca nada, y WhatsApp sí abre la conversación con
      esa persona. Un fijo no va a estar en WhatsApp — es el costo de que los
      celulares, que son casi todos, queden a un click.
*/
"use strict";

  // Los textos de interfaz de este módulo son el tooltip de "Escribirle por
  // WhatsApp" (nav.*) y de quién es un contacto en las pantallas del médico
  // (admin.queue.contacto.*, contactoQuienHtml) — el resto son reglas de
  // formato, no strings de UI. Mismo resolver que nav.js/tiles.js.
  var I18n = await (
      (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
        ? import(window.SANA_STATIC["platform/i18n.mjs"])
        : import(new URL("./i18n.mjs", import.meta.url))
    );
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* Sólo los dígitos de un valor (null/undefined -> ""): lo que el panel y el sitio usan para comparar DNI, códigos y teléfonos. */
  function digits(v) { return String(v == null ? "" : v).replace(/\D/g, ""); }

  /* ====================== EL PAÍS VIVE EN EL PACK =======================
     Se movió a packs/ar/country.json ("phone") en la fase 2b (2026-07-30):
     ya no hay un bloque hardcodeado acá, ni en format.py — para otro país
     se edita SÓLO ese archivo. El servidor lo inyecta como
     window.SANA_COUNTRY_PHONE en un <script> ANTES de este archivo (ver
     patient_web/site_*.py y templates/admin/base.html); format.py hace la misma
     normalización con la misma clave inglesa → variable local, así que un
     review de las dos funciones de acá abajo alcanza para ver que dicen lo
     mismo. No hay respaldo local: si el shell no publica el pack, el módulo
     falla en vez de disfrazar una instalación rota como Argentina. */
  var _PHONE = (typeof globalThis !== "undefined") && globalThis.SANA_COUNTRY_PHONE;
  var _REQUIRED_PHONE = ["code", "exit_prefix", "national_length", "mobile_prefix",
    "mobile_local_suffix", "trunk_prefix", "areas_2", "areas_3", "area_default",
    "final_group", "non_geographic"];
  if (!_PHONE || _REQUIRED_PHONE.some(function (key) { return _PHONE[key] == null; })) {
    throw new Error("format.mjs requires SANA_COUNTRY_PHONE from the active data pack");
  }
  var PAIS = {
    codigo: _PHONE.code,
    salida: _PHONE.exit_prefix,
    largo_nacional: _PHONE.national_length,
    movil_prefijo: _PHONE.mobile_prefix,
    movil_local: _PHONE.mobile_local_suffix,
    larga_distancia: _PHONE.trunk_prefix,
    areas_2: _PHONE.areas_2,
    areas_3: _PHONE.areas_3,
    area_default: _PHONE.area_default,
    grupo_final: _PHONE.final_group,
    no_geograficos: _PHONE.non_geographic
  };
  /* ================== fin del bloque de país =========================== */

  var AREA3 = PAIS.areas_3.reduce(function (a, k) { a[k] = 1; return a; }, {});
  var AREA2 = PAIS.areas_2.reduce(function (a, k) { a[k] = 1; return a; }, {});

  function largoArea(d) {
    if (AREA2[d.slice(0, 2)]) return 2;
    return AREA3[d.slice(0, 3)] ? 3 : PAIS.area_default;
  }

  function noGeografico(d) {
    return PAIS.no_geograficos.indexOf(d.slice(0, 3)) !== -1;
  }

  /* Descompone cualquier forma de escribir el número en las dos cosas que
     hacen falta para escribirlo canónicamente: los dígitos nacionales
     (característico + abonado) y si es un CELULAR.

     Lo de móvil no se adivina: se sabe sólo si el número lo trae marcado, con
     el 9 después del país (+54 9…) o con el 15 después del característico
     (02254 15…). Un número pelado se muestra sin el 9 — inventarlo cambiaría
     el número, y en la Argentina +54 11 4321-5678 y +54 9 11 4321-5678 son
     dos líneas distintas. */
  function telPartes(v) {
    var d = digits(v);
    var movil = false;
    if (!d) return { d: "", movil: false, ok: false };
    if (d.slice(0, PAIS.salida.length) === PAIS.salida) d = d.slice(PAIS.salida.length);
    if (d.length > PAIS.largo_nacional && d.slice(0, PAIS.codigo.length) === PAIS.codigo) {
      d = d.slice(PAIS.codigo.length);
    }
    if (d.length > PAIS.largo_nacional && d.charAt(0) === PAIS.movil_prefijo) {
      d = d.slice(1); movil = true;
    }
    if (d.length > PAIS.largo_nacional && d.charAt(0) === PAIS.larga_distancia) d = d.slice(1);
    if (d.length > PAIS.largo_nacional) {           // 15 histórico, después del área
      var a = largoArea(d);
      if (d.substr(a, PAIS.movil_local.length) === PAIS.movil_local) {
        d = d.slice(0, a) + d.slice(a + PAIS.movil_local.length);
        movil = true;
      }
    }
    var ok = d.length === PAIS.largo_nacional && !noGeografico(d);
    return { d: d, movil: movil, ok: ok };
  }

  /* Los dígitos nacionales, sin país ni 9 ni 15 — la clave con la que se
     compara un número contra otro venga como venga escrito. */
  function telNacional(v) {
    var p = telPartes(v);
    return p.d.length === PAIS.largo_nacional ? p.d : (p.d || "");
  }

  function agrupar(resto) {
    if (resto.length <= PAIS.grupo_final) return resto;
    return resto.slice(0, resto.length - PAIS.grupo_final) + "-" + resto.slice(-PAIS.grupo_final);
  }

  /* LA forma canónica, la única que se muestra y la única que se guarda:
       +54 9 11 3547-7302   celular del AMBA
       +54 11 4321-5678     fijo del AMBA
       +54 9 2254 12-3456   celular de Pinamar
       +54 2254 40-1234     fijo de Pinamar
     Lo que no sea un número del país se devuelve como vino: inventarle un
     formato a un número extranjero lo haría ilegible. */
  /* Un campo puede traer VARIOS números ("(02254) 49-1670 / 49-1671"): se
     formatea cada parte, y la que no sea un número completo (la segunda
     comparte el característico) se deja tal cual — inventarle uno sería
     mentir. */
  function telTexto(v) {
    return String(v == null ? "" : v).split("/")
      .map(function (p) { return p.trim(); })
      .filter(Boolean)
      .map(telTextoUno)
      .join(" / ");
  }

  function telTextoUno(v) {
    var p = telPartes(v);
    if (!p.ok) return String(v == null ? "" : v).trim();
    var a = largoArea(p.d);
    return "+" + PAIS.codigo + " " + (p.movil ? PAIS.movil_prefijo + " " : "") +
      p.d.slice(0, a) + " " + agrupar(p.d.slice(a));
  }

  // Un E.164 o un link de WhatsApp son de UN número: con varios, el primero.
  function primero(v) { return String(v == null ? "" : v).split("/")[0].trim(); }

  /* ¿Se puede llamar? (TASK-51, 26.10) Un número del país completo, o uno
     extranjero escrito con su + (o el 00 de salida) y 8 a 15 dígitos. Lo
     demás — «2254» solo, el característico pegado dos veces, un 0800 — no se
     guarda como teléfono. Misma regla que format.py tel_valido. */
  function telValido(v) {
    var s = primero(v);
    if (telPartes(s).ok) return true;
    var d = digits(s);
    var salida = s.charAt(0) === "+" ? d : (d.slice(0, PAIS.salida.length) === PAIS.salida ? d.slice(PAIS.salida.length) : "");
    if (!salida || salida.slice(0, PAIS.codigo.length) === PAIS.codigo) return false;
    return salida.length >= 8 && salida.length <= 15;
  }

  // Para guardar: la misma forma canónica sin separadores (E.164).
  function telE164(v) {
    var p = telPartes(primero(v));
    if (!p.ok) return "";
    return "+" + PAIS.codigo + (p.movil ? PAIS.movil_prefijo : "") + p.d;
  }

  // Lo que va después de `tel:` para el número que muestra telTexto: el
  // E.164 del primero si está completo, si no sus dígitos (un 107, un 0800,
  // uno extranjero conserva su +). El link marca exactamente lo que se ve.
  // Misma regla que format.py tel_href.
  function telHref(v) {
    var uno = primero(v);
    var e = telE164(uno);
    if (e) return e;
    return (uno.charAt(0) === "+" ? "+" : "") + digits(uno);
  }

  // wa.me quiere sólo dígitos con país. El 9 va siempre: es lo que WhatsApp
  // pide para un celular argentino y es lo que es casi todo lo que hay
  // cargado (un fijo simplemente no va a tener cuenta).
  function telWa(v) {
    var uno = primero(v);
    var p = telPartes(uno);
    if (p.ok) return PAIS.codigo + PAIS.movil_prefijo + p.d;
    if (noGeografico(p.d)) return "";        // un 0800 no tiene WhatsApp
    var d = digits(uno);
    return d.length >= 8 ? d : "";           // extranjero: se manda tal cual
  }

  // El texto YA es la forma internacional completa, así que el title no
  // repite el número: sólo dice qué pasa al hacer click.
  function telHtml(v, clase) {
    var txt = telTexto(v);
    if (!txt) return "";
    var wa = telWa(v);
    if (!wa) return esc(txt);
    return '<a class="' + (clase || "lnk") + '" href="https://wa.me/' + esc(wa)
      + '" target="_blank" rel="noopener" title="' + esc(T("nav.whatsapp_title", null, "Escribirle por WhatsApp"))
      + '">' + esc(txt) + "</a>";
  }

  function emailHtml(v, clase) {
    var e = String(v == null ? "" : v).trim();
    if (!e) return "";
    return '<a class="' + (clase || "lnk") + '" href="mailto:' + esc(e) + '">' + esc(e) + "</a>";
  }

  // Un `contacto` puede ser un mail o un teléfono: es UN campo en `usuarios`.
  function contactoHtml(v, clase) {
    var s = String(v == null ? "" : v).trim();
    if (!s) return "";
    return s.indexOf("@") !== -1 ? emailHtml(s, clase) : telHtml(s, clase);
  }

  /* Un contacto en una LISTA no se escribe: es un icono (responsable del producto, 2026-11-03:
     «¿para qué escribiste los teléfonos y mails a todos? ¿para copiarlos de
     la pantalla? Hacé iconos que abran el cliente de mail o de llamadas, y
     que se pueda copiar, con el botón derecho»). Click: tel: / mailto:. Botón
     derecho (en el teléfono, mantener apretado): copia el valor y dice
     «Copiado». El valor y de quién es van en el globo. Los estilos y el
     manejador se instalan una vez, cuando se dibuja el primer icono. */
  var ICONOS = { tel: "/static/icons/phone-sf-regular.svg", mail: "/static/icons/email-sf-regular.svg", wa: "/static/icons/whatsapp-sf-regular.svg" };
  var contactoListo = false;

  function copiarContacto(a) {
    var v = a.getAttribute("data-copy") || "";
    if (!v) return;
    var ok = function () {
      var fn = typeof window !== "undefined" && window.toast;
      if (typeof fn === "function") fn(T("admin.queue.contacto.copiado", null, "Copiado"));
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(v).then(ok, function () {}); }
    } catch (e) { /* sin portapapeles: no hay nada que copiar */ }
  }

  function instalarContacto() {
    if (contactoListo || typeof document === "undefined") return;
    contactoListo = true;
    var st = document.createElement("style");
    st.textContent =
      ".cnt-ico-link,.cnt-wa-link{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;margin:-4px 0;border-radius:8px;color:var(--text-2);vertical-align:middle;-webkit-touch-callout:none;}" +
      ".cnt-ico-link:hover,.cnt-wa-link:hover{background:var(--hover-bg);color:var(--accent);}" +
      ".cnt-ico{display:block;width:20px;height:20px;background:currentColor;-webkit-mask:var(--cnt-ico) center/contain no-repeat;mask:var(--cnt-ico) center/contain no-repeat;}" +
      "@media(max-width:640px),(pointer:coarse){.cnt-ico-link,.cnt-wa-link{width:44px;height:44px;margin:-10px 0;}}";
    document.head.appendChild(st);
    document.addEventListener("contextmenu", function (e) {
      var a = e.target && e.target.closest && e.target.closest("a[data-copy]");
      if (!a) return;
      e.preventDefault();
      copiarContacto(a);
    });
    var timer = 0, largo = null;
    document.addEventListener("touchstart", function (e) {
      var a = e.target && e.target.closest && e.target.closest("a[data-copy]");
      if (!a) return;
      clearTimeout(timer);
      timer = setTimeout(function () { largo = a; copiarContacto(a); }, 550);
    }, { passive: true });
    var cancelar = function () { clearTimeout(timer); };
    document.addEventListener("touchend", cancelar);
    document.addEventListener("touchmove", cancelar, { passive: true });
    document.addEventListener("click", function (e) {
      var a = e.target && e.target.closest && e.target.closest("a[data-copy]");
      if (a && a === largo) { largo = null; e.preventDefault(); }
    }, true);
  }

  // `v` es un mail o un teléfono; `nota`, de quién es o de dónde salió.
  function contactoIconoHtml(v, nota) {
    var s = String(v == null ? "" : v).trim();
    if (!s) return "";
    var mail = s.indexOf("@") !== -1, valor, href;
    if (mail) { valor = s; href = "mailto:" + s; }
    else {
      valor = telTexto(s);
      if (!valor) return "";
      href = "tel:" + telHref(s);
    }
    instalarContacto();
    var titulo = nota ? valor + " — " + nota : valor;
    var out = '<a class="cnt-ico-link" href="' + esc(href) + '" data-copy="' + esc(valor) + '" title="' + esc(titulo)
      + '" aria-label="' + esc(titulo) + '"><span class="cnt-ico" style="--cnt-ico:url(' + (mail ? ICONOS.mail : ICONOS.tel)
      + ')" aria-hidden="true"></span></a>';
    // Un teléfono con WhatsApp lleva al lado el icono de WhatsApp (responsable del producto,
    // 2026-11-04: «los contactos son casi todos WhatsApp»): wa.me al mismo
    // número, el mismo globo y la misma copia con el botón derecho.
    var wa = mail ? "" : telWa(s);
    if (wa) {
      var tituloWa = T("nav.whatsapp_title", null, "Escribirle por WhatsApp") + " — " + titulo;
      out += '<a class="cnt-wa-link" href="https://wa.me/' + esc(wa) + '" target="_blank" rel="noopener" data-copy="' + esc(valor)
        + '" title="' + esc(tituloWa) + '" aria-label="' + esc(tituloWa) + '"><span class="cnt-ico" style="--cnt-ico:url(' + ICONOS.wa
        + ')" aria-hidden="true"></span></a>';
    }
    return out;
  }

  // El contacto de un paciente en las pantallas del médico, con DE QUIÉN es
  // (problems.md №4, 2026-10-23): el servidor decide cuál es y lo marca
  // (core/patient_contact.py: contacto_de = solicitud | paciente |
  // responsable); acá sólo se escribe, igual en «Mis turnos» y «Mis pacientes».
  // Principal: el teléfono de la solicitud. Si la cuenta tiene OTRO número
  // (`contacto_cuenta`), va un segundo icono: el médico ve los dos y la
  // cuenta no se pisa (responsable del producto, hub, 2026-10-23). La procedencia ya no es una
  // línea de texto: va en el globo de cada icono.
  function contactoQuienHtml(p) {
    var principal = p && p.contacto;
    if (!contactoIconoHtml(principal)) return "";
    var de = p.contacto_de, quien = "";
    if (de === "paciente") quien = T("admin.queue.contacto.de_paciente", null, "Del paciente");
    else if (de === "responsable") quien = p.contacto_responsable
      ? T("admin.queue.contacto.de_responsable_nombre", { nombre: p.contacto_responsable }, "De quien sacó el turno: {nombre}")
      : T("admin.queue.contacto.de_responsable", null, "De quien sacó el turno");
    else if (de === "solicitud") quien = T("admin.queue.contacto.de_solicitud", null, "Dado al pedir el turno");
    var out = contactoIconoHtml(principal, quien);
    var cuenta = "";
    if (p.contacto_cuenta) {
      cuenta = p.contacto_cuenta_de === "responsable"
        ? (p.contacto_responsable
          ? T("admin.queue.contacto.cuenta_responsable_nombre", { nombre: p.contacto_responsable }, "En la cuenta de {nombre}, quien sacó el turno")
          : T("admin.queue.contacto.cuenta_responsable", null, "En la cuenta de quien sacó el turno"))
        : T("admin.queue.contacto.cuenta_paciente", null, "En la cuenta del paciente");
    }
    return out + contactoIconoHtml(p.contacto_cuenta, cuenta);
  }

  /* ---- nombres de personas -------------------------------------------------
     UN nombre se escribe SIEMPRE en capitalización normal (responsable del producto, 2026-08-01:
     "don't capitalize the names, write them everywhere normally"). "ALEJO
     VARELA" gritado en una tarjeta es ruido: viene así del HSI y de lo que
     la mesa tipea con Bloq Mayús, no es cómo se llama la persona.

     Sólo se RE-ESCRIBE lo que está entero en mayúsculas o entero en
     minúsculas. Un nombre que ya viene mezclado se deja intacto: "Hugo De
     Montes", "María del Carmen O'Higgins" y "van der Berg" los cargó alguien
     que sabe cómo se escriben, y adivinar arriba de eso rompe apellidos
     reales. Las partículas (de, del, la, van, von…) van en minúscula salvo
     que abran el nombre; los guiones y apóstrofes capitalizan la parte que
     sigue ("Ana-María", "O'Brien"), y los títulos (Dr./Dra./Lic.) se
     conservan. Espejo exacto de format.py:person_name — el test de paridad
     compara las dos. */
  var NOM_PARTICULAS = {
    de: 1, del: 1, "de'": 1, la: 1, las: 1, los: 1, y: 1, e: 1, da: 1, das: 1,
    do: 1, dos: 1, di: 1, du: 1, van: 1, von: 1, der: 1, den: 1, ter: 1, le: 1,
    "l'": 1, san: 0, santa: 0
  };
  var NOM_TITULOS = { "dr.": "Dr.", "dra.": "Dra.", "lic.": "Lic.", "prof.": "Prof.", "esp.": "Esp." };

  function nomCapPieza(p) {
    if (!p) return p;
    // Un token con dígitos o sin letras (un DNI colado, "3°") se deja como está.
    if (!/[a-záéíóúüñç]/i.test(p)) return p;
    return p.charAt(0).toUpperCase() + p.slice(1).toLowerCase();
  }

  function nomCapPalabra(w) {
    // Capitaliza cada parte separada por guion o apóstrofe: O'Brien, Ana-María.
    return w.split(/([-'’])/).map(function (t, i) {
      return i % 2 ? t : nomCapPieza(t);
    }).join("");
  }

  function nombre(v) {
    var s = String(v == null ? "" : v).trim().replace(/\s+/g, " ");
    if (!s) return "";
    var palabras = s.split(" ");
    // ¿Mezclado ya? Se respeta tal cual. El título no vota: "Dr. DIEGO CORONEL"
    // es un nombre gritado con el título bien escrito adelante.
    var letras = palabras.filter(function (w) { return !NOM_TITULOS[w.toLowerCase()]; })
      .join(" ").replace(/[^a-záéíóúüñçA-ZÁÉÍÓÚÜÑÇ]/g, "");
    if (!letras) return s;
    var todoAlta = letras === letras.toUpperCase();
    var todoBaja = letras === letras.toLowerCase();
    if (!todoAlta && !todoBaja) return s;
    return palabras.map(function (w, i) {
      var k = w.toLowerCase();
      if (NOM_TITULOS[k]) return NOM_TITULOS[k];
      if (i > 0 && i < palabras.length - 1 && NOM_PARTICULAS[k] === 1) return k;
      return nomCapPalabra(w);
    }).join(" ");
  }

  /* El DNI se escribe con puntos de miles (30.123.456), igual en el panel y
     en la cuenta del paciente. Extraído de admin-app.js (2026-08-31): era la
     única pieza del formateo de personas que el panel no resolvía por acá,
     y las pantallas server-routed sin admin-app.js lo mostraban pelado.
     Sólo dígitos; vacío o basura queda vacío, nunca "0". */
  function dni(v) {
    var d = digits(v);
    return d ? d.replace(/\B(?=(\d{3})+(?!\d))/g, ".") : "";
  }

  /* El DNI que se muestra se COPIA (responsable del producto, 2026-11-04: «DNI, al copiarlo, en
     todo el sitio: con el mouse, un iconito de copiar al lado al pasar por
     encima; en el teléfono, nada aparece: tocás y dice "Copiado"»). UNA
     función para todas las pantallas: devuelve el DNI con puntos dentro de un
     elemento que lleva los dígitos pelados en `data-copy-dni` (lo que se
     pega en HSI o en un buscador). Un click o Enter/Espacio copia y avisa; el
     iconito (Icons8 sf-regular) sólo existe donde hay mouse (hover:hover) y
     aparece al pasar o enfocar. El manejador de documento se instala una vez.
     Dentro de un link o un botón NO va: ahí el click ya es de otro. */
  var COPIA_DNI_ICONO = "/static/icons/copy-sf-regular.svg";
  var copiaDniLista = false;

  function copiarTexto(valor, avisar) {
    var listo = function () { if (avisar) avisar(); };
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(valor).then(listo, function () {});
        return;
      }
    } catch (e) { /* cae al respaldo */ }
    try {
      var ta = document.createElement("textarea");
      ta.value = valor; ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
      document.body.appendChild(ta); ta.select();
      var ok = document.execCommand && document.execCommand("copy");
      document.body.removeChild(ta);
      if (ok) listo();
    } catch (e2) { /* sin portapapeles: no hay nada que copiar */ }
  }

  function copiarDniEn(el) {
    var v = el && el.getAttribute("data-copy-dni");
    if (!v) return;
    copiarTexto(v, function () {
      var msg = T("admin.queue.contacto.copiado", null, "Copiado");
      var fn = typeof window !== "undefined" && window.toast;
      if (typeof fn === "function") { fn(msg); return; }
      // El sitio del paciente no tiene el aviso del panel: uno mínimo, propio.
      if (typeof document === "undefined") return;
      var viejo = document.getElementById("dni-cp-aviso");
      if (viejo) viejo.remove();
      var av = document.createElement("div");
      av.id = "dni-cp-aviso"; av.setAttribute("role", "status");
      av.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:2147483000;background:var(--text-1,#1d2733);color:var(--surface,#fff);font:500 15px/1.2 system-ui,sans-serif;padding:10px 16px;border-radius:10px;";
      av.textContent = msg;
      document.body.appendChild(av);
      setTimeout(function () { if (av.parentNode) av.remove(); }, 1800);
    });
  }

  function instalarCopiaDni() {
    if (copiaDniLista || typeof document === "undefined") return;
    copiaDniLista = true;
    var st = document.createElement("style");
    st.textContent =
      ".dni-cp{cursor:pointer;white-space:nowrap;-webkit-tap-highlight-color:transparent;}" +
      ".dni-cp-ico{display:none;}" +
      "@media(hover:hover) and (pointer:fine){" +
      // Dibujado del tamaño de los de teléfono y WhatsApp que van al lado
      // (.cnt-ico, 20px), en una caja de 14px: el dibujo de 20px deja 3-4px
      // vacíos en cada borde, así que el ícono entero cabe en 14x14 y la fila
      // ocupa lo mismo que antes (4 + 14 = 18px). Ninguna tabla se ensancha
      // por el DNI (con una caja de 20px la columna Asistencia de la cola
      // quedaba 3px afuera a 1280).
      ".dni-cp-ico{display:inline-block;width:14px;height:14px;margin-left:4px;vertical-align:-2px;background:currentColor;opacity:0;" +
      "-webkit-mask:url(" + COPIA_DNI_ICONO + ") center/20px 20px no-repeat;mask:url(" + COPIA_DNI_ICONO + ") center/20px 20px no-repeat;}" +
      ".dni-cp:hover .dni-cp-ico,.dni-cp:focus-visible .dni-cp-ico{opacity:.7;}" +
      ".dni-cp:hover{color:var(--accent);}}";
    document.head.appendChild(st);
    document.addEventListener("click", function (e) {
      var el = e.target && e.target.closest && e.target.closest("[data-copy-dni]");
      if (el) copiarDniEn(el);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var el = e.target && e.target.closest && e.target.closest("[data-copy-dni]");
      if (!el) return;
      e.preventDefault();
      copiarDniEn(el);
    });
  }

  // `prefijo`: texto que va antes del número, dentro de lo que se copia al tocar («DNI »).
  function dniHtml(v, prefijo) {
    var txt = dni(v);
    if (!txt) return "";
    instalarCopiaDni();
    var titulo = T("admin.queue.contacto.copiar_dni", null, "Copiar DNI");
    return '<span class="dni-cp" role="button" tabindex="0" data-copy-dni="' + esc(digits(v)) + '" title="' + esc(titulo)
      + '" aria-label="' + esc(titulo + " " + txt) + '">' + esc((prefijo || "") + txt)
      + '<span class="dni-cp-ico" aria-hidden="true"></span></span>';
  }

  // ---- fechas y edad ------------------------------------------------------

  var MES3 = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
              "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

  /* "14 Mar 2015". Con el mes en número, 05/05 y 11/08 en la misma columna
     obligan a decidir cuál es cuál en cada fila. */
  function fechaCorta(iso) {
    if (!iso) return "";
    var p = String(iso).slice(0, 10).split("-");
    if (p.length !== 3) return String(iso);
    var m = MES3[+p[1] - 1];
    return m ? +p[2] + " " + m + " " + p[0] : String(iso);
  }

  // `hoy` (opcional, "AAAA-MM-DD") fija el día de hoy para las pruebas; sin él,
  // es el reloj del navegador.
  function ahora(hoy) {
    return hoy ? new Date(String(hoy).slice(0, 10) + "T00:00:00") : new Date();
  }

  function edad(iso, hoy) {
    if (!iso) return null;
    var b = new Date(String(iso).slice(0, 10) + "T00:00:00");
    if (isNaN(b)) return null;
    var n = ahora(hoy), a = n.getFullYear() - b.getFullYear();
    var m = n.getMonth() - b.getMonth();
    if (m < 0 || (m === 0 && n.getDate() < b.getDate())) a--;
    return a >= 0 && a < 130 ? a : null;
  }

  // Un bebé no tiene "0 años": tiene meses, y en pediatría esa diferencia es
  // el dato. Sólo abajo del año — de ahí para arriba el número pelado alcanza.
  function edadTexto(iso, hoy) {
    var a = edad(iso, hoy);
    if (a == null) return "";
    if (a > 0) return String(a);
    var b = new Date(String(iso).slice(0, 10) + "T00:00:00"), n = ahora(hoy);
    var meses = (n.getFullYear() - b.getFullYear()) * 12 + (n.getMonth() - b.getMonth());
    if (n.getDate() < b.getDate()) meses--;
    return meses <= 0 ? "recién nacido" : meses + (meses === 1 ? " mes" : " meses");
  }

  /* Quién resolvió una solicitud (solicitudes.resuelta_por, hospital 2026-11-13).
     La base guarda «auto:web», «mesa:<usuario>» o «medico:<usuario>»; al
     humano se le muestra una frase. `nombreResolutor` es el nombre del médico
     si el servidor lo conoce (la mesa no tiene nombre propio), si no el usuario;
     `corto` es la versión de la columna angosta. Un prefijo desconocido se
     muestra sin prefijo; vacío, nada. Espejo de format.py:resolved_by_text. */
  function resueltaPor(valor, nombreResolutor, corto) {
    var v = String(valor == null ? "" : valor).trim();
    if (!v) return "";
    var i = v.indexOf(":");
    var pref = i < 0 ? "" : v.slice(0, i).toLowerCase();
    var resto = i < 0 ? v : v.slice(i + 1);
    var quien = nombreResolutor ? nombre(nombreResolutor) : resto;
    if (pref === "auto") {
      if (resto === "web") {
        return corto
          ? T("admin.queue.resuelta_por.auto_web_corto", null, "Automática (web)")
          : T("admin.queue.resuelta_por.auto_web", null, "Automática: el paciente sacó el turno en la página");
      }
      return T("admin.queue.resuelta_por.auto", null, "Automática");
    }
    if (pref === "mesa") return T("admin.queue.resuelta_por.mesa", { nombre: quien }, "Mesa de entrada · {nombre}");
    if (pref === "medico") return T("admin.queue.resuelta_por.medico", { nombre: quien }, "Médico · {nombre}");
    return resto;
  }

  // La persona como la escribe la gente: "Ilya Belov, 48" (responsable del producto, 2026-11-02:
  // «nombre, apellido, coma, edad… no escribir "edad" en cada fila»). Una sola
  // función para cada lista de la consola; sin fecha de nacimiento no hay
  // coma colgando. Espejo de format.py:person_age.
  function personaEdad(nombrePila, apellido, nacimiento, hoy) {
    var n = nombre([nombrePila, apellido].filter(Boolean).join(" "));
    var e = edadTexto(nacimiento, hoy);
    return n && e ? n + ", " + e : n;
  }

  /* Con qué médico(s) tiene turnos una persona (responsable del producto, 2026-11-03: «el más
     popular, al que más va, y más uno, más dos»). `lista` = [{nombre, n}],
     como la manda el servidor. Un médico: su nombre. Varios: el de más
     turnos (a igual cantidad, el primero por nombre) y «+N» por los demás;
     `medicosTitulo` los nombra a todos para el tooltip. Espejo de
     format.py:doctors_text / doctors_title. */
  function medicosOrden(lista) {
    return (lista || []).filter(function (m) { return m && m.nombre; })
      .slice().sort(function (a, b) {
        var d = (b.n || 0) - (a.n || 0);
        return d || (a.nombre < b.nombre ? -1 : a.nombre > b.nombre ? 1 : 0);
      });
  }

  function medicosTexto(lista) {
    var o = medicosOrden(lista);
    if (!o.length) return "";
    return o.length === 1 ? o[0].nombre : o[0].nombre + " +" + (o.length - 1);
  }

  function medicosTitulo(lista) {
    return medicosOrden(lista).map(function (m) { return m.nombre; }).join(", ");
  }

  // Los nombres, en ese mismo orden, para la lista que muestra cuantos entran
  // en la línea y deja el «+N» sólo para los que no caben (responsable del producto, 2026-11-04).
  function medicosLista(lista) {
    return medicosOrden(lista).map(function (m) { return m.nombre; });
  }

  /* Every number and percentage the panel and its reports print
     (ui-expert 2026-11-15: «4188 de 10238» beside «4.185»; «41 %», «49,0 %»
     and «69,2 %» on one screen). numero(): thousands always grouped, at most
     `decimales` decimals (1 by default), never a trailing zero — «1.466 h»,
     «44,5 h». porcentaje(): «52%», a decimal only under 10 or just short of
     100. The implementation is i18n.mjs formatNumber()/formatPercent(),
     mirrored by format.py number()/percent(). */
  function numero(n, decimales) { return I18n.formatNumber(n, decimales == null ? 1 : decimales); }
  function porcentaje(n) { return I18n.formatPercent(n); }

var api = {
  numero: numero,
  porcentaje: porcentaje,
  scheduleBand: scheduleBand,
  medicosLista: medicosLista,
  medicosTexto: medicosTexto,
  medicosTitulo: medicosTitulo,
  telNacional: telNacional,
  telTexto: telTexto,
  telE164: telE164,
  telHref: telHref,
  telValido: telValido,
  telPartes: telPartes,
  telWa: telWa,
  telHtml: telHtml,
  emailHtml: emailHtml,
  contactoHtml: contactoHtml,
  contactoIconoHtml: contactoIconoHtml,
  contactoQuienHtml: contactoQuienHtml,
  nombre: nombre,
  dni: dni,
  dniHtml: dniHtml,
  digits: digits,
  fechaCorta: fechaCorta,
  edad: edad,
  edadTexto: edadTexto,
  personaEdad: personaEdad,
  resueltaPor: resueltaPor
};

function scheduleBand(band) {
  const keys=['sun','mon','tue','wed','thu','fri','sat'], labels=['dom','lun','mar','mié','jue','vie','sáb'];
  const selected=[1,2,3,4,5,6,0].filter(d=>(band.dias || []).includes(d)).map(d=>{const label=T('date.day_short.'+keys[d],null,labels[d]);return I18n.activeLang()==='es'?label.toLowerCase():label;});
  if(!selected.length)return '';
  const joined=selected.length>1 ? T('admin.doctor.days_join',{first:selected.slice(0,-1).join(', '),last:selected.at(-1)},'{first} y {last}') : selected[0];
  return T('admin.doctor.schedule_line',{days:joined[0].toUpperCase()+joined.slice(1),from:band.desde || '',to:band.hasta || '',minutes:band.cada_min || 20},'{days}, {from} a {to} · turnos de {minutes} min');
}


export {
  numero,
  porcentaje,
  scheduleBand,
  medicosLista,
  medicosTexto,
  medicosTitulo,
  telNacional,
  telTexto,
  telE164,
  telHref,
  telValido,
  telPartes,
  telWa,
  telHtml,
  emailHtml,
  contactoHtml,
  contactoIconoHtml,
  contactoQuienHtml,
  nombre,
  dni,
  dniHtml,
  digits,
  fechaCorta,
  edad,
  edadTexto,
  personaEdad,
  resueltaPor
};
export default api;
