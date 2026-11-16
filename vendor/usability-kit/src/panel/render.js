'use strict';
/* The ONE panel renderer, used server-side by Node hosts and in the browser
 * by client.js. Pure: no DOM, no filesystem, no fetch — render(store, opts)
 * in, {html, css} out. The html is a fragment (no <html>/<body>, no <script>
 * at all, CSP-safe); the css is scoped under .ukit and leans on the host's
 * --ink/--line/--bg/--danger/--good variables when present, falling back to
 * neutrals that flip with prefers-color-scheme.
 *
 * Browser load order: store/rank.js (window.UsabilityKitRank) then this file
 * (window.UsabilityKitPanel). In Node both load through require().
 *
 * Content, top to bottom: status line (worst counts, last run, Run preflight)
 * · Worst problems · Findings table (Problem/Pages/Severity/Seen by/Status/
 * Evidence, filter links) · Sessions · Runs. */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('../store/rank.js'));
  } else {
    const rank = root.UsabilityKitRank || null;
    root.UsabilityKitPanel = factory(rank); /* eslint-disable-line no-param-reassign */
  }
})(typeof self !== 'undefined' ? self : this, function (rank) {
  const SEV_LABEL = {
    critical: ['Critical', 'Crítico'],
    serious: ['Serious', 'Grave'],
    moderate: ['Moderate', 'Moderado'],
    minor: ['Minor', 'Menor'],
  };
  const STATUS_LABEL = {
    open: ['Open', 'Abierto'],
    fixed: ['Fixed, awaiting confirmation', 'Corregido, sin confirmar'],
    confirmed: ['Confirmed', 'Confirmado'],
    failing: ['Still failing', 'Sigue fallando'],
  };
  const RUN_STATUS = {
    queued: ['Queued', 'En cola'],
    running: ['Running', 'Ejecutando'],
    done: ['Done', 'Hecho'],
    failed: ['Failed', 'Falló'],
  };
  const OUTCOME_LABEL = { success: ['Success', 'Éxito'], fail: ['Fail', 'Fallo'], abandon: ['Abandoned', 'Abandonó'] };

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  /* Relative dates, digits not words, both languages. */
  function when(iso, lang, now) {
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return null;
    const es = lang === 'es';
    const mins = Math.max(0, Math.round(((now != null ? Date.parse(now) : Date.now()) - t) / 60000));
    if (mins < 1) return es ? 'Recién' : 'Just now';
    if (mins < 60) return es ? `Hace ${mins} min` : `${mins} min ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return es ? `Hace ${hours} h` : `${hours} h ago`;
    const days = Math.round(hours / 24);
    if (days === 1) return es ? 'Ayer' : 'Yesterday';
    if (days < 30) return es ? `Hace ${days} días` : `${days} days ago`;
    const d = new Date(t);
    const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()];
    const monEs = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'][d.getUTCMonth()];
    return es ? `${d.getUTCDate()} ${monEs}` : `${mon} ${d.getUTCDate()}`;
  }

  const CSS = `
.ukit{--uk-ink:var(--ink,#202124);--uk-line:var(--line,#dadce0);--uk-line-2:var(--line,#c4c7cb);
--uk-danger:var(--danger,#c5221f);--uk-good:var(--good,#188038);--uk-link:#1a56c4;
--uk-amber:#8a5a00;--uk-gutter:16px;
display:flex;flex-direction:column;gap:26px;color:var(--uk-ink);
font-size:15px;line-height:1.5;text-wrap:pretty;max-width:1180px}
.ukit *{box-sizing:border-box}
@media(prefers-color-scheme:dark){
.ukit{--uk-ink:var(--ink,#e9e7e2);--uk-line:var(--line,#3c4043);--uk-line-2:var(--line,#4a4e52);
--uk-danger:var(--danger,#f28b82);--uk-good:var(--good,#81c995);--uk-link:#8ab4f8;--uk-amber:#fdd663}
}
.ukit h2{font-size:20px;line-height:1.25;font-weight:800;letter-spacing:-.01em;margin:0;text-wrap:balance;color:var(--uk-ink)}
.ukit a{color:var(--uk-link)}
.ukit code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;color:var(--uk-ink)}
.ukit-status{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin:0;font-size:16px}
.ukit-status .sep{color:var(--uk-ink);opacity:.55}
.ukit-status b{font-weight:700}
.ukit-status form{display:inline}
.ukit button{font-family:inherit;font-weight:600;font-size:14px;line-height:1.2;
min-height:36px;padding:8px 14px;border:0;border-radius:6px;
background:var(--ink,#202124);color:var(--bg,#fff);cursor:pointer}
.ukit button:disabled{opacity:.45;cursor:default}
.ukit .ukit-why{font-size:14px;color:var(--uk-ink)}
@media(prefers-color-scheme:dark){.ukit button{background:var(--ink,#e9e7e2);color:var(--bg,#202124)}}
.ukit-worst{list-style:none;margin:0;padding:0}
.ukit-worst li{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;padding:10px 0;border-top:1px solid var(--uk-line)}
.ukit-worst li:first-child{border-top:0}
.ukit-worst .t{font-weight:700;min-width:16ch}
.ukit-worst .m{color:var(--uk-ink)}
.ukit-worst .m code{font-size:14px}
.ukit-filters{display:flex;flex-wrap:wrap;gap:4px 18px;margin:0 0 6px}
.ukit-filters a{font-size:15px;font-weight:500;text-decoration:none;padding:2px 0}
.ukit-filters a[aria-current]{font-weight:800;color:var(--uk-ink);border-bottom:2px solid var(--uk-ink)}
.ukit-table{margin:0}
.ukit table{width:100%;border-collapse:collapse;font-size:14.5px}
.ukit th{font-size:14px;font-weight:600;text-align:left;vertical-align:bottom;
padding:8px 10px;border-bottom:1px solid var(--uk-line-2);white-space:nowrap}
.ukit td{vertical-align:top;padding:10px;border-bottom:1px solid var(--uk-line)}
.ukit tr:last-child td{border-bottom:0}
.ukit .p{font-weight:600;min-width:20ch;max-width:42ch;overflow-wrap:anywhere}
.ukit .pg{max-width:22ch}
.ukit .pg code{display:block;overflow-wrap:anywhere}
.ukit .sv{font-weight:700;white-space:nowrap}
.ukit .sn{white-space:nowrap;font-variant-numeric:tabular-nums}
.ukit .st{white-space:nowrap;font-weight:600}
.ukit .st[data-s="open"],.ukit .st[data-s="failing"]{color:var(--uk-danger)}
.ukit .st[data-s="fixed"]{color:var(--uk-amber)}
.ukit .st[data-s="confirmed"]{color:var(--uk-good)}
.ukit .shots{display:flex;gap:8px;align-items:flex-start}
.ukit .shots a{display:block;border:1px solid var(--uk-line);border-radius:4px;overflow:hidden;line-height:0}
.ukit .shots img{display:block;width:120px;height:75px;object-fit:cover;object-position:top}
.ukit .oc{font-weight:600;white-space:nowrap}
.ukit .oc[data-o="success"]{color:var(--uk-good)}
.ukit .oc[data-o="fail"]{color:var(--uk-danger)}
.ukit .rs{font-weight:600;white-space:nowrap}
.ukit .rs[data-r="failed"]{color:var(--uk-danger)}
.ukit .rs[data-r="running"],.ukit .rs[data-r="queued"]{color:var(--uk-amber)}
.ukit .rs[data-r="done"]{color:var(--uk-good)}
.ukit .num{font-variant-numeric:tabular-nums}
@media(max-width:767px){
.ukit{font-size:15px;gap:22px}
.ukit-table{margin:0 calc(var(--uk-gutter) * -1)}
.ukit table,.ukit tbody,.ukit tr,.ukit td{display:block;width:100%}
.ukit thead{display:none}
.ukit tr{border-top:1px solid var(--uk-line-2);padding:6px 0 2px}
.ukit td{border:0;padding:3px var(--uk-gutter);white-space:normal}
.ukit td.p{font-weight:700}
.ukit td:not(.p):not(.shots):before{content:attr(data-l) ": ";font-weight:500;opacity:.75}
.ukit .shots{padding:6px var(--uk-gutter) 8px}
.ukit .shots img{width:calc(50vw - var(--uk-gutter) * 2);height:auto;aspect-ratio:8/5}
}
`;

  /* ---- pieces ---- */

  function sevLabel(sev, lang) {
    const s = SEV_LABEL[sev] || SEV_LABEL.minor;
    return s[lang === 'es' ? 1 : 0];
  }

  function seenBy(f, t) {
    if (f.source === 'simulated' || f.participants_ran) {
      return t(`${f.participants_hit} of ${f.participants_ran}`, `${f.participants_hit} de ${f.participants_ran}`);
    }
    const vs = (f.viewports || []).slice().sort((a, b) => a - b);
    return vs.length ? esc(vs.join(', ')) : '—';
  }

  function shot(base, f, slot) {
    const last = (f.occurrences || []).slice().reverse().find((o) => o.evidence && o.evidence[slot]);
    const file = last && last.evidence[slot];
    if (!file) return '';
    const name = file.split('/').pop();
    return `<a href="${esc(base)}/evidence/${esc(f.id)}/${esc(name)}"><img src="${esc(base)}/evidence/${esc(f.id)}/${esc(name)}" alt="${esc(sevLabel(f.severity, 'en'))}: ${esc(f.title)} (${slot})" loading="lazy"></a>`;
  }

  function runLine(store, t, lang, now) {
    const worstCounts = { critical: 0, serious: 0, moderate: 0, minor: 0 };
    for (const f of rank.worst(store, Infinity)) worstCounts[f.severity] += 1;
    const parts = [];
    const worstText = ['critical', 'serious', 'moderate', 'minor']
      .filter((s) => worstCounts[s]).map((s) => `<b>${worstCounts[s]} ${esc(sevLabel(s, lang))}</b>`);
    parts.push(worstText.length
      ? t(`${worstText.join('<span class="sep">·</span>')} open`, `${worstText.join('<span class="sep">·</span>')} abiertos`)
      : `<b>${t('No open problems', 'Sin problemas abiertos')}</b>`);
    let lastRun = null;
    for (const r of store.runs || []) {
      if (r.status === 'done' || r.status === 'failed') lastRun = r;
    }
    const w = lastRun && when(lastRun.finished_at || lastRun.requested_at, lang, now);
    if (w) parts.push(`${t('Last run', 'Última corrida')} ${esc(w)}`);
    return parts.join('<span class="sep">·</span>');
  }

  function runButton(store, opts, t) {
    const running = (store.runs || []).some((r) => r.status === 'queued' || r.status === 'running');
    const canRun = opts.canRun !== undefined ? opts.canRun : true;
    const dis = running || !canRun;
    const label = running ? t('Running…', 'Ejecutando…') : t('Run preflight', 'Correr preflight');
    const csrf = opts.csrf ? `<input type="hidden" name="csrf" value="${esc(opts.csrf)}">` : '';
    const why = !running && !canRun
      ? `<span class="ukit-why">${esc(opts.runDisabledReason || t('Runner not available here', 'No hay runner aquí'))}</span>`
      : '';
    return `<form method="post" action="${esc(opts.base)}/run">${csrf}<button type="submit"${dis ? ' disabled' : ''}>${esc(label)}</button></form>${why}`;
  }

  function worstSection(store, t, lang, now) {
    const list = rank.worst(store, 5);
    if (!list.length) return '';
    const items = list.map((f) => {
      const m = [
        `<code>${esc((f.pages || []).join(', ') || '—')}</code>`,
        esc(sevLabel(f.severity, lang)),
        f.source === 'simulated' || f.participants_ran ? esc(t(`${f.participants_hit} of ${f.participants_ran}`, `${f.participants_hit} de ${f.participants_ran}`)) : '',
      ].filter(Boolean).join('<span class="sep">·</span>');
      return `<li><span class="t">${esc(f.title)}</span><span class="m">${m}</span></li>`;
    }).join('');
    return `<section><h2>${t('Worst problems', 'Peores problemas')}</h2><ol class="ukit-worst">${items}</ol></section>`;
  }

  function filters(active, counts, t) {
    const mk = (id, label) => {
      const n = id === 'all' ? counts.total : id === 'open' ? counts.open + counts.failing : counts[id];
      const cur = active === id ? ' aria-current="true"' : '';
      const href = id === 'all' ? '?' : `?status=${id}`;
      return `<a href="${href}"${cur}>${label} ${n}</a>`;
    };
    return `<nav class="ukit-filters" aria-label="${t('Filter findings', 'Filtrar hallazgos')}">${
      mk('all', t('All', 'Todos'))}${mk('open', t('Open', 'Abiertos'))}${mk('fixed', t('Fixed', 'Corregidos'))}${mk('confirmed', t('Confirmed', 'Confirmados'))}</nav>`;
  }

  function findingsSection(store, opts, t, lang) {
    const counts = rank.counts(store.findings);
    if (!counts.total) return '';
    const active = opts.status && opts.status !== 'all' ? opts.status : 'all';
    const rows = rank.bySeverity(rank.statusFilter(store.findings, active)).map((f) => {
      const st = STATUS_LABEL[f.status] || STATUS_LABEL.open;
      return `<tr>` +
        `<td class="p">${esc(f.title)}</td>` +
        `<td class="pg" data-l="${t('Pages', 'Páginas')}">${(f.pages || []).map((p) => `<code>${esc(p)}</code>`).join('') || '—'}</td>` +
        `<td class="sv" data-l="${t('Severity', 'Gravedad')}">${esc(sevLabel(f.severity, lang))}</td>` +
        `<td class="sn" data-l="${t('Seen by', 'Visto por')}">${seenBy(f, t)}</td>` +
        `<td class="st" data-s="${esc(f.status)}" data-l="${t('Status', 'Estado')}">${esc(lang === 'es' ? st[1] : st[0])}</td>` +
        `<td class="shots">${shot(opts.base, f, 'before')}${shot(opts.base, f, 'after')}</td>` +
        `</tr>`;
    }).join('');
    return `<section><h2>${t('Findings', 'Hallazgos')}</h2>${filters(active, counts, t)}`
      + `<div class="ukit-table"><table><thead><tr>` +
      `<th>${t('Problem', 'Problema')}</th><th>${t('Pages', 'Páginas')}</th><th>${t('Severity', 'Gravedad')}</th>` +
      `<th>${t('Seen by', 'Visto por')}</th><th>${t('Status', 'Estado')}</th><th>${t('Evidence', 'Evidencia')}</th>` +
      `</tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function sessionsSection(store, t, lang, now) {
    const sessions = [...(store.sessions || [])].sort((a, b) => String(b.at || '').localeCompare(String(a.at || ''))).slice(0, 10);
    if (!sessions.length) return '';
    const rows = sessions.map((s) => {
      const oc = OUTCOME_LABEL[s.outcome] || OUTCOME_LABEL.abandon;
      return `<tr>` +
        `<td class="p">${esc(s.persona)}</td>` +
        `<td data-l="${t('Task', 'Tarea')}">${esc(s.task || '—')}</td>` +
        `<td class="oc" data-o="${esc(s.outcome)}" data-l="${t('Outcome', 'Resultado')}">${esc(lang === 'es' ? oc[1] : oc[0])}</td>` +
        `<td class="num" data-l="${t('Steps', 'Pasos')}">${Number(s.steps) || 0}</td>` +
        `<td class="sn" data-l="${t('When', 'Cuándo')}">${esc(when(s.at, lang, now) || '—')}</td>` +
        `</tr>`;
    }).join('');
    return `<section><h2>${t('Sessions', 'Sesiones')}</h2><div class="ukit-table"><table><thead><tr>` +
      `<th>${t('Persona', 'Persona')}</th><th>${t('Task', 'Tarea')}</th><th>${t('Outcome', 'Resultado')}</th>` +
      `<th>${t('Steps', 'Pasos')}</th><th>${t('When', 'Cuándo')}</th>` +
      `</tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function countsText(summary, t) {
    if (!summary) return '—';
    const parts = ['critical', 'serious', 'moderate', 'minor']
      .filter((s) => summary[s]).map((s) => `${summary[s]} ${s}`);
    return parts.length ? esc(parts.join(', ')) : t('Clean', 'Limpio');
  }

  function runsSection(store, t, lang, now) {
    const runs = [...(store.runs || [])].sort((a, b) => String(b.requested_at || '').localeCompare(String(a.requested_at || ''))).slice(0, 10);
    if (!runs.length) return '';
    const rows = runs.map((r) => {
      const st = RUN_STATUS[r.status] || RUN_STATUS.queued;
      return `<tr>` +
        `<td class="p">${r.kind === 'study' ? t('Study', 'Estudio') : t('Preflight', 'Preflight')}</td>` +
        `<td class="rs" data-r="${esc(r.status)}" data-l="${t('Status', 'Estado')}">${esc(lang === 'es' ? st[1] : st[0])}</td>` +
        `<td class="sn" data-l="${t('When', 'Cuándo')}">${esc(when(r.finished_at || r.requested_at, lang, now) || '—')}</td>` +
        `<td data-l="${t('Counts', 'Conteos')}">${countsText(r.summary, t)}</td>` +
        `</tr>`;
    }).join('');
    return `<section><h2>${t('Runs', 'Corridas')}</h2><div class="ukit-table"><table><thead><tr>` +
      `<th>${t('Kind', 'Tipo')}</th><th>${t('Status', 'Estado')}</th><th>${t('When', 'Cuándo')}</th><th>${t('Counts', 'Conteos')}</th>` +
      `</tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  /* render(store, {base, lang, canRun, csrf, status, now, runDisabledReason})
   * → {html, css}. Pure; `now` (ISO) is injectable for deterministic tests. */
  function render(storeData, opts = {}) {
    const s = storeData || {};
    s.findings = s.findings || [];
    s.runs = s.runs || [];
    s.sessions = s.sessions || [];
    const lang = opts.lang === 'es' ? 'es' : 'en';
    const t = (en, es) => (lang === 'es' ? es : en);
    const base = String(opts.base || '').replace(/\/+$/, '');
    const html = `<div class="ukit" lang="${lang}">` +
      `<div class="ukit-status">${runLine(s, t, lang, opts.now)}${runButton(s, opts, t)}</div>` +
      worstSection(s, t, lang, opts.now) +
      findingsSection(s, opts, t, lang) +
      sessionsSection(s, t, lang, opts.now) +
      runsSection(s, t, lang, opts.now) +
      `</div>`;
    return { html, css: CSS.trim() };
  }

  return { render, esc, when };
});
