/* static/platform/search.mjs — match + ranking for home search (and every
   consumer that used to load root search.js). Canonical ES module (audit
   rows 1.1/4.1). Named exports; no browser global, no classic twin, no
   CommonJS wrapper. Lives under static/platform/ and is loaded through
   window.SANA_STATIC / sanaImportStatic. Top-level await on
   platform/i18n.mjs so a consumer that awaits this module also awaits
   its translation dependency — T() is live from the first servRow call.

   DOS piezas:

   1. ordenarPorPopularidad(rows) — ranking por horas semanales de atención
      (the product owner, 2026-07-12: "medicina gene" → "Medicina general" pelado primero).
      Lo consume listing.js (opts.rank:"popularidad") en los catálogos SIN query.

   2. buscar(rows, q, getCampos) — la búsqueda con query (home). Reglas del
      estudio de búsqueda 2026-07-16 (responsable del producto: "si escribo gine me da resultados
      equivocados"):

      a) STEMMING de profesiones: la gente busca el PROFESIONAL y el catálogo
         nombra la ESPECIALIDAD — "ginecólogo" tiene que encontrar "Ginecología",
         "pediatra" → "Pediatría", "obstetra" → "Obstetricia". Cada palabra del
         query genera variantes morfológicas (-ólogo→-olog, -iatra→-iatr,
         singular/plural, mapa de irregulares) y matchea si CUALQUIER variante
         aparece. Sin esto, "ginecólogo"/"cardiólogo"/"obstetra" daban CERO.

      b) TIERS de relevancia: un match en el NOMBRE del servicio vale más que
         un match en sus sinónimos (`palabras`). Sin tiers, "gine" mostraba 6×
         "Medicina general" (que lista "ginecología" entre sus palabras y tiene
         más horas) ANTES que el servicio Ginecología. Por palabra del query:
           0 = el nombre EMPIEZA con la palabra ("pediatra" → "Pediatría")
           1 = arranque de otra palabra del nombre ("pediatra" → "Demanda
               espontánea pediatría"; "obstetra" → "Ecografía obstétrica")
           2 = substring del nombre            ("cardio" en "Electrocardiograma")
           3 = arranque de palabra del resto   ("gine" en palabra "ginecología")
           4 = substring del resto             ("sicolog" en "psicología")
           5 = across-word initials of the name (Cyrillic "IB" → "Ilya Belov" in Cyrillic,
               Latin "IB" → "Ilya Belov", 2026-10-21) — only when the word matched
               nothing else, 2+ letters, consecutive words, same script
               (no transliteration).
         Puntaje de la fila = suma de tiers (menor = mejor). TODAS las palabras
         del query deben matchear en algún lado (AND), como siempre.

      c) La popularidad (horas) ordena SOLO dentro de cada puntaje — desempata
         entre igual de relevantes, nunca entierra a un más relevante.

   3. buscarConPuntaje(rows, q, getCampos) — igual que buscar() pero devuelve
      [{row, score}] en vez de sólo las filas: lo usa admin-reports.js
      (sub-reporte "Búsquedas", 2026-07-22) para comparar el mejor match de
      un catálogo contra otro (servicios vs médicos) sin reescribir buscar().

   4. servRow(s, centrosBySlug, opts) — la fila de listing.js para un servicio,
      MISMO row-builder que consume la home (index.html) y "Subí tu orden"
      (upload-order.js) — un solo builder, no dos copias que divergen (responsable del producto,
      2026-07-16). opts.extraQuery (p.ej. "orden=<token>") se anexa SOLO a los
      hrefs que van al wizard (href cuando es reservable, y slot_href) — nunca
      a info_href ni a la página informativa del walk-in.

   5. sugerirQuery(catalogos, q) — "quizás quisiste decir…" (responsable del producto, 2026-08-04:
      "como hace Google" con un typo). Si buscar() da cero, acerca cada
      palabra del query a la más parecida que SÍ existe en el catálogo real
      (distancia de edición, nunca un diccionario genérico) y devuelve el
      query corregido. El llamador (home) vuelve a correr buscar() con ese
      query y sólo lo ofrece si esta vez trae resultados — nunca se corrige
      a ciegas.

   6. Morfología en INGLÉS (2026-08-04, unit 2 del batch de i18n): "if the
      page is in English, the chips are in English, searches in English, and
      results in English" (responsable del producto). UN SOLO motor — buscar()/puntuar()/
      tierPalabra()/variantes() ahora reciben un `lang` opcional ("es"/"en");
      sin él se autodetecta con activeLang() (document.documentElement.lang,
      si no window.SANA_I18N.active, si no "es" — nunca rompe en Node, que no
      tiene ninguno de los dos). El vocabulario español (STOP_ES/META_ES/
      IRREGULARES_ES/variantesEs) es BYTE-IDÉNTICO al de siempre; el inglés
      vive en su propio STOP_EN/META_EN/IRREGULARES_EN/variantesEn, con sus
      propias reglas de sufijo (-ologist/-ology→"olog", -iatrician/-iatrics→
      "iatr", -graphy/-grapher→"graph", plural -s/-es/-ies) y un fold de
      grafía británica→americana (paediatrics/gynaecology/orthopaedics/
      haematology) aplicado solo del lado del query. sugerirQuery() (arriba)
      tokeniza con el mismo palabrasQuery(q, lang) que puntuar() — el "quizás
      quisiste decir" también respeta el idioma activo. */
"use strict";

var I18n = await (
  (typeof window !== "undefined")
    ? ((window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"])
      : Promise.reject(new Error("[static] platform/i18n.mjs is not in window.SANA_STATIC")))
    : import(new URL("./i18n.mjs", import.meta.url))
);
function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}

  function norm(s) {
    return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/\s+/g, " ").trim();
  }

  function ordenarPorPopularidad(rows) {
    rows = rows || [];
    var groups = {}, order = [];
    rows.forEach(function (r, i) {
      var key = norm(r.nombre);
      if (!groups[key]) { groups[key] = { total: 0, items: [], first: i }; order.push(key); }
      var h = Number(r.horas) || 0;
      groups[key].total += h;
      groups[key].items.push({ r: r, i: i, horas: h });
    });
    var glist = order.map(function (k) { return groups[k]; });
    glist.sort(function (a, b) { return b.total - a.total || a.first - b.first; });
    var out = [];
    glist.forEach(function (g) {
      var items = g.items.slice().sort(function (a, b) { return b.horas - a.horas || a.i - b.i; });
      items.forEach(function (it) { out.push(it.r); });
    });
    return out;
  }

  // ---- matching ------------------------------------------------------------

  /* Idioma activo de la página (unit 2, 2026-08-04: "if the page is in
     English, the chips are in English, searches in English, and results in
     English" — el responsable del producto). NO fork del matcher: un motor, dos vocabularios de
     STOP/META/variantes elegidos acá según el idioma detectado.

     Se lee de document.documentElement.lang (lo que i18n.py resuelve una vez
     por request y estampa en <html lang>) o, en su ausencia, de
     window.SANA_I18N.active (el bootstrap que inyecta {{i18n_bootstrap}}).
     Node (scripts/search_probe.mjs corre este módulo sin window/document) no
     tiene ninguno de los dos → degrada a español, que es el comportamiento de
     SIEMPRE: una instalación de un solo idioma no puede notar esta pieza. */
  function activeLang() {
    try {
      if (typeof document !== "undefined" && document && document.documentElement
          && document.documentElement.lang) {
        return String(document.documentElement.lang).slice(0, 2).toLowerCase() === "en"
          ? "en" : "es";
      }
      if (typeof window !== "undefined" && window && window.SANA_I18N
          && window.SANA_I18N.active) {
        return String(window.SANA_I18N.active).slice(0, 2).toLowerCase() === "en"
          ? "en" : "es";
      }
    } catch (e) { /* host sin document/window (Node) → español, nunca rompe */ }
    return "es";
  }

  // artículos/preposiciones + "dr/dra" (la gente escribe "dr lopez")
  var STOP_ES = ["de", "del", "la", "las", "el", "los", "un", "una", "y", "o",
              "para", "con", "en", "al", "dr", "dra", "lic"];
  // palabras sobre el TRÁMITE, no sobre el servicio ("turno con cardiólogo"
  // busca cardiólogo — acá todo es sacable): se ignoran sólo si queda algo más.
  var META_ES = ["turno", "turnos", "sacar", "pedir", "cita", "hora", "consulta"];

  // profesión → tronco compartido con la especialidad, para los que no salen
  // por regla ("obstetra"/"obstetricia" comparten "obstetr", no un sufijo).
  var IRREGULARES_ES = {
    obstetra: "obstetr", obstetras: "obstetr", obstetricia: "obstetr",
    medico: "medic", medica: "medic", medicos: "medic", medicas: "medic",
    diabetes: "diabet", diabetico: "diabet", diabetica: "diabet",
    diabeticos: "diabet", diabeticas: "diabet",
    enfermero: "enfermer", enfermera: "enfermer", enfermeria: "enfermer",
    enfermeros: "enfermer", enfermeras: "enfermer",
    // Nobody types the name of the place: they type what is happening.
    // «urgencias»/«emergencia» also reach the «Guardia» (usability study
    // 2026-10-15: two patients searched and found nothing). A variant, never a
    // replacement — «urgencias odontológicas» still matches its own service.
    urgencia: "guardia", urgencias: "guardia",
    emergencia: "guardia", emergencias: "guardia"
  };

  // equivalentes en inglés (2026-08-04, unit 2): mismo rol que los de arriba,
  // vocabulario propio del idioma. "dr" se frena igual que en español
  // ("dr smith" busca "smith"); "doctor" NO está acá — a diferencia de "dr"
  // puede ser justo la palabra que el catálogo usa, frenarlo sin condición
  // vaciaría queries legítimos.
  var STOP_EN = ["the", "a", "an", "of", "for", "with", "in", "on", "at",
              "to", "and", "or", "dr"];
  var META_EN = ["appointment", "appointments", "book", "booking",
              "schedule", "scheduling", "visit", "get", "make"];

  // irregulares en inglés: pares profesión↔especialidad que ninguna regla de
  // sufijo cubre (ver stemEn) — mismo criterio que IRREGULARES_ES.
  var IRREGULARES_EN = {
    obstetrician: "obstetr", obstetricians: "obstetr",
    obstetrics: "obstetr", obstetric: "obstetr",
    diabetes: "diabet", diabetic: "diabet", diabetics: "diabet",
    nurse: "nurs", nurses: "nurs", nursing: "nurs",
    physician: "physic", physicians: "physic"
  };

  // Grafía británica → americana ("paediatrics"→"pediatrics",
  // "gynaecology"→"gynecology", "orthopaedics"→"orthopedics",
  // "haematology"→"hematology"): un visitante escribe cualquiera de las dos,
  // el catálogo probablemente sólo tiene una. Sólo se aplica del lado del
  // QUERY (variantesEn) — nunca al texto del catálogo, que no se toca acá.
  function foldBritish(w) {
    return w.replace(/ae/g, "e").replace(/oe/g, "e");
  }

  // sufijos propios del inglés médico (mismo espíritu que las reglas
  // españolas de arriba, ver docstring del módulo):
  //   -ologist(s)/-ology(ies)        → tronco+"olog"   (gynecologist/gynecology)
  //   -iatrician(s)/-iatric(s)/-iatry → tronco+"iatr"   (pediatrician/pediatrics)
  //   -graphy(ies)/-grapher(s)        → tronco+"graph"  (radiography/radiographer)
  //   plural -ies/-(s|x|z|ch|sh)es/-s → singular
  function stemEn(w) {
    var out = [], m;
    if ((m = w.match(/^(.{2,})olog(?:y|ies|ist|ists)$/))) out.push(m[1] + "olog");
    if ((m = w.match(/^(.{2,})iatr(?:ician|icians|ics|ic|y)$/))) out.push(m[1] + "iatr");
    if ((m = w.match(/^(.{2,})graph(?:y|ies|er|ers)$/))) out.push(m[1] + "graph");
    if ((m = w.match(/^(.{3,})ies$/))) out.push(m[1] + "y"); // biopsies→biopsy
    else if (w.length > 4 && /(?:[sxz]|ch|sh)es$/.test(w)) out.push(w.slice(0, -2)); // boxes→box
    else if (w.length > 3 && /s$/.test(w) && !/ss$/.test(w)) out.push(w.slice(0, -1)); // tests→test
    return out;
  }

  /* Variantes morfológicas de UNA palabra del query (ya normalizada) en
     ESPAÑOL: siempre la palabra tal cual; + tronco profesión↔especialidad —
     las DOS direcciones, porque el dato puede nombrar cualquiera de las dos
     formas ("ginecólogo"→"ginecolog" ⊂ "ginecología", y "cardiología"→
     "cardiolog" ⊂ "cardiólogo"; "pediatra"→"pediatr" ⊂ "pediatría",
     "psiquiatría"→"psiquiatr" ⊂ "psiquiatra"); + singular si vino en plural. */
  function variantesEs(w) {
    var out = [w], m;
    if (IRREGULARES_ES[w]) out.push(IRREGULARES_ES[w]);
    // {2,}: "urólogo" es ur+ólogo — el prefijo más corto del catálogo
    if ((m = w.match(/^(.{2,})olog(?:[oa]s?|ias?)$/))) out.push(m[1] + "olog");
    if ((m = w.match(/^(.{2,})iatr(?:as?|ias?)$/))) out.push(m[1] + "iatr");
    // ecografía/ecografista/radiografía comparten "-graf"
    if ((m = w.match(/^(.{2,})graf(?:ias?|istas?)$/))) out.push(m[1] + "graf");
    // adjetivo ↔ sustantivo: "clínico" ⊂ "clínica" (médico clínico → Clínica Médica)
    if ((m = w.match(/^(.{4,})ic[oa]s?$/))) out.push(m[1] + "ic");
    if (w.length > 4 && /[a-z]es$/.test(w)) out.push(w.slice(0, -2)); // papeles→papel
    if (w.length > 3 && /[aeiou]s$/.test(w)) out.push(w.slice(0, -1)); // vacunas→vacuna
    return out;
  }

  /* Idem en INGLÉS: la palabra tal cual, su grafía americana si vino en
     británico, y los troncos de stemEn/IRREGULARES_EN aplicados a AMBAS
     grafías (así "gynaecologist" matchea por el mismo tronco que
     "gynecologist"). */
  function variantesEn(w) {
    var out = [w], folded = foldBritish(w);
    if (IRREGULARES_EN[w]) out.push(IRREGULARES_EN[w]);
    if (folded !== w) {
      out.push(folded);
      if (IRREGULARES_EN[folded]) out.push(IRREGULARES_EN[folded]);
    }
    out = out.concat(stemEn(w));
    if (folded !== w) out = out.concat(stemEn(folded));
    return out;
  }

  /* Variantes de UNA palabra del query según el idioma activo (default:
     autodetectado — ver activeLang()). Firma retrocompatible: todo llamador
     que ya pasaba una sola palabra (tests, código existente) sigue viendo el
     comportamiento español de siempre cuando no pasa `lang`. */
  function variantes(w, lang) {
    return (lang || activeLang()) === "en" ? variantesEn(w) : variantesEs(w);
  }

  // texto con separadores uniformes para detectar arranques de palabra
  function bordes(s) {
    return " " + norm(s).replace(/[^a-z0-9]+/g, " ") + " ";
  }

  /* Concatenated initials of every word of the principal field:
     Cyrillic "Ilya Belov" → "ib", "Clínica Médica" → "cm". The initials of any
     CONSECUTIVE run of words (Cyrillic "Petrovich Belov" → "pb") are a contiguous
     substring of this string, which is how tierPalabra() matches them. */
  function iniciales(texto) {
    // Unicode letters/numbers define words, not arbitrary non-ASCII bytes:
    // punctuation, emoji and hyphens must not become a person's initials.
    // No transliteration: Cyrillic and Latin remain distinct alphabets.
    return (norm(texto).match(/[\p{L}\p{N}]+/gu) || []).map(function (word) {
      return Array.from(word)[0];
    }).join("");
  }

  /* Tier de una palabra del query contra {principal, resto} pre-normalizados
     por prepararCampos(). null = no matchea con ninguna variante. */
  function tierPalabra(w, campos, lang) {
    var vs = variantes(w, lang), i, p;
    for (i = 0; i < vs.length; i++) {
      p = campos.pb.indexOf(" " + vs[i]);
      if (p === 0) return 0;
      if (p > 0) return 1;
    }
    for (i = 0; i < vs.length; i++) if (campos.pt.indexOf(vs[i]) >= 0) return 2;
    for (i = 0; i < vs.length; i++) if (campos.rb.indexOf(" " + vs[i]) >= 0) return 3;
    for (i = 0; i < vs.length; i++) if (campos.rt.indexOf(vs[i]) >= 0) return 4;
    // Tier 5, initials across the words of the name, fires ONLY when the
    // word matched nothing else — every existing score stays as it was and
    // initials always rank below exact (0), word-start (1) and substring (2)
    // matches. 2+ letters only: a single letter is the plain prefix search
    // of always. Consecutive word initials in the SAME script, no
    // transliteration (Cyrillic «IB»→«Ilya Belov», Latin «IB»→«Ilya Belov», never across).
    if (w.length >= 2 && campos.pi && campos.pi.indexOf(w) !== -1) return 5;
    return null;
  }

  function prepararCampos(c) {
    var p = (c.principal || []).filter(Boolean).join(" ");
    var r = (c.resto || []).filter(Boolean).join(" ");
    var exc = (c.excluidas || []).map(norm).filter(Boolean);
    return { pt: norm(p), pb: bordes(p), pi: iniciales(p), rt: norm(r), rb: bordes(r),
             exc: exc.filter(function (e) { return e.indexOf(" ") === -1; }),
             frases: exc.filter(function (e) { return e.indexOf(" ") !== -1; })
               .map(function (e) { return bordes(e).trim(); }) };
  }

  /* Una exclusión de VARIAS palabras («fiebre con confusión», «tose
     sangre» — las de guardia) tapa la fila sólo cuando el query la dice
     entera, o cuando un query de dos palabras o más ya la está escribiendo.
     Probarla palabra por palabra tapaba «fiebre» y «tos» sueltas y sacaba a
     Telemedicina de la búsqueda de síntomas (contraparte técnica, 2026-10-19). */
  function fraseExcluida(q, frases) {
    var qs = bordes(q).trim();
    if (!qs) return false;
    for (var i = 0; i < frases.length; i++) {
      var f = frases[i];
      if ((" " + qs).indexOf(" " + f) !== -1) return true;
      if (qs.indexOf(" ") !== -1 && f.indexOf(qs) === 0) return true;
    }
    return false;
  }

  /* Palabras EXCLUIDAS de la fila (reunión municipal 2026-07-17: "podés elegir
     que su consultorio no aparezca a PAP" — el profesional no hace esa
     práctica y no quiere aparecer en esa búsqueda). Si CUALQUIER palabra del
     query cae en la lista de exclusión, la fila desaparece de ESA búsqueda.
     Prefijo en las dos direcciones: la exclusión "pap" tapa el query "pap" y
     también "papanicolaou" (mínimo 3 letras del query para la dirección
     query⊂exclusión, así "p" suelto no tapa nada). La lista viene curada del
     admin (`palabras_excluidas`), nunca del usuario. */
  function excluida(w, exc, lang) {
    var vs = variantes(w, lang);
    for (var i = 0; i < vs.length; i++) {
      for (var j = 0; j < exc.length; j++) {
        var e = exc[j];
        if (vs[i].indexOf(e) === 0) return true;
        if (vs[i].length >= 3 && e.indexOf(vs[i]) === 0) return true;
      }
    }
    return false;
  }

  // palabras "de contenido" del query: sin stopwords, y sin meta-palabras
  // salvo que sean lo único que quedó ("turno" a secas sigue buscando
  // "turno"). `lang` fija el idioma ("es"/"en"); sin él se autodetecta
  // (activeLang()) — el vocabulario de stop/meta-palabras que elige es el
  // mismo que puntuar() usa para matchear, así sugerirQuery() (unit "quizás
  // quisiste decir") tokeniza igual que la búsqueda real sin duplicar la
  // lista acá.
  function palabrasQuery(q, lang) {
    lang = lang || activeLang();
    var STOPW = lang === "en" ? STOP_EN : STOP_ES;
    var METAW = lang === "en" ? META_EN : META_ES;
    // «fiebre, tos»: la coma separa, no es parte de la palabra.
    var words = norm(q).replace(/[.,;:!?¿¡()"“”«»]+/g, " ").trim().split(" ").filter(function (w) {
      return w && STOPW.indexOf(w) === -1;
    });
    var sinMeta = words.filter(function (w) { return METAW.indexOf(w) === -1; });
    return sinMeta.length ? sinMeta : words;
  }

  /* Puntaje de una fila para el query: suma de tiers por palabra (menor =
     más relevante), o null si alguna palabra no matchea en ningún campo.
     `lang` fija el idioma ("es"/"en"); sin él se autodetecta (activeLang()) —
     así el llamador de siempre (que nunca pasaba idioma) sigue funcionando
     tal cual, y en el navegador la página en inglés stemea en inglés sola. */
  function puntuar(campos, q, lang) {
    lang = lang || activeLang();
    var words = palabrasQuery(q, lang);
    if (!words.length) return null;
    if (campos.exc && campos.exc.length) {
      for (var j = 0; j < words.length; j++) {
        if (excluida(words[j], campos.exc, lang)) return null;
      }
    }
    if (campos.frases && campos.frases.length && fraseExcluida(q, campos.frases)) return null;
    var score = 0;
    for (var i = 0; i < words.length; i++) {
      var t = tierPalabra(words[i], campos, lang);
      if (t == null) return null;
      score += t;
    }
    return score;
  }

  /* Búsqueda completa: filtra + ordena. getCampos(row) devuelve
     {principal: [nombre...], resto: [especialidad/categoria/palabras/centro...],
      excluidas: [palabras_excluidas...] (opcional)}.
     Orden final: por puntaje asc; dentro de cada puntaje, popularidad.
     `lang` opcional ("es"/"en") — sin él se autodetecta del documento activo
     (ver activeLang()); todo llamador existente sigue sin pasarlo. */
  function buscar(rows, q, getCampos, lang) {
    var buckets = {}, scores = [];
    (rows || []).forEach(function (r) {
      var s = puntuar(prepararCampos(getCampos(r)), q, lang);
      if (s == null) return;
      if (!buckets[s]) { buckets[s] = []; scores.push(s); }
      buckets[s].push(r);
    });
    scores.sort(function (a, b) { return a - b; });
    var out = [];
    scores.forEach(function (s) {
      ordenarPorPopularidad(buckets[s]).forEach(function (r) { out.push(r); });
    });
    return out;
  }

  /* Variante de buscar() que expone el PUNTAJE de cada fila en vez de sólo
     el orden final — para el sub-reporte admin "Búsquedas" (2026-07-22), que
     necesita comparar el mejor match entre DOS catálogos (servicios vs
     médicos) para decidir a cuál pertenece cada búsqueda: menor puntaje =
     más relevante. Devuelve [{row, score}, ...] ordenado por puntaje asc
     (empate: más horas primero) — buscar() NO se reescribe sobre esto para
     no tocar su agrupado por nombre+popularidad ya cubierto por los tests. */
  function buscarConPuntaje(rows, q, getCampos, lang) {
    var out = [];
    (rows || []).forEach(function (r) {
      var s = puntuar(prepararCampos(getCampos(r)), q, lang);
      if (s == null) return;
      out.push({ row: r, score: s });
    });
    out.sort(function (a, b) {
      return a.score - b.score || (Number(b.row.horas) || 0) - (Number(a.row.horas) || 0);
    });
    return out;
  }

  /* Búsqueda de una FRASE de orden médica ("Ergometría de 12 derivaciones"):
     buscar() es AND entre palabras, así que la letra del médico alrededor del
     nombre del estudio suele dar cero. Se degrada por la COLA (la orden nombra
     el estudio primero) y gana el nivel con MEJOR tier promedio — no el primer
     nivel que devuelve algo: "Ergometría de 12" matchea Cardiología por la
     palabra clave "ergometría" en `palabras` + "12" en el horario (tiers 4+4),
     y frenar ahí enterraba al servicio Ergometría, que con la query degradada
     "Ergometría" matchea por NOMBRE (tier 0). Empate de promedio → gana la
     query más larga (más específica). */
  function buscarFrase(rows, q, getCampos, lang) {
    var words = norm(q).split(" ").filter(Boolean);
    var best = null;
    while (words.length) {
      var query = words.join(" ");
      var buckets = {}, scores = [], n = 0;
      (rows || []).forEach(function (r) {
        var s = puntuar(prepararCampos(getCampos(r)), query, lang);
        if (s == null) return;
        if (!buckets[s]) { buckets[s] = []; scores.push(s); }
        buckets[s].push(r);
      });
      if (scores.length) {
        scores.sort(function (a, b) { return a - b; });
        var out = [];
        scores.forEach(function (s) {
          ordenarPorPopularidad(buckets[s]).forEach(function (r) { out.push(r); });
        });
        var avg = scores[0] / words.length; // tier promedio del MEJOR match
        if (best === null || avg < best.avg) best = { rows: out, avg: avg };
      }
      words.pop();
    }
    return best ? best.rows : [];
  }

  // ---- "quizás quisiste decir…" (Google-style did-you-mean) ----------------

  /* Distancia de edición (Levenshtein) entre dos strings ya normalizados. */
  function distancia(a, b) {
    if (a === b) return 0;
    var m = a.length, n = b.length;
    if (!m) return n;
    if (!n) return m;
    var prev = [], cur = [], i, j;
    for (j = 0; j <= n; j++) prev[j] = j;
    for (i = 1; i <= m; i++) {
      cur[0] = i;
      var ca = a.charAt(i - 1);
      for (j = 1; j <= n; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1,
          prev[j - 1] + (ca === b.charAt(j - 1) ? 0 : 1));
      }
      var tmp = prev; prev = cur; cur = tmp;
    }
    return prev[n];
  }

  // tolerancia según largo: una palabra corta perdona menos letras mal que
  // una larga, si no "pap" corrige a cualquier cosa de 3 letras.
  function tolerancia(w) { return w.length <= 4 ? 1 : (w.length <= 9 ? 2 : 3); }

  // vocabulario real del catálogo (principal + resto de cada fila, de cada
  // catálogo pasado): sólo contra ESO tiene sentido corregir, nunca contra
  // un diccionario genérico. `lang` elige qué lista de stopwords filtra
  // (STOP_ES/STOP_EN se separaron en la morfología en inglés, unit 2 del
  // batch de i18n — antes había una sola `STOP`).
  function vocabularioDe(catalogos, lang) {
    var STOPW = (lang || activeLang()) === "en" ? STOP_EN : STOP_ES;
    var set = {};
    (catalogos || []).forEach(function (cat) {
      (cat.rows || []).forEach(function (r) {
        var c = cat.getCampos(r) || {};
        (c.principal || []).concat(c.resto || []).forEach(function (v) {
          norm(v).split(/[^a-z0-9]+/).forEach(function (w) {
            if (w.length >= 3 && STOPW.indexOf(w) === -1) set[w] = true;
          });
        });
      });
    });
    return Object.keys(set);
  }

  /* Distancia entre `w` y `v`, probando también PREFIJOS de `v` del largo de
     `w` (± tolerancia). Cubre el caso muy común de escribir sólo el
     PRINCIPIO de una palabra larga ("otori" para "otorrino"/
     "otorrinolaringología") — comparar contra la palabra COMPLETA la
     penaliza por todas las letras que el usuario ni llegó a escribir
     (the product owner, 2026-08-04: "otori" daba CERO). */
  function distanciaConPrefijo(w, v, max) {
    var best = distancia(w, v);
    var hi = Math.min(v.length, w.length + max);
    for (var k = Math.max(0, w.length - max); k <= hi; k++) {
      var d = distancia(w, v.slice(0, k));
      if (d < best) best = d;
    }
    return best;
  }

  // palabra más cercana del vocabulario dentro de tolerancia, o null si la
  // palabra ya existe tal cual (nada que corregir) o nada calza lo bastante.
  function corregirPalabra(w, vocab) {
    var max = tolerancia(w), mejor = null, mejorD = max + 1;
    for (var i = 0; i < vocab.length; i++) {
      var v = vocab[i];
      if (v === w) return null;
      // corte rápido: ni de largo comparable ni lo bastante larga para dar
      // un prefijo del largo de w — ninguna de las dos formas puede calzar.
      if (Math.abs(v.length - w.length) > max && v.length < w.length - max) continue;
      var d = distanciaConPrefijo(w, v, max);
      if (d < mejorD) { mejorD = d; mejor = v; }
    }
    return mejor;
  }

  /* Query corregido palabra por palabra contra el vocabulario real de
     `catalogos` ([{rows, getCampos}, ...]), o null si no hay nada para
     corregir. El llamador decide si el query corregido sirve — acá sólo se
     acerca cada palabra a la más parecida que SÍ existe en el catálogo
     (responsable del producto, 2026-08-04: "como hace Google" — nunca se muestra a ciegas, el
     llamador vuelve a buscar con el query corregido y sólo lo ofrece si esta
     vez trae resultados). */
  function sugerirQuery(catalogos, q, lang) {
    var words = palabrasQuery(q, lang);
    if (!words.length) return null;
    var vocab = vocabularioDe(catalogos, lang);
    var cambio = false;
    var corregidas = words.map(function (w) {
      var c = corregirPalabra(w, vocab);
      if (c) { cambio = true; return c; }
      return w;
    });
    return cambio ? corregidas.join(" ") : null;
  }

  // ---- servRow: fila de listado.js para un servicio (compartida) -----------
  function appendQuery(href, extra) {
    if (!extra) return href;
    return href + (href.indexOf("?") >= 0 ? "&" : "?") + extra;
  }
  function servRow(s, centrosBySlug, opts) {
    opts = opts || {};
    var CENT = centrosBySlug || {};
    var c = CENT[s.centro] || {};
    // sede anexada al display del centro ("CAPS Ostende · Centro Cultural"):
    // sin ella, dos "Medicina general" del mismo centro son indistinguibles.
    var centro = (c.name || "") + (s.sede ? " · " + s.sede : "");
    // La acción va DERECHO al wizard (/service/<id>/book) en los modos con
    // solicitud; los walk-in a su página informativa (responsable del producto 2026-07-12).
    var esWizard = s.modo === "solicitud" || s.modo === "solicitud_con_orden";
    var accion = esWizard ? "/service/" + s.id + "/book" : "/service/" + s.id;
    // Mismo texto/clave que site_availability._next_from() (fase 3b, listing.next.*)
    // — reuso, no una segunda copia (CLAUDE.md: un formatter por dato).
    var row = { proximo: s.proximo || { v: T("listing.next.confirmed_by_center", null,
                 "Te confirma el centro"), kind: "solicitud", sort: "2" },
             nombre: s.nombre, href: accion, info_href: "/service/" + s.id,
             centro: centro, centro_href: c.slug ? "/center/" + c.slug : "",
             orden: s.modo === "walkin_con_orden" || s.modo === "solicitud_con_orden",
             horas: s.horas || 0 };
    // Turno real: la pastilla lleva ESE turno al wizard (?slot=… — clic en la
    // hora = hora elegida, sin re-mostrar la grilla) y "más horarios" abre la
    // grilla (responsable del producto, 2026-07-14). Espejo de _slot_fields en site_listing.py.
    if (esWizard && s.proximo && s.proximo.kind === "turno" && s.proximo.dia && s.proximo.hora) {
      row.slot_href = accion + "?slot=" + s.proximo.dia + "T" + s.proximo.hora;
      row.info_href = accion;
    }
    if (esWizard && opts.extraQuery) {
      row.href = appendQuery(row.href, opts.extraQuery);
      if (row.slot_href) row.slot_href = appendQuery(row.slot_href, opts.extraQuery);
    }
    return row;
  }

  /* Centers are searchable by their own name and address (usability study
     2026-10-15: «hospital» found nothing that said where the hospital is).
     Only the name and the address — never the description — so a center
     appears when the person names the place, not on every symptom.
     centroCampos() reads a centroRow(), the shape buscar() ranks. */
  function centroCampos(row) {
    return { principal: [row.nombre], resto: [row.centro] };
  }
  function centroRow(c) {
    // `href`: the center's canonical page when it is not /center/<slug>
    // (Zoonosis → /zoonosis, site_centers._centers_web()).
    var href = c.href || "/center/" + c.slug;
    // The address is a link to its Maps page, in a new tab (centro_href_ext).
    return { nombre: c.name, href: href, info_href: href, centro: c.address || "",
             centro_href: c.address && c.maps_url ? c.maps_url : "", centro_href_ext: !!(c.address && c.maps_url),
             orden: null, horas: 0,
             proximo: { v: "", kind: "info", sort: "0" } };
  }

export {
  centroCampos,
  centroRow,
  ordenarPorPopularidad,
  buscar,
  buscarConPuntaje,
  buscarFrase,
  sugerirQuery,
  puntuar,
  variantes,
  norm,
  servRow,
  activeLang,
};
