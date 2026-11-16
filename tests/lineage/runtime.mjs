import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
export const source = file => readFileSync(new URL('../../' + file, import.meta.url), 'utf8');
export const plain = value => JSON.parse(JSON.stringify(value));
export const flush = () => new Promise(resolve => setImmediate(resolve));
export const T = (_key, _vars, fallback) => fallback;
export function storage(initial = {}) {
  const values = new Map(Object.entries(initial).map(([k, v]) => [k, JSON.stringify(v)]));
  return { getItem: k => values.get(k) ?? null, setItem: (k,v) => values.set(k, String(v)), removeItem: k => values.delete(k) };
}
// Legacy closures have no public export. Execute their verbatim function
// declarations in a VM with explicit boundary doubles; never reproduce logic.
export function declaration(file, name) {
  const text = source(file);
  const match = new RegExp('^([ \t]*)(?:async )?function ' + name + '\\(', 'm').exec(text);
  assert.ok(match, `${file}: missing production function ${name}`);
  const start = match.index, indent = match[1];
  const lines = text.slice(start).split('\n');
  for (let n = 1; n <= lines.length; n++) {
    if (n > 1 && !lines[n-1].startsWith(indent + '}')) continue;
    const code = lines.slice(0, n).join('\n');
    try { new vm.Script('(' + code.trim() + ')'); return code; } catch {}
  }
  throw new Error(`Cannot isolate ${file}#${name}`);
}
export function runtime(file, names, globals = {}, extra = '') {
  const context = vm.createContext({ console, T, ...globals });
  vm.runInContext(names.map(name => declaration(file, name)).join('\n') + '\n' + extra, context, { filename: file });
  return context;
}
export function adminRuntime(row, server) {
  const listeners = new Map(), requests = [];
  const context = runtime('static/root/admin-app.js', ['executePending', 'slotsPara', 'poblarTurnoSel', 'turnoPreferido', 'agendaMarcar', 'agendaReintentar', 'svcDeTarjeta'], {
    ALL: [structuredClone(row)], EMR_SYNC_ACTIVE: false, CURRENT_ROLE: 'admin', recordarAprobada() {},
    defaultsDe: () => ({ turno: '2026-09-17T09:00', servicio_id: 'svc', medico: 'Doctor One' }),
    toIsoWithOffset: x => x, esc: String, fmtTurno: String, prefGeneral: () => '', toast() {},
    document: { addEventListener: (name, fn) => listeners.set(name, fn) },
    // Desk slots (/api/admin/appointment-slots): flat `libres`, each with its doctor.
    fetch: async url => { requests.push(url); return new Response(JSON.stringify({libres: server.slots.map(t => ({...t, medico: 'Doctor One'}))})); },
    api: async (url, options) => {
      requests.push(url);
      if (url.endsWith('/approve')) server.slots = server.slots.slice(1);
      return new Response(JSON.stringify({solicitud: server.row}));
    }
  }, 'const SLOTS_CACHE = new Map(); let AGENDA_TOKEN = 0;');
  const text = source('static/root/admin-app.js');
  const start = text.indexOf("document.addEventListener('change', e=>{\n  const medSel=");
  const end = text.indexOf('\n// pager:', start);
  assert.ok(start >= 0 && end > start, 'assignment event boundary exists');
  vm.runInContext(text.slice(start, end), context, {filename:'admin-app.js'});
  return {context, listeners, requests};
}
