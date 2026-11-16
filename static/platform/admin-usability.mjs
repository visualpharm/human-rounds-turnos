/* Anonymous results and findings. All participant text is rendered as text. */
export const css = `
/* One left edge and one rhythm (8/16/24, product owner 2026-10-16): every heading,
   paragraph, list, table cell and form label starts on the same line; a
   number reads as a phrase («2/5 completaron»), never a label + value pair. */
.ux{font-size:16px;line-height:1.5;color:var(--text,#222);text-wrap:pretty;min-width:0}
.ux>*{margin:0 0 16px}.ux p{margin:0 0 8px}.ux section{margin:0 0 24px}
.ux h2{font-size:22px;line-height:1.3;margin:24px 0 8px}.ux h3{font-size:18px;line-height:1.3;margin:16px 0 8px}
.ux nav,.ux-actions,.ux-filters{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:0 0 16px}
.ux nav a{min-height:44px;display:inline-flex;align-items:center}.ux nav [aria-current=page]{font-weight:600}
.ux button,.ux select,.ux input,.ux textarea,.ux summary{font:inherit;min-height:44px;color:var(--text,#222);max-width:100%;box-sizing:border-box}
.ux button{width:auto}.ux input,.ux textarea{padding:8px}.ux textarea{width:100%;min-height:90px}
.ux-tasks{list-style:none;margin:0 0 16px;padding:0}.ux-tasks>li{padding:12px 0;border-top:1px solid var(--border,#d3d8de)}
.ux-tasks>li:first-child{border-top:0;padding-top:0}.ux-tasks strong{display:block;font-weight:600}
.ux-tasks span{display:block}
.ux-story>li{padding:0}.ux-story>li:first-child{padding-top:0}.ux-story summary{padding:10px 0}
.ux-story details[open]{padding-bottom:16px}.ux-story details[open]>summary{font-weight:600}
.ux-story p{margin:0 0 8px;max-width:72ch}.ux-story a{display:inline-flex;align-items:center;min-height:44px}
.ux table{width:100%;table-layout:auto;border-collapse:collapse}
.ux th,.ux td{text-align:left;padding:12px 16px 12px 0;vertical-align:top;overflow-wrap:anywhere;border-top:1px solid var(--border,#d3d8de)}
.ux thead th{border-top:0;padding-top:0;font-weight:600}
.ux td button{white-space:normal}.ux .ux-status{background:var(--surface,#f1f3f5);border:1px solid var(--border,#bdc5ce);border-radius:16px;padding:6px 12px}
.ux details{position:relative}.ux summary{cursor:pointer}
.ux-filters summary{padding:8px 12px;border:1px solid var(--border,#d3d8de);border-radius:8px}
.ux details>div{padding:8px 0}.ux details label{display:flex;align-items:center;gap:8px;min-height:44px}
.ux input[type=checkbox]{width:20px;min-height:20px;height:20px;margin:0}
/* Form rows keep form-design §5: label right-aligned on the left, stacked on narrow screens. */
.ux-row{display:grid;grid-template-columns:160px minmax(0,1fr);gap:8px 16px;margin:0 0 16px;align-items:start}
.ux-row label{text-align:right;padding-top:10px}.ux-row select{width:max-content}
.ux-row input,.ux-row textarea{width:100%;max-width:640px}.ux code{font:inherit}
@media(max-width:1040px){.ux table,.ux tbody,.ux tr,.ux td{display:block}.ux thead{display:none}
.ux tr{padding:12px 0;border-top:1px solid var(--border,#d3d8de)}.ux td{padding:4px 0;border-top:0}
.ux td:before{content:attr(data-label);display:block;font-weight:600}
}
/* At ≤720px the shell's content wrap drops its side padding (admin-direct.css);
   the screen keeps the same 16px edge as its title. */
@media(max-width:720px){.ux{padding:0 16px}}
@media(max-width:640px){.ux-row{grid-template-columns:1fr}.ux-row label{text-align:left;padding-top:0}}
`;
export function summarize(data, role) {
  const sessions = data.sessions.filter(s => s.role === role);
  return {participants: sessions.length, finished: sessions.filter(s => s.finished_at).length,
    tasks: data.definition.roles[role].tasks.map(task => {
      const rows = data.results.filter(r => r.role === role && r.task_id === task.id);
      const rated = rows.filter(r => r.ease != null);
      return {...task, completed: rows.filter(r => r.outcome === 'ok' || r.outcome === 'doubt').length,
        ease: rated.length ? rated.reduce((sum, r) => sum + r.ease, 0) / rated.length : null,
        stuck: rows.filter(r => r.outcome === 'stuck').length};
    })};
}
// The story the page opens with: who took part, what was found and what
// became of each finding (the product owner 2026-10-18, «what we found, what we did»).
export const STORY_ORDER = ['fixed', 'in_progress', 'not_done', 'found', 'dismissed'];
const SEVERITY_RANK = {high: 0, medium: 1, low: 2};
export function story(data) {
  const roles = {};
  for (const role of Object.keys(data.definition.roles)) roles[role] = data.sessions.filter(s => s.role === role).length;
  const groups = {};
  for (const status of STORY_ORDER) {
    groups[status] = data.findings.filter(f => f.status === status)
      .sort((a, b) => (SEVERITY_RANK[a.severity] ?? 3) - (SEVERITY_RANK[b.severity] ?? 3) || a.id - b.id);
  }
  return {people: data.sessions.length, finished: data.sessions.filter(s => s.finished_at).length,
    roles, period: data.period || null, findings: data.findings.length, groups};
}
// Progressive disclosure for the findings tab (the product owner, 2026-10-26: «if there's
// no content, why to show all the ui?»). No findings → no filters, no bulk
// form, no table: one empty state and the two ways to get findings. Filters
// that hide every finding → the filters stay, with a way to clear them. A
// filter shows only when the findings have two values to choose between, or
// when it is already set.
// `Filters` is the shared rule (admin-filter-controls.mjs: disclosure and
// offerFilter, so under FILTER_MIN_ROWS findings no filter shows), passed in
// because this module loads its dependencies at mount time.
export function findingsView(data, selected, Filters) {
  const all = data.findings || [];
  const rows = all.filter(f => (!selected.status.size || selected.status.has(f.status))
    && (!selected.role.size || selected.role.has(f.role)));
  const filtered = selected.status.size > 0 || selected.role.size > 0;
  const state = Filters.disclosure({total: all.length, shown: rows.length, filtered});
  const distinct = key => new Set(all.map(f => f[key])).size;
  const offer = key => state !== 'empty' && Filters.offerFilter(distinct(key), selected[key].size > 0, all.length);
  return {state, rows,
    statusFilter: offer('status'),
    roleFilter: offer('role'),
    bulk: !!data.editable && rows.length > 0};
}
export function mount(host) {
  let disposed = false, generation = 0, controller, data, tab = 'summary', Table, I18n, Dates, Filters, fits = [];
  let selected = {status: new Set(), role: new Set()};
  const selectedFindings = new Set();
  const style = document.createElement('style'); style.textContent = css; host.append(style);
  const root = document.createElement('div'); root.className = 'ux'; host.append(root);
  function T(key, vars, fallback) { return I18n ? I18n.t(key, vars, fallback) : fallback; }
  // One decimal through the panel's one number formatter («2,5», «3»).
  function num(n) { return I18n && I18n.formatNumber ? I18n.formatNumber(n, 1) : (Math.round(n * 10) / 10).toString(); }
  // Number agreement through i18n.mjs tn() — «1 persona», never «1 personas».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }
  function node(tag, text, attrs = {}) {
    const e = document.createElement(tag); if (text != null) e.textContent = text;
    for (const [k,v] of Object.entries(attrs)) e.setAttribute(k, v); return e;
  }
  function action(text, fn) { const b = node('button', text, {type:'button',class:'btn'}); b.onclick = fn; return b; }
  function labelStatus(status) {
    const labels = {found: 'Encontrado', in_progress: 'En curso', fixed: 'Corregido', not_done: 'No realizado', dismissed:'Descartado'};
    return T('study.status.' + status, {}, labels[status] || status);
  }
  function roleName(role) { return data.definition.roles[role]?.label || role; }
  function taskName(role, tid) {
    return data.definition.roles[role]?.tasks.find(t => t.id === tid)?.instruction
      || data.definition.final_questions.find(q => q.id === tid)?.title || tid;
  }
  function signal(kind) {
    const labels={difficulty:'Dudas o bloqueo',low_ease:'Poca facilidad',duration:'Tarea lenta',
      dead_click:'Clics sin respuesta',backtrack:'Idas y vueltas',abandoned:'Tarea abandonada'};
    return T('study.signal.'+kind,{},labels[kind] || kind);
  }
  function evidence(f) {
    if(f.source!=='auto') return f.detail;
    const e=f.evidence_summary || {}, vars={n:e.participants,total:e.total};
    const fallbacks={difficulty:'{n} de {total} respuestas con dudas o bloqueo.',
      low_ease:'{n} de {total} respuestas con facilidad de 1 o 2 sobre 5.',
      dead_click:'{n} de {total} participantes tuvieron clics sin respuesta.',
      backtrack:'{n} de {total} participantes volvieron varias veces entre las mismas páginas.',
      abandoned:'{n} de {total} participantes dejaron esta tarea sin completar.'};
    // The noun agrees with the total: «1 de 1 participante».
    const ones={difficulty:'{n} de {total} respuesta con dudas o bloqueo.',
      low_ease:'{n} de {total} respuesta con facilidad de 1 o 2 sobre 5.',
      dead_click:'{n} de {total} participante tuvo clics sin respuesta.',
      backtrack:'{n} de {total} participante volvió varias veces entre las mismas páginas.',
      abandoned:'{n} de {total} participante dejó esta tarea sin completar.'};
    if(f.kind==='duration') {
      vars.median=num(e.median_ms/60000);vars.threshold=num(e.threshold_ms/60000);
      return T('study.evidence.duration',vars,'Duración mediana: {median} min. Referencia: {threshold} min.');
    }
    return Tn('study.evidence.'+f.kind,e.total,vars,ones[f.kind] || null,fallbacks[f.kind] || f.detail);
  }
  function message(text) { root.replaceChildren(node('p', text, {role:'status'}),
    action(T('study.reload', {}, 'Recargar'), () => Table && I18n ? load() : location.reload())); }
  async function request(path, payload, method = 'POST') {
    const response = await fetch(path, {method, credentials:'same-origin', signal:controller.signal,
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body && body.error === 'demo_solo_lectura' ? body.error : String(response.status)); }
    return response.json();
  }
  // TASK-48: el código del server (demo_solo_lectura…) lo traduce apiError.
  function saveError(err) {
    const fallback = T('study.save_error', {}, 'No se pudo guardar. Probá de nuevo.');
    return I18n && I18n.apiError ? I18n.apiError(err, fallback) : fallback;
  }
  async function mutation(button, path, payload, method) {
    button.disabled = true;
    try { await request(path, payload, method); if (!disposed) await load(); }
    catch (err) { if (!disposed) {
      const error = node('p', saveError(err), {role:'alert'});
      button.after(error);
    }} finally { button.disabled = false; }
  }
  function table(parent, headings, rows, key) {
    if (!rows.length) return;
    const table = node('table'), head = node('thead'), hr = node('tr'), body = node('tbody');
    headings.forEach(h => hr.append(node('th', h, {scope:'col'}))); head.append(hr); table.append(head,body);
    rows.forEach(values => { const tr = node('tr'); values.forEach((v,i) => {
      const td = node('td', undefined, {'data-label':headings[i]});
      if (v?.nodeType) td.append(v); else td.textContent = String(v ?? ''); tr.append(td);
    }); body.append(tr); }); parent.append(table);
    // Width preferences belong to one column layout, never unlike tables.
    const fit = Table.crear({clave:'usability-'+key,tablas:()=>[table],
      apilado:()=>window.matchMedia('(max-width:1040px)').matches});
    fits.push(fit); fit.programarFit();
  }
  function day(iso) { return (Dates && Dates.corta(iso)) || iso; }
  function summary() {
    const s = story(data), tested = node('section'); root.append(tested);
    tested.append(node('h2', T('study.admin.story_tested_title', {}, 'Qué probamos')));
    if (!s.people) { tested.append(node('p', T('study.admin.story_nobody', {}, 'Todavía nadie hizo la prueba.'))); return; }
    const vars = {n:s.people, patients:s.roles.patient || 0, doctors:s.roles.doctor || 0, admins:s.roles.admin || 0};
    tested.append(node('p', Tn('study.admin.story_people', s.people, vars,
      '{n} persona probó el demo con tareas reales: {patients} como paciente, {doctors} como médico y {admins} como administrador.',
      '{n} personas probaron el demo con tareas reales: {patients} como paciente, {doctors} como médico y {admins} como administrador.')));
    if (s.period) {
      tested.append(node('p', s.period.first === s.period.last
        ? T('study.admin.story_day', {day:day(s.period.first)}, 'Todas las respuestas son del {day}.')
        : T('study.admin.story_period', {from:day(s.period.first), to:day(s.period.last)}, 'Las respuestas llegaron entre el {from} y el {to}.')));
    }
    tested.append(node('p', Tn('study.admin.story_finished', s.finished, {finished:s.finished, n:s.people},
      '{finished} de {n} llegó al final de sus tareas.', '{finished} de {n} llegaron al final de sus tareas.')));
    const found = node('section'); root.append(found);
    found.append(node('h2', T('study.admin.story_found_title', {}, 'Qué encontramos y qué hicimos')));
    if (!s.findings) { found.append(node('p', T('study.admin.story_no_findings', {}, 'Todavía no hay hallazgos cargados.'))); return; }
    // No «11 problemas: 5 corregidos y 6 sin hacer» line: the groups below
    // say it by themselves (product owner 2026-10-14: «never make counters»).
    // The group's title is its name; its rows say how many (no «(n)»).
    // Each finding is its title; who said what and what was done opens on a
    // click, so the screen reads as a list and not as a wall of text.
    const heads = {fixed:'Corregidos', in_progress:'En curso', not_done:'Sin hacer',
      found:'Por revisar', dismissed:'Descartados'};
    for (const status of STORY_ORDER) {
      const rows = s.groups[status]; if (!rows.length) continue;
      found.append(node('h3', T('study.admin.story_group.' + status, null, heads[status])));
      const list = node('ul', undefined, {class:'ux-tasks ux-story'});
      for (const f of rows) {
        const li = node('li'), more = node('details');
        more.append(node('summary', f.source === 'auto' ? signal(f.kind) : f.title),
          node('p', roleName(f.role) + ' · ' + taskName(f.role, f.task_id)));
        const proof = evidence(f); if (proof) more.append(node('p', proof));
        if (f.fix_note) more.append(node('p', f.fix_note));
        if (f.live_url) more.append(node('a', T('study.admin.story_live', {}, 'Verlo en el sitio'), {href:f.live_url, rel:'noreferrer'}));
        li.append(more); list.append(li);
      }
      found.append(list);
    }
  }
  function urlState() {
    const url = new URL(location.href); tab = ['summary','results','findings','links'].includes(url.searchParams.get('tab')) ? url.searchParams.get('tab') : 'summary';
    selected = {status:new Set(url.searchParams.getAll('status').filter(s => ['found','in_progress','fixed','not_done','dismissed'].includes(s))),
      role:new Set(url.searchParams.getAll('role').filter(r => r in data.definition.roles))};
  }
  function saveState() {
    const url = new URL(location.href); url.searchParams.set('tab',tab);
    for (const key of ['status','role']) { url.searchParams.delete(key); selected[key].forEach(v => url.searchParams.append(key,v)); }
    history.pushState({},'',url); render();
  }
  function results() {
    for (const role of ['patient','doctor','admin']) {
      const summary = summarize(data,role), section = node('section'); root.append(section);
      const terminaron = Tn('study.admin.counts_finished', summary.finished, null, '{n} terminó', '{n} terminaron');
      section.append(node('h2',roleName(role)), node('p', Tn('study.admin.counts', summary.participants,
        {terminaron}, '{n} participante, {terminaron}.', '{n} participantes, {terminaron}.')));
      // Each task is one block: its instruction, then one phrase per line —
      // «2/5 completaron», «Facilidad 3.4/5», «1 se trabó» — never a
      // «Completaron | 2/5» label/value grid (the product owner 2026-10-16).
      const tasks = node('ul', undefined, {class:'ux-tasks'});
      summary.tasks.forEach(t => {
        const li = node('li'); li.append(node('strong', t.instruction),
          node('span', T('study.admin.completed_of', {done:t.completed,n:summary.participants}, '{done}/{n} completaron')),
          node('span', t.ease === null ? T('study.admin.unrated', {}, 'Sin respuestas')
            : T('study.admin.ease_of', {ease:num(t.ease)}, 'Facilidad {ease}/5')));
        if (t.stuck) li.append(node('span', T('study.admin.stuck_n', {n:t.stuck}, '{n} se trabaron')));
        tasks.append(li);
      });
      if (summary.tasks.length) section.append(tasks);
      const rows = data.results.filter(r => r.role === role && (r.comment || r.outcome === 'stuck'));
      if (rows.length) {
        section.append(node('h3', T('study.admin.comments', {}, 'Comentarios')));
        const list = node('ul', undefined, {class:'ux-tasks'});
        rows.forEach(r => {
          const li = node('li'), outcome = T('study.outcome.' + r.outcome, {}, {ok:'Sí',doubt:'Con dudas',stuck:'Me trabé',skipped:'Saltada'}[r.outcome]);
          const comment = r.task_id==='F3' && r.comment ? T('study.answer.'+r.comment,{}, {yes:'Sí',maybe:'Tal vez',no:'No'}[r.comment]) : r.comment;
          li.append(node('strong', taskName(role,r.task_id)), node('span', outcome));
          if (comment) li.append(node('span', comment));
          list.append(li);
        });
        section.append(list);
      }
    }
  }
  function filter(parent, key, label, values, text) {
    const details = node('details'), summary = node('summary', label), choices = node('div'); details.append(summary,choices);
    for (const value of values) {
      const row = node('label'), input = node('input', undefined, {type:'checkbox',value}); input.checked = selected[key].has(value);
      input.onchange = () => { if (input.checked) selected[key].add(value); else selected[key].delete(value); saveState(); };
      row.append(input,node('span',text(value))); choices.append(row);
    }
    choices.append(action(T('study.clear', {}, 'Limpiar'),() => { selected[key].clear(); saveState(); })); parent.append(details);
  }
  function field(form,name,label,value='',type='input') {
    const row = node('div',undefined,{class:'ux-row'}), control = node(type,undefined,{name,id:'ux-'+name});
    control.value = value; if (type==='input') control.setAttribute('autocomplete','off');
    row.append(node('label',label,{for:'ux-'+name}),control); form.append(row); return control;
  }
  function formFinding(finding) {
    root.querySelector('#ux-editor')?.remove(); const form=node('form',undefined,{id:'ux-editor'});
    root.append(form); form.append(node('h2',finding ? `U-${finding.id}` : T('study.admin.add', {}, 'Agregar hallazgo')));
    if (finding) {
      form.append(node('p',finding.source==='auto'?signal(finding.kind):finding.title),node('p',evidence(finding)));
      const status=field(form,'status',T('study.admin.status', {}, 'Estado'),finding.status,'select');
      ['found','in_progress','fixed','not_done','dismissed'].forEach(s=>status.append(node('option',labelStatus(s),{value:s})));
      status.value=finding.status;
      field(form,'fix_note',T('study.admin.fix', {}, 'Nota de resolución'),finding.fix_note,'textarea');
      field(form,'live_url',T('study.admin.link', {}, 'Página publicada'),finding.live_url);
      field(form,'commit',T('study.admin.commit', {}, 'Commit'),finding.commit);
    } else {
      field(form,'title',T('study.admin.finding', {}, 'Hallazgo'));
      field(form,'detail',T('study.admin.detail', {}, 'Detalle'),'','textarea');
      const role=field(form,'role',T('study.admin.role', {}, 'Rol'),'patient','select');
      Object.keys(data.definition.roles).forEach(r=>role.append(node('option',roleName(r),{value:r})));
      const task=field(form,'task_id',T('study.admin.task', {}, 'Tarea'),'','select');
      const fill=()=>{ task.replaceChildren(); data.definition.roles[role.value].tasks.forEach(t=>task.append(node('option',t.instruction,{value:t.id}))); };
      role.onchange=fill; fill();
      const severity=field(form,'severity',T('study.admin.severity', {}, 'Gravedad'),'medium','select');
      ['low','medium','high'].forEach(s=>severity.append(node('option',T('study.severity.'+s,{}, {low:'Baja',medium:'Media',high:'Alta'}[s]),{value:s})));
      severity.value='medium';
    }
    const submit=node('button',T('study.admin.save', {}, 'Guardar'),{type:'submit',class:'btn'}); form.append(submit);
    form.onsubmit=e=>{ e.preventDefault(); if (submit.disabled) return;
      mutation(submit,'/api/admin/usability/findings'+(finding?'/'+finding.id:''),Object.fromEntries(new FormData(form)),'POST'); };
    form.querySelector('input,select,textarea')?.focus();
  }
  function htmlBlock(html) { const box = node('div'); box.innerHTML = html; return box.firstElementChild || box; }
  function findings() {
    const view = findingsView(data, selected, Filters);
    Filters.applyDisclosure(root, view.state);
    if (view.statusFilter || view.roleFilter) {
      const filters=node('div',undefined,{class:'ux-filters'}); root.append(filters);
      if (view.statusFilter) filter(filters,'status',T('study.admin.status', {}, 'Estado'),['found','in_progress','fixed','not_done','dismissed'],labelStatus);
      if (view.roleFilter) filter(filters,'role',T('study.admin.role', {}, 'Rol'),Object.keys(data.definition.roles),roleName);
    }
    if (data.editable) {
      const actions=node('div',undefined,{class:'ux-actions'}), analyze=action(T('study.admin.analyze', {}, 'Analizar respuestas'),()=>mutation(analyze,'/api/admin/usability/analyze',{}));
      actions.append(analyze,action(T('study.admin.add', {}, 'Agregar hallazgo'),()=>formFinding(null))); root.append(actions);
    }
    if (view.state === 'empty') {
      root.append(htmlBlock(Filters.emptyStateHtml({title: T('study.admin.findings_empty', {},
        'Todavía no hay hallazgos: aparecen al analizar las respuestas de la prueba o al agregarlos a mano.')})));
      return;
    }
    if (view.state === 'no-match') {
      const none = htmlBlock(Filters.noMatchHtml({title: T('study.admin.no_findings', {}, 'No hay hallazgos con estos filtros.'),
        clearLabel: T('admin.shell.list.clear_filters', {}, 'Limpiar filtros')}));
      none.querySelector('[data-clear-filters]').onclick = () => { selected.status.clear(); selected.role.clear(); saveState(); };
      root.append(none);
      return;
    }
    if (view.bulk) {
      const bulk = node('form',undefined,{class:'ux-actions'}), status = node('select',undefined,
        {'aria-label':T('study.admin.status', {}, 'Estado')});
      status.append(node('option',T('study.admin.status', {}, 'Estado'),{value:''}));
      ['found','in_progress','fixed','not_done','dismissed'].forEach(s=>status.append(node('option',labelStatus(s),{value:s})));
      const apply = node('button',T('study.admin.apply_selected', {}, 'Aplicar a seleccionados'),{type:'submit',class:'btn'});
      bulk.append(status,apply); root.append(bulk);
      bulk.onsubmit = async e => {
        e.preventDefault(); if(apply.disabled) return;
        if(!status.value || !selectedFindings.size) {
          apply.after(node('p',T('study.admin.select_rows', {}, 'Seleccioná hallazgos y un estado.'),{role:'alert'})); return;
        }
        apply.disabled=true;
        try {
          for(const id of [...selectedFindings]) {
            await request('/api/admin/usability/findings/'+id,{status:status.value}); selectedFindings.delete(id);
          }
          if(!disposed) await load();
        } catch (err) {if(!disposed) apply.after(node('p',saveError(err),{role:'alert'}));}
        finally {apply.disabled=false;}
      };
    }
    const rows=view.rows;
    table(root,[T('study.admin.number', {}, 'Número'),T('study.admin.finding', {}, 'Hallazgo'),T('study.admin.role', {}, 'Rol'),
      T('study.admin.task', {}, 'Tarea'),T('study.admin.severity', {}, 'Gravedad'),T('study.admin.status', {}, 'Estado'),
      T('study.admin.fix', {}, 'Nota de resolución'),T('study.admin.link', {}, 'Página publicada'),T('study.admin.commit', {}, 'Commit')],rows.map(f=>{
        const title=node('details'), detail=node('div'); title.append(node('summary',f.source==='auto'?signal(f.kind):f.title),detail);
        detail.append(node('p',evidence(f)),node('p',T('study.source.'+f.source,{}, {auto:'Análisis automático',manual:'Manual'}[f.source])));
        const status=data.editable?action(labelStatus(f.status),()=>formFinding(f)):node('span',labelStatus(f.status)); status.className='ux-status';
        const link=f.live_url?node('a',T('study.admin.live_page', {}, 'Página'),{href:f.live_url,rel:'noreferrer'}):'';
        let number=`U-${f.id}`;
        if(data.editable) {
          number=node('label');const check=node('input',undefined,{type:'checkbox','aria-label':T('study.admin.select_finding',{id:f.id},'Seleccionar U-{id}')});
          check.checked=selectedFindings.has(f.id);
          check.onchange=()=>{if(check.checked) selectedFindings.add(f.id); else selectedFindings.delete(f.id);};number.append(check,node('span',`U-${f.id}`));
        }
        return [number,title,roleName(f.role),taskName(f.role,f.task_id),
          T('study.severity.'+f.severity,{}, {low:'Baja',medium:'Media',high:'Alta'}[f.severity]),status,f.fix_note,link,f.commit];
      }),'findings');
  }
  function links() {
    const url=new URL('/study',location.origin).href;
    root.append(node('a',url,{href:url}));
    const copy=action(T('study.admin.copy', {}, 'Copiar enlace'),async()=>{ try {await navigator.clipboard.writeText(url); copy.textContent=T('study.admin.copied', {}, 'Copiado');}
      catch { const input=node('input',undefined,{value:url,readonly:'', 'aria-label':T('study.admin.link', {}, 'Página publicada')}); root.append(input); input.select(); }});
    root.append(copy);
    const list=node('ul'); root.append(list);
    Object.keys(data.definition.roles).forEach(role=>{const li=node('li'); li.append(node('a',roleName(role),{href:new URL('/study?role='+role,location.origin).href}));list.append(li);});
  }
  function render() {
    if(disposed || !data) return;
    fits.forEach(f => f.dispose()); fits=[];
    root.replaceChildren(); root.removeAttribute('data-disclosure'); const nav=node('nav',undefined,{'aria-label':T('study.admin.title', {}, 'Usabilidad')}); root.append(nav);
    [['summary',T('study.admin.summary', {}, 'Resumen')],['results',T('study.admin.results', {}, 'Resultados')],['findings',T('study.admin.findings', {}, 'Hallazgos')],['links',T('study.admin.links', {}, 'Enlaces')]].forEach(([value,label])=>{
      const a=node('a',label,{href:'?tab='+value}); if(tab===value) a.setAttribute('aria-current','page');
      a.onclick=e=>{e.preventDefault();tab=value;saveState();};nav.append(a);});
    if(tab==='summary') summary(); else if(tab==='results') results(); else if(tab==='findings') findings(); else links();
  }
  async function load() {
    const own=++generation; controller?.abort(); controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),8000);
    try {
      const response=await fetch('/api/admin/usability',{credentials:'same-origin',signal:controller.signal});
      if(!response.ok) throw new Error(String(response.status));
      const next=await response.json(); if(disposed || own!==generation) return;
      data=next;urlState();render();
    } catch { if(!disposed && own===generation) message(T('study.admin.load_error', {}, 'No se pudieron cargar las respuestas.')); }
    finally {clearTimeout(timeout);}
  }
  const historyChanged=()=>{if(data){urlState();render();}};
  window.addEventListener('popstate',historyChanged);
  const ready = Promise.all([window.sanaImportStatic('platform/i18n.mjs'),window.sanaImportStatic('platform/admin-table.mjs'),
    window.sanaImportStatic('platform/dates.mjs').catch(()=>null),
    window.sanaImportStatic('platform/admin-filter-controls.mjs')]).then(([i,t,d,f])=>{
    if(disposed) return;I18n=i;Table=t;Dates=d;Filters=f;
    return load();
  }).catch(()=>{if(!disposed) message('No se pudieron cargar las respuestas.');});
  const dispose=()=>{disposed=true;generation++;controller?.abort();fits.forEach(f=>f.dispose());window.removeEventListener('popstate',historyChanged);};
  dispose.ready=ready;return dispose;
}
