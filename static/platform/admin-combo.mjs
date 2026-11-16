/* admin-combo.mjs — the admin panel's typeable combo box (input + filterable
   list), extracted from admin-app.js on 2026-08-21 so admin-content.js (and
   any future direct /admin/<screen> page) can render and operate the widget
   WITHOUT loading the legacy queue controller.

   This file is the ONLY source of the combo's markup, keyboard, pointer and
   focus behavior. Consumers never re-implement `.cmb` / `.cmb-in` /
   `.cmb-list` — they call the boundary below and the document-level
   listeners installed here drive every combo on the page, whoever painted
   it. The CSS stays where it was (admin.html and templates/admin/base.html:
   "combos tipeables"); this module owns behavior, not design.

   Boundary (named ES-module exports — the complete, stable surface):
     html(cls, items, value, display, placeholder, title) -> string
         The control. `cls` is the class of the hidden <input> that carries
         the canonical value (consumers read it: .svc-sel, .med-sel,
         .fr-svc-sel, .pat-medico-sel) and fires a real 'change' on pick.
         items = [{v, t, s?, k?}] — value, visible text, subtitle, hidden
         search keywords.
     register(cls, policy)
         Per-hidden-class behavior. policy.freeText: typed text that matches
         no item is accepted as the value (the queue's "Médico"); default is
         catalog-only — free text reverts to the last valid display.
         policy.emptyHtml(q, box) -> html|'' : what the open list says when
         nothing matches; '' falls back to the shared "Sin resultados".
     parts(box), filter(items, q), open(box, q), pick(box, el), close(box)
         The behavior pieces, exported for tests and for a consumer that has
         to repaint a list it already drew. Nothing else is public.
   Consumers import this exact fingerprinted module before their first render.
   It needs nothing at load time; at CALL
   time it imports platform/search.mjs (stemming filter, substring
   as the safety net when ranking returns nothing). Search is a local
   binding from top-level await — never a global, never optional.
   A consumer that needs the combo fails its boot promise when this file is
   missing — never paint a second implementation or publish a global shim. */
"use strict";

  // Same resolver as every module (i18n.js): without window.SanaI18n the
  // Spanish literal wins, never the raw key.
  var I18n = await (
      (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
        ? import(window.SANA_STATIC["platform/i18n.mjs"])
        : import(new URL("./i18n.mjs", import.meta.url))
    );
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  var Search = await (
    (typeof window !== "undefined")
      ? ((window.SANA_STATIC && window.SANA_STATIC["platform/search.mjs"])
        ? import(window.SANA_STATIC["platform/search.mjs"])
        : Promise.reject(new Error("[static] platform/search.mjs is not in window.SANA_STATIC")))
      : import(new URL("./search.mjs", import.meta.url))
  );
  const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function _norm(s){
    return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
  }

  // --- per-class policies ----------------------------------------------------
  // Keyed by the class of the hidden <input> (the one consumers already read),
  // so a consumer declares HOW its combo closes and what its empty list says
  // without the widget knowing the consumer. Unregistered classes get the
  // strict default: catalog-only value, shared "Sin resultados".
  const POLICIES = Object.create(null);
  export function register(cls, policy){
    if(!cls || typeof cls !== 'string') throw new Error('admin-combo.register: cls must be a hidden-input class name');
    POLICIES[cls] = Object.assign({}, POLICIES[cls] || {}, policy || {});
  }
  function policyOf(hid){
    if(!hid || !hid.classList) return {};
    for(const cls of Object.keys(POLICIES)){
      if(hid.classList.contains(cls)) return POLICIES[cls];
    }
    return {};
  }

  // --- combo box (input + filterable list) -----------------------------------
  // "Route to" and "Doctor" are typeable combo boxes (the product owner, 2026-07-19):
  // focus opens the complete list, typing filters without accents, and arrow
  // keys plus Enter select an item. The canonical value lives in a hidden
  // input that preserves the consumer's class (.svc-sel/.med-sel) and emits a
  // real change event after a selection.
  export function html(cls, items, value, display, placeholder, title){
    const data = esc(JSON.stringify(items));
    // Size from real content (2026-07-20): the selected value and a typical
    // long item must fit in full. Use the 90th percentile rather than the
    // maximum so one outlier does not widen every control. Measure only text
    // that can reach the input: a service's centre subtitle does, while a
    // doctor's availability tail exists only in the wider drop-down list.
    const lens = items.map(x=>((x.t||'') + (/^—/.test(x.s||'') ? '' : ' '+(x.s||''))).length).sort((a,b)=>a-b);
    const p90 = lens.length ? lens[Math.min(lens.length-1, Math.floor(lens.length*0.9))] : 0;
    const largo = Math.max(p90, (display||placeholder||'').length);
    // `ch` is the width of «0»: capitals and the chevron run wider, so +3 cut
    // the last letter of «Tomas Pereyra» (the reference-deployment counterpart, 2026-10-19). +6 fits it.
    const w = Math.min(48, Math.max(15, largo + 6));
    return `<span class="cmb" data-items="${data}" data-disp="${esc(display||'')}" title="${esc(title||'')}">
    <input type="hidden" class="${cls}" value="${esc(value||'')}">
    <input type="text" class="cmb-in" style="width:${w}ch" value="${esc(display||'')}" placeholder="${esc(placeholder||'')}" autocomplete="off" spellcheck="false">
    <div class="cmb-list" hidden></div>
  </span>`;
  }

  // --- combo behavior --------------------------------------------------------
  // Focus opens the full list; typing filters accent-insensitively; arrows,
  // Enter, and pointer input select. The policy registered for the hidden
  // input's class decides how free text and an empty list behave.
  export function parts(box){
    return {hid: box.querySelector('input[type=hidden]'), inp: box.querySelector('.cmb-in'),
            list: box.querySelector('.cmb-list'), items: JSON.parse(box.dataset.items||'[]')};
  }
  // Reuse the patient home's search ranker so professional nouns can match
  // specialty names. Hidden service keywords (`k`) participate just as they
  // do in public search. Plain accent-insensitive substring matching remains
  // the safety net when the ranker discards the complete query.
  export function filter(items, q){
    const nq = _norm(q||'');
    if(!nq) return items;
    if(Search && Search.buscarConPuntaje){
      const r = Search.buscarConPuntaje(items, q,
        it=>({principal:[it.t], resto:[it.s||'', it.k||'']}));
      if(r.length) return r.map(x=>x.row);
    }
    return items.filter(it=>_norm(`${it.t} ${it.s||''} ${it.k||''}`).includes(nq));
  }
  export function open(box, q){
    const {list, items} = parts(box);
    const nq = _norm(q||'');
    const match = filter(items, q);
    list.innerHTML = match.slice(0,200).map((it,i)=>
      `<div class="cmb-it${i===0&&nq?' sel':''}" data-v="${esc(it.v)}" data-t="${esc(it.t)}" data-s="${esc(it.s||'')}">${esc(it.t)}${it.s?` <span class="cmb-sub">${esc(it.s)}</span>`:''}</div>`
    ).join('') || cmbVacio(box, q);
    list.hidden = false;
  }

  // A consumer policy may replace the generic empty result with useful domain
  // guidance. An empty policy response falls back to the shared message.
  function cmbVacio(box, q){
    const hid = box.querySelector('input[type=hidden]');
    const pol = policyOf(hid);
    if(typeof pol.emptyHtml === 'function'){
      const html = pol.emptyHtml(q, box);
      if(html) return html;
    }
    return '<div class="cmb-none">'+esc(T("admin.queue.solicitudes.sin_resultados", null, "Sin resultados"))+'</div>';
  }
  export function pick(box, el){
    const {hid, inp, list} = parts(box);
    // A service's centre subtitle ("· Hospital de Pinamar") disambiguates two
    // services with the same name, so it stays in the closed control. A
    // doctor's availability tail ("— libre …") only orients the open list: the
    // closed control shows the name alone (same rule html() sizes by).
    const sub = el.dataset.s || '';
    const disp = el.dataset.t + (sub && !/^—/.test(sub) ? ' '+sub : '');
    inp.value = disp;
    box.dataset.disp = disp;
    list.hidden = true;
    if(hid.value !== el.dataset.v){
      hid.value = el.dataset.v;
      hid.dispatchEvent(new Event('change', {bubbles:true}));
    }
  }
  export function close(box){
    const {hid, inp} = parts(box);
    box.querySelector('.cmb-list').hidden = true;
    const txt = inp.value.trim();
    const disp = box.dataset.disp || '';
    if(txt === disp) return;
    if(!txt){ hid.value=''; box.dataset.disp=''; hid.dispatchEvent(new Event('change',{bubbles:true})); return; }
    if(policyOf(hid).freeText){
      // Free text is allowed for this registered policy.
      hid.value = txt; box.dataset.disp = txt;
      hid.dispatchEvent(new Event('change', {bubbles:true}));
    }else{
      inp.value = disp;   // The value must come from the catalog.
    }
  }
  function install(doc){
    doc.addEventListener('focusin', e=>{
      if(e.target.classList && e.target.classList.contains('cmb-in')){
        e.target.select();
        open(e.target.closest('.cmb'), '');
      }
    });
    doc.addEventListener('input', e=>{
      if(e.target.classList && e.target.classList.contains('cmb-in'))
        open(e.target.closest('.cmb'), e.target.value);
    });
    doc.addEventListener('keydown', e=>{
      if(!(e.target.classList && e.target.classList.contains('cmb-in'))) return;
      const box = e.target.closest('.cmb'), list = box.querySelector('.cmb-list');
      if(e.key==='Escape'){ list.hidden = true; return; }
      const its = [...list.querySelectorAll('.cmb-it')];
      if(!its.length) return;
      if(e.key==='ArrowDown' || e.key==='ArrowUp'){
        e.preventDefault();
        if(list.hidden){ open(box, ''); return; }
        const i = its.findIndex(x=>x.classList.contains('sel'));
        const j = e.key==='ArrowDown' ? Math.min(i+1, its.length-1) : Math.max(i-1, 0);
        its.forEach((x,k)=>x.classList.toggle('sel', k===j));
        its[j].scrollIntoView({block:'nearest'});
      }else if(e.key==='Enter'){
        const sel = its.find(x=>x.classList.contains('sel')) || (its.length===1 && its[0]);
        if(sel && !list.hidden){ e.preventDefault(); pick(box, sel); }
      }
    });
    doc.addEventListener('pointerdown', e=>{
      const it = e.target.closest('.cmb-it');
      if(it){ e.preventDefault(); pick(it.closest('.cmb'), it); return; }
    });
    doc.addEventListener('focusout', e=>{
      if(e.target.classList && e.target.classList.contains('cmb-in')){
        const box = e.target.closest('.cmb');
        setTimeout(()=>{ if(!box.contains(doc.activeElement)) close(box); }, 120);
      }
    });
  }

if (typeof document !== "undefined" && document && document.addEventListener) install(document);
