/* Consent precedes this module's recorder. No document text or field values. */
export const screenCaptureExtension = null; // Future opt-in capture belongs here; MVP never calls getDisplayMedia.
const staticPaths = new Set(['/', '/doctors', '/centers', '/account', '/upload-order', '/my-requests',
  '/d/diagnostico', '/d/grupos', '/admin', '/admin/solicitudes', '/admin/cola', '/admin/my-patients',
  '/admin/mis-turnos', '/admin/my-schedules', '/admin/onboarding', '/admin/account', '/admin/pacientes',
  '/admin/services', '/admin/centers', '/admin/doctors', '/admin/users', '/admin/demo-access',
  '/admin/demo-doctor-access', '/account/demo-access', '/admin/login']);
const prefixes = ['/admin/patients/', '/doctor/', '/center/', '/service/', '/preparation/'];
export function safePath(value) {
  let path;
  try { path = new URL(value || '/', 'https://study.invalid').pathname; } catch { return '/other'; }
  if (staticPaths.has(path)) return path;
  for (const prefix of prefixes) {
    if (path.startsWith(prefix) && path.slice(prefix.length) && !path.slice(prefix.length).includes('/')) return prefix + ':item';
  }
  return '/other';
}
export function describeTarget(target, index = 0) {
  const allowed = new Set(['a', 'button', 'input', 'select', 'textarea', 'label', 'div', 'span',
    'p', 'li', 'td', 'th', 'img', 'summary', 'h1', 'h2', 'body']);
  const tag = String(target?.tagName || '').toLowerCase();
  return {tag: allowed.has(tag) ? tag : 'other', index: Math.max(0, Math.min(100000, index)),
    interactive: !!target?.closest?.('a,button,input,select,textarea,label,summary,[role="button"],[contenteditable="true"]')};
}
export function detectDeadClicks(events, now) {
  return events.filter(e => e.kind === 'click' && !e.target?.interactive && now >= e.t + 1500
    && !events.some(n => n.kind === 'nav' && n.t >= e.t && n.t <= e.t + 1500));
}
export function resumeIndex(tasks, results) {
  const completed = new Set(results.map(r => r.task_id));
  const next = tasks.findIndex(t => !completed.has(t.id));
  return next < 0 ? tasks.length : next;
}
let I18n;
function T(key, vars, fallback) { return I18n ? I18n.t(key, vars, fallback) : fallback; }
async function translations() {
  if (!I18n) I18n = await import(window.SANA_STATIC['platform/i18n.mjs']);
}
async function post(url, payload) {
  const response = await fetch(url, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}
function element(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}
function button(text, fn) {
  const node = element('button', text, {type: 'button', class: 'btn'});
  node.onclick = fn;
  return node;
}
function choice(name, label, options) {
  const row = element('div', undefined, {class: 'study-row'});
  row.append(element('label', label, {for: 'study-' + name}));
  const select = element('select', undefined, {name, id: 'study-' + name});
  for (const [value, text] of options) select.append(element('option', text, {value}));
  row.append(select);
  return row;
}
function comment(name, label) {
  const row = element('div', undefined, {class: 'study-row'});
  row.append(element('label', label, {for: 'study-' + name}),
    element('textarea', undefined, {name, id: 'study-' + name, maxlength: '2000', autocomplete: 'off'}));
  return row;
}
export function mountLanding(form) {
  const error = form.querySelector('[role=alert]');
  let busy = false;
  form.onsubmit = async e => {
    e.preventDefault();
    if (busy) return;
    await translations();
    error.hidden = true;
    const role = form.elements.namedItem('role').value;
    const consent = form.elements.namedItem('consent').checked;
    if (!role || !consent) {
      error.textContent = T('study.consent_error', {}, 'Elegí tu rol y aceptá participar para empezar.');
      error.hidden = false; return;
    }
    const submit = form.querySelector('[type=submit]');
    busy = true; submit.disabled = true;
    try { const data = await post('/api/study/session', {role, consent}); location.assign(data.url); }
    catch { error.textContent = T('study.save_error', {}, 'No se pudo guardar. Probá de nuevo.'); error.hidden = false; }
    finally { busy = false; submit.disabled = false; }
  };
  return () => { form.onsubmit = null; };
}
export function mountRunner(host, data) {
  let disposed = false, index = resumeIndex(data.group.tasks, data.completed), taskStart = performance.now();
  let queue = [], captureDispose = () => {}, frame = null, busy = false, error, card, answerForm;
  let stopped = false, recordingStopped = false, flushing = null, timer = null;
  const endpoint = '/api/study/s/' + encodeURIComponent(data.token);
  const task = () => data.group.tasks[index];
  function elapsed() { return Math.min(86400000, Math.round(performance.now() - taskStart)); }
  function emit(kind, path, target, time = elapsed()) {
    if (disposed || stopped || recordingStopped || !task()) return;
    if (queue.length >= 100) return; // Bounded client buffer, never unlimited capture.
    queue.push({task_id: task().id, kind, path: safePath(path), t: time,
      target: target || {tag: 'body', interactive: false, index: 0}});
  }
  async function flush() {
    if (flushing) { await flushing; if (!queue.length) return; }
    if (!queue.length) return;
    const batch = queue.splice(0, 100);
    flushing = post(endpoint + '/event', {events: batch}).then(result => {
      if (result.accepted < batch.length) { recordingStopped = true; queue = []; captureDispose(); }
      return result;
    }).catch(e => {
      queue = [...batch, ...queue].slice(0, 100); throw e;
    }).finally(() => { flushing = null; });
    return flushing;
  }
  function failed() {
    if (disposed) return;
    error.textContent = T('study.save_error', {}, 'No se pudo guardar. Probá de nuevo.'); error.hidden = false;
  }
  function instrument() {
    captureDispose();
    if (disposed || stopped || !task()) return;
    let doc, win;
    try { win = frame.contentWindow; doc = win.document; if (win.location.origin !== location.origin) return; }
    catch { return; }
    const cleanups = [], timers = new Set();
    let lastPath = safePath(win.location.pathname), navAt = -1, scrollTimer;
    emit('nav', lastPath);
    function listen(target, name, fn) {
      target.addEventListener(name, fn, true);
      cleanups.push(() => target.removeEventListener(name, fn, true));
    }
    function navigation() {
      let current;
      try { current = safePath(win.location.pathname); } catch { return; }
      if (current !== lastPath) { navAt = elapsed(); lastPath = current; emit('nav', current); }
    }
    listen(doc, 'click', e => {
      const descriptor = describeTarget(e.target, Array.from(e.target?.parentElement?.children || []).indexOf(e.target));
      const time = elapsed(), ownerTask = task().id;
      emit('click', lastPath, descriptor, time);
      if (!descriptor.interactive) {
        const timeout = setTimeout(() => {
          timers.delete(timeout); navigation();
          if (task()?.id === ownerTask && navAt < time) emit('dead_click', lastPath, descriptor, time + 1500);
        }, 1500);
        timers.add(timeout);
      }
    });
    listen(win, 'scroll', () => {
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => emit('scroll_stop', lastPath), 400);
    });
    listen(doc, 'visibilitychange', () => emit('visibility', lastPath));
    listen(win, 'pagehide', () => { navAt = elapsed(); });
    const poll = setInterval(navigation, 250);
    captureDispose = () => { cleanups.forEach(fn => fn()); timers.forEach(clearTimeout);
      clearTimeout(scrollTimer); clearInterval(poll); };
  }
  function status() {
    error = element('p', '', {role: 'alert', hidden: ''}); card.append(error);
  }
  function renderTask() {
    captureDispose(); taskStart = performance.now(); stopped = false;
    host.replaceChildren(); card = element('section'); host.append(card); status();
    if (!task()) { renderFinal(); return; }
    card.append(element('p', T('study.progress', {n: index + 1, total: data.group.tasks.length}, 'Tarea {n} de {total}')),
      element('h1', task().instruction));
    if (task().stop_before_save) card.append(element('p', T('study.stop_save', {}, 'Llegá hasta el botón de guardar o confirmar, sin apretarlo.')));
    const actions = element('div', undefined, {class: 'study-actions'});
    actions.append(button(T('study.done', {}, 'Terminé'), () => rate('ok')),
      button(T('study.stuck', {}, 'Me trabé'), () => rate('stuck')),
      button(T('study.skip', {}, 'Saltar tarea'), () => rate('skipped')));
    card.append(actions);
    card.append(button(T('study.leave', {}, 'Dejar de participar'), () => finishStudy({})));
    frame = element('iframe', undefined, {title: T('study.frame', {}, 'Sitio de prueba'),
      sandbox: 'allow-scripts allow-same-origin allow-forms allow-downloads', referrerpolicy: 'no-referrer'});
    frame.onload = instrument;
    host.append(frame);
    // Only fixed data-defined entry URLs, never arbitrary participant URLs.
    frame.src = data.group.entry;
    // Task starts even if the embedded site fails to load (abandonment signal).
    emit('visibility', '/');
  }
  function rate(outcome) {
    if (busy || answerForm) return;
    stopped = true; captureDispose();
    const duration = elapsed();
    answerForm = element('form');
    if (outcome === 'ok') answerForm.append(choice('outcome', T('study.did_it', {}, '¿Pudiste?'), [
      ['ok', T('study.yes', {}, 'Sí')], ['doubt', T('study.doubt', {}, 'Con dudas')]]));
    answerForm.append(choice('ease', T('study.ease', {}, '¿Qué tan fácil fue?'), [
      ['', T('study.choose_ease', {}, 'Elegí del 1 al 5')], ['1', T('study.ease1', {}, '1 · Muy difícil')],
      ['2', '2'], ['3', '3'], ['4', '4'], ['5', T('study.ease5', {}, '5 · Muy fácil')]]),
      comment('comment', T('study.comment', {}, 'Comentario (opcional, sin datos personales)')));
    const submit = element('button', T('study.next', {}, 'Siguiente tarea'), {type: 'submit', class: 'btn'});
    answerForm.append(submit); card.append(answerForm); answerForm.querySelector('select').focus();
    answerForm.onsubmit = async e => {
      e.preventDefault(); if (busy) return;
      const ease = answerForm.elements.namedItem('ease').value;
      if (!ease && outcome !== 'skipped') {
        error.textContent = T('study.ease_error', {}, 'Elegí qué tan fácil fue, del 1 al 5.'); error.hidden = false; return;
      }
      busy = true; submit.disabled = true; error.hidden = true;
      try {
        await flush();
        await post(endpoint + '/result', {task_id: task().id,
          outcome: answerForm.elements.namedItem('outcome')?.value || outcome,
          ease: ease ? Number(ease) : null, comment: answerForm.elements.namedItem('comment').value,
          duration_ms: duration});
        if (disposed) return;
        index++; answerForm = null; renderTask();
      } catch { failed(); }
      finally { busy = false; submit.disabled = false; }
    };
    frame.hidden = true;
  }
  function renderFinal() {
    stopped = true; captureDispose();
    card.append(element('h1', T('study.final_title', {}, 'Para terminar')));
    const form = element('form');
    for (const question of data.final_questions) {
      if (question.id !== 'F3') form.append(comment(question.id, question.title));
      else form.append(choice('F3', question.title, [['', T('study.optional', {}, 'Opcional')],
        ['yes', T('study.yes', {}, 'Sí')], ['maybe', T('study.maybe', {}, 'Tal vez')], ['no', T('study.no', {}, 'No')]]));
    }
    const submit = element('button', T('study.finish', {}, 'Enviar respuestas'), {type: 'submit', class: 'btn'});
    form.append(submit); card.append(form);
    form.onsubmit = e => { e.preventDefault(); finishStudy(Object.fromEntries(new FormData(form))); };
    card.append(button(T('study.leave', {}, 'Dejar de participar'), () => finishStudy({})));
  }
  async function finishStudy(answers) {
    if (busy || disposed) return;
    busy = true; stopped = true; captureDispose();
    const buttons = host.querySelectorAll('button'); buttons.forEach(b => { b.disabled = true; });
    try {
      await flush(); await post(endpoint + '/finish', {answers});
      if (!disposed) { host.replaceChildren(element('h1', T('study.thanks', {}, 'Gracias por participar'))); dispose(); }
    } catch { failed(); }
    finally { busy = false; buttons.forEach(b => { b.disabled = false; }); }
  }
  const onVisibility = () => { if (task() && !stopped) emit('visibility', '/'); };
  document.addEventListener('visibilitychange', onVisibility);
  function dispose() {
    if (disposed) return;
    disposed = true; stopped = true; captureDispose(); clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisibility);
    if (frame) frame.onload = null;
    if (queue.length) navigator.sendBeacon?.(endpoint + '/event', new Blob([JSON.stringify({events: queue.splice(0, 100)})], {type: 'application/json'}));
  }
  dispose.ready = translations().then(() => {
    if (disposed) return;
    renderTask(); timer = setInterval(() => { flush().catch(failed); }, 1500);
  }).catch(() => { if (!disposed) host.replaceChildren(
    element('p', T('study.load_error', {}, 'No se pudo cargar. Probá de nuevo.'), {role: 'alert'}),
    button(T('study.reload', {}, 'Recargar'), () => location.reload())); });
  return dispose;
}
