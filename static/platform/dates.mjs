/* static/platform/dates.mjs — canonical ES module for Human Rounds date formatting.
   Browser and Node consumers import the same named functions. There is no
   compatibility global and no CommonJS wrapper.

   Regla (wizard-design, responsable del producto 2026-07-10): toda fecha/hora que lee una persona
   se escribe como la diría una persona —
     hoy      -> "Hoy, 14:40"
     mañana   -> "Mañana, miércoles 11, 14:40"  (con su día: «Mañana» solo
                 sobre las horas de la mañana se lee «a la mañana», 2026-11-10)
     resto   -> "Martes 14 de julio, 14:40"  (con año sólo si no es el actual)
   Nunca "mar 14/07 14:40" ni ISO. El cuadradito de calendario (dow/day/mon
   gráfico de listado.js) queda exento — esto es para fechas EN TEXTO.

   Multi-idioma (responsable del producto, 2026-08-04): día/mes/"Hoy"/"Mañana" salen del catálogo
   vía T() — inglés no es castellano con las palabras cambiadas, así que el
   ARMADO de la frase (orden de las piezas, "de" o no, mayúscula de mes) es
   también una clave de catálogo (`date.long`/`date.long_year`), no
   concatenación fija. Sin `window.SanaI18n` (node --test, o el panel admin
   que no inyecta catálogo) el fallback es exactamente el texto de siempre.

   API:
     amigable(diaIso, hora?, now?) -> "Mañana, miércoles 11, 14:40" | "" si el día no parsea
     diaFechado(diaIso, now?)      -> la parte de fecha de amigable() ("Hoy",
                                      "Mañana, miércoles 11", "Martes 14 de julio")
     corta(diaIso, hora?, now?)    -> "Hoy, 14:40" | "Mié 29 jul, 10:00" — versión
                                      abreviada para CONTROLES del admin (combos),
                                      donde la fecha entera no entra
     dia(diaIso, now?)             -> la palabra sola ("Mañana", "Martes 14 de julio")
                                      para comparar o recortar, no para escribir
     larga(diaIso, hora?, now?)    -> SIEMPRE absoluta ("Martes 14 de julio, 14:40",
                                      nunca "Hoy"/"Mañana") — para texto que se
                                      GUARDA y se lee días después (preferencia de
                                      turno, admin), donde lo relativo se pudre.
     delServidor(iso, now?)        -> amigable() de un datetime ISO del server, con
                                      SU reloj (el de la instalación), nunca el
                                      del navegador
     instanteServidor(iso, now?)   -> "hace 5 minutos" while fresh, then
                                      delServidor() — an instant the server
                                      reports, never on the reader's clock
   `now` es inyectable para tests (Date).

   "Hoy" y "Mañana" se cuentan en el huso de la INSTALACIÓN (America/Argentina/
   Buenos_Aires, el mismo valor que admin-queue.mjs), nunca con los getDate()
   del navegador: una compu en UTC o en otro país, cerca de medianoche,
   rotulaba "Hoy" un turno de mañana (y al revés). relativo() queda en la hora
   del navegador a propósito: arma el día con los componentes locales del
   timestamp, así que también compara contra el "hoy" local. */
"use strict";

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

  // T() sólo sustituye {vars} cuando hay un SanaI18n real cargado (i18n.js
  // hace el applyVars adentro de i18n.t()); en `node --test` no hay `window`
  // en absoluto, así que la rama tonta de T() devuelve el fallback TAL CUAL.
  // Por eso el ARMADO (day/n/month/year) siempre se pide con vars=null —
  // trae la plantilla cruda en los dos casos — y esta función hace la
  // sustitución acá mismo, así el resultado es el mismo con o sin catálogo.
  function fmt(template, vars) {
    var out = template;
    for (var k in vars) {
      if (Object.prototype.hasOwnProperty.call(vars, k)) {
        out = out.split("{" + k + "}").join(String(vars[k]));
      }
    }
    return out;
  }

  // Nombres CANÓNICOS (castellano) — quedan como fallback de T() y como base
  // de los índices (getDay()/getMonth()), nunca como el texto final si hay
  // catálogo cargado.
  var DIAS = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"];
  var MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  var DIAS_CORTOS = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];
  var MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic"];
  // getDay(): 0=domingo … 6=sábado — mismo orden que las claves date.day.*
  var DAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

  function dayName(getDayIndex) {
    return T("date.day." + DAY_KEYS[getDayIndex], null, DIAS[getDayIndex]);
  }

  function dayShort(getDayIndex) {
    return T("date.day_short." + DAY_KEYS[getDayIndex], null, DIAS_CORTOS[getDayIndex]);
  }

  function monthFull(monthIndex) { // 0-11
    return T("date.month_full." + (monthIndex + 1), null, MESES[monthIndex]);
  }

  function monthShort(monthIndex) { // 0-11
    return T("date.month." + (monthIndex + 1), null, MESES_CORTOS[monthIndex]);
  }

  // "2026-07-14" -> Date local (mediodía, para esquivar cambios de huso). null si no parsea.
  function parseIso(diaIso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(diaIso == null ? "" : diaIso));
    if (!m) return null;
    var d = new Date(+m[1], +m[2] - 1, +m[3], 12, 0, 0);
    return isNaN(d.getTime()) ? null : d;
  }

  // El huso de la instalación (core/tz.py). Mismo literal que admin-queue.mjs:
  // el cliente no recibe el nombre del server.
  var HUSO = "America/Argentina/Buenos_Aires";

  // "Hoy" de la instalación como Date local al mediodía (mismo formato que
  // parseIso, así sameDay/getDate/getFullYear comparan el mismo calendario).
  // Si Intl no resuelve el huso cae al reloj del navegador, como antes.
  function hoyInstalacion(now) {
    var t = now instanceof Date ? now : new Date();
    try {
      var parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: HUSO, year: "numeric", month: "2-digit", day: "2-digit"
      }).formatToParts(t);
      var v = {};
      for (var i = 0; i < parts.length; i++) v[parts[i].type] = +parts[i].value;
      if (v.year && v.month && v.day) return new Date(v.year, v.month - 1, v.day, 12, 0, 0);
    } catch (e) { /* sin Intl: reloj del navegador */ }
    return new Date(t.getTime());
  }

  // "Hoy" de la instalación como "YYYY-MM-DD" (lo que la mesa ofrece como día de hoy).
  function hoyIsoInstalacion(now) {
    var d = hoyInstalacion(now), p = function (n) { return String(n).length < 2 ? "0" + n : "" + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  // El reloj de pared de la instalación en este instante como Date LOCAL: sus
  // getDay()/getHours()/getMinutes()/getDate() leen la hora de Pinamar aunque el
  // navegador esté en otro huso (hours.mjs saca de acá "abierto ahora"). Sólo para leer
  // componentes, nunca para comparar instantes. Sin Intl, el reloj del navegador.
  function ahoraInstalacion(now) {
    var t = now instanceof Date ? now : new Date();
    try {
      var parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: HUSO, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit"
      }).formatToParts(t);
      var v = {};
      for (var i = 0; i < parts.length; i++) v[parts[i].type] = +parts[i].value;
      if (v.year && v.month && v.day) return new Date(v.year, v.month - 1, v.day, v.hour, v.minute, 0);
    } catch (e) { /* sin Intl: reloj del navegador */ }
    return new Date(t.getTime());
  }

  // Desfase de la instalación (ms, positivo al este de UTC) en el instante `ms`;
  // null si Intl no resuelve el huso.
  function desfaseInstalacion(ms) {
    try {
      var parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: HUSO, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit"
      }).formatToParts(new Date(ms));
      var v = {};
      for (var i = 0; i < parts.length; i++) v[parts[i].type] = +parts[i].value;
      return Date.UTC(v.year, v.month - 1, v.day, v.hour, v.minute, v.second) - Math.floor(ms / 1000) * 1000;
    } catch (e) { return null; }
  }

  // conHusoInstalacion("2026-10-26T07:00") -> "2026-10-26T07:00:00-03:00": una hora de
  // pared de la INSTALACIÓN (la agenda, un datetime-local de la mesa) con el desfase de
  // la instalación en ese momento, nunca el del navegador. Con el del navegador una compu
  // en UTC mandaba "07:00+00:00" = las 04:00 en Pinamar. El desfase se toma en el
  // instante resultante (sirve también con horario de verano). Sin Intl, o con un valor
  // que no es fecha-hora, devuelve null y quien llama decide.
  function conHusoInstalacion(local) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(String(local == null ? "" : local));
    if (!m) return null;
    var pared = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
    var off = desfaseInstalacion(pared);
    if (off === null) return null;
    off = desfaseInstalacion(pared - off);
    if (off === null) return null;
    var min = Math.round(off / 60000), a = Math.abs(min);
    var p = function (n) { return String(n).length < 2 ? "0" + n : "" + n; };
    return m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":" + (m[6] || "00")
      + (min >= 0 ? "+" : "-") + p(Math.floor(a / 60)) + ":" + p(a % 60);
  }

  // paredInstalacion("2026-10-27T01:00:00Z") -> {dia: "2026-10-26", hora: "22:00"}: el día y
  // la hora de un instante en el reloj de la INSTALACIÓN, para MOSTRAR un datetime del server
  // (con desfase o Z) sin pasar por los getHours() del navegador. Sin desfase el texto ya es
  // hora de pared de la instalación y se lee literal. null si no parsea.
  function paredInstalacion(iso) {
    var s = String(iso == null ? "" : iso).replace(" ", "T");
    var m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}(:?\d{2})?)?$/.exec(s);
    if (!m) return null;
    if (!m[5]) return { dia: m[1], hora: m[2] };
    // "+00" (Postgres) -> "+00:00": Date no lee el desfase de sólo horas.
    var t = new Date(m[6] || m[5] === "Z" ? s : s + ":00").getTime();
    if (isNaN(t)) return null;
    try {
      var parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: HUSO, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit"
      }).formatToParts(new Date(t));
      var v = {};
      for (var i = 0; i < parts.length; i++) v[parts[i].type] = parts[i].value;
      return { dia: v.year + "-" + v.month + "-" + v.day, hora: v.hour + ":" + v.minute };
    } catch (e) { return null; }
  }

  // El "hoy" del calendario LOCAL del navegador — sólo para relativo().
  function hoyLocal(now) {
    return new Date((now instanceof Date ? now : new Date()).getTime());
  }

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  // Día + mes en palabras, con año si hace falta — la MISMA clave de catálogo
  // que site_schedule._friendly_date, para que el server (SSR) y el cliente
  // (re-render de listing.js) digan exactamente lo mismo en cualquier idioma.
  function longDate(d, hoy) {
    var day = dayName(d.getDay()), n = d.getDate(), month = monthFull(d.getMonth());
    if (d.getFullYear() !== hoy.getFullYear()) {
      var tplY = T("date.long_year", null, "{day} {n} de {month} de {year}");
      return fmt(tplY, { day: day, n: n, month: month, year: d.getFullYear() });
    }
    var tpl = T("date.long", null, "{day} {n} de {month}");
    return fmt(tpl, { day: day, n: n, month: month });
  }

  function diaDesde(diaIso, hoy) {
    var d = parseIso(diaIso);
    if (!d) return "";
    if (sameDay(d, hoy)) return T("date.today", null, "Hoy");
    var man = new Date(hoy.getTime());
    man.setDate(man.getDate() + 1);
    if (sameDay(d, man)) return T("date.tomorrow", null, "Mañana");
    return longDate(d, hoy);
  }

  function dia(diaIso, now) {
    return diaDesde(diaIso, hoyInstalacion(now));
  }

  // diaFechado(diaIso, now?) -> "Hoy" | "Mañana, miércoles 11" | "Jueves 12 de
  // noviembre". Lo que se le ESCRIBE al paciente: un «Mañana» a secas sobre
  // las horas de la mañana se lee como «a la mañana» (plan 2026-11-10), así
  // que mañana siempre lleva su día y su número. dia() sigue devolviendo la
  // palabra sola: hay quien la compara con date.tomorrow o corta su primera
  // palabra para una pastilla. Gemelo: site_schedule._friendly_date.
  function diaFechado(diaIso, now) {
    var base = dia(diaIso, now);
    if (base !== T("date.tomorrow", null, "Mañana")) return base;
    var d = parseIso(diaIso);
    var day = dayName(d.getDay());
    var tpl = T("date.tomorrow_dated", null, "Mañana, {day_lower} {n}");
    return fmt(tpl, { day: day, day_lower: day.charAt(0).toLowerCase() + day.slice(1), n: d.getDate() });
  }

  // corta(diaIso, hora?, now?) -> "Hoy, 12:00" | "Mié 29 jul, 10:00".
  // Misma voz que amigable() pero abreviada, para CONTROLES del admin donde la
  // fecha entera ("Miércoles 29 de julio, 10:00") obliga a un combo enorme y
  // empuja Aprobar/Rechazar a otra línea (responsable del producto, 2026-07-22). Es para el panel
  // interno: al paciente se le sigue escribiendo la fecha completa. Sin "de"
  // entre las piezas en NINGÚN idioma (siempre fue así), así que no hace
  // falta una clave de armado — sólo traducir día y mes.
  function corta(diaIso, hora, now) {
    var d = parseIso(diaIso);
    if (!d) return "";
    var hoy = hoyInstalacion(now);
    var man = new Date(hoy.getTime());
    man.setDate(man.getDate() + 1);
    var base;
    if (sameDay(d, hoy)) base = T("date.today", null, "Hoy");
    else if (sameDay(d, man)) base = T("date.tomorrow", null, "Mañana");
    else {
      base = dayShort(d.getDay()) + " " + d.getDate() + " " + monthShort(d.getMonth());
      if (d.getFullYear() !== hoy.getFullYear()) base += " " + d.getFullYear();
    }
    var h = String(hora == null ? "" : hora).trim();
    return h ? base + ", " + h : base;
  }

  // "dom 8 nov" always, never "Hoy": for a control that already says «Hoy»
  // next to the date (the product owner, 2026-11-08: «Hoy» once on the day row).
  function cortaFija(diaIso, now) {
    var d = parseIso(diaIso);
    if (!d) return "";
    var base = dayShort(d.getDay()) + " " + d.getDate() + " " + monthShort(d.getMonth());
    if (d.getFullYear() !== hoyInstalacion(now).getFullYear()) base += " " + d.getFullYear();
    return base;
  }

  function amigable(diaIso, hora, now) {
    var base = diaFechado(diaIso, now);
    if (!base) return "";
    var h = String(hora == null ? "" : hora).trim();
    return h ? base + ", " + h : base;
  }

  // pasada(diaIso, now?) -> "2 de octubre" | "2 de octubre de 2025": la fecha de
  // algo que YA pasó, sin día de la semana ni hora (responsable del producto, 2026-11-04: «para los
  // pasados sin hora, simplemente "2 de octubre"»); con año sólo si no es el
  // actual. Mismo armado que nacimiento(): una clave de catálogo por forma.
  // Lo de HOY que ya pasó dice «Hoy», como corta() y como el turno de al lado
  // que todavía no llegó («Hoy, 17:00»): una misma fecha no se escribe de dos
  // maneras según el ancho de la pantalla.
  function pasada(diaIso, now) {
    var d = parseIso(diaIso);
    if (!d) return "";
    var hoy = hoyInstalacion(now);
    if (sameDay(d, hoy)) return T("date.today", null, "Hoy");
    if (d.getFullYear() !== hoy.getFullYear()) {
      return fmt(T("date.past_year", null, "{n} de {month} de {year}"),
        { n: d.getDate(), month: monthFull(d.getMonth()), year: d.getFullYear() });
    }
    return fmt(T("date.past", null, "{n} de {month}"), { n: d.getDate(), month: monthFull(d.getMonth()) });
  }

  function larga(diaIso, hora, now) {
    var d = parseIso(diaIso);
    if (!d) return "";
    var base = longDate(d, hoyInstalacion(now));
    var h = String(hora == null ? "" : hora).trim();
    return h ? base + ", " + h : base;
  }

  // relativo(iso, now) — "recién" / "hace 20 minutos" / "hace 3 horas" y, más
  // atrás, la fecha amigable con hora ("Hoy, 14:09" / "Martes 14 de julio,
  // 14:09"). Para timestamps que se LEEN al momento (ej: "Solicitado …"), no
  // para fechas que se guardan y se releen días después (esas usan larga()).
  // El orden de las palabras ("hace 20 minutos" vs "20 minutes ago") es
  // distinto por idioma, así que cada tramo es su propia clave de catálogo
  // (una para singular, una para plural — inglés no necesita más).
  function relativo(iso, now) {
    var d = new Date(String(iso == null ? "" : iso).replace(" ", "T"));
    if (isNaN(d.getTime())) return "";
    var ahora = now instanceof Date ? now : new Date();
    var diff = Math.round((ahora.getTime() - d.getTime()) / 1000);
    if (diff < 0) diff = 0;
    if (diff < 45) return T("date.just_now", null, "recién");
    if (diff < 5400) {
      var m = Math.max(1, Math.round(diff / 60));
      return Tn("date.minutes_ago", m, { m: m }, "hace {n} minuto", "hace {m} minutos");
    }
    if (diff < 6 * 3600) {
      var h = Math.round(diff / 3600);
      return Tn("date.hours_ago", h, { h: h }, "hace {n} hora", "hace {h} horas");
    }
    var p = function (n) { return String(n).length < 2 ? "0" + n : "" + n; };
    var diaIso = d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
    var base = diaDesde(diaIso, hoyLocal(ahora));
    var hm = p(d.getHours()) + ":" + p(d.getMinutes());
    return base ? base + ", " + hm : "";
  }

  // delServidor(iso, now) -> amigable() de un datetime ISO que mandó el SERVER
  // ("2026-10-25T09:00:00-03:00"). El server ya lo escribe en el huso de la
  // instalación (tz.py), así que el día y la hora se leen literales del texto
  // y nunca pasan por Date: una compu del médico en UTC leía 12:00 para un
  // turno de las 09:00. NO sirve para un "Z"/UTC armado en el cliente
  // (toISOString) — eso sigue yendo por relativo(). "" si no parsea.
  function delServidor(iso, now) {
    var m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(String(iso == null ? "" : iso));
    return m ? amigable(m[1], m[2], now) : "";
  }

  // instanteServidor(iso, now) — an instant the SERVER reports with the
  // installation's offset («the last transmission to HSI»): «hace 5 minutos»
  // while it is fresh — a difference between two instants, the same on every
  // clock — and from six hours on the installation's own day and hour
  // (delServidor), where relativo() would print the reader's: a hospital
  // administrator abroad read «Hoy, 17:09» for a transmission of 14:09.
  function instanteServidor(iso, now) {
    var d = new Date(String(iso == null ? "" : iso).replace(" ", "T"));
    if (isNaN(d.getTime())) return "";
    var ahora = now instanceof Date ? now : new Date();
    return ahora.getTime() - d.getTime() < 6 * 3600 * 1000 ? relativo(iso, ahora) : delServidor(iso, ahora);
  }

  // nacimiento(iso) -> "28 de octubre de 1978". El eco de una fecha que la
  // persona TIPEÓ se escribe en palabras (responsable del producto, 2026-07-12): con "28/10/1978"
  // sigue teniendo que adivinar cuál número es el mes. Sin día de la semana:
  // en una fecha de nacimiento no aporta nada.
  function nacimiento(diaIso) {
    var d = parseIso(diaIso);
    if (!d) return "";
    var tpl = T("date.birth", null, "{n} de {month} de {year}");
    return fmt(tpl, { n: d.getDate(), month: monthFull(d.getMonth()), year: d.getFullYear() });
  }

export { amigable, corta, cortaFija, dia, diaFechado, larga, pasada, relativo, delServidor, instanteServidor, nacimiento,
  hoyIsoInstalacion, conHusoInstalacion, paredInstalacion, ahoraInstalacion };
export default { amigable, corta, cortaFija, dia, diaFechado, larga, pasada, relativo, delServidor, instanteServidor,
  nacimiento, hoyIsoInstalacion, conHusoInstalacion, paredInstalacion, ahoraInstalacion };
