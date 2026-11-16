/* admin-patient-summary.mjs — «Resumen del paciente» (2026-08-01): el panel
   pre-visita que el médico ve dentro de la consulta (and any
   otra pantalla que lo monte). No tiene routing propio: expone
   mount(container, {dni, medicoId}) y el dueño
   de la pantalla decide dónde va.

   Contrato de API (chart_summary.py):
     GET  /api/admin/patient-summary?dni=<dni>[&medico=<id>]
     POST /api/admin/patient-summary  {dni, medico?}   (regenerar)
   rol medico: el server usa SIEMPRE su propio medico_id (el param se
   ignora); admin/operador mandan medico=.

   Dos solapas, Pendientes primero (la que el médico usa en los 30 segundos
   antes de abrir la puerta): indicadores visuales, acciones priorizadas y
   preguntas para el consultorio. Historia: medicación a verificar,
   antecedentes y tendencias ampliadas. Los gráficos tienen banda/límite de
   referencia, etiquetas directas y valores exactos en cada punto. Los datos
   vienen del server ya extraídos por código de los laboratorios reales: este
   archivo jamás inventa un número. */
"use strict";

  var Dates = null;
  var datesUrl = window.SANA_STATIC && window.SANA_STATIC["platform/dates.mjs"];
  var datesReady = datesUrl ? import(datesUrl).then(function (m) { Dates = m; })
    .catch(function () { return null; }) : Promise.resolve(null);
  var sessionUrl = window.SANA_STATIC && window.SANA_STATIC["platform/admin-session.mjs"];
  var sessionReady = sessionUrl
    ? import(sessionUrl)
    : Promise.reject(new Error("platform/admin-session.mjs is not in window.SANA_STATIC"));

  var I18n = await (
      (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
        ? import(window.SANA_STATIC["platform/i18n.mjs"])
        : import(new URL("./i18n.mjs", import.meta.url))
    );
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  var CSS_LISTO = false;
  function inyectarCss() {
    if (CSS_LISTO) return;
    CSS_LISTO = true;
    var css = [
      ".rp-card{--rp-chart:#3978a8;--rp-chart-2:#7a638f;--rp-chart-fill:#dbe9f2;border:1px solid var(--border);border-radius:12px;background:var(--panel);padding:18px;margin:14px 0;}",
      ".rp-titular{font-size:15px;line-height:1.45;font-weight:700;margin:0;max-width:68ch;}",
      ".rp-meta{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline;margin-top:6px;color:var(--hint-ink);font-size:13px;}",
      ".rp-refresh{font:inherit;font-size:13px;border:0;background:none;color:var(--accent);cursor:pointer;padding:0;}",
      ".rp-refresh:hover{color:var(--accent-hover);}",
      ".rp-tabs{display:flex;gap:18px;border-bottom:1px solid var(--border);margin:12px 0 0;}",
      ".rp-tab{font:inherit;font-size:13px;border:0;background:none;cursor:pointer;padding:6px 2px 8px;color:var(--hint-ink,var(--text-2));border-bottom:2px solid transparent;margin-bottom:-1px;}",
      ".rp-tab.on{color:var(--text);font-weight:650;border-bottom-color:var(--accent);}",
      ".rp-tab-n{color:var(--hint-ink,var(--text-2));font-size:13px;margin-left:4px;}",
      ".rp-section-title{font-size:13px;font-weight:700;margin:16px 0 8px;}",
      ".rp-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:10px 0 18px;}",
      ".rp-metric{min-width:0;border:1px solid var(--border);border-radius:10px;padding:11px 12px 8px;background:var(--panel);overflow:hidden;}",
      ".rp-metric-head{display:block;}",
      ".rp-metric-label{font-size:13px;line-height:1.3;font-weight:700;margin:0;min-width:0;}",
      ".rp-metric-now{margin-top:8px;white-space:nowrap;}",
      ".rp-metric-value{font:700 22px/1 var(--font);letter-spacing:-.03em;color:var(--text);}",
      ".rp-metric-unit{font-size:13px;color:var(--hint-ink);margin-left:3px;}",
      ".rp-metric-status{font-size:13px;line-height:1.35;font-weight:650;margin:7px 0 0;}",
      ".rp-status-alta{color:var(--danger);}.rp-status-media{color:var(--warn);}.rp-status-ok{color:var(--done);}",
      ".rp-metric-range{font-size:13px;color:var(--hint-ink);margin:2px 0 0;}",
      ".rp-spark{display:block;width:calc(100% + 24px);height:58px;margin:8px -12px -8px;}",
      ".rp-spark-line{fill:none;stroke:var(--rp-chart);stroke-width:2.25;stroke-linejoin:round;stroke-linecap:round;}",
      ".rp-spark-line--2{stroke:var(--rp-chart-2);}",
      ".rp-spark-area{fill:var(--rp-chart-fill);opacity:.52;}",
      ".rp-spark-band{fill:var(--done);opacity:.08;}",
      ".rp-priority-head{margin-top:2px;}",
      ".rp-adv{margin:12px 0 0;padding:8px 12px;border:1px solid var(--danger);border-radius:8px;color:var(--danger);font-size:13px;}",
      ".rp-item{display:flex;gap:12px;align-items:flex-start;padding:12px 0;border-bottom:1px solid var(--border);}",
      ".rp-item:last-child{border-bottom:0;}",
      ".rp-item-num{flex:0 0 22px;height:22px;border-radius:50%;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;color:#fff;margin-top:1px;}",
      ".rp-pri-alta{background:var(--danger);}",
      ".rp-pri-media{background:var(--warn);}",
      ".rp-pri-baja{background:var(--done);}",
      ".rp-item-body{flex:1 1 auto;min-width:0;}",
      ".rp-item-t{font-size:13.5px;font-weight:650;margin:0;}",
      ".rp-item-sub{font-size:13px;color:var(--hint-ink);margin:1px 0 0;}",
      ".rp-item-det{font-size:13px;line-height:1.5;margin:6px 0 0;}",
      ".rp-item-src{font:13px/1.4 var(--font);color:var(--hint-ink);margin-top:5px;}",
      ".rp-evidence{margin-top:7px;}",
      ".rp-evidence summary{width:max-content;max-width:100%;cursor:pointer;font-size:13px;color:var(--hint-ink);}",
      ".rp-evidence[open] summary{margin-bottom:4px;}",
      ".rp-kpi{flex:0 0 auto;text-align:right;}",
      ".rp-kpi-v{font-size:19px;font-weight:700;line-height:1.1;}",
      ".rp-kpi-v.rp-alerta{color:var(--danger);}",
      ".rp-kpi-v.rp-atencion{color:var(--warn);}",
      ".rp-kpi-u{font-size:13px;color:var(--hint-ink);}",
      ".rp-h4{font-size:13px;font-weight:700;margin:16px 0 4px;}",
      ".rp-preguntas{margin:4px 0 0;padding:0 0 0 18px;font-size:13.5px;line-height:1.7;}",
      ".rp-med-estado{display:inline-flex;align-items:center;gap:6px;font-size:13px;}",
      ".rp-dot{width:8px;height:8px;border-radius:50%;display:inline-block;}",
      ".rp-dot-activo{background:var(--done);}",
      ".rp-dot-suspendido{background:var(--text-3);}",
      ".rp-dot-verificar{background:var(--warn);}",
      ".rp-ant{font-size:13px;line-height:1.55;margin:6px 0 0;}",
      ".rp-ant b{font-weight:650;}",
      ".rp-timeline{list-style:none;margin:7px 0 0;padding:0;}",
      ".rp-timeline-item{display:grid;grid-template-columns:42px minmax(0,1fr);gap:10px;padding:9px 0;border-bottom:1px solid var(--border);}",
      ".rp-timeline-item:last-child{border-bottom:0;}",
      ".rp-timeline-year{font:700 13px/1.45 var(--font);color:var(--hint-ink);}",
      ".rp-timeline-title{font-size:13px;font-weight:700;margin:0;}",
      ".rp-timeline-text{font-size:13px;line-height:1.48;color:var(--hint-ink);margin:3px 0 0;}",
      ".rp-chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:8px;}",
      ".rp-chart{min-width:0;border:1px solid var(--border);border-radius:10px;padding:12px 12px 9px;background:var(--panel);}",
      ".rp-chart-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px;}",
      ".rp-chart-t{font-size:13px;font-weight:700;margin:0;}",
      ".rp-chart-latest{font:700 14px/1 var(--font);white-space:nowrap;}",
      ".rp-chart-c{font-size:13px;line-height:1.45;color:var(--hint-ink);margin:4px 0 0;}",
      ".rp-chart svg{width:100%;height:auto;display:block;}",
      ".rp-fuentes{font:13px/1.5 var(--font);color:var(--hint-ink);margin-top:14px;border-top:1px solid var(--border);padding-top:9px;}",
      ".rp-fuentes-t{font-weight:700;color:var(--hint-ink);}",
      ".rp-fuentes ul{margin:4px 0 0;padding-left:17px;}",
      ".rp-fuentes li+li{margin-top:2px;}",
      ".rp-vacio{color:var(--hint-ink);font-size:13px;margin:8px 0 0;}",
      ".rp-external{margin-top:16px;color:var(--text);font-size:16px;line-height:1.5;overflow-wrap:anywhere;text-wrap:pretty;}",
      ".rp-external summary{cursor:pointer;min-height:44px;align-content:center;font-weight:650;}",
      ".rp-external button{font:inherit;min-height:44px;max-width:100%;white-space:normal;}",
      ".rp-external ol{padding-left:24px;margin:12px 0;}.rp-external li{margin:12px 0;}.rp-external p{margin:6px 0;}",
      // En un teléfono la 5.ª columna (Fuente) parte la tabla y desborda
      // 5px: la procedencia ya vive en el pie de fuentes, así que se oculta.
      "@media(max-width:640px){.rp-card{padding:16px 14px}.rp-med-fuente{display:none}.rp-chart-grid{grid-template-columns:1fr}}"
    ].join("");
    var tag = document.createElement("style");
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  function api(path, opts) {
    return fetch(path, Object.assign({ credentials: "same-origin" }, opts || {}))
      .then(function (res) {
        if (res.status === 401 || res.status === 403) {
          return sessionReady.then(function (mod) {
            return mod.confirmar(res);
          }).then(function () {
            return res.json().catch(function () { return {}; }).then(function (j) {
              return { ok: res.ok, j: j };
            });
          });
        }
        return res.json().catch(function () { return {}; }).then(function (j) {
          return { ok: res.ok, j: j };
        });
      });
  }

  // ---- gráficos (SVG inline, sin librerías) -------------------------------

  var W = 560, H = 132, PAD = { l: 42, r: 58, t: 12, b: 22 };
  function fechaBreve(fecha) {
    return Dates && Dates.corta ? Dates.corta(fecha) : String(fecha || "");
  }

  function escala(vals, lo, hi) {
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (lo != null) min = Math.min(min, lo);
    if (hi != null) max = Math.max(max, hi);
    var margen = (max - min) * 0.12 || Math.abs(max) * 0.1 || 1;
    return { min: min - margen, max: max + margen };
  }

  function fmtNum(v) {
    return (Math.round(v * 10) / 10).toString().replace(".", ",");
  }

  function fmtDelta(v) {
    var n = Math.abs(v) < 1 ? Math.round(v * 100) / 100 : Math.round(v * 10) / 10;
    return n.toString().replace(".", ",");
  }

  function estadoValor(valor, rango) {
    if (!rango) return { clase: "ok", texto: T("admin.reports.patient_summary.sin_rango", null, "Sin rango de referencia"), fuera: false };
    if (valor > rango[1]) return {
      clase: valor > rango[1] * 1.15 ? "alta" : "media",
      texto: "▲ " + fmtDelta(valor - rango[1]) + " " + T("admin.reports.patient_summary.sobre_el_rango", null, "sobre el rango"),
      fuera: true
    };
    if (valor < rango[0]) return {
      clase: valor < rango[0] * 0.85 ? "alta" : "media",
      texto: "▼ " + fmtDelta(rango[0] - valor) + " " + T("admin.reports.patient_summary.bajo_el_rango", null, "bajo el rango"),
      fuera: true
    };
    return { clase: "ok", texto: T("admin.reports.patient_summary.dentro_del_rango", null, "Dentro del rango"), fuera: false };
  }

  function rangoTexto(rango) {
    return rango ? T("admin.reports.patient_summary.referencia", { min: fmtNum(rango[0]), max: fmtNum(rango[1]) },
      "Referencia " + fmtNum(rango[0]) + "–" + fmtNum(rango[1])) : "";
  }

  // Una serie = [["YYYY-MM-DD", valor], ...] ya ordenada. `lineas`: 1 o 2
  // series en el mismo plano. En TA cada línea conserva SU límite; no se
  // pinta una banda sistólica falsa detrás de la diastólica.
  function chartSvg(lineas) {
    var todosV = [], todasF = [];
    lineas.forEach(function (l) {
      l.puntos.forEach(function (p) { todasF.push(p[0]); todosV.push(p[1]); });
      if (l.rango) todosV = todosV.concat(l.rango);
    });
    if (!todosV.length) return "";
    todasF.sort();
    var f0 = Date.parse(todasF[0]), f1 = Date.parse(todasF[todasF.length - 1]);
    if (!(f1 > f0)) f1 = f0 + 1;
    var e = escala(todosV);
    var x = function (f) { return PAD.l + (Date.parse(f) - f0) / (f1 - f0) * (W - PAD.l - PAD.r); };
    var y = function (v) { return PAD.t + (e.max - v) / (e.max - e.min) * (H - PAD.t - PAD.b); };

    var descripcion = lineas.map(function (l) {
      var ult = l.puntos[l.puntos.length - 1];
      return (l.etiqueta || T("admin.reports.patient_summary.serie", null, "Serie")) + ": " + fmtNum(ult[1]) + " "
        + (l.unidad || "") + " " + T("admin.reports.patient_summary.el_dia", null, "el") + " " + fechaBreve(ult[0]);
    }).join(". ");
    var out = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(descripcion) + '">';
    if (lineas.length === 1 && lineas[0].rango) {
      var rango = lineas[0].rango;
      out += '<rect x="' + PAD.l + '" y="' + y(rango[1]).toFixed(1) + '" width="' + (W - PAD.l - PAD.r) +
        '" height="' + Math.max(0, y(rango[0]) - y(rango[1])).toFixed(1) +
        '" fill="var(--done)" opacity="0.09"/>';
    } else if (lineas.length > 1) {
      lineas.forEach(function (l, i) {
        if (!l.rango) return;
        var yy = y(l.rango[1]).toFixed(1);
        out += '<line x1="' + PAD.l + '" x2="' + (W - PAD.r) + '" y1="' + yy + '" y2="' + yy +
          '" stroke="' + (i === 0 ? "var(--rp-chart)" : "var(--rp-chart-2)") + '" stroke-width="1" stroke-dasharray="4 4" opacity=".46"/>';
        out += '<text x="' + (PAD.l + 4) + '" y="' + (Number(yy) - 4).toFixed(1) + '" font-size="9.5" fill="var(--text-3)">' +
          esc((l.etiqueta || T("admin.reports.patient_summary.limite", null, "Límite")) + " ≤" + fmtNum(l.rango[1])) + "</text>";
      });
    }
    // eje: sólo min y max del rango de datos, tinta recesiva
    out += '<text x="' + (PAD.l - 6) + '" y="' + (PAD.t + 8) + '" text-anchor="end" font-size="10" fill="var(--text-3)">' + fmtNum(e.max) + "</text>";
    out += '<text x="' + (PAD.l - 6) + '" y="' + (H - PAD.b) + '" text-anchor="end" font-size="10" fill="var(--text-3)">' + fmtNum(e.min) + "</text>";
    // años primero/último
    out += '<text x="' + PAD.l + '" y="' + (H - 4) + '" font-size="10" fill="var(--text-3)">' + esc(todasF[0].slice(0, 4)) + "</text>";
    out += '<text x="' + (W - PAD.r) + '" y="' + (H - 4) + '" text-anchor="end" font-size="10" fill="var(--text-3)">' + esc(todasF[todasF.length - 1].slice(0, 4)) + "</text>";

    lineas.forEach(function (l, i) {
      var pts = l.puntos.map(function (p) { return x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1); }).join(" ");
      var color = i === 0 ? "var(--rp-chart)" : "var(--rp-chart-2)";
      out += '<polyline points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
      l.puntos.forEach(function (p) {
        out += '<circle cx="' + x(p[0]).toFixed(1) + '" cy="' + y(p[1]).toFixed(1) + '" r="2.5" fill="' + color + '">' +
          "<title>" + esc(fechaBreve(p[0])) + ": " + fmtNum(p[1]) + " " + esc(l.unidad || "") + "</title></circle>";
      });
      var ult = l.puntos[l.puntos.length - 1];
      var fuera = estadoValor(ult[1], l.rango).fuera;
      out += '<circle cx="' + x(ult[0]).toFixed(1) + '" cy="' + y(ult[1]).toFixed(1) + '" r="4" fill="' +
        (fuera ? "var(--danger)" : color) + '" stroke="var(--panel)" stroke-width="1.5"/>';
      // etiqueta directa del último valor (identidad si hay 2 líneas)
      var etiqueta = (lineas.length > 1 ? esc(l.etiqueta || "") + " " : "") + fmtNum(ult[1]);
      out += '<text x="' + (W - PAD.r + 6) + '" y="' + (y(ult[1]) + 4).toFixed(1) + '" font-size="11" font-weight="700" fill="' +
        (fuera ? "var(--danger)" : "var(--text)") + '">' + etiqueta + "</text>";
    });
    return out + "</svg>";
  }

  function sparkSvg(lineas) {
    var sw = 240, sh = 62, sp = { l: 0, r: 0, t: 5, b: 4 };
    var vals = [], fechas = [];
    lineas.forEach(function (l) {
      l.puntos.forEach(function (p) { fechas.push(p[0]); vals.push(p[1]); });
      if (l.rango) vals = vals.concat(l.rango);
    });
    if (!vals.length) return "";
    fechas.sort();
    var f0 = Date.parse(fechas[0]), f1 = Date.parse(fechas[fechas.length - 1]);
    if (!(f1 > f0)) f1 = f0 + 1;
    var e = escala(vals);
    var x = function (f) { return sp.l + (Date.parse(f) - f0) / (f1 - f0) * (sw - sp.l - sp.r); };
    var y = function (v) { return sp.t + (e.max - v) / (e.max - e.min) * (sh - sp.t - sp.b); };
    var desc = lineas.map(function (l) {
      var u = l.puntos[l.puntos.length - 1];
      return (l.etiqueta || T("admin.reports.patient_summary.serie", null, "Serie")) + " " + fmtNum(u[1]) + " " + (l.unidad || "");
    }).join(", ");
    var out = '<svg class="rp-spark" viewBox="0 0 ' + sw + " " + sh + '" preserveAspectRatio="none" role="img" aria-label="' + esc(desc) + '">';
    if (lineas.length === 1 && lineas[0].rango) {
      out += '<rect class="rp-spark-band" x="0" y="' + y(lineas[0].rango[1]).toFixed(1) + '" width="' + sw + '" height="' +
        Math.max(0, y(lineas[0].rango[0]) - y(lineas[0].rango[1])).toFixed(1) + '"/>';
    }
    lineas.forEach(function (l, i) {
      var pts = l.puntos.map(function (p) { return x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1); });
      if (lineas.length === 1) {
        out += '<polygon class="rp-spark-area" points="0,' + sh + " " + pts.join(" ") + " " + sw + "," + sh + '"/>';
      }
      out += '<polyline class="rp-spark-line' + (i ? " rp-spark-line--2" : "") + '" vector-effect="non-scaling-stroke" points="' + pts.join(" ") + '"/>';
      var ult = l.puntos[l.puntos.length - 1];
      var est = estadoValor(ult[1], l.rango);
      var color = est.fuera ? (est.clase === "alta" ? "var(--danger)" : "var(--warn)") : "var(--done)";
      out += '<circle cx="' + x(ult[0]).toFixed(1) + '" cy="' + y(ult[1]).toFixed(1) + '" r="3.8" fill="' + color + '" stroke="var(--panel)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>';
    });
    return out + "</svg>";
  }

  // Un solo lugar para "Tensión arterial"/"Sistólica"/"Diastólica": antes
  // vivían hardcodeadas dos veces (metricasHtml y chartsHtml), y una
  // traducción a inglés sólo hubiera alcanzado a una de las dos.
  function taLabels() {
    return {
      tension: T("admin.reports.patient_summary.tension_arterial", null, "Tensión arterial"),
      sistolica: T("admin.reports.patient_summary.sistolica", null, "Sistólica"),
      diastolica: T("admin.reports.patient_summary.diastolica", null, "Diastólica")
    };
  }

  function metricasHtml(tendencias) {
    if (!tendencias || !tendencias.length) return "";
    var porId = {};
    tendencias.forEach(function (t) { porId[t.id] = t; });
    var usadas = {}, tarjetas = [];
    tendencias.forEach(function (t) {
      if (usadas[t.id] || !t.puntos || !t.puntos.length) return;
      var lineas, label, valor, unidad, estado, rango;
      if ((t.id === "ta_sistolica" || t.id === "ta_diastolica") && porId.ta_sistolica && porId.ta_diastolica) {
        var s = porId.ta_sistolica, d = porId.ta_diastolica;
        usadas.ta_sistolica = usadas.ta_diastolica = true;
        var etiquetasTa = taLabels();
        lineas = [{ puntos: s.puntos, unidad: s.unidad, etiqueta: etiquetasTa.sistolica, rango: s.rango_ok },
                  { puntos: d.puntos, unidad: d.unidad, etiqueta: etiquetasTa.diastolica, rango: d.rango_ok }];
        var su = s.puntos[s.puntos.length - 1][1], du = d.puntos[d.puntos.length - 1][1];
        label = etiquetasTa.tension;
        valor = fmtNum(su) + "/" + fmtNum(du);
        unidad = "mmHg";
        var se = estadoValor(su, s.rango_ok), de = estadoValor(du, d.rango_ok);
        estado = se.fuera || de.fuera
          ? { clase: se.clase === "alta" || de.clase === "alta" ? "alta" : "media",
              texto: T("admin.reports.patient_summary.revisar_ta", null, "Revisar sistólica y diastólica") }
          : { clase: "ok", texto: T("admin.reports.patient_summary.dentro_del_rango", null, "Dentro del rango") };
        rango = T("admin.reports.patient_summary.referencia_ta", null, "Referencia 90–130 / 60–85");
      } else {
        usadas[t.id] = true;
        var ult = t.puntos[t.puntos.length - 1];
        lineas = [{ puntos: t.puntos, unidad: t.unidad, etiqueta: t.label, rango: t.rango_ok }];
        label = t.label;
        valor = fmtNum(ult[1]);
        unidad = t.unidad || "";
        estado = estadoValor(ult[1], t.rango_ok);
        rango = rangoTexto(t.rango_ok);
      }
      tarjetas.push('<section class="rp-metric"><div class="rp-metric-head"><p class="rp-metric-label">' + esc(label) + '</p>' +
        '<div class="rp-metric-now"><span class="rp-metric-value">' + esc(valor) + '</span><span class="rp-metric-unit">' + esc(unidad) + '</span></div></div>' +
        '<p class="rp-metric-status rp-status-' + esc(estado.clase) + '">' + esc(estado.texto) + '</p>' +
        (rango ? '<p class="rp-metric-range">' + esc(rango) + '</p>' : "") + sparkSvg(lineas) + '</section>');
    });
    return tarjetas.length ? '<h4 class="rp-section-title">'
      + esc(T("admin.reports.patient_summary.indicadores_para_revisar", null, "Indicadores para revisar"))
      + '</h4><div class="rp-metrics">' + tarjetas.join("") + '</div>' : "";
  }

  // Empareja TA sistólica+diastólica en UN gráfico; el resto, uno por serie.
  function chartsHtml(tendencias) {
    if (!tendencias || !tendencias.length) return "";
    var porId = {};
    tendencias.forEach(function (t) { porId[t.id] = t; });
    var usadas = {}, bloques = [];
    tendencias.forEach(function (t) {
      if (usadas[t.id]) return;
      var lineas, titulo, comentario = t.comentario || "", latest;
      if ((t.id === "ta_sistolica" || t.id === "ta_diastolica") &&
          porId.ta_sistolica && porId.ta_diastolica) {
        var s = porId.ta_sistolica, d = porId.ta_diastolica;
        usadas.ta_sistolica = usadas.ta_diastolica = true;
        var etiquetasTaChart = taLabels();
        lineas = [{ puntos: s.puntos, unidad: "mmHg", etiqueta: etiquetasTaChart.sistolica, rango: s.rango_ok },
                  { puntos: d.puntos, unidad: "mmHg", etiqueta: etiquetasTaChart.diastolica, rango: d.rango_ok }];
        titulo = etiquetasTaChart.tension;
        latest = fmtNum(s.puntos[s.puntos.length - 1][1]) + "/" + fmtNum(d.puntos[d.puntos.length - 1][1]) + " mmHg";
        comentario = s.comentario || d.comentario || "";
        if (!comentario) {
          var se = estadoValor(s.puntos[s.puntos.length - 1][1], s.rango_ok);
          var de = estadoValor(d.puntos[d.puntos.length - 1][1], d.rango_ok);
          comentario = se.fuera || de.fuera
            ? T("admin.reports.patient_summary.comentario_fuera_ta", null,
              "Sobre los límites de referencia. Revisar mediciones y adherencia en esta consulta.")
            : T("admin.reports.patient_summary.comentario_ok_ta", null,
              "Dentro de los límites de referencia; no requiere prioridad por este indicador.");
        }
      } else {
        usadas[t.id] = true;
        lineas = [{ puntos: t.puntos, unidad: t.unidad, etiqueta: t.label, rango: t.rango_ok }];
        titulo = t.label;
        latest = fmtNum(t.puntos[t.puntos.length - 1][1]) + (t.unidad ? " " + t.unidad : "");
        if (!comentario) {
          var est = estadoValor(t.puntos[t.puntos.length - 1][1], t.rango_ok);
          comentario = est.fuera
            ? est.texto + T("admin.reports.patient_summary.comentario_fuera_generico", null,
              ". Revisar junto con antecedentes y medicación.")
            : (t.rango_ok ? T("admin.reports.patient_summary.comentario_ok_generico", null,
              "Dentro del rango de referencia; no requiere prioridad por este indicador.")
              : T("admin.reports.patient_summary.comentario_sin_rango", null, "Evolución del valor registrado."));
        }
      }
      bloques.push('<section class="rp-chart"><div class="rp-chart-head"><p class="rp-chart-t">' + esc(titulo) + '</p><span class="rp-chart-latest">' + esc(latest) + '</span></div>' +
        chartSvg(lineas) +
        (comentario ? '<p class="rp-chart-c">' + esc(comentario) + "</p>" : "") + "</section>");
    });
    return bloques.length ? '<h4 class="rp-section-title">'
      + esc(T("admin.reports.patient_summary.tendencias_clinicas", null, "Tendencias clínicas"))
      + '</h4><div class="rp-chart-grid">' + bloques.join("") + "</div>" : "";
  }

  // ---- solapas ------------------------------------------------------------

  function pendientesHtml(r) {
    var out = metricasHtml(r.indicadores || r.tendencias);
    (r.advertencias || []).forEach(function (a) {
      out += '<div class="rp-adv">' + esc(a) + "</div>";
    });
    var pend = r.pendientes || [];
    if (pend.length) {
      out += '<div class="rp-priority-head"><h4 class="rp-section-title">'
        + esc(T("admin.reports.patient_summary.prioridades_titulo", null, "Prioridades para esta consulta")) + '</h4></div>';
    }
    out += pend.map(function (p, i) {
      var kpi = p.kpi_valor
        ? '<div class="rp-kpi"><div class="rp-kpi-v rp-' + esc(p.kpi_estado || "atencion") + '">' + esc(p.kpi_valor) +
          '</div><div class="rp-kpi-u">' + esc(p.kpi_unidad || "") + "</div></div>"
        : "";
      return '<div class="rp-item">' +
        '<div class="rp-item-num rp-pri-' + esc(p.prioridad || "media") + '">' + (i + 1) + "</div>" +
        '<div class="rp-item-body"><p class="rp-item-t">' + esc(p.titulo) + "</p>" +
        (p.sub ? '<p class="rp-item-sub">' + esc(p.sub) + "</p>" : "") +
        (p.detalle || p.fuente ? '<details class="rp-evidence"><summary>'
          + esc(T("admin.reports.patient_summary.evidencia", null, "Evidencia")) + '</summary>' +
          (p.detalle ? '<p class="rp-item-det">' + esc(p.detalle) + "</p>" : "") +
          (p.fuente ? '<div class="rp-item-src">' + esc(p.fuente) + "</div>" : "") + '</details>' : "") +
        "</div>" + kpi + "</div>";
    }).join("");
    if (!pend.length && !(r.advertencias || []).length) {
      out += '<p class="rp-vacio">' + esc(T("admin.reports.patient_summary.sin_pendientes", null, "Sin pendientes marcados para esta visita.")) + '</p>';
    }
    var preguntas = r.preguntas || [];
    if (preguntas.length) {
      out += '<h4 class="rp-h4">' + esc(T("admin.reports.patient_summary.preguntas_titulo", null, "Preguntas para conversar")) + '</h4>';
      if (preguntas.length === 1) {
        out += '<p class="rp-ant">' + esc(preguntas[0]) + "</p>";
      } else {
        out += '<ul class="rp-preguntas">' + preguntas.map(function (q) {
          return "<li>" + esc(q) + "</li>";
        }).join("") + "</ul>";
      }
    }
    return out;
  }

  // Estado de medicación: conjunto cerrado (activo/suspendido/verificar) que
  // también nombra la clase CSS del punto de color — la clase se queda con el
  // valor crudo (contrato con el CSS), sólo el TEXTO visible pasa por T().
  function estadoMedLabel(estado) {
    var e = estado || "verificar";
    if (e === "activo") return T("admin.reports.patient_summary.med_activo", null, "activo");
    if (e === "suspendido") return T("admin.reports.patient_summary.med_suspendido", null, "suspendido");
    if (e === "verificar") return T("admin.reports.patient_summary.med_verificar", null, "verificar");
    return e;
  }

  function historiaHtml(r) {
    var out = chartsHtml(r.indicadores || r.tendencias);
    var meds = r.medicacion || [];
    if (meds.length) {
      out += '<h4 class="rp-h4">' + esc(T("admin.reports.patient_summary.medicacion_titulo", null, "Medicación registrada")) + '</h4>' +
        '<table class="cst-med-table"><thead><tr><th>' + esc(T("admin.reports.patient_summary.col_medicamento", null, "Medicamento")) + '</th>'
        + '<th>' + esc(T("admin.reports.patient_summary.col_dosis", null, "Dosis")) + '</th>'
        + '<th>' + esc(T("admin.reports.patient_summary.col_desde", null, "Desde")) + '</th>'
        + '<th>' + esc(T("admin.reports.patient_summary.col_estado", null, "Estado")) + '</th>'
        + '<th class="rp-med-fuente">' + esc(T("admin.reports.patient_summary.col_fuente", null, "Fuente")) + '</th></tr></thead><tbody>' +
        meds.map(function (m) {
          return "<tr><td>" + esc(m.nombre) + "</td><td>" + esc(m.dosis || "—") + "</td><td>" + esc(m.desde || "—") + "</td>" +
            '<td><span class="rp-med-estado"><span class="rp-dot rp-dot-' + esc(m.estado || "verificar") + '"></span>'
            + esc(estadoMedLabel(m.estado)) + "</span></td>" +
            '<td class="rp-item-src rp-med-fuente">' + esc(m.fuente || "") + "</td></tr>";
        }).join("") + "</tbody></table>";
    }
    var ants = r.antecedentes || [];
    if (ants.length) {
      out += '<h4 class="rp-h4">' + esc(T("admin.reports.patient_summary.historia_clinica_titulo", null, "Historia clínica")) + '</h4><ol class="rp-timeline">' + ants.map(function (a) {
        var titulo = String(a.titulo || "");
        var m = /^(.*?)(?:\s*·\s*(\d{4}))$/.exec(titulo);
        var nombre = m ? m[1] : titulo;
        var anio = m ? m[2] : "";
        return '<li class="rp-timeline-item"><span class="rp-timeline-year">' + esc(anio) + '</span><div>' +
          '<p class="rp-timeline-title">' + esc(nombre) + '</p><p class="rp-timeline-text">' + esc(a.texto) + '</p></div></li>';
      }).join("") + '</ol>';
    }
    if (!out) out = '<p class="rp-vacio">' + esc(T("admin.reports.patient_summary.sin_historia", null, "Sin historia previa en los sistemas conectados.")) + '</p>';
    return out;
  }

  // ---- montaje ------------------------------------------------------------

  function externalText(item) {
    // Wire fields are unverified. Read scalar text without claiming a vendor
    // schema, interpreting markup, or exposing identity/technical identifiers.
    var values = [];
    function walk(value, key, depth) {
      if (depth > 6 || values.length >= 30 || /(?:id$|identifier|dni|document|patient|person|token)/i.test(key)) return;
      if (typeof value === "string" && value.trim()) {
        var text = value.trim();
        values.push(/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(text) ? fechaBreve(text.slice(0, 10)) : text);
      } else if (Array.isArray(value)) value.forEach(function (v) { walk(v, "", depth + 1); });
      else if (value && typeof value === "object") Object.keys(value).forEach(function (k) { walk(value[k], k, depth + 1); });
    }
    walk(item, "", 0);
    return Array.from(new Set(values));
  }

  function externalHtml(data) {
    var key = "admin.reports.patient_summary.external_";
    if (data.available !== true) {
      var message;
      if (data.reason === "forbidden") message = T(key + "forbidden", null, "Tu usuario no tiene acceso en HSI");
      else if (data.reason === "not_deployed") message = T(key + "not_deployed", null, "La instalación de HSI no tiene la federación activa");
      else if (data.reason === "not_supported") message = T(key + "not_supported", null, "El conector no admite historia externa");
      else message = T(key + "error", null, "No se pudo cargar la historia");
      return '<p>' + esc(message) + '</p>';
    }
    if (!Array.isArray(data.items) || !data.items.length) {
      return '<p>' + esc(T(key + "empty", null, "Sin historia en otras ciudades")) + '</p>';
    }
    return '<ol>' + data.items.map(function (item) {
      var texts = externalText(item);
      return '<li>' + (texts.length ? texts.map(function (text) { return '<p>' + esc(text) + '</p>'; }).join("")
        : esc(T(key + "unreadable", null, "Registro sin texto disponible"))) + '</li>';
    }).join("") + '</ol>';
  }

  function bindExternal(cont, opts) {
    var block = cont.querySelector(".rp-external");
    if (!block) return;
    var button = block.querySelector(".rp-external-load");
    var output = block.querySelector(".rp-external-output");
    button.addEventListener("click", async function () {
      if (button.disabled) return;
      button.disabled = true;
      button.textContent = T("admin.reports.patient_summary.external_loading", null, "Cargando…");
      var controller = new AbortController();
      var timeout = setTimeout(function () { controller.abort(); }, 35000);
      try {
        var result = await api("/api/admin/patient-external-history?dni=" + encodeURIComponent(opts.dni), { signal: controller.signal });
        // A summary refresh, navigation or another patient owns a new block.
        if (cont.querySelector(".rp-external") !== block) return;
        var data = result.j || {};
        if (data.reason === "flag_off") { block.hidden = true; output.innerHTML = ""; return; }
        if (!result.ok && !data.reason) data = { available: false, reason: "error" };
        output.innerHTML = externalHtml(data);
        button.textContent = data.reason === "error"
          ? T("admin.reports.patient_summary.external_retry", null, "Reintentar")
          : T("admin.reports.patient_summary.external_load", null, "Cargar historia");
      } catch (_) {
        if (cont.querySelector(".rp-external") !== block) return;
        output.innerHTML = externalHtml({ available: false, reason: "error" });
        button.textContent = T("admin.reports.patient_summary.external_retry", null, "Reintentar");
      } finally { clearTimeout(timeout); button.disabled = false; }
    });
  }

  // The AI notice (ai.notice.clinical, Decreto 742/2026) leads the summary.
  // A screen that already shows the same notice next to its own AI text (the
  // consultation) mounts with `aiNotice: false`, so it reads once per screen.
  function avisoIA(opts) {
    if (opts && opts.aiNotice === false) return "";
    return '<p style="font-size:16px;color:var(--text);">' + esc(T("ai.notice.clinical", null, "Contenido elaborado con inteligencia artificial. Revisá las fuentes antes de confirmar o usarlo en la atención.")) + '</p>';
  }

  function render(cont, data, opts) {
    var r = data.resumen || {};
    var pendN = (r.pendientes || []).length + (r.advertencias || []).length;
    var relativo = (data.generado && Dates && Dates.relativo(data.generado)) || "";
    var vacio = !pendN && !(r.preguntas || []).length && !(r.medicacion || []).length &&
      !(r.antecedentes || []).length && !(r.tendencias || []).length && !(r.indicadores || []).length;

    var html = avisoIA(opts) + '<div class="rp-card">' +
      '<p class="rp-titular">' + esc(r.titular || T("admin.reports.patient_summary.titulo_default", null, "Resumen del paciente")) + "</p>" +
      '<div class="rp-meta">' +
      (relativo ? "<span>" + esc(T("admin.reports.patient_summary.generado", { fecha: relativo }, "Generado " + relativo)) + "</span>" : "") +
      (data.especialidad ? "<span>" + esc(T("admin.reports.patient_summary.filtrado_para", { especialidad: data.especialidad },
        "Filtrado para " + data.especialidad)) + "</span>" : "") +
      '<button type="button" class="rp-refresh">' + esc(T("admin.reports.patient_summary.actualizar", null, "Actualizar")) + '</button>' +
      "</div>";

    if (!vacio) {
      html += '<div class="rp-tabs" role="tablist">' +
        '<button type="button" class="rp-tab on" data-tab="pend" role="tab">' + esc(T("admin.reports.patient_summary.tab_pendientes", null, "Pendientes")) +
        (pendN ? '<span class="rp-tab-n">' + pendN + "</span>" : "") + "</button>" +
        '<button type="button" class="rp-tab" data-tab="hist" role="tab">' + esc(T("admin.reports.patient_summary.tab_historia", null, "Historia")) + '</button>' +
        "</div>" +
        '<div data-panel="pend">' + pendientesHtml(r) + "</div>" +
        '<div data-panel="hist" style="display:none">' + historiaHtml(r) + "</div>";
    }
    if (r.fuentes && r.fuentes.length) {
      html += '<div class="rp-fuentes"><span class="rp-fuentes-t">' + esc(T("admin.reports.patient_summary.fuentes_titulo", null, "Fuentes")) + '</span>' +
        (r.fuentes.length === 1 ? '<p>' + esc(r.fuentes[0]) + '</p>' : '<ul>' + r.fuentes.map(function (f) {
          return '<li>' + esc(f) + '</li>';
        }).join("") + '</ul>') + '</div>';
    }
    if (data.external_history_enabled === true) {
      html += '<details class="rp-external"><summary>'
        + esc(T("admin.reports.patient_summary.external_title", null, "Historia en otras ciudades"))
        + '</summary><button type="button" class="rp-external-load">'
        + esc(T("admin.reports.patient_summary.external_load", null, "Cargar historia"))
        + '</button><div class="rp-external-output" role="status" aria-live="polite"></div></details>';
    }
    cont.innerHTML = html + "</div>";
    bindExternal(cont, opts);

    cont.querySelectorAll(".rp-tab").forEach(function (b) {
      b.addEventListener("click", function () {
        cont.querySelectorAll(".rp-tab").forEach(function (x) { x.classList.toggle("on", x === b); });
        cont.querySelectorAll("[data-panel]").forEach(function (p) {
          p.style.display = p.getAttribute("data-panel") === b.getAttribute("data-tab") ? "" : "none";
        });
      });
    });
    var refresh = cont.querySelector(".rp-refresh");
    if (refresh) refresh.addEventListener("click", function () {
      refresh.disabled = true;
      refresh.textContent = T("admin.reports.patient_summary.actualizando", null, "Actualizando…");
      api("/api/admin/patient-summary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dni: opts.dni, medico: opts.medicoId || undefined })
      }).then(function (res) {
        if (res.ok) {
          CACHE[opts.dni + "|" + (opts.medicoId || "")] = res.j;
          render(cont, res.j, opts);
          return;
        }
        // La demo de solo lectura lo rechaza: su frase única (TASK-48), no
        // un botón que vuelve sin decir nada.
        if (res.j && res.j.error === "demo_solo_lectura" && window.toast && I18n && I18n.apiError) {
          window.toast(I18n.apiError(res.j, ""));
        }
        throw new Error("refresh");
      }).catch(function () {
        refresh.disabled = false;
        refresh.textContent = T("admin.reports.patient_summary.actualizar", null, "Actualizar");
      });
    });
  }

  // Cache por (dni, médico): un re-render de la pantalla que nos montó no
  // debe re-pedir (ni re-generar) el resumen, ni mostrar un frame intermedio
  // de "Generando…" sobre contenido que ya se vio (regla de un solo paint).
  var CACHE = {};

  function montar(cont, opts) {
    if (!cont || !opts || !opts.dni) return;
    inyectarCss();
    var clave = opts.dni + "|" + (opts.medicoId || "");
    if (CACHE[clave]) { render(cont, CACHE[clave], opts); return; }
    cont.innerHTML = avisoIA(opts) + '<div class="rp-card"><p class="rp-vacio">'
      + esc(T("admin.reports.patient_summary.generando", null, "Generando el resumen del paciente…")) + '</p></div>';
    var qs = "?dni=" + encodeURIComponent(opts.dni) +
      (opts.medicoId ? "&medico=" + encodeURIComponent(opts.medicoId) : "");
    api("/api/admin/patient-summary" + qs).then(function (res) {
      if (!res.ok) { cont.innerHTML = ""; return; }
      CACHE[clave] = res.j;
      render(cont, res.j, opts);
    }).catch(function () { cont.innerHTML = ""; });
  }

export function mount(cont, opts) {
  return datesReady.then(function () { return montar(cont, opts); });
}
