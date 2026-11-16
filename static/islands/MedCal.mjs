// static/islands/MedCal.mjs — Tier 5 slice 3 pilot island: the doctor
// Calendario tab's month grid (docs/roadmap/tier5-frontend-design.md,
// "Interactive screens as Preact+HTM islands"). Picked per that doc's own
// "Decisions for the product owner" recommendation — the doctor Calendario tab, "already
// visually and functionally self-contained" per admin-cola-y-agenda.md.
//
// This island wraps the EXISTING renderer, it does not reimplement markup:
// gridHtml()/css come from the canonical calendar-grid.mjs used by every
// other caller and by stories/CalendarGrid.stories.js. What this
// file adds is ONLY the interactivity admin-content.js used to hand-roll as
// imperative state (MEDCAL_SELECTED_DAY + a full host.innerHTML re-render
// on every day click) — Preact's diffing now owns that re-render loop, and
// htm's dangerouslySetInnerHTML-style binding injects the SAME HTML string
// gridHtml() already produced before this slice.
//
// admin-content.js (medicoCalendarioHtml/wireMedicoCalendario/
// cargarMedicoCalendario) still owns everything BELOW the grid — the
// per-day appointment tables, filters, sort, "Crear turno", the solicitud
// modal — none of that moved. It talks to this island through the plain
// controller `mount()` returns: `.update({dias, selected})` when fresh data
// arrives from the server, and this island calls `props.onSelect(diaIso)`
// so the legacy code can still re-render its own body and scroll to the
// selected day's block, exactly like today.
import { render } from "preact";
import { html } from "htm/preact";
import { gridHtml, css } from "sana/calendar-grid";

let styleInjected = false;
const STYLE_ID = "sana-medcal-css";
function ensureStyle() {
  if (styleInjected || document.getElementById(STYLE_ID)) {
    styleInjected = true;
    return;
  }
  // The same CSS is also injected once by admin-content.js for every other
  // caller of gridHtml() — this
  // island only needs its own copy when it mounts before that has run (a
  // Storybook story, or a future page that never loads admin-content.js).
  const tag = document.createElement("style");
  tag.id = STYLE_ID;
  tag.setAttribute("data-sana-island", "MedCal");
  tag.textContent = css;
  document.head.appendChild(tag);
  styleInjected = true;
}

// A CALLBACK ref, not useEffect(fn, [onSelect]) — deliberately. The grid's
// FIRST paint (mount(), before any real data has arrived) has dias=[],
// gridHtml() returns "" for an empty list, and Grid returns null: no DOM
// node exists yet, so an effect gated on `[onSelect]` runs once with a null
// ref and then never fires again once real data replaces the empty render,
// because `onSelect` itself never changes across that transition — a real
// bug this island shipped with and caught via the Playwright proof (day
// click did nothing; the listener was never actually attached). A callback
// ref sidesteps the whole "which render creates the node" question: Preact
// calls it exactly when the DOM node is created, whichever render that is.
function attachClick(el) {
  if (!el || el._sanaMedCalBound) return;
  el._sanaMedCalBound = true;
  el.addEventListener("click", (e) => {
    const day = e.target.closest("[data-med-date]");
    if (day && el._sanaMedCalOnSelect) el._sanaMedCalOnSelect(day.getAttribute("data-med-date"));
  });
}

function Grid({ dias, selected, onSelect }) {
  const markup = gridHtml(dias || [], { selected: selected || "", dateAttr: "data-med-date" });
  if (!markup) return null;
  // onSelect is read from the element at click time (see attachClick) rather
  // than closed over here, so a later render with a DIFFERENT onSelect
  // (not something this island's own mount()/update() ever does today, but
  // cheap to keep correct) still reaches the right callback without needing
  // a second listener.
  return html`<div ref=${(el) => { attachClick(el); if (el) el._sanaMedCalOnSelect = onSelect; }}
                    dangerouslySetInnerHTML=${{ __html: markup }}></div>`;
}

/** Called by islands.mjs (or directly by a caller that already resolved the
 * module, e.g. a Storybook story) — mount(el, props) renders the grid into
 * `el` and returns a controller with `.update(nextProps)` for imperative
 * refresh, the shape admin-content.js needs since its data arrives from a
 * client-side fetch, not server-rendered props — and `.dispose()`, the
 * teardown half of the islands lifetime contract (islands.mjs's
 * unmount(el) calls it): it runs Preact's own unmount path over `el`,
 * releasing this controller's whole tree, and is idempotent; update()
 * after dispose() is inert. It is also what islands.mjs calls on a
 * controller that completed after losing its element. Element identity,
 * not assumed microtask order, protects a replacement tree. */
const owners = new WeakMap();
export function mount(el, props) {
  owners.get(el)?.dispose();
  ensureStyle();
  let state = {
    dias: (props && props.dias) || [],
    selected: (props && props.selected) || "",
  };
  const select = (props && props.onSelect) || null;
  let disposed = false;
  let controller;
  const onSelect = select ? function (day) {
    if (!disposed && owners.get(el) === controller) select(day);
  } : null;

  function paint() {
    if (disposed || owners.get(el) !== controller) return;
    render(
      html`<${Grid} dias=${state.dias} selected=${state.selected} onSelect=${onSelect} />`,
      el
    );
  }

  controller = {
    update(nextProps) {
      nextProps = nextProps || {};
      state = {
        dias: "dias" in nextProps ? nextProps.dias : state.dias,
        selected: "selected" in nextProps ? nextProps.selected : state.selected,
      };
      paint();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (owners.get(el) !== controller) return;
      owners.delete(el);
      // The vendored preact.module.js this island loads via the import map
      // exports no `unmount` (verified against the file: render, hydrate,
      // h, options, ... only) — render(null, container) IS that build's
      // unmount path: the diff removes the tree it tracks on `el` and
      // leaves the element empty for whoever owns it next.
      render(null, el);
    },
  };
  owners.set(el, controller);
  try { paint(); } catch (error) { controller.dispose(); throw error; }
  return controller;
}

export default { mount };
