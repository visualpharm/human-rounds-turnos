/* One-page doctor setup: owned requests/readers/listeners, canonical saves. */
const inBrowser = typeof window !== 'undefined';
const dep = name => inBrowser ? import(window.SANA_STATIC[name]) : import(new URL('./' + name.split('/').pop(), import.meta.url));
const I18n = await dep('platform/i18n.mjs');
const Hours = await dep('platform/admin-hours-editor.mjs');
const Format = await dep('platform/format.mjs');
const Session = inBrowser ? await dep('platform/admin-session.mjs') : null;
const T = (key, vars, fallback) => I18n.t(key, vars, fallback);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Weekday order is always Lu..Do (id 0 is Sunday, as stored). `label` is the
// picker chip (Lu/Ma/…), `name` the full day a row is titled with.
const dayKeys = ['mon','tue','wed','thu','fri','sat','sun'];
const days = () => [1,2,3,4,5,6,0].map((id, n) => ({id,
  label:T('admin.content.hours.day.' + dayKeys[n], null, ['Lu','Ma','Mi','Ju','Vi','Sá','Do'][n]),
  name:T('date.day.' + dayKeys[n], null, ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'][n])}));
const dayOrder = id => [1,2,3,4,5,6,0].indexOf(Number(id));

export const scheduleBand = Format.scheduleBand;

// A 24-hour typed control, never a native <input type="time">: on a phone in
// an English locale the native picker renders an AM/PM wheel that clips, and
// Argentina reads the clock in 24 hours. The numeric keyboard plus this
// pattern are the whole control (onboarding audit, 2026-10-21).
export function timeInput(name, value, placeholder) {
  const hint = T('admin.doctor.time_hint', null, 'Escribí la hora en formato 24 horas, por ejemplo 08:30.');
  return '<input class="dw-time" type="text" inputmode="numeric" autocomplete="off" maxlength="5" pattern="([01]\\d|2[0-3]):[0-5]\\d" placeholder="' + esc(placeholder) + '" data-time="' + name + '" value="' + esc(value) + '" required title="' + esc(hint) + '">';
}

export function formatTime(raw) {
  const digits = Format.digits(raw).slice(0, 4);
  if (digits.length < 3) return digits;
  // A numeric keyboard has no colon: "830" is 8:30 while "083" is 08:3x, and
  // a two-digit start above 23 can only be a one-digit hour. Four digits are
  // always HH:MM.
  const hour = parseInt(digits.slice(0, 2), 10);
  if (digits.length === 3 && hour > 23) return '0' + digits[0] + ':' + digits.slice(1);
  return digits.slice(0, 2) + ':' + digits.slice(2);
}

// One interval of one weekday: the row the doctor edits. `source` is the
// index of the stored band the interval came from — the ONLY link between a
// rendered row and that band's own facts (cupos, closures…), so a day split,
// a removal or a reordering can never graft one band's facts onto another.
// A row without `source` is brand new and inherits nothing.
export function slotEditor(slot = {}, services = []) {
  const quitar = T('admin.content.hours.remove_slot', null, 'Quitar horario');
  const none = T('admin.content.hours.service.none', null, 'Sin especificar');
  return '<div class="dw-slot"' + (slot.source == null ? '' : ' data-source="' + slot.source + '"') + '><div class="dw-times">' +
    '<label>' + esc(T('admin.doctor.from', null, 'Desde')) + timeInput('desde', slot.desde || '08:00', '08:00') + '</label>' +
    '<label>' + esc(T('admin.doctor.to', null, 'Hasta')) + timeInput('hasta', slot.hasta || '12:00', '12:00') + '</label>' +
    '<label>' + esc(T('admin.doctor.interval', null, 'Cada (minutos)')) + Hours.stepperHtml('min="1" max="240" step="1" data-time="cada_min" required', slot.cada_min || 20, null, T('admin.doctor.interval', null, 'Cada (minutos)')) + '</label>' +
    '<label>' + esc(T('admin.content.hours.col.service', null, 'Servicio')) + '<select data-service><option value="">' + esc(none) + '</option>' +
    (services || []).map(svc => '<option value="' + esc(svc.id) + '"' + (slot.servicio === svc.id ? ' selected' : '') + '>' +
      esc(svc.nombre + (svc.consultorio ? ' · ' + svc.consultorio : '')) + '</option>').join('') + '</select></label>' +
    '<button type="button" class="dw-x hr-x" data-remove-slot title="' + esc(quitar) + '" aria-label="' + esc(quitar) + '"></button></div></div>';
}

// One weekday's card: its name, its intervals (a split shift is just a second
// interval) and its own add action. Copying to other days lives here too.
export function dayRowEditor(day, slots = [], services = []) {
  const copy = T('admin.doctor.copy_day', null, 'Copiar a otros días');
  const add = T('admin.doctor.add_hours', null, 'Agregar horario');
  return '<div class="dw-day" data-day-row="' + day.id + '">' +
    '<div class="dw-day-head"><h4>' + esc(day.name || day.label) + '</h4>' +
    '<button type="button" class="dw-copy-btn" data-copy-day="' + day.id + '">' + esc(copy) + '</button></div>' +
    '<div data-slots>' + (slots || []).map(slot => slotEditor(slot, services)).join('') + '</div>' +
    '<button type="button" class="dw-add-slot" data-add-slot="' + day.id + '">+ ' + esc(add) + '</button></div>';
}

// Stored bands split into per-weekday slots; each slot keeps the index of the
// band it came from, so two days of one multi-day band both stay anchored to
// that band's facts while remaining separately editable.
export function dayRows(franjas = []) {
  return days().map(day => {
    const slots = [];
    (franjas || []).forEach((band, index) => { if ((band.dias || []).includes(day.id)) slots.push({...band, source: index}); });
    return slots.length ? {day, slots} : null;
  }).filter(Boolean);
}

function dayPicker(franjas = []) {
  const active = new Set(dayRows(franjas).map(row => row.day.id));
  return '<div class="dw-days" data-day-pick>' + days().map(day =>
    '<label><input type="checkbox" data-day="' + day.id + '"' + (active.has(day.id) ? ' checked' : '') + '><span>' + esc(day.label) + '</span></label>').join('') + '</div>';
}

export function scheduleEditor(rows) {
  return rows.map(row => '<section class="dw-center" data-center="' + esc(row.centro) + '"><h3>' + esc(row.center) + '</h3>' +
    (row.editable ? dayPicker(row.franjas) + '<div data-days>' + dayRows(row.franjas).map(({day, slots}) => dayRowEditor(day, slots, row.servicios)).join('') + '</div>' :
      (row.franjas || []).map(band => '<p class="dw-summary">' + esc(scheduleBand(band)) + '</p>').join('') + '<p>' + esc(row.message || '') + '</p>') + '</section>').join('');
}

// One editable `[data-center]` section -> the doctor's `centros` entry with
// the bands the form holds now. Each day row yields one band per interval,
// carrying only its own day; a slot's stored facts ride the stored band its
// `data-source` names, never the band that happens to sit at the same index
// (that positional merge is what once moved cupos onto the wrong interval).
export function collectCenter(section, base) {
  const stored = (base && base.franjas) || [];
  const franjas = [];
  for (const row of section.querySelectorAll('.dw-day')) {
    const dia = Number(row.dataset.dayRow);
    for (const slot of row.querySelectorAll('.dw-slot')) {
      const source = slot.dataset ? slot.dataset.source : undefined;
      const entry = {...(source === undefined || source === '' ? {} : stored[Number(source)] || {})};
      for (const input of slot.querySelectorAll('[data-time]'))
        entry[input.dataset.time] = input.dataset.time === 'cada_min' ? Number(input.value) : input.value;
      entry.dias = [dia];
      const service = (slot.querySelector('[data-service]') || {}).value || '';
      if (service) entry.servicio = service; else delete entry.servicio;
      franjas.push(entry);
    }
  }
  return {centro: (base && base.centro) || section.dataset.center, franjas};
}

export function collectSchedule(sections, stored = []) {
  return [...sections].map(section => collectCenter(section, stored.find(c => c.centro === section.dataset.center)));
}

// The save's own gate: inverted windows, a band with no day at all, and —
// because one doctor has one body — hours that overlap on the same weekday,
// in the same center or across two centers of the network. Everything is
// stopped here with the concrete fix, before anything leaves the page.
export function scheduleProblems(centers) {
  const problems = [];
  const windows = [];
  for (const center of centers || []) {
    const name = center.center || center.centro;
    for (const band of center.franjas || []) {
      if (!band.dias || !band.dias.length) return [T('admin.doctor.no_days', null, 'Marcá al menos un día en cada horario.')];
      if (band.desde >= band.hasta)
        problems.push(T('admin.doctor.inverted', {center: name, range: band.desde + '–' + band.hasta},
          'En {center}, un horario termina antes de empezar ({range}). Corregilo para guardar.'));
      for (const day of band.dias)
        windows.push({day, desde: band.desde, hasta: band.hasta, center: name, centro: center.centro});
    }
  }
  for (let i = 0; i < windows.length; i++)
    for (let j = i + 1; j < windows.length; j++) {
      const left = windows[i], right = windows[j];
      if (left.day !== right.day || left.desde >= right.hasta || right.desde >= left.hasta) continue;
      problems.push(left.centro === right.centro
        ? T('admin.doctor.overlap', {center: left.center, first: left.desde + '–' + left.hasta, second: right.desde + '–' + right.hasta},
          'Dos horarios de {center} se superponen ({first} y {second}). Separalos para guardar.')
        : T('admin.doctor.overlap_centers', {first_center: left.center, second_center: right.center, first: left.desde + '–' + left.hasta, second: right.desde + '–' + right.hasta},
          'El mismo día te superpone {first} en {first_center} y {second} en {second_center}. Elegí un horario para cada uno.'));
    }
  return problems;
}

// A server message reaches the doctor only when it reads as a sentence for
// people: it starts with a capital and has words. A validator string
// («centro 1: franja 1: agenda.dias debe ser … (0=domingo..6=sábado)») or a
// bare code («agenda_servicio_aplica») becomes the generic retry line.
export function humanMessage(message) {
  return typeof message === 'string' && /\s/.test(message) && /^[\p{Lu}¿¡]/u.test(message) && !message.includes('=');
}

export function requestOwner(transport = fetch) {
  let alive = true;
  const pending = new Set();
  function ensure() { if (!alive) throw new DOMException('Disposed', 'AbortError'); }
  async function request(url, payload, method = 'POST', itemRequired = false) {
    ensure();
    const controller = new AbortController(); pending.add(controller);
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await transport(url, {method, credentials:'same-origin', signal:controller.signal,
        ...(payload === undefined ? {} : {headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})});
      let body;
      try { body = await response.json(); }
      catch { throw new Error(T('admin.doctor.failed', null, 'No se pudo guardar. Volvé a intentar.')); }
      ensure();
      if (Session && (response.status === 401 || response.status === 403)) {
        await Session.confirmar(response, body, () => alive); ensure();
      }
      const acknowledged = typeof itemRequired === 'function' ? itemRequired(body)
        : !itemRequired || (body?.item && body.item.id === decodeURIComponent(url.split('/').pop()));
      if (!response.ok || body?.ok !== true || !acknowledged) {
        const message = body?.detalle || body?.error;
        // TASK-48: un código conocido (demo_solo_lectura) tiene su frase.
        const failed = T('admin.doctor.failed', null, 'No se pudo guardar. Volvé a intentar.');
        throw new Error(humanMessage(message) ? message
          : (message === 'demo_solo_lectura' && I18n.apiError ? I18n.apiError(message, failed) : failed));
      }
      return body;
    } finally { clearTimeout(timer); pending.delete(controller); }
  }
  return {mutate:request, read:url=>request(url, undefined, 'GET'), dispose(){alive=false; pending.forEach(c=>c.abort()); pending.clear();}};
}

// Clipboard access is confined to an explicit paste event on the photo area.
export function photoFileFromEvent(event) {
  if (!event || event.defaultPrevented) return null;
  if (event.target?.closest?.('input,textarea,select,[contenteditable="true"],[contenteditable=""]')) return null;
  const transfer = event.clipboardData || event.dataTransfer;
  const files = [...(transfer?.files || [])];
  for (const item of transfer?.items || []) if (item.kind === 'file') {
    const file = item.getAsFile(); if (file && !files.includes(file)) files.push(file);
  }
  return files.find(file => /^image\//.test(file.type || '') || /\.(jpe?g|png|webp|heic|heif)$/i.test(file.name || '')) || null;
}

export function mount(root, data, navigate = url => { location.href = url; }) {
  const doctor = data.doctors[0];
  if (!doctor) return () => {};
  // DOM source indexes refer to the bands rendered on entry. A successful
  // profile save may split/reorder those bands before completion fails; keep
  // that immutable source map for retries until this page is reloaded.
  const renderedCenters = structuredClone(doctor.centros?.length ? doctor.centros : [{centro:doctor.centro,franjas:doctor.agenda?.franjas || (doctor.agenda?.dias ? [doctor.agenda]:[]),excepciones:doctor.agenda?.excepciones || []}]);
  const owner = requestOwner();
  const listeners = [], readers = new Set(), reservations = new Set();
  let alive = true, rotation = data.photo?.rotation || 0, photoGeneration = 0, running = 0;
  const dirty = new Set();
  let profileRevision = 0, scheduleRevision = 0;
  const $ = sel => root.querySelector(sel);
  const listen = (el, type, fn) => { if (el) { el.addEventListener(type, fn); listeners.push(()=>el.removeEventListener(type, fn)); } };
  function status(form, message, error = false) {
    const el = form.querySelector('[role="status"]');
    if (el && alive) { el.textContent = message; el.classList.toggle('dw-error', error); }
  }
  function release(reservation) {
    if (!reservations.delete(reservation)) return;
    if (reservation.hadPending) reservation.form.dataset.pending = reservation.pending;
    else delete reservation.form.dataset.pending;
    reservation.controls.forEach(([control, disabled]) => { control.disabled = disabled; });
  }
  async function mutation(form, operation) {
    if (form.dataset.pending === '1') return;
    const reservation = {
      form, hadPending: Object.prototype.hasOwnProperty.call(form.dataset, 'pending'), pending: form.dataset.pending,
      controls: [...form.querySelectorAll('button,input[type="file"]')].map(control => [control, control.disabled]),
    };
    reservations.add(reservation);
    form.dataset.pending = '1'; running++;
    reservation.controls.forEach(([control]) => { control.disabled = true; });
    // La operación puede rechazar el guardado (formulario inválido) devolviendo
    // false: sin este chequeo el paso pintaba «Guardado.» aunque no hubiera
    // guardado nada y solo el globito del navegador decía la verdad.
    try { const saved = await operation(); if (alive && saved !== false) status(form, T('admin.doctor.saved', null, 'Guardado.')); }
    catch (error) { if (alive) status(form, error.name === 'AbortError' ? T('admin.doctor.timeout', null, 'La respuesta no llegó. Volvé a intentar.') : error.message, true); }
    finally { running--; release(reservation); }
  }
  const info = $('[data-profile]');
  listen(info, 'input', ()=>{profileRevision++;dirty.add('profile');});
  listen(info, 'submit', event=>{event.preventDefault();finish.click();});

  const photo = $('[data-photo]');
  function paint(body) {
    if (!alive) return;
    rotation = body.rotation || 0;
    const preview = $('[data-photo-preview]');
    if (body.style || body.foto_url) {
      preview.setAttribute('style', body.style || 'background-image:url("' + body.foto_url + '");background-size:cover;background-position:center');
      preview.hidden=false;
    }
    else { preview.removeAttribute('style'); preview.hidden=false; }
    const uploadLabel=photo.querySelector('.dw-upload-label');
    if(uploadLabel)uploadLabel.textContent=T(body.has_upload || body.foto_url ? 'admin.doctor.replace_photo' : 'admin.doctor.upload_photo',null,body.has_upload || body.foto_url ? 'Reemplazar foto' : 'Subir foto');
    photo.querySelectorAll('[data-photo-action]').forEach(el=>el.hidden = ['restore','original'].includes(el.dataset.photoAction) ? !body.has_original : !(body.has_upload || body.foto_url));
  }
  if (photo) {
    const url = '/api/admin/doctors/' + encodeURIComponent(doctor.id) + '/photo';
    const photoAck = body => typeof body?.foto_url === 'string' && body.foto_url.startsWith('/doctor-photo/' + encodeURIComponent(doctor.id) + '?')
      && body.has_original === true && ['original','procesada'].includes(body.status);
    const initialGeneration=photoGeneration;
    if (data.photo) paint(data.photo);
    else owner.read(url).then(body=>{if(initialGeneration===photoGeneration)paint(body);}).catch(error=>{if(alive && initialGeneration===photoGeneration)status(photo,error.message,true);});
    function uploadFile(file) {
      if (!file) return;
      if (photo.dataset.pending === '1' || readers.size) {
        status(photo,T('admin.doctor.wait_photo',null,'Esperá a que termine de guardarse la foto.')); return;
      }
      const input = $('[data-upload]');
      if (file.size > 20*1024*1024) {status(photo,T('admin.doctor.photo_too_large',null,'La foto supera los 20 MB. Elegí una más liviana.'),true); if(input)input.value=''; return;}
      const generation=++photoGeneration;
      const reader = new FileReader(); readers.add(reader);
      status(photo,T('admin.doctor.photo_saving',null,'Guardando foto…'));
      reader.onload = ()=>{
        readers.delete(reader);
        if (!alive || generation !== photoGeneration) return;
        mutation(photo,async()=>{
          try { const result=await owner.mutate(url,{data_b64:reader.result},'POST',photoAck); if(alive && generation===photoGeneration)paint(result); }
          finally { if(input)input.value=''; }
        });
      };
      reader.onerror = ()=>{readers.delete(reader); if(input)input.value=''; if(alive)status(photo,T('admin.doctor.photo_read_failed',null,'No se pudo leer la foto. Elegí el archivo de nuevo.'),true);};
      reader.onabort = ()=>{readers.delete(reader);};
      reader.readAsDataURL(file);
    }
    listen($('[data-upload]'), 'change', event => uploadFile(event.target.files?.[0]));
    listen(photo, 'paste', event => {
      const file=photoFileFromEvent(event); if(!file)return;
      event.preventDefault(); uploadFile(file);
    });
    listen(photo, 'dragover', event => {
      if ([...(event.dataTransfer?.types || [])].includes('Files')) event.preventDefault();
    });
    listen(photo, 'drop', event => {
      if (event.defaultPrevented) return;
      const file=photoFileFromEvent(event);
      // Even an unsupported file must not navigate away from entered details.
      const transfer=event.dataTransfer;
      const hasFiles=transfer?.files?.length || [...(transfer?.items || [])].some(item=>item.kind==='file') || [...(transfer?.types || [])].includes('Files');
      if (hasFiles) event.preventDefault();
      if (!file) {
        if (hasFiles) status(photo,T('admin.doctor.photo_invalid',null,'Elegí una foto en JPG, PNG, WEBP o HEIC.'),true);
        return;
      }
      event.preventDefault(); uploadFile(file);
    });
    listen(photo, 'click', event => {
      const button = event.target.closest('[data-photo-action]'); if(!button)return;
      const action=button.dataset.photoAction;
      if(action==='original') {
        const dialog=$('[data-original-dialog]'), image=dialog.querySelector('img');
        dialog.querySelector('[data-original-error]').textContent='';image.src=url+'?original=1';dialog.showModal();return;
      }
      mutation(photo,async()=>{
        const body = action==='left' || action==='right' ? {action:'rotate',rotation:(rotation+(action==='left'?270:90))%360} : {action};
        const result=await owner.mutate(url,body,'POST',photoAck); if(alive)paint(result);
      });
    });
  }

  const schedule = $('[data-schedule]');
  const originalDialog=$('[data-original-dialog]');
  if(originalDialog) listen(originalDialog.querySelector('img'),'error',()=>{originalDialog.querySelector('[data-original-error]').textContent=T('admin.doctor.original_failed',null,'No se pudo cargar el original. Volvé a iniciar sesión o reintentá.');});
  listen($('[data-close-original]'),'click',()=>{originalDialog.close();originalDialog.querySelector('img').removeAttribute('src');});
  const servicesOf = section => { try { return JSON.parse(section.dataset.services || '[]'); } catch { return []; } };
  // A weekday card is created in weekday order inside [data-days]; unchecking
  // the day removes its card (and its intervals) — the working copy, not the
  // saved schedule, is what changes.
  function toggleDay(section, id, on) {
    const holder = section.querySelector('[data-days]');
    if (!holder) return;
    const row = holder.querySelector('.dw-day[data-day-row="' + id + '"]');
    if (!on) { if (row) row.remove(); return; }
    if (row) return;
    const day = days().find(d => d.id === id) || {id, label: String(id), name: String(id)};
    holder.insertAdjacentHTML('beforeend', dayRowEditor(day, [], servicesOf(section)));
    const fresh = holder.lastElementChild, order = dayOrder(id);
    for (const existing of holder.querySelectorAll('.dw-day'))
      if (dayOrder(existing.dataset.dayRow) > order) { holder.insertBefore(fresh, existing); break; }
  }
  // «Copiar a otros días»: one open panel per center, explicitly chosen target
  // days, and any overwrite named before the copy happens.
  function copyPanel(dayRow) {
    const section = dayRow.closest('[data-center]');
    const open = section.querySelector('[data-copy-panel]');
    if (open) { const same = open.closest('.dw-day') === dayRow; open.remove(); if (same) return; }
    const from = Number(dayRow.dataset.dayRow);
    const source = days().find(d => d.id === from) || {label: String(from), name: String(from)};
    dayRow.insertAdjacentHTML('beforeend',
      '<div class="dw-copy" data-copy-panel data-from="' + from + '">' +
      '<span class="dw-copy-label">' + esc(T('admin.doctor.copy_to', {day: source.name || source.label}, 'Copiar el horario de {day} a:')) + '</span>' +
      '<div class="dw-days dw-copy-days">' + days().filter(d => d.id !== from).map(d =>
        '<label><input type="checkbox" data-copy-to="' + d.id + '"><span>' + esc(d.label) + '</span></label>').join('') + '</div>' +
      '<p class="dw-copy-warn" data-copy-warn hidden></p>' +
      '<div class="dw-copy-actions"><button type="button" class="btn" data-copy-confirm>' + esc(T('admin.doctor.copy_confirm', null, 'Copiar horarios')) + '</button>' +
      '<button type="button" class="btn" data-copy-cancel>' + esc(T('admin.content.schedule.reset_cancel', null, 'Cancelar')) + '</button></div></div>');
  }
  function warnCopy(panel) {
    const section = panel.closest('[data-center]');
    const replacing = [];
    for (const chip of panel.querySelectorAll('[data-copy-to]:checked')) {
      const row = section.querySelector('.dw-day[data-day-row="' + chip.dataset.copyTo + '"]');
      if (row && row.querySelectorAll('.dw-slot').length) replacing.push(chip.parentElement.querySelector('span').textContent);
    }
    const warn = panel.querySelector('[data-copy-warn]');
    warn.hidden = !replacing.length;
    warn.textContent = replacing.length
      ? T('admin.doctor.copy_replace_warn', {days: replacing.join(', ')}, 'Vas a reemplazar los horarios de: {days}.') : '';
  }
  function applyCopy(panel) {
    const dayRow = panel.closest('.dw-day');
    const section = dayRow.closest('[data-center]');
    // Visible hours only: a copied interval is a new band and must not carry
    // the source band's hidden facts (cupos, closures).
    const values = [...dayRow.querySelectorAll('.dw-slot')].map(slot => ({
      desde: (slot.querySelector('[data-time="desde"]') || {}).value || '08:00',
      hasta: (slot.querySelector('[data-time="hasta"]') || {}).value || '12:00',
      cada_min: Number((slot.querySelector('[data-time="cada_min"]') || {value: 20}).value) || 20,
      servicio: (slot.querySelector('[data-service]') || {}).value || ''}));
    if (values.length)
      for (const chip of panel.querySelectorAll('[data-copy-to]:checked')) {
        const id = Number(chip.dataset.copyTo);
        toggleDay(section, id, true);
        const pick = section.querySelector('input[data-day="' + id + '"]');
        if (pick) pick.checked = true;
        section.querySelector('.dw-day[data-day-row="' + id + '"] [data-slots]').innerHTML =
          values.map(value => slotEditor(value, servicesOf(section))).join('');
      }
    panel.remove();
  }
  listen(schedule,'input',()=>{scheduleRevision++;dirty.add('schedule');});
  listen(schedule,'change',event=>{
    const target=event.target;
    if(target && target.matches && target.matches('input[data-day]')){
      toggleDay(target.closest('[data-center]'),Number(target.dataset.day),target.checked);
      scheduleRevision++;dirty.add('schedule');return;}
    const chip=target && target.closest ? target.closest('[data-copy-to]') : null;
    if(chip){warnCopy(chip.closest('[data-copy-panel]'));return;}
    // Times shape on commit (blur/Enter), never per keystroke: rewriting the
    // value mid-typing moved the caret and ate digits.
    const field=target && target.closest ? target.closest('.dw-time') : null;
    if(field){const shaped=formatTime(field.value);if(shaped!==field.value){field.value=shaped;scheduleRevision++;dirty.add('schedule');}}
  });
  listen(schedule,'click',event=>{
    const remove=event.target.closest('[data-remove-slot]');
    if(remove){remove.closest('.dw-slot').remove();scheduleRevision++;dirty.add('schedule');return;}
    const copyDay=event.target.closest('[data-copy-day]');
    if(copyDay){copyPanel(copyDay.closest('.dw-day'));return;}
    const copyConfirm=event.target.closest('[data-copy-confirm]');
    if(copyConfirm){applyCopy(copyConfirm.closest('[data-copy-panel]'));scheduleRevision++;dirty.add('schedule');return;}
    const copyCancel=event.target.closest('[data-copy-cancel]');
    if(copyCancel){copyCancel.closest('[data-copy-panel]').remove();return;}
    const add=event.target.closest('[data-add-slot]'); if(!add)return;
    const day=add.closest('.dw-day'),section=day.closest('[data-center]');
    day.querySelector('[data-slots]').insertAdjacentHTML('beforeend',slotEditor({},servicesOf(section)));
    scheduleRevision++;dirty.add('schedule');
  });
  listen(schedule,'submit',event=>{event.preventDefault();finish.click();});
  const finish=$('[data-finish]');
  listen(finish,'click',()=>mutation(finish.closest('[data-finish-section]'),async()=>{
    if(running>1 || readers.size)throw new Error(T('admin.doctor.wait_photo',null,'Esperá a que termine de guardarse la foto.'));
    // Invited without a center: retain the existing one-time first-center
    // choice. Its weekday rows use the same collector as assigned centers.
    const newCenter=data.needs_center ? schedule.querySelector('[data-center-new]') : null;
    const sendsSchedule=data.editable || !!newCenter;
    if(sendsSchedule)schedule.querySelectorAll('.dw-time').forEach(field=>{const shaped=formatTime(field.value);if(shaped!==field.value)field.value=shaped;});
    if (!info.reportValidity() || (sendsSchedule && !schedule.reportValidity())) return false;
    const revision=[profileRevision,scheduleRevision];
    const fields=Object.fromEntries(new FormData(info));
    if(newCenter)newCenter.dataset.center=newCenter.querySelector('[data-center-select]').value;
    const centers=newCenter
      ? [{centro:newCenter.dataset.center,franjas:[],excepciones:[]}]
      : structuredClone(doctor.centros?.length ? doctor.centros : [{centro:doctor.centro,franjas:doctor.agenda?.franjas || (doctor.agenda?.dias ? [doctor.agenda]:[]),excepciones:doctor.agenda?.excepciones || []}]);
    if(sendsSchedule){
      const names=new Map([...(data.schedules||[]).map(row=>[row.centro,row.center]),...(data.center_options||[]).map(row=>[row.slug,row.name])]);
      const sections=newCenter ? [newCenter] : [...schedule.querySelectorAll('[data-center][data-editable]')];
      const edited=collectSchedule(sections,newCenter ? [] : renderedCenters)
        .map(center=>({...center,center:names.get(center.centro)||center.centro}));
      const problems=scheduleProblems(edited);
      // A selected day without intervals would silently disappear on reload.
      const emptyDay=sections.flatMap(section=>[...section.querySelectorAll('.dw-day')]).find(row=>!row.querySelectorAll('.dw-slot').length);
      if(emptyDay || (edited.length && !edited.some(center=>center.franjas.length)))
        problems.unshift(T('admin.doctor.empty_schedule',null,'Marcá al menos un día y cargá un horario.'));
      if(problems.length)throw new Error(problems[0]);
      for(const center of edited){
        const target=centers.find(c=>c.centro===center.centro);
        if(target)target.franjas=center.franjas;
      }
    }
    const saved=await owner.mutate('/api/admin/content/doctors/'+encodeURIComponent(doctor.id),{...fields,...(sendsSchedule?{centros:centers}:{})},'PUT',true);
    if(!alive)return;
    if(revision[0]!==profileRevision || revision[1]!==scheduleRevision || readers.size || running>1)throw new Error(T('admin.doctor.changed_during_save',null,'Los datos cambiaron mientras se guardaban. Guardá de nuevo.'));
    Object.assign(doctor,saved.item);dirty.clear();
    const result=await owner.mutate('/api/admin/onboarding/complete',{},'POST',body=>['/admin/cola','/admin/account'].includes(body?.next)); if(alive)navigate(result.next);
  }));
  return ()=>{alive=false; if(originalDialog && originalDialog.open)originalDialog.close(); photoGeneration++;owner.dispose();reservations.forEach(release);listeners.forEach(fn=>fn());readers.forEach(r=>r.abort());readers.clear();};
}
