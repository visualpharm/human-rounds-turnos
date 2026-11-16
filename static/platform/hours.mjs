/* static/platform/hours.mjs — canonical ES module for Human Rounds schedule parsing.
   No compatibility global and no CommonJS wrapper. Browser and Node callers
   import the same named functions from this file.

   API:
     parse(horarioStr)            -> { <getDay 0..6>: [{start,end}] } | null
     estado(horarioStr, now?)     -> (now = instante; se lee en el reloj de la instalación)
                                    { abierto:bool, proxima:"lu 8:00"|null } | null
       abierto:true suma `hasta` (minutos desde medianoche del cierre del tramo
       actual — "abierto hasta las 13:00"); con próxima apertura suma los campos
       crudos { dia:0..6, min, off } (días hasta la apertura, 0=hoy) para que la
       UI formatee como quiera. Las recurrencias MENSUALES ("El primer día hábil
       de cada mes", "el segundo lunes del mes a las 9 h") también resuelven:
       suman `fecha` (ISO de la próxima ocurrencia, puede caer en otro mes,
       off > 7) y `min` puede ser null si el cartel no dice hora.
     frase(estadoObj, now?)       -> texto listo para una banda/cartel:
       "Atiende ahora · hasta las 13:00" | "Atiende el jueves, 08:00" |
       "Atiende el lunes 3 de agosto, 09:00" | "Es hoy" | "Lunes 3 de agosto" | null

   `start`/`end` son minutos desde medianoche. Los tramos con horario de cierre
   (intervalos "de 8 a 13 h") tienen end != null y definen "abierto ahora"; los
   horarios de apertura puntuales ("a las 8 h") tienen end === null y sólo aportan
   la próxima apertura. Cuando el string no se puede interpretar devuelve null:
   la UI muestra el texto crudo, nunca adivina. */
"use strict";

  var I18n = await (
      (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
        ? import(window.SANA_STATIC["platform/i18n.mjs"])
        : import(new URL("./i18n.mjs", import.meta.url))
    );
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  // "Ahora" es el reloj de la instalación (dates.mjs), no el del navegador del paciente:
  // un visitante en otro huso veía "abierto ahora" con las horas de su compu. Si el módulo
  // no carga, cae al reloj del navegador, como antes.
  var Dates = await (
      (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/dates.mjs"])
        ? import(window.SANA_STATIC["platform/dates.mjs"])
        : import(new URL("./dates.mjs", import.meta.url))
    ).catch(function () { return null; });
  function pared(now) {
    return Dates && Dates.ahoraInstalacion ? Dates.ahoraInstalacion(now) : now;
  }

  // T() sólo sustituye {vars} con un SanaI18n real cargado; en `node --test`
  // no hay `window`. Por eso las plantillas se piden con vars=null (trae el
  // texto crudo en los dos casos) y ESTA función hace la sustitución acá.
  function applyVars(template, vars) {
    var out = template;
    for (var k in vars) {
      if (Object.prototype.hasOwnProperty.call(vars, k)) {
        out = out.split("{" + k + "}").join(String(vars[k]));
      }
    }
    return out;
  }

  function activeLang() {
    try {
      var doc = (typeof window !== "undefined") && window.document;
      var attr = doc && doc.documentElement && doc.documentElement.getAttribute("lang");
      return (attr || (I18n && I18n.lang) || "es").toLowerCase();
    } catch (e) {
      return (I18n && I18n.lang) || "es";
    }
  }

  // getDay(): 0=domingo … 6=sábado
  var DAY_WORDS = { domingo: 0, lunes: 1, martes: 2, miercoles: 3, jueves: 4, viernes: 5, sabado: 6 };
  var DAY_ABBR = { do: 0, lu: 1, ma: 2, mi: 3, ju: 4, vi: 5, sa: 6 };
  // convención AR de letra única: L M X J V S D (M=martes, X=miércoles)
  var SINGLE_NAME = { d: "domingo", l: "lunes", m: "martes", x: "miercoles", j: "jueves", v: "viernes", s: "sabado" };
  var ABBR_OUT = ["do", "lu", "ma", "mi", "ju", "vi", "sá"];
  var ORDER = [1, 2, 3, 4, 5, 6, 0]; // orden semanal lunes→domingo (para expandir rangos)

  // palabras de día para la regex (nombres completos ANTES de abreviaturas, para
  // que "martes" gane sobre "ma", "miercoles" sobre "mi", etc.)
  var DAYS = "lunes|martes|miercoles|jueves|viernes|sabado|domingo|lu|ma|mi|ju|vi|sa|do";

  function norm(s) {
    return String(s == null ? "" : s).toLowerCase()
      .normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  function dayIndex(tok) {
    if (Object.prototype.hasOwnProperty.call(DAY_WORDS, tok)) return DAY_WORDS[tok];
    if (Object.prototype.hasOwnProperty.call(DAY_ABBR, tok)) return DAY_ABBR[tok];
    return null;
  }

  function parseTime(str) {
    var mt = String(str).match(/^(\d{1,2})(?::(\d{2}))?$/);
    if (!mt) return null;
    var h = +mt[1], mm = mt[2] ? +mt[2] : 0;
    if (h > 23 || mm > 59) return null;
    return h * 60 + mm;
  }

  function rangeDays(a, b) {
    var ia = ORDER.indexOf(a), ib = ORDER.indexOf(b);
    if (ia < 0 || ib < 0) return [a];
    if (ib < ia) ib += 7; // rango que cruza el fin de semana (vi a lu)
    var out = [];
    for (var i = ia; i <= ib; i++) out.push(ORDER[i % 7]);
    return out;
  }

  function buildRe() {
    return new RegExp(
      "\\b(" + DAYS + ")\\s*(?:a|al|-|\\u2014|hasta)\\s*(" + DAYS + ")\\b" +               // 1,2 rango de días
      "|(?:de\\s*)?(\\d{1,2}(?::\\d{2})?)\\s*(?:a|-|\\u2014|al)\\s*(\\d{1,2}(?::\\d{2})?)\\s*h?" + // 3,4 intervalo horario
      "|a\\s*las?\\s*(\\d{1,2}(?::\\d{2})?)\\s*h?" +                                          // 5 apertura "a las N"
      "|\\b(" + DAYS + ")\\b" +                                                              // 6 día suelto
      "|(\\d{1,2}(?::\\d{2})?)",                                                             // 7 número suelto (apertura)
      "g"
    );
  }

  function parse(input) {
    if (input == null) return null;
    var s = norm(input);
    if (!s) return null;
    // expandir rangos de letra única: "l-v" -> "lunes a viernes"
    s = s.replace(/\b([lmxjvsd])\s*-\s*([lmxjvsd])\b/g, function (_m, a, b) {
      return SINGLE_NAME[a] + " a " + SINGLE_NAME[b];
    });

    var week = {}; // dayIndex -> [{start,end}]
    function addOpening(days, start, end) {
      for (var i = 0; i < days.length; i++) {
        var d = days[i];
        if (!week[d]) week[d] = [];
        var dup = false;
        for (var k = 0; k < week[d].length; k++) {
          if (week[d][k].start === start && week[d][k].end === end) { dup = true; break; }
        }
        if (!dup) week[d].push({ start: start, end: end });
      }
    }

    var re = buildRe();
    var m, pd = [], lastWasTime = false, sawAny = false;
    while ((m = re.exec(s))) {
      if (m.index === re.lastIndex) re.lastIndex++; // guardarraíl anti-loop
      if (m[1] != null && m[2] != null) {           // rango de días
        var a = dayIndex(m[1]), b = dayIndex(m[2]);
        if (a == null || b == null) continue;
        if (lastWasTime) pd = [];
        pd = pd.concat(rangeDays(a, b));
        lastWasTime = false;
      } else if (m[3] != null && m[4] != null) {     // intervalo
        var s1 = parseTime(m[3]), e1 = parseTime(m[4]);
        if (s1 == null || e1 == null || e1 <= s1) continue;
        addOpening(pd, s1, e1); lastWasTime = true; sawAny = true;
      } else if (m[5] != null) {                      // "a las N"
        var t = parseTime(m[5]);
        if (t == null) continue;
        addOpening(pd, t, null); lastWasTime = true; sawAny = true;
      } else if (m[6] != null) {                      // día suelto
        var d6 = dayIndex(m[6]);
        if (d6 == null) continue;
        if (lastWasTime) pd = [];
        pd.push(d6); lastWasTime = false;
      } else if (m[7] != null) {                      // número suelto (apertura)
        var t2 = parseTime(m[7]);
        if (t2 == null) continue;
        addOpening(pd, t2, null); lastWasTime = true; sawAny = true;
      }
    }

    if (!sawAny) return null;
    var has = false;
    for (var kk in week) { if (Object.prototype.hasOwnProperty.call(week, kk) && week[kk].length) { has = true; break; } }
    return has ? week : null;
  }

  function fmt(min) {
    var h = Math.floor(min / 60), mm = min % 60;
    return h + ":" + (mm < 10 ? "0" + mm : mm);
  }

  function p2(n) { return (n < 10 ? "0" : "") + n; }

  // ---- recurrencias mensuales ("El primer día hábil de cada mes") ----

  // Feriados nacionales inamovibles (mes-día, valen todos los años). Los
  // trasladables y los de fecha móvil (carnaval, viernes santo) no se modelan:
  // para "primer día hábil" sólo pesan los que caen a principio de mes (1/1,
  // 1/5); en el año raro en que un móvil corra la cuenta un día, manda el cartel.
  var FERIADOS = { "1-1": 1, "3-24": 1, "4-2": 1, "5-1": 1, "5-25": 1,
                   "6-20": 1, "7-9": 1, "12-8": 1, "12-25": 1 };
  var NTH = { primer: 1, primero: 1, "1er": 1, segundo: 2, "2do": 2,
              tercer: 3, tercero: 3, "3er": 3, cuarto: 4, "4to": 4,
              ultimo: -1, ultima: -1 };

  function esHabil(d) {
    var dw = d.getDay();
    return dw >= 1 && dw <= 5 && !FERIADOS[(d.getMonth() + 1) + "-" + d.getDate()];
  }

  // "el primer día hábil de cada mes [a las 8 h]" | "el segundo lunes del mes"
  // -> { nth: 1..4 | -1 (último), habil, dow: 0..6|null, min: minutos|null } | null
  function parseMensual(sN) {
    var re = new RegExp("\\b(primer|primero|1er|segundo|2do|tercer|tercero|3er|cuarto|4to|ultimo|ultima)\\s+(dia\\s+habil|dia|" + DAYS + ")\\s+(?:de\\s+cada|del|de\\s+el)\\s+mes\\b");
    var m = sN.match(re);
    if (!m) return null;
    var what = m[2].replace(/\s+/g, " ");
    var rule = { nth: NTH[m[1]], habil: what === "dia habil", dow: null, min: null };
    if (!rule.habil && what !== "dia") {
      rule.dow = dayIndex(what);
      if (rule.dow == null) return null;
    }
    var mt = sN.match(/a\s*las?\s*(\d{1,2}(?::\d{2})?)\s*h?\b/);
    if (mt) rule.min = parseTime(mt[1]);
    return rule;
  }

  // n-ésima ocurrencia de la regla dentro de un mes (Date | null).
  function ocurrenciaMensual(rule, y, mo) {
    var last = new Date(y, mo + 1, 0).getDate(), days = [];
    for (var dd = 1; dd <= last; dd++) {
      var d = new Date(y, mo, dd);
      if (rule.habil ? !esHabil(d) : (rule.dow != null && d.getDay() !== rule.dow)) continue;
      days.push(d);
    }
    if (!days.length) return null;
    return rule.nth === -1 ? days[days.length - 1] : (days[rule.nth - 1] || null);
  }

  function estadoMensual(rule, now) {
    var hoy0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var nm = now.getHours() * 60 + now.getMinutes();
    for (var k = 0; k <= 14; k++) {
      var base = new Date(now.getFullYear(), now.getMonth() + k, 1);
      var d = ocurrenciaMensual(rule, base.getFullYear(), base.getMonth());
      if (!d) continue;
      var off = Math.round((d - hoy0) / 86400000);
      if (off < 0) continue;
      if (off === 0 && rule.min != null && nm >= rule.min) continue; // ya pasó hoy
      return { abierto: false,
               proxima: ABBR_OUT[d.getDay()] + (rule.min != null ? " " + fmt(rule.min) : ""),
               dia: d.getDay(), min: rule.min, off: off,
               fecha: d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate()) };
    }
    return null;
  }

  function estado(str, now) {
    now = (now instanceof Date) ? now : (now != null ? new Date(now) : new Date());
    if (isNaN(now.getTime())) now = new Date();
    now = pared(now);
    var rule = parseMensual(norm(str));
    if (rule) {
      var rm = estadoMensual(rule, now);
      if (rm) return rm;
    }
    var week = parse(str);
    if (!week) return null;
    var nd = now.getDay(), nm = now.getHours() * 60 + now.getMinutes();

    var today = week[nd] || [];
    for (var i = 0; i < today.length; i++) {
      var op = today[i];
      if (op.end != null && op.start <= nm && nm < op.end) return { abierto: true, proxima: null, hasta: op.end };
    }

    var best = null;
    for (var off = 0; off <= 7; off++) {
      var d = (nd + off) % 7;
      var list = week[d] || [];
      for (var j = 0; j < list.length; j++) {
        var t = list[j].start;
        if (off === 0 && t <= nm) continue; // ya pasó hoy
        if (best === null || off < best.off || (off === best.off && t < best.t)) best = { off: off, t: t, d: d };
      }
    }
    if (!best) return { abierto: false, proxima: null };
    return { abierto: false, proxima: ABBR_OUT[best.d] + " " + fmt(best.t),
             dia: best.d, min: best.t, off: best.off };
  }

  // Texto de banda/cartel a partir de un estado(). Autocontenido (no necesita
  // fechas.js): las bandas de médico y de servicio lo usan tal cual; listado.js
  // arma su propia pastilla estructurada.
  //
  // Multi-idioma (responsable del producto, 2026-08-04): día/mes/verbo/artículo salen de
  // locales/*/date.json vía T(). El artículo ("el jueves") y la minúscula a
  // mitad de frase son una regla del CASTELLANO, no un accidente de
  // concatenación — inglés capitaliza el día SIEMPRE y no lleva artículo, así
  // que esa rama queda explícita (esGram) en vez de adivinada desde el texto
  // ya traducido (ese fue el bug: "Sacar el tomorrow, 07:00").
  var DOW_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]; // getDay()

  function dayName(getDayIndex) {
    var DOWS_LARGO_CAP = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"];
    return T("date.day." + DOW_KEYS[getDayIndex], null, DOWS_LARGO_CAP[getDayIndex]);
  }

  function monthFullName(monthIndex) { // 0-11
    var MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
    return T("date.month_full." + (monthIndex + 1), null, MESES[monthIndex]);
  }

  function frase(e, now) {
    if (!e) return null;
    // verbo médico "Atiende" (no "Abre", que suena a comercio) — mismo verbo que
    // el resto del sitio: "Atiende en CAPS Ostende", "Cuándo atiende" (responsable del producto, 2026-07-11).
    if (e.abierto) {
      return T("date.hours_now", null, "Atiende ahora") + (e.hasta != null
        ? applyVars(T("date.hours_until", null, " · hasta las {time}"), { time: fmt(e.hasta) })
        : "");
    }
    if (e.dia == null) return null;
    now = pared((now instanceof Date) ? now : new Date());
    var esGram = activeLang() === "es";

    // `phrase` es la forma de INICIO de frase ("Jueves 3 de agosto") — el
    // catálogo ya da el día/mes con la mayúscula correcta en cualquier idioma.
    var phrase;
    if (!e.fecha) {
      phrase = dayName(e.dia);
    } else {
      var mF = /^(\d{4})-(\d{2})-(\d{2})/.exec(e.fecha);
      var vars = { day: dayName(e.dia), n: +mF[3], month: monthFullName(+mF[2] - 1), year: +mF[1] };
      phrase = (+mF[1] !== now.getFullYear())
        ? applyVars(T("date.long_year", null, "{day} {n} de {month} de {year}"), vars)
        : applyVars(T("date.long", null, "{day} {n} de {month}"), vars);
    }

    // `mid` es la forma EMBEBIDA ("Atiende el jueves…" / "Open Thursday…"):
    // castellano antepone "el" y baja el día a minúscula; inglés no antepone
    // nada y no toca la mayúscula que ya trajo el catálogo.
    var mid;
    if (e.off === 0) mid = T("date.today_lower", null, "hoy");
    else if (e.off === 1) mid = T("date.tomorrow_lower", null, "mañana");
    else if (esGram) mid = T("date.article", null, "el") + " " + phrase.charAt(0).toLowerCase() + phrase.slice(1);
    else mid = phrase;

    if (e.min != null) {
      return T("date.hours_verb", null, "Atiende") + " " + mid + ", " + p2(Math.floor(e.min / 60)) + ":" + p2(e.min % 60);
    }
    // sin hora en el cartel (recurrencia mensual sin horario): sólo la fecha
    if (e.off === 0) return T("date.hours_is_today", null, "Es hoy");
    if (e.off === 1) return T("date.hours_is_tomorrow", null, "Es mañana");
    return phrase; // "Jueves 3 de agosto" — ya viene con mayúscula del catálogo
  }

export { parse, estado, frase };
export default { parse, estado, frase };
