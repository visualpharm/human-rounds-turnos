// Roles use the existing permission store; each route owns all pending work.
function dependency(name) {
  const url = window.SANA_STATIC?.[name];
  if (!url) throw new Error('[static] ' + name + ' is not in window.SANA_STATIC');
  return import(url);
}
const [I18n, Session, Table] = await Promise.all([
  dependency('platform/i18n.mjs'), dependency('platform/admin-session.mjs'),
  dependency('platform/admin-table.mjs'),
]);
const T = (key, fallback) => I18n.t('admin.roles.' + key, null, fallback);
// A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
const errText = (raw, fallback) => (I18n.apiError ? I18n.apiError(raw, fallback) : fallback);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[c]);

export function mount(host) {
  let sequence = 0;
  let active = true;
  let controller;
  let table;
  const status = document.getElementById('roles-status');
  const current = seq => active && host.isConnected && sequence === seq;
  const notify = text => { status.textContent = text; status.hidden = !text; };
  async function request(url, options, seq) {
    const response = await fetch(url, { credentials: 'same-origin', signal: controller.signal, ...options });
    if (!current(seq)) throw new DOMException('Old screen', 'AbortError');
    if ([401, 403].includes(response.status)
        && await Session.confirmar(response, undefined, () => current(seq))) {
      throw new DOMException('Session boundary', 'AbortError');
    }
    if (!current(seq)) throw new DOMException('Old screen', 'AbortError');
    let body;
    try { body = await response.json(); }
    catch { throw new Error(T('save_error', 'No se pudo guardar. Reintentá.')); }
    if (!response.ok) throw Object.assign(new Error(body?.error || T('save_error', 'No se pudo guardar. Reintentá.')), { field: body?.field });
    return body;
  }
  const href = slug => '/admin/roles?role=' + encodeURIComponent(slug);
  function navigate(url, replace = false) {
    history[replace ? 'replaceState' : 'pushState']({}, '', url);
    return route();
  }
  function list(roles) {
    document.getElementById('adm-h1').textContent = T('title', 'Roles y permisos');
    // The list takes the full content width and spreads its columns over it,
    // like every other panel list (admin-direct.css lifts the content cap);
    // the editor below keeps the capped form width.
    listView(true);
    titleAction();
    host.innerHTML = '<div class="cnt-table-wrap"><table class="cnt-table"><thead><tr>'
      + '<th scope="col" class="cnt-th" data-col="name">' + esc(T('name', 'Nombre')) + '</th>'
      + '</tr></thead><tbody>' + roles.map(role => '<tr><td data-col="name"><a href="' + href(role.slug) + '">'
        + esc(role.name) + '</a>'
        // «Sí» in every row said nothing (the product owner, 02.11: what repeats on every
        // row goes outside the brackets): only a switched-off role is marked.
        + (role.enabled ? '' : ' <span class="roles-off">' + esc(T('inactive', 'inactivo')) + '</span>')
        + '</td></tr>').join('')
      + '</tbody></table></div>';
    table = Table.crear({ clave: 'roles', selector: '#sec-roles .cnt-table', host: () => host,
      tablas: () => host.querySelectorAll('.cnt-table'),
      cols: () => [{ key: 'name', min: 150, max: 9999, flex: 1 }] });
    table.programarFit();
  }
  // One screen header (the product owner, 2026-11-08): «Nuevo rol» sits at the right end
  // of the title's own line (.adm-title-action, admin-direct.css) while the
  // list is on screen; every route takes it away first.
  function titleAction() {
    const row = document.querySelector('.adm-h1row');
    if (!row || document.getElementById('role-new')) return;
    const link = document.createElement('a');
    link.id = 'role-new';
    link.className = 'btn btn-accent adm-title-action';
    link.href = '/admin/roles?new=1';
    link.textContent = T('new', 'Nuevo rol');
    link.addEventListener('click', click);
    row.insertBefore(link, row.querySelector('.alert-bell-wrap'));
  }
  const dropTitleAction = () => document.getElementById('role-new')?.remove();
  function listView(on) {
    if (typeof host.setAttribute !== 'function') return;
    if (on) host.setAttribute('data-list-view', '1'); else host.removeAttribute('data-list-view');
  }
  function editor(role, catalog, seq) {
    const fresh = !role;
    const locked = !!role?.locked;
    listView(false);
    // «Roles y permisos / name» in the title band, like a doctor's card: the
    // crumb no longer sits under the name it leads back from (ui-expert 15.11).
    const h1 = document.getElementById('adm-h1');
    h1.innerHTML = '<a class="adm-crumb" href="/admin/roles">' + esc(T('title', 'Roles y permisos'))
      + '</a><span class="adm-crumb-sep">/</span>' + esc(fresh ? T('new', 'Nuevo rol') : role.name);
    h1.querySelector('a').addEventListener('click', click);
    const row = (key, label, input) => '<div class="cnt-row"><label for="role-' + key + '">'
      + esc(label) + '</label><div class="cnt-field">' + input
      + '<p class="cnt-msg" id="role-' + key + '-error" role="alert" hidden></p></div></div>';
    host.innerHTML = '<form id="role-form"><fieldset class="roles-fields"' + (locked ? ' disabled' : '') + '>'
      + row('name', T('name', 'Nombre'), '<input class="cnt-in cnt-field-name" id="role-name" name="name" maxlength="80" required value="' + esc(role?.name) + '">')
      + row('description', T('description', 'Descripción'), '<textarea class="cnt-in" id="role-description" name="description" maxlength="300" required>' + esc(role?.description) + '</textarea>')
      + '<div class="cnt-row"><span>' + esc(T('permissions', 'Puede')) + '</span><div class="roles-permissions">'
      + catalog.map(permission => '<label class="cnt-check"><input type="checkbox" name="role-permission" value="'
        + esc(permission.id) + '"' + (role?.permissions.includes(permission.id) ? ' checked' : '') + '><span>'
        + esc(permission.label) + '</span></label>').join('') + '</div></div>'
      + (fresh ? '' : '<div class="cnt-row"><span></span><div class="cnt-field"><label class="cnt-check"><input id="role-enabled" name="enabled" type="checkbox"' + (role.enabled ? ' checked' : '') + '><span>' + esc(T('enabled', 'Activo')) + '</span></label><p class="cnt-msg" id="role-enabled-error" role="alert" hidden></p></div></div>')
      + '</fieldset>' + (locked ? '' : '<div class="cnt-actions"><button id="role-save" type="submit" class="btn btn-ok">'
        + esc(T('save', 'Guardar')) + '</button></div>') + '</form>';
    const form = host.querySelector('form');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const fieldset = form.querySelector('fieldset');
      const button = form.querySelector('#role-save');
      if (locked || !current(seq) || button.disabled) return;
      const body = {
        name: form.elements.name.value, description: form.elements.description.value,
        permissions: [...form.querySelectorAll('[name="role-permission"]:checked')].map(input => input.value),
        ...(fresh ? {} : { enabled: form.elements.enabled.checked }),
      };
      notify('');
      form.querySelectorAll('.cnt-msg').forEach(message => { message.textContent = ''; message.hidden = true; });
      button.disabled = true;
      fieldset.disabled = true;
      try {
        const saved = await request('/api/admin/roles' + (fresh ? '' : '/' + encodeURIComponent(role.slug)), {
          method: fresh ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        }, seq);
        if (!current(seq)) return;
        if (!saved || !/^[a-z][a-z0-9_]{0,39}$/.test(saved.slug)
            || (!fresh && saved.slug !== role.slug)
            || typeof saved.name !== 'string' || typeof saved.description !== 'string'
            || typeof saved.enabled !== 'boolean' || !Array.isArray(saved.permissions)
            || saved.permissions.some(id => !catalog.some(permission => permission.id === id))) {
          throw new Error(T('save_error', 'No se pudo guardar. Reintentá.'));
        }
        if (fresh) {
          await navigate(href(saved.slug), true);
        } else {
          form.elements.name.value = saved.name;
          form.elements.description.value = saved.description;
        }
        if (active) notify(T('saved', 'Guardado.'));
      } catch (error) {
        if (current(seq) && error.name !== 'AbortError') {
          const message = ['name', 'description', 'enabled'].includes(error.field)
            ? form.querySelector('#role-' + error.field + '-error') : null;
          if (message) { message.textContent = errText(error, T('save_error', 'No se pudo guardar. Reintentá.')); message.hidden = false; }
          else notify(errText(error, T('save_error', 'No se pudo guardar. Reintentá.')));
          fieldset.disabled = false;
          const input = form.elements.namedItem(error.field);
          input?.focus?.();
        }
      } finally {
        if (current(seq)) { button.disabled = false; fieldset.disabled = false; }
      }
    });
  }
  async function route() {
    const seq = ++sequence;
    table?.dispose();
    table = null;
    controller?.abort();
    controller = new AbortController();
    notify('');
    dropTitleAction();
    try {
      const params = new URLSearchParams(location.search);
      const slug = params.get('role');
      const fresh = params.get('new') === '1' && !slug;
      const roles = await request('/api/admin/roles', {}, seq);
      if (!Array.isArray(roles)) throw new Error(T('load_error', 'No se pudo cargar. Reintentá.'));
      if (!current(seq)) return;
      if (!fresh && !slug) { list(roles); return; }
      const role = fresh ? null : roles.find(item => item.slug === slug);
      if (!fresh && !role) throw new Error(T('not_found', 'No se encontró ese rol.'));
      const catalog = await request('/api/admin/permissions', {}, seq);
      if (!Array.isArray(catalog) || !catalog.length) throw new Error(T('load_error', 'No se pudo cargar. Reintentá.'));
      if (current(seq)) editor(role, catalog, seq);
    } catch (error) {
      if (!current(seq) || error.name === 'AbortError') return;
      // The band never names a role that is not on screen.
      document.getElementById('adm-h1').textContent = T('title', 'Roles y permisos');
      host.innerHTML = '<p role="alert">' + esc(errText(error, T('load_error', 'No se pudo cargar. Reintentá.')))
        + '</p><button type="button" class="btn" id="role-retry">' + esc(T('retry', 'Reintentar')) + '</button>';
      host.querySelector('#role-retry').addEventListener('click', route);
    }
  }
  function click(event) {
    const link = event.target.closest('a[href]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button) return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || url.pathname !== '/admin/roles') return;
    event.preventDefault();
    navigate(url.pathname + url.search);
  }
  const dispose = () => { active = false; ++sequence; dropTitleAction(); controller?.abort(); table?.dispose(); host.removeEventListener('click', click); window.removeEventListener('popstate', route); window.removeEventListener('pagehide', dispose); };
  host.addEventListener('click', click);
  window.addEventListener('popstate', route);
  window.addEventListener('pagehide', dispose);
  dispose.ready = route();
  return dispose;
}
