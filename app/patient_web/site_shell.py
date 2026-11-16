"""patient_web/site_shell.py — The page shell of the public site.

The shared stylesheet, footer, chat bootstrap, page icons and the frame every page is rendered in.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

from app.core import i18n
from app.core import jinja_env
from app.patient_web import seo
from app.core import site_identity
from app.core import static_fingerprint
from app.core import theme

from app.patient_web import site_base


# ---------------------------------------------------------------- CSS / shell
# NOTE: the CSS block below (_PAGE_CSS) is embedded product output served
# byte-for-byte to the browser — its internal Spanish comments are page
# content, not module documentation, and are intentionally left as-is.
_PAGE_CSS = """
  /* El navegador recibe el wizard ya armado, no un hueco que service.js llena
     después.  El alto de la barra se reserva también antes de que nav.js
     monte sus enlaces: así el primer contenido no salta cuando llegan los
     scripts diferidos. La reserva es el alto REAL montado: marca de 52px
     + 28 de padding + 1 de borde = 81px; en el teléfono (≤420px) la marca
     baja a 36px y manda el burger de 44px = 73px. */
  #stznav:empty{display:block;min-height:91px;background:var(--panel);border-bottom:1px solid var(--border);}
  @media(max-width:420px){#stznav:empty{min-height:73px;}}
  /* Primer paso del wizard, server-rendered. service.js usa estas mismas
     clases al rehidratarlo; esta parte del CSS tiene que estar en el <head>
     porque wz-upload.js corre después del primer pintado. */
  .wz-stepper{display:flex;align-items:center;margin:0;padding:0;list-style:none;}
  .wz-st{position:relative;display:flex;align-items:center;}
  .wz-st:not(:first-child){margin-left:34px;}
  .wz-st:not(:first-child):before{content:"";position:absolute;right:100%;top:50%;transform:translateY(-50%);width:34px;height:2px;background:var(--border);}
  .wz-st-n{box-sizing:border-box;flex:none;width:31px;height:31px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:var(--panel);border:2px solid var(--border);color:var(--text-3);font:700 14.5px var(--font);}
  .wz-st.on .wz-st-n{background:var(--accent);border-color:var(--accent);color:#fff;}
  .wz-uploadzone{border:1px solid var(--border-strong);background:var(--panel);border-radius:20px;padding:30px 22px;display:flex;flex-direction:column;align-items:center;text-align:center;gap:10px;cursor:pointer;}
  .wz-uploadzone:hover,.wz-uploadzone:focus-visible{outline:none;border-color:var(--hover-bd-strong);}
  .wz-cam{width:64px;height:64px;border-radius:50%;background:var(--accent-soft);display:flex;align-items:center;justify-content:center;color:var(--accent-hover);}
  .wz-up-h{display:inline-flex;align-items:center;justify-content:center;min-height:52px;max-width:100%;box-sizing:border-box;padding:0 24px;border-radius:var(--btn-radius,14px);background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);font-size:18px;font-weight:600;}
  .wz-up-sub{font-size:16px;color:var(--text-2);}
  .wz-up-paste{display:none;font-size:14.5px;color:var(--text-3);margin-top:2px;}
  .wz-up-desk{display:none;}
  @media(hover:hover) and (pointer:fine){.wz-up-paste{display:block;}.wz-up-desk{display:inline;}.wz-up-touch,.wz-up-sub{display:none;}}
  .wz-altcard{display:inline-block;margin-top:18px;cursor:pointer;}
  .wz-altcard-txt{display:inline;}
  .wz-altcard-t{font-size:16px;color:inherit;}
  .wz-altcard-s{display:none;}
  .wz-altcard svg{display:none;}
  /* Lugar reservado para la tabla que dibuja listing.js. Con el <script> en
     `defer` el primer pintado ocurre ANTES de que exista una sola fila, así
     que sin esto el pie de página nacía visible y se iba miles de píxeles
     para abajo (CLS). La altura NO se adivina: el servidor sabe cuántas filas
     manda y la escribe en --lst-reserva (una fila apilada de mobile mide
     ~109px; en tabla, ~63px), por eso hay dos valores y el corte es el mismo
     1040px donde listing.js pasa de tabla a filas apiladas.
     `:empty` hace que la reserva se borre sola apenas se dibuja la tabla:
     si el listado rinde más corto no queda ningún hueco.
     Esta regla vive ACÁ, inline en el <head>, y no en el CSS que inyecta
     listing.js: ese CSS recién existe cuando el script corre, o sea después
     del pintado que queremos estabilizar. */
  .lst-slot:empty,.lst-slot--ssr{min-height:var(--lst-reserva,0);}
  @media(min-width:1041px){.lst-slot:empty,.lst-slot--ssr{min-height:var(--lst-reserva-d,0);}}
  /* The catalog listing stays in the content column on desktop (review
     2026-11-15, D19/P26): no full-viewport breakout, one left edge with the
     title. */
  /* La lista de links que el servidor pinta adentro del slot/mosaico (sin JS
     es lo único que hay; listing.mjs/tiles.mjs la reemplazan al montar). */
  .lst-ssr{list-style:none;margin:14px 0 0;padding:0;}
  .lst-ssr li{padding:4px 0;}
  /* Mismo trato para el mosaico de tiles: sólo cuenta el <script> de datos
     adentro, así que `:empty` no sirve — se usa :not(:has(.tile)), que deja
     de aplicar apenas tiles.mjs dibuja la primera.
     El servidor da sólo cuentas (--tiles-n, --tiles-filas-2/-3); la altura de
     una fila es la foto (ancho de columna por su proporción) más el texto y el
     gap, con las mismas columnas que la grilla de tiles.mjs (minmax(300px,1fr),
     gap 16, .wrap 24px de lado: 2 columnas desde 664px, 3 desde 980px; hasta
     600px una sola, foto 16:9, gap 13, .wrap 16px). Texto+gap medido en el demo
     2026-11-10: 145-170px. Y el mismo margin-top que pone .tiles-grid, para que
     el mosaico no baje 14px al dibujarse. */
  [data-tiles]{margin-top:14px;}
  [data-tiles]>.lst-ssr{margin-top:0;}
  [data-tiles]:not(:has(.tile)){min-height:calc(var(--tiles-n,0) * ((100vw - 32px) * 0.5625 + 160px));}
  @media(min-width:601px){[data-tiles]:not(:has(.tile)){min-height:calc(var(--tiles-n,0) * ((100vw - 48px) * 0.75 + 164px));}}
  @media(min-width:664px){[data-tiles]:not(:has(.tile)){min-height:calc(var(--tiles-filas-2,0) * ((min(100vw, 1120px) - 64px) / 2 * 0.75 + 166px));}}
  @media(min-width:980px){[data-tiles]:not(:has(.tile)){min-height:calc(var(--tiles-filas-3,0) * ((min(100vw, 1120px) - 80px) / 3 * 0.75 + 166px));}}
  .wrap{width:100%;max-width:1120px;box-sizing:border-box;margin:0 auto;padding:26px 24px 80px;}
  /* flujo enfocado (asistente de pasos): mismo ancho de página que el resto
     del sitio (1000px) — SIN logotipo ni barra, el título del proceso es el
     encabezado (wizard-design, 2026-07-10) — pero el contenido (título,
     subtítulo, todo lo que arma el wizard adentro de #svc-form-host) se
     mantiene en una columna de lectura angosta, como el resto del sitio
     (.lede/.wz-card/.prep ya usan 560-660px pese al wrap de 1000px). */
  .wrap--focus{padding-top:12px;padding-bottom:60px;}
  /* La cabecera del asistente (título, pasos, subtítulo) usa una columna ancha;
     el cuerpo con los controles se queda angosto y prolijo. */
  .svc-head,.svc-sub{width:100%;max-width:840px;box-sizing:border-box;}
  /* min-height:0 explícito: desde que el footer compartido hace de .wrap una
     columna flex, el host es un ítem flex y su mínimo automático dejaría de
     ser 0 — fuera de la reserva del horario, cada paso usa su alto natural. */
  #svc-form-host{width:100%;max-width:600px;box-sizing:border-box;min-height:0;}
  /* The shared chat bootstrap arrives in the header, before the booking host.
     This server-rendered anchor reserves the exact inline launcher footprint
     before its native module settles, so moving the launcher into normal
     booking flow cannot push help/footer after first paint. */
  .stzc-booking-launcher-anchor{display:block;min-height:0;}
  @media(max-width:1100px){.stzc-booking-launcher-anchor{min-height:76px;}}
  /* A scheduled booking knows its first task before deferred modules arrive:
     choose a time. Paint that real task so the footer never begins above a
     blank form host. The reservation is the measured first screen, not the
     full, variable appointment list. service.js ends the boot marker when it
     renders a grid, real fallback form, or recovery message; the latter two
     retain this measured geometry while their useful content is in flow. */
  #svc-form-host.svc-form-host--schedule-boot,#svc-form-host.svc-form-host--schedule-fallback,#svc-form-host.svc-form-host--schedule-recovery{max-width:none;min-height:min(600px,66vh);min-height:min(600px,66svh);}
  /* From a tablet up the first screen shows two days (#20): the ready grid is
     340-460px tall there, so the reservation sits between it and the empty fallback (~800px) instead of 600px. */
  @media(min-width:600px){#svc-form-host.svc-form-host--schedule-boot,#svc-form-host.svc-form-host--schedule-fallback,#svc-form-host.svc-form-host--schedule-recovery{min-height:min(500px,66vh);min-height:min(500px,66svh);}}
  /* The host owns the measured height. The placeholder section must not add its
     own copy of it: the section's 34px top margin (.sec) would stack on top and
     the host would shrink by that much the moment the module replaces the
     section (a layout shift of the whole page below). */
  .svc-first-schedule{display:flex;align-items:flex-start;}
  #svc-form-host.svc-form-host--schedule-boot .wz-card{box-sizing:border-box;width:100%;min-width:0;max-width:560px;overflow-wrap:anywhere;background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px 18px 20px;margin-top:10px;}
  #svc-form-host.svc-form-host--schedule-boot .wz-spin{display:flex;align-items:center;gap:11px;color:var(--text-2);font-size:16px;padding:22px 4px;}
  #svc-form-host.svc-form-host--schedule-boot .wz-spin i{width:18px;height:18px;border:2.5px solid var(--border-strong);border-top-color:var(--accent);border-radius:50%;display:inline-block;animation:wzspin .8s linear infinite;}
  @keyframes wzspin{to{transform:rotate(360deg)}}
  /* El progreso sigue inmediatamente al H1: primero la acción actual, después
     1·2·3. No flota en la esquina ni compite con el título. */
  .svc-head{display:block;margin-top:6px;}
  .svc-head .h1--focus{margin:0;}
  #svc-steps{margin-top:var(--space-1);}
  #svc-steps:empty,.svc-sub:empty{display:none;}
  .svc-sub{font-size:19px;line-height:1.5;color:var(--text);margin:var(--space-2) 0 var(--space-3);}
  .h1.h1--focus{margin-top:4px;font-size:38px;}
  /* breadcrumb sobre el título del asistente (servicio · centro), texto plano —
     sin pills (responsable del producto 2026-07-12). El centro linkea a su página. */
  .svc-crumb{margin:24px 0 0;font-size:14.5px;color:var(--text-2);line-height:1.4;}
  .svc-crumb a{color:var(--link,var(--accent));}
  .svc-crumb a:hover{color:var(--link-hover,var(--accent-hover));}
  .svc-crumb-sep{color:var(--text-4);margin:0 5px;}
  /* último tramo = la página actual: texto, no link (no se linkea a sí misma) */
  .svc-crumb-cur{color:var(--text-3);}
  .svc-crumb+.h1{margin-top:var(--space-1);}
  /* El teléfono del centro queda antes del divisor del footer común. */
  .wizard-help{margin:var(--space-3) 0 0;font-size:14.5px;line-height:1.5;color:var(--text-2);max-width:min(620px,calc(100% - 180px));}
  @media(max-width:480px){.wizard-help{max-width:calc(100% - 64px);}}
  .wizard-help a{color:var(--link,var(--accent));}
  .wizard-help a:hover{color:var(--link-hover,var(--accent-hover));}
  .svc-sub{margin:var(--space-2) 0 var(--space-3);font-size:20px;font-weight:600;color:var(--text-2);letter-spacing:-.01em;}
  .svc-when{margin:12px 0 0;font-size:16px;color:var(--text-2);display:flex;align-items:center;gap:8px;}
  .svc-when svg{color:var(--text-3);flex-shrink:0;}
  .svc-when b{color:var(--text);font-weight:600;}
  .prep{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px 18px;margin:20px 0 0;max-width:560px;}
  /* La misma tarjeta cuando lleva la preparación entera y no sólo la entrada al
     asistente: necesita el ancho para que las secciones entren en columnas. */
  .prep--pagina{max-width:none;padding:20px;}
  .prep--pagina .ai-line{margin-top:18px;}
  .prep-h{font-size:17px;font-weight:700;color:var(--text);margin:0 0 10px;letter-spacing:-.01em;}
  .prep-key{font-size:16px;line-height:1.45;color:#7a5410;font-weight:600;background:#f7f0df;border:1px solid #ecdfbd;border-radius:10px;padding:10px 13px;margin:0 0 10px;}
  .prep-nota{margin:0 0 12px;font-size:15px;line-height:1.5;color:var(--text-2);max-width:640px;}
  /* La preparación se lee como la diría una persona: primero cuánta hay en
     realidad, después las listas. El resumen es la línea que evita que alguien
     lea cuatro secciones para enterarse de que no tenía que hacer nada. */
  .prep-resumen{margin:0 0 16px;font-size:18px;line-height:1.5;color:var(--text);max-width:640px;text-wrap:pretty;}
  /* Multicolumna, no grilla: las secciones tienen largos muy distintos y una
     grilla las alinearía por fila dejando huecos. `break-inside:avoid` mantiene
     cada sección entera en su columna. */
  .prep-cols{columns:300px;column-gap:36px;}
  .prep-sec{break-inside:avoid;margin:0 0 20px;}
  .prep-sec:last-child{margin-bottom:0;}
  .prep-sec-h{margin:0 0 8px;font-size:18px;font-weight:700;letter-spacing:-.01em;color:var(--text);}
  .prep-list{margin:0;padding:0;list-style:none;}
  .prep-list li{font-size:16px;line-height:1.5;color:var(--text);margin:0 0 7px;padding-left:17px;position:relative;}
  .prep-list li:last-child{margin-bottom:0;}
  .prep-list li:before{content:"";position:absolute;left:3px;top:9px;width:6px;height:6px;border-radius:50%;background:var(--accent);}
  /* "No hace falta" son cosas que NO hay que hacer: viñeta en raya y texto más
     callado, para que no se lean como una instrucción más. */
  .prep-sec--no li{color:var(--text-2);}
  .prep-sec--no li:before{top:12px;left:1px;width:10px;height:2px;border-radius:1px;background:var(--text-3);}
  .prep-sec--aviso li:before{background:var(--warn,#b07d18);}
  .prep-single{margin:0;font-size:16px;line-height:1.5;color:var(--text);}
  .prep-single--no{color:var(--text-2);}
  /* Entrada al asistente que LEE la orden (no es una guía: subís la orden y te
     dice la preparación de TUS estudios — el responsable del producto, 2026-07-12). Botón + una frase,
     no una tarjeta con párrafo (responsable del producto, 2026-07-25): la acción se ve, no se lee.
     El botón es el mismo objeto que el "Subí tu orden" de la barra —blanco,
     borde neutro, ícono de orden— porque es la misma acción; la frase de al
     lado es texto plano, sin link (accent-is-link: un solo link acá). */
  .ai-line{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:16px 0 0;}
  /* The section under the button already brings its own top margin: the
     table title's 26px on top of it left ~60px of nothing between «Subí tu
     orden» and «Estudios» (ui review 2026-11-15). */
  .ai-line+.sec>.lst-slot:first-child>.lst-h:first-child{margin-top:0;}
  .ai-btn{display:inline-flex;align-items:center;gap:8px;flex-shrink:0;padding:10px 16px;
    font-size:15px;font-weight:500;color:var(--text);text-decoration:none;background:var(--panel);
    border:1.5px solid var(--border-strong);border-radius:10px;
    transition:background .12s,border-color .12s,transform .1s;}
  .ai-btn:hover{background:var(--hover-bg-soft);border-color:var(--hover-bd-strong);}
  .ai-btn:active{transform:translateY(1px);}
  .ai-btn svg{flex-shrink:0;}
  /* Consejo de espera-vs-viaje en la página de un estudio: texto, no cartel
     (ui-rules: los datos no van en pastillas). 16px = piso de legibilidad del
     skill patient-flow-design — es justo la letra chica que el paciente tiene
     que leer para elegir bien. */
  .elegir-espera{margin:14px 0 0;font-size:16px;line-height:1.5;color:var(--text-2);max-width:620px;}
  a.stz-link{color:var(--link,var(--accent));}
  a.stz-link:hover{color:var(--link-hover,var(--accent-hover));}
  .h1{margin:26px 0 8px;font-size:34px;line-height:1.16;font-weight:680;letter-spacing:-.02em;text-wrap:balance;}
  .wrap>.h1:first-child{margin-top:6px;}
  /* A page that opens with a section holding its h1 (/news, the 404 page):
     the page padding, the section's margin and the h1's own 26px stacked to
     84px of empty band under the header, 32px on every other page
     (2026-11-15). The heading starts where `.wrap>.h1:first-child` does. */
  .wrap>.sec:first-child:has(>h1:first-child){margin-top:0;}
  .wrap>.sec:first-child>h1:first-child{margin-top:6px;}
  .lede{margin:0 0 6px;font-size:16px;line-height:1.55;color:var(--text-2);max-width:660px;text-wrap:pretty;}
  .subtabs{display:flex;gap:22px;margin:4px 0 0;}
  .subtabs a{display:inline-flex;align-items:center;min-height:44px;font-size:16px;color:var(--text-2);text-decoration:none;border-bottom:2px solid transparent;}
  .subtabs+.h1{margin-top:14px;}
  .subtabs a:hover{color:var(--hover-ink,var(--text));}
  .subtabs a[aria-current="page"]{color:var(--text);border-bottom-color:var(--text);}
  .meta{margin:10px 0 0;font-size:14px;color:var(--text-2);line-height:1.6;}
  .meta--service{display:flex;flex-direction:column;align-items:flex-start;gap:7px;margin-top:16px;font-size:18px;line-height:1.45;}
  .meta--service a{font-size:inherit;}
  .cartel--walkin .ct-h{font-size:18px;}
  .cartel--walkin p{font-size:17px;line-height:1.55;}
  .svc-lede{margin:4px 0 0;max-width:720px;font-size:19px;line-height:1.5;color:var(--text-2);text-wrap:pretty;}
  .svc-section{max-width:720px;margin-top:28px;}
  .svc-section-h{margin:0 0 10px;font-size:19px;line-height:1.3;font-weight:680;letter-spacing:-.01em;color:var(--text);}
  .svc-req{margin:0;padding-left:22px;display:flex;flex-direction:column;gap:7px;}
  .svc-req li{font-size:17px;line-height:1.5;color:var(--text);}
  /* Dónde se atiende y Requisitos arriba, la preparación entera abajo (responsable del producto,
     2026-07-29). Las tres en columnas iguales daban una tarjeta de preparación
     angosta y larguísima al lado de dos casi vacías; ahora las dos cortas
     comparten la fila de arriba y la preparación usa todo el ancho, que es lo
     que necesita para acomodar sus secciones en columnas. */
  .svc-major-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:18px;align-items:start;margin-top:28px;}
  .svc-major--prep{grid-column:1/-1;}
  .svc-major{min-width:0;margin:0;padding:20px;background:var(--panel);border:1px solid var(--border);border-radius:14px;}
  .svc-major .svc-section-h{margin-bottom:14px;}
  .svc-major .svc-req{gap:10px;}
  .svc-major .prep-name{margin:-5px 0 13px;font-size:14px;line-height:1.4;color:var(--text-2);}
  .svc-major .prep-key{margin-bottom:12px;}
  .svc-major .svc-contact{gap:10px;}
  .svc-major .sem-t{margin:24px 0 10px;font-size:16px;}
  .svc-major .sem-grid{display:flex;flex-direction:column;border:0;border-radius:0;background:transparent;overflow:visible;}
  .svc-major .sem-b{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:5px 0;border:0;}
  .svc-major .sem-hs{margin:0;text-align:right;}
  .svc-major .svc-hours-note{margin-top:12px;font-size:14px;}
  .svc-contact{display:flex;flex-direction:column;align-items:flex-start;gap:8px;font-size:17px;line-height:1.45;}
  .svc-contact a{display:inline-flex;align-items:center;gap:8px;}
  .svc-contact a svg{width:17px;height:17px;flex-shrink:0;opacity:.78;}
  .svc-contact-place{font-weight:650;}
  .svc-hours{max-width:720px;display:flow-root;}
  .svc-hours-note{margin:10px 0 0;max-width:720px;font-size:16px;line-height:1.5;color:var(--text-2);}
  @media(max-width:860px){.svc-major-grid{grid-template-columns:1fr;max-width:720px;}.svc-major{padding:18px;}}
  .meta b{color:var(--text);font-weight:600;}
  /* Address, phone and email are key facts, not the descriptive floor: full ink
     (the 50% --text-2 tint read 3.9:1 on --bg). Links inherit it (--link:currentColor). */
  .c-contact{margin:10px 0 0;font-size:15px;line-height:1.5;color:var(--text);}
  details.more{margin:18px 0 0;border:1px solid var(--border);border-radius:12px;background:var(--panel);overflow:hidden;}
  details.more>summary{cursor:pointer;list-style:none;min-height:44px;box-sizing:border-box;padding:10px 16px;font-size:16px;font-weight:600;color:var(--text);
    display:flex;align-items:center;gap:9px;}
  details.more>summary::-webkit-details-marker{display:none;}
  /* Icons8 chevron (expand-arrow), never a typed «+»/«–» (review D39). */
  details.more>summary::before{content:"";flex-shrink:0;width:18px;height:18px;background:currentColor;
    -webkit-mask:url(/static/icons/expand-arrow-sf-regular.svg) center/contain no-repeat;mask:url(/static/icons/expand-arrow-sf-regular.svg) center/contain no-repeat;
    transform:rotate(-90deg);transition:transform .15s;}
  details.more[open]>summary::before{transform:none;}
  details.more[open]>summary{border-bottom:1px solid var(--border);}
  .more-body{padding:14px 16px 16px;display:flex;flex-direction:column;gap:9px;font-size:14px;color:var(--text-2);line-height:1.55;}
  /* Gris + negrita al lado del valor en negro sin negrita ("Consultorio" en
     .ci-k pegado a "Servicio de imágenes" sin clase) es exactamente la
     inversión de jerarquía que el responsable del producto prohibió (PS16): la negrita gana sobre
     el color, así que la etiqueta "secundaria" terminaba leyéndose más
     fuerte que el dato. Peso regular — la misma regla que ya usa la
     variante .mp-lugar .ci-k más abajo, no un segundo tratamiento del mismo
     rótulo. */
  .ci-k{display:inline-block;min-width:78px;color:var(--text-3);font-size:13px;font-weight:400;}
  .st-badge{display:inline-flex;align-items:center;gap:5px;font:600 13px/1 var(--font-mono);letter-spacing:.02em;
    color:var(--text-2);background:var(--chip-bg);border:1px solid var(--border);border-radius:20px;padding:5px 9px;white-space:nowrap;}
  .st-badge.orden{color:var(--warn);background:#f7f0df;border-color:#ecdfbd;}
  .st-badge.solicitud{color:var(--accent);background:var(--accent-soft);border-color:color-mix(in oklch,var(--accent) 24%,transparent);}
  .st-badge.walkin{color:var(--done);background:var(--state-done-bg);border-color:var(--state-done-bd);}
  .st-badge.edad{color:var(--text-2);}
  .cartel{background:var(--accent-soft);border:1px solid color-mix(in oklch,var(--accent) 24%,transparent);border-radius:13px;
    padding:16px 18px;margin:22px 0;display:flex;gap:12px;align-items:flex-start;}
  .cartel svg{flex-shrink:0;margin-top:2px;color:var(--accent);}
  .cartel .ct-h{font-size:15px;font-weight:680;color:var(--accent-hover);margin:0 0 4px;letter-spacing:-.01em;}
  .cartel p{margin:0;font-size:15px;line-height:1.55;color:var(--text);}
  .cartel .ct-next{margin-top:7px;font-weight:680;color:var(--text);}
  .cartel .ct-next--open{color:#0f8a4d;}
  /* tema humanrounds: nada de verde propio, el mismo tono que --done/--state-done. */
  :root[data-theme="humanrounds"] .cartel .ct-next--open{color:var(--done);}
  .note{background:var(--panel);border:1px solid var(--border);border-radius:11px;padding:13px 15px;margin:14px 0;font-size:13.5px;line-height:1.55;color:var(--text-2);}
  .note b{color:var(--text);}
  .sec{margin:var(--space-4) 0 0;}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:13px;overflow:hidden;}
  /* fila de servicio: estado fuerte a la IZQUIERDA (mismo layout que la home) */
  .srow{display:grid;grid-template-columns:118px 1fr;align-items:center;gap:14px;padding:12px 16px;
    text-decoration:none;color:var(--text);border-top:1px solid var(--border);transition:background .1s;}
  .srow:first-child{border-top:0;}
  .srow:hover{background:var(--hover-bg);}
  .s-status{display:flex;flex-direction:column;gap:2px;min-width:0;}
  .ss-top{display:flex;align-items:center;gap:6px;font:650 13px/1.2 var(--font);letter-spacing:-.01em;}
  .ss-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;background:currentColor;}
  .ss-sub{font-size:13px;line-height:1.25;color:var(--text-4);}
  .s-status--green .ss-top{color:var(--done);}
  .s-status--amber .ss-top{color:var(--warn);}
  .s-status--accent .ss-top{color:var(--accent);}
  .s-status--gray .ss-top{color:var(--text-4);}
  .s-status--gray .ss-dot{background:var(--border-strong);}
  .srow .s-main{min-width:0;}
  .srow .s-name{font-weight:600;font-size:14.5px;letter-spacing:-.01em;}
  .srow:hover .s-name{color:var(--link,var(--accent));}
  .s-age{display:inline-block;margin-left:7px;font:600 13px/1 var(--font-mono);color:var(--text-2);
    background:var(--chip-bg);border:1px solid var(--border);border-radius:20px;padding:3px 7px;vertical-align:middle;white-space:nowrap;}
  .srow .s-sub{font-size:13px;color:var(--text-3);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  /* médicos: avatar de iniciales + fila */
  .avx{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;border-radius:50%;
    font:600 13.5px var(--font);letter-spacing:.01em;flex-shrink:0;}
  .avx--0{background:var(--av0-bg,#eaf4ec);color:var(--av0-fg,#0e8b50);} .avx--1{background:var(--av1-bg,#e9f0fb);color:var(--av1-fg,#2b5cad);}
  .avx--2{background:var(--av2-bg,#e0f2f0);color:var(--av2-fg,#0e9488);} .avx--3{background:var(--av3-bg,#f7efdd);color:var(--av3-fg,#b67a1e);}
  .avx--4{background:var(--av4-bg,#eaf4ec);color:var(--av4-fg,#0b6e33);} .avx--5{background:var(--av5-bg,#e9f0fb);color:var(--av5-fg,#3a6bbf);}
  .avx-ph{background-color:var(--av-ph,#eef0ee);background-repeat:no-repeat;}
  .drow{display:flex;align-items:center;gap:13px;padding:11px 16px;text-decoration:none;color:var(--text);
    border-top:1px solid var(--border);transition:background .1s;}
  .drow:first-child{border-top:0;}
  .drow:hover{background:var(--hover-bg);}
  .drow .d-name{font-weight:600;font-size:14.5px;letter-spacing:-.01em;}
  .drow:hover .d-name{color:var(--link,var(--accent));}
  .drow .d-sub{font-size:13px;color:var(--text-3);margin-top:1px;}
  .dis{margin:12px 2px 0;font-size:13px;color:var(--text-4);line-height:1.5;}
  /* header de la página de centro: en desktop, dos columnas (texto | foto
     contenida); en mobile, una sola columna en orden de documento (Brila-style,
     no más tira full-width). Sin foto → sin grid, layout plano de siempre. */
  .centro-head{display:block;}
  .ch-photo{margin:16px 0 4px;}
  @media(min-width:701px){
    .centro-head{display:grid;grid-template-columns:1fr 40%;column-gap:40px;
      grid-template-areas:"top photo" "bottom photo";}
    .ch-top{grid-area:top;}
    .ch-photo{grid-area:photo;margin:0;}
    .ch-bottom{grid-area:bottom;}
  }
  /* foto hero de la página de centro: bloque contenido, no tira full-width */
  .hero-photo{margin:0;max-width:100%;}
  .hero-photo a{display:block;cursor:zoom-in;}
  .hero-photo img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:14px;}
  @media(min-width:701px){.hero-photo img{aspect-ratio:4/3;}}
  /* datos verificados: lista de hechos en texto plano, legible (≥16px) */
  .facts{margin:14px 0 0;padding:0;list-style:none;display:flex;flex-direction:column;gap:7px;max-width:660px;}
  .facts li{position:relative;padding-left:18px;font-size:16px;line-height:1.5;color:var(--text);}
  .facts li:before{content:"";position:absolute;left:3px;top:9px;width:6px;height:6px;border-radius:50%;background:var(--accent);}
  /* pliegues apilados (Fotos / vecinos): juntos leen como un acordeón */
  details.more+details.more{margin-top:8px;}
  .gal-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;}
  .gal-thumb{display:block;border-radius:10px;overflow:hidden;aspect-ratio:4/3;background:var(--bg-2);cursor:zoom-in;}
  .gal-thumb img{width:100%;height:100%;object-fit:cover;display:block;}
  @media(min-width:701px){.gal-grid{grid-template-columns:repeat(4,1fr);}}
  /* citas y consejos de vecinos (adentro de "Más información"): lista
     tipográfica plana separada por hairlines — SIN card ni barra de acento
     (ui-rules) */
  .quotes{list-style:none;margin:0;padding:0;}
  .quotes li{padding:10px 0;border-top:1px solid var(--border-soft);font-size:15px;line-height:1.55;color:var(--text);}
  .quotes li:first-child{padding-top:0;border-top:0;}
  .consejos{margin:0;padding-left:18px;}
  .consejos li{font-size:15px;line-height:1.6;color:var(--text);margin-bottom:6px;}
  .consejos li:last-child{margin-bottom:0;}
  /* formulario de solicitud */
  .f-form{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:20px 20px 22px;margin-top:8px;max-width:520px;}
  .f-field{display:flex;flex-direction:column;gap:6px;margin-bottom:15px;}
  .f-field label{font-size:13px;font-weight:600;color:var(--text-2);}
  .f-field input,.f-field select{font-size:14.5px;padding:11px 13px;border:1px solid var(--border);border-radius:10px;background:var(--bg);color:var(--text);font-family:inherit;}
  .f-field input:focus,.f-field select:focus{outline:none;background:var(--panel);border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring);}
  .f-hint{font-size:13px;color:var(--text-3);}
  .f-file{border:1.5px dashed var(--border-strong);border-radius:11px;padding:14px;background:var(--bg);}
  .f-file input{font-size:13px;}
  .stz-cta{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:0;cursor:pointer;
    background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);font:600 15px var(--font);letter-spacing:-.01em;
    padding:13px 22px;border-radius:var(--btn-radius,11px);text-decoration:none;transition:filter .12s,transform .1s,background .12s;}
  .stz-cta:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}
  .stz-cta:active{transform:translateY(1px);}
  .f-msg{margin-top:12px;font-size:13.5px;line-height:1.5;display:none;}
  .f-ok{background:var(--state-done-bg);border:1px solid var(--state-done-bd);border-radius:12px;padding:16px 18px;color:#2f6a34;}
  .f-ok h3{margin:0 0 5px;font-size:16px;color:#2f6a34;}
  /* tema sana: el texto de éxito no puede quedar verde sobre un fondo bronce. */
  :root[data-theme="humanrounds"] .f-ok,:root[data-theme="humanrounds"] .f-ok h3{color:var(--done);}
  .gate{background:#f7f0df;border:1px solid #ecdfbd;border-radius:12px;padding:15px 17px;margin-top:8px;font-size:14.5px;line-height:1.55;color:#7a5a12;}
  .gate b{color:#5f4708;}
  .campaign-page{max-width:760px;}
  .zoo-photo{max-width:480px;margin-top:16px;}
  /* Informational pages (telemedicine, blood, zoonosis): ONE body size for
     lede, paragraphs and lists; a notice is the theme's text on its panel,
     never brown on yellow (demo review 2026-11-15, D10/D28). */
  .campaign-page .lede{font-size:17px;line-height:1.55;color:var(--text);max-width:720px;}
  .campaign-page .facts li,.campaign-page .campaign-steps li{font-size:17px;}
  .info-alert{max-width:720px;margin:18px 0 0;padding:14px 16px;border:1px solid var(--border-strong);border-radius:12px;background:var(--panel);color:var(--text);font-size:17px;line-height:1.55;}
  .info-alert b{color:var(--text);}
  .campaign-section{margin-top:30px;max-width:720px;}
  .campaign-section h2{margin:0 0 10px;font-size:22px;line-height:1.25;letter-spacing:-.015em;}
  .campaign-section p{margin:0 0 10px;font-size:17px;line-height:1.55;color:var(--text);text-wrap:pretty;}
  .campaign-steps{counter-reset:steps;margin:12px 0 0;padding:0;list-style:none;display:flex;flex-direction:column;gap:12px;}
  .campaign-steps li{counter-increment:steps;position:relative;padding-left:42px;font-size:16px;line-height:1.5;color:var(--text);}
  .campaign-steps li:before{content:counter(steps);position:absolute;left:0;top:0;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:var(--accent-soft);color:var(--accent-hover);font-weight:700;}
  .campaign-action{margin-top:22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;}
  .campaign-action-note{margin:0;font-size:15px;line-height:1.45;color:var(--text-2);}
  .wa-action{display:inline-flex;align-items:center;justify-content:center;gap:9px;min-height:46px;padding:11px 17px;border-radius:11px;background:#137c45;color:#fff;text-decoration:none;font-weight:680;}
  .wa-action:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}.wa-action img{width:21px;height:21px;filter:brightness(0) invert(1);}
  .wa-action--inline{min-height:0;padding:0;border-radius:0;background:none;color:var(--accent);font-weight:inherit;vertical-align:middle;}
  .wa-action--inline img{width:18px;height:18px;filter:none;}
  .service-announcement{max-width:720px;margin:18px 0 4px;padding:15px 17px;border:1px solid #d9b35a;border-radius:12px;background:#fff7df;color:#503b08;}
  .service-announcement h2{margin:0 0 4px;font-size:17px;line-height:1.3;color:#3e2e08;}
  .service-announcement p{margin:0;font-size:15.5px;line-height:1.5;color:#503b08;}
  .service-announcement a{color:var(--link,var(--accent-hover));}
  /* Phone: buttons keep their content width (not a full-width bar), at
     least 44px tall (demo review 2026-11-15, D28). */
  .campaign-action .stz-cta{min-height:46px;box-sizing:border-box;}
  @media(max-width:560px){.campaign-action{align-items:flex-start}.campaign-action-note{width:100%;}}
  footer.site{border-top:1px solid var(--border-strong);margin-top:52px;padding-top:24px;display:flex;flex-direction:column;gap:12px;}
  footer.site .fmision{margin:0;font-size:13.5px;color:var(--text-3);line-height:1.55;}
  footer.site .flinks{display:flex;flex-wrap:wrap;gap:8px 24px;}
  footer.site a{color:var(--link,var(--accent));font-size:14px;}
  footer.site a:hover{color:var(--link-hover,var(--accent-hover));}

  /* /news — historia de actualizaciones, columna de lectura */
  .nv-page{max-width:680px;}
  .nv-page h1{font-size:30px;margin:26px 0 6px;}
  /* Texto de las novedades: oscuro y de 17px (lectores mayores); las viñetas
     cuelgan fuera de la columna, así fecha, párrafo y lista comparten un borde. */
  .nv h2{font-size:18px;margin:28px 0 8px;}
  .nv ul{margin:0;padding-left:0;display:flex;flex-direction:column;gap:10px;}
  /* En el teléfono el margen de la página es angosto: la viñeta colgada quedaba
     pegada al borde de la pantalla, así que ahí la lista entra en la columna. */
  @media (max-width:600px){.nv ul{padding-left:1.15em;}}
  .nv li,.nv p{font-size:17px;line-height:1.55;color:var(--text);text-wrap:pretty;}
  .nv p{margin:0;}
  .nv li b,.nv p b{font-weight:600;}
  /* Meses anteriores plegados: la página corta se lee y se captura entera. */
  .nv-mes{border-top:1px solid var(--border);margin-top:22px;}
  .nv-mes:last-child{border-bottom:1px solid var(--border);}
  .nv-mes+.nv-mes{margin-top:0;}
  .nv-mes>summary{display:flex;align-items:center;gap:10px;min-height:52px;cursor:pointer;list-style:none;font-size:18px;font-weight:600;color:var(--text);}
  .nv-mes>summary::-webkit-details-marker{display:none;}
  .nv-mes>summary::before{content:"+";width:14px;text-align:center;font-weight:600;}
  .nv-mes[open]>summary::before{content:"\\2013";}
  .nv-mes[open]{padding-bottom:18px;}
  .nv-mes .nv:first-child h2{margin-top:6px;}
  /* ---- página propia del médico (/doctor/<id>) ---- */
  .mp-hero{display:flex;align-items:center;gap:20px;margin:20px 0 6px;}
  .mp-av{flex-shrink:0;width:112px;height:112px;border-radius:50%;background:var(--bg-2) center/cover no-repeat;
    border:1px solid var(--border);box-shadow:0 0 0 3px var(--panel),0 8px 22px -14px rgba(40,30,20,.28);
    display:inline-flex;align-items:center;justify-content:center;font:650 40px/1 var(--font);letter-spacing:-.02em;
    padding:0;appearance:none;-webkit-appearance:none;font-family:inherit;}
  .mp-av--ph{background-color:var(--av-ph,#eef0ee);background-repeat:no-repeat;cursor:zoom-in;}
  .mp-av-img{display:block;width:100%;height:100%;border-radius:50%;object-fit:cover;}
  .mp-av--0{background:var(--av0-bg,#eaf4ec);color:var(--av0-fg,#0e8b50);} .mp-av--1{background:var(--av1-bg,#e9f0fb);color:var(--av1-fg,#2b5cad);}
  .mp-av--2{background:var(--av2-bg,#e0f2f0);color:var(--av2-fg,#0e9488);} .mp-av--3{background:var(--av3-bg,#f7efdd);color:var(--av3-fg,#b67a1e);}
  .mp-av--4{background:var(--av4-bg,#eaf4ec);color:var(--av4-fg,#0b6e33);} .mp-av--5{background:var(--av5-bg,#e9f0fb);color:var(--av5-fg,#3a6bbf);}
  .mp-hgroup{min-width:0;}
  .mp-h1{margin:0;font-size:33px;line-height:1.14;font-weight:700;letter-spacing:-.02em;text-wrap:balance;}
  .mp-demo-tag{display:inline-block;vertical-align:middle;margin-left:6px;padding:3px 9px;border-radius:6px;
    background:var(--recessed,#eef0ee);color:var(--text-3);font-size:13px;font-weight:600;letter-spacing:.04em;
    text-transform:uppercase;}
  .mp-demo-note{margin:10px 2px 0;font-size:13.5px;color:var(--text-3);line-height:1.5;}
  .mp-sub{margin:8px 0 0;font-size:16px;color:var(--text-2);line-height:1.5;}
  .mp-sub a{color:var(--link,var(--accent));}
  .mp-sub a:hover{color:var(--link-hover,var(--accent-hover));}
  /* Enlace de texto a la página del servicio del médico: referencia, no acción
     (la única acción primaria de la pantalla es la banda de turno). */
  .mp-service{margin:30px 0 0;font-size:16px;line-height:1.5;color:var(--text-2);}
  .mp-service a{display:inline-flex;align-items:center;min-height:44px;}
  /* banda de turno + acción — a todo el ancho, bajo el encabezado */
  .mp-band{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:var(--accent-soft);
    border:1px solid color-mix(in oklch,var(--accent) 22%,transparent);border-radius:14px;padding:16px 18px;margin:22px 0 0;}
  .mp-band--off{background:var(--bg-2);border-color:var(--border);}
  .mp-band-ico{flex-shrink:0;width:46px;height:46px;border-radius:12px;background:var(--panel);color:var(--accent);
    display:flex;align-items:center;justify-content:center;border:1px solid color-mix(in oklch,var(--accent) 22%,transparent);}
  .mp-band--off .mp-band-ico{color:var(--text-3);border-color:var(--border-strong);}
  /* The icon and both explanatory lines are one stable row. Without a flex
     share, the text could wrap below the icon at phone widths. */
  .mp-band-txt{min-width:0;flex:1 1 0;}
  /* Sentence case, full size (hr-patient review #22, 15.11): the caps label
     read as a shout above the one action. */
  .mp-band-k{font-size:16px;font-weight:600;color:var(--accent-hover);}
  .mp-band--off .mp-band-k{color:var(--text-3);}
  .mp-band-v{margin-top:4px;font-size:19px;font-weight:680;letter-spacing:-.01em;color:var(--text);}
  .mp-band-sub{margin-top:5px;font-size:13.5px;color:var(--text-3);line-height:1.45;}
  /* walk-in abierto ahora: mismo verde que la pastilla "ya" de los listados */
  .mp-band--open{background:#e9f7ef;border-color:#a6dcbd;}
  .mp-band--open .mp-band-ico{color:#0f8a4d;border-color:#a6dcbd;}
  .mp-band--open .mp-band-k{color:#0f8a4d;}
  /* Hay turno = BUENA noticia, no una alerta. El pote rojo/accent hacía leer
     "Turno disponible" como un error (the product owner, 2026-07-12: "it's red, like it's an
     error"). Verde de disponibilidad, el mismo de --open y de la pastilla "ya"
     de los listados. El rojo/accent queda para lo que ES accionable: el botón. */
  .mp-band--turno{background:#e9f7ef;border-color:#a6dcbd;}
  .mp-band--turno .mp-band-ico{color:#0f8a4d;border-color:#a6dcbd;}
  .mp-band--turno .mp-band-k{color:#0f8a4d;}
  /* tema humanrounds: la banda NO se pinta (responsable del producto, 2026-09-14: "look at the
     colors… look how Linear deals with it"). Ni el verde de disponibilidad ni
     el wash del acento: papel con un filete, rótulo en gris, y la pastilla
     negra como único elemento fuerte de la banda. El verde sigue vivo en
     el otro tema (las tres reglas de arriba) y en los ESTADOS de este tema
     (--done), pero no como decoración de un bloque entero. */
  :root[data-theme="humanrounds"] .mp-band,
  :root[data-theme="humanrounds"] .mp-band--open,
  :root[data-theme="humanrounds"] .mp-band--turno{background:var(--panel);border-color:var(--border);}
  :root[data-theme="humanrounds"] .mp-band .mp-band-ico,
  :root[data-theme="humanrounds"] .mp-band--open .mp-band-ico,
  :root[data-theme="humanrounds"] .mp-band--turno .mp-band-ico{color:var(--text-3);border-color:var(--border-strong);}
  :root[data-theme="humanrounds"] .mp-band .mp-band-k,
  :root[data-theme="humanrounds"] .mp-band--open .mp-band-k,
  :root[data-theme="humanrounds"] .mp-band--turno .mp-band-k{color:var(--text);}
  .mp-band-actions{margin-left:auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
  /* La FORMA sigue al tema (pastilla en humanrounds), el COLOR no: las
     variantes de abajo pintan por cercanía del turno y eso es una regla de
     producto, no de marca. */
  .mp-cta{display:inline-flex;align-items:center;justify-content:center;gap:9px;min-height:56px;
    background:var(--accent);color:#fff;text-decoration:none;font:700 17px var(--font);letter-spacing:-.01em;
    padding:0 26px;border-radius:var(--btn-radius,12px);transition:filter .12s,transform .1s;}
  .mp-cta:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}
  .mp-cta:active{transform:translateY(1px);}
  /* Segunda opción, deliberadamente más callada que la primera: el turno más
     próximo es el botón lleno; "elegir otros horarios" es el camino más largo
     (the product owner: "it must be clear the second is a later/worse option"). */
  .mp-cta-2{display:inline-flex;align-items:center;justify-content:center;min-height:56px;
    background:var(--btn2-bg,var(--panel));color:var(--btn2-fg,var(--text-2));text-decoration:none;font:600 15.5px var(--font);
    padding:0 20px;border:1px solid var(--btn2-bd,var(--border-strong));border-radius:var(--btn-radius,12px);transition:background .12s,color .12s;}
  .mp-cta-2:hover{background:var(--btn2-bg-hover,var(--hover-bg));color:var(--hover-ink);}
  /* Segunda acción de la banda (otros horarios / contar el caso): un enlace
     común (el estilo de enlace lo pone el tema), no un segundo botón al lado
     del primero — igual que el enlace de las filas de los listados. */
  .mp-more{font-size:16px;font-weight:400;white-space:nowrap;}
  /* variantes del CTA consistentes con los botones de los listados (responsable del producto,
     2026-07-14): tinta = sin fecha conocida; w0..w10 = el mismo color por
     lejanía que la celda del listado. */
  .mp-cta--ink{background:var(--btn-bg,var(--text));}
  .mp-cta--ya{background:var(--btn-bg,var(--accent));}
  /* Todos los turnos son verdes (responsable del producto, 2026-10-19): la lejanía sólo vira el
     verde del tema hacia un azul, en 11 pasos de 3 días (ver _cta_bucket). */
  .mp-cta--w0{background:var(--accent);}
  .mp-cta--w1{background:color-mix(in srgb,#2c5f9e 10%,var(--accent));}
  .mp-cta--w2{background:color-mix(in srgb,#2c5f9e 20%,var(--accent));}
  .mp-cta--w3{background:color-mix(in srgb,#2c5f9e 30%,var(--accent));}
  .mp-cta--w4{background:color-mix(in srgb,#2c5f9e 40%,var(--accent));}
  .mp-cta--w5{background:color-mix(in srgb,#2c5f9e 50%,var(--accent));}
  .mp-cta--w6{background:color-mix(in srgb,#2c5f9e 60%,var(--accent));}
  .mp-cta--w7{background:color-mix(in srgb,#2c5f9e 70%,var(--accent));}
  .mp-cta--w8{background:color-mix(in srgb,#2c5f9e 80%,var(--accent));}
  .mp-cta--w9{background:color-mix(in srgb,#2c5f9e 90%,var(--accent));}
  .mp-cta--w10{background:#2c5f9e;}
  .mp-band-note{font-size:16px;color:var(--text-2);}
  /* etiqueta "Con orden" junto al rótulo — es un DATO, no una acción: pastilla
     chica en el texto, no un bloque del tamaño de un botón al lado del CTA. */
  .mp-band-badge{display:inline-flex;align-items:center;margin-left:8px;padding:1px 8px;border-radius:7px;
    background:#f4ecd8;color:#7a5a12;border:1px solid #e6d6a8;font:700 13px var(--font);
    letter-spacing:.02em;text-transform:none;white-space:nowrap;vertical-align:middle;}
  /* "Cuándo atiende" (design A, 2026-10-06): one card per center, the week
     as merged blocks — consecutive days with the same hours share one block
     as wide as the days it covers; working blocks carry the done tint. */
  .mp-agenda{margin-top:30px;}
  .mp-agenda-h{margin:0 0 12px;font-size:17px;font-weight:650;letter-spacing:-.01em;color:var(--text);}
  .mp-ag-groups{display:grid;gap:14px;}
  .mp-ag-group{background:var(--panel);border:1px solid var(--border);border-radius:13px;overflow:hidden;}
  .mp-ag-center{margin:0;padding:14px 18px;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;
    font-size:16.5px;font-weight:650;line-height:1.35;}
  .mp-ag-center a,.mp-ag-office a{color:var(--link,var(--accent));}
  .mp-ag-center a:hover,.mp-ag-office a:hover{color:var(--link-hover,var(--accent-hover));}
  .mp-ag-office{font-size:14px;font-weight:500;color:var(--text);}
  .mp-ag-office-k{color:var(--text-2);font-weight:400;}
  .mp-ag-sub{padding:10px 18px 8px;border-top:1px solid var(--border);}
  .mp-ag-week{display:grid;border-top:1px solid var(--border);}
  .mp-ag-b{padding:13px 16px 15px;border-left:1px solid var(--border);min-width:0;}
  .mp-ag-b:first-child{border-left:0;}
  .mp-ag-b--on{background:var(--state-done-bg);}
  .mp-ag-d{display:flex;flex-wrap:wrap;align-items:center;gap:4px 7px;font-size:14px;font-weight:500;color:var(--text-2);}
  .mp-ag-b--on .mp-ag-d{color:var(--done);}
  .mp-ag-h{margin-top:7px;font-size:16px;color:var(--text-3);font-variant-numeric:tabular-nums;}
  .mp-ag-b--on .mp-ag-h{color:var(--text);font-weight:500;}
  .mp-ag-hoy{padding:1px 6px;border-radius:4px;background:var(--text);color:var(--panel);
    font-size:13px;font-weight:700;white-space:nowrap;}
  @media(max-width:640px){
    .mp-ag-week{display:flex;flex-direction:column;}
    .mp-ag-b{display:flex;align-items:center;justify-content:space-between;gap:12px;
      border-left:0;border-top:1px solid var(--border);padding:11px 16px;}
    .mp-ag-b:first-child{border-top:0;}
    .mp-ag-h{margin:0;text-align:right;}
  }
  @media(max-width:600px){
    .mp-band-actions .mp-cta{padding-inline:0;}
  }
  /* dónde atiende: centro + dirección + teléfono + horario, juntos y a la vista */
  .mp-lugar{margin-top:30px;display:flex;flex-direction:column;gap:4px;font-size:16px;color:var(--text-2);line-height:1.35;}
  .mp-lugar-h{margin:0 0 4px;font-size:17px;font-weight:650;letter-spacing:-.01em;color:var(--text);}
  .mp-lugar-h a{color:var(--link,var(--accent));}
  .mp-lugar-h a:hover{color:var(--link-hover,var(--accent-hover));}
  .mp-lugar .ci{font-size:16px;font-weight:400;color:var(--text-2);}
  .mp-lugar .ci-k{min-width:auto;margin-right:12px;font:400 16px var(--font);color:var(--text-2);}
  a.ci-ico{display:inline-flex;align-items:center;gap:6px;}
  a.ci-ico svg{flex-shrink:0;opacity:.75;}
  /* sd-ref: párrafo de UN solo link, reusado por las pantallas de bloqueo/
     redirect de la entrevista previa (appointment_offer.py, interview_patient.py,
     entrevista.js) — "Mis solicitudes" / "Ver mi solicitud". */
  .sd-ref{margin-top:38px;font-size:15px;color:var(--text-2);line-height:1.6;}
  .sd-ref a{color:var(--link,var(--accent));}
  .sd-ref a:hover{color:var(--link-hover,var(--accent-hover));}
  @media(max-width:600px){
    .wrap{padding:18px 16px 60px;}
    .wrap--focus{padding-top:10px;}
    .h1{font-size:28px;}
    .wrap>.h1:first-child,.wrap>.sec:first-child>h1:first-child{margin-top:2px;}
    /* `.h1.h1--focus`, the same weight as the 38px desktop rule: with the bare
       class the desktop size won on the phone too (2026-11-10). */
    .h1.h1--focus{font-size:28px;line-height:1.14;margin-bottom:6px;}
    .svc-crumb{font-size:13.5px;margin-top:8px;}
    .svc-sub{font-size:18px;}
    .srow{grid-template-columns:96px 1fr;gap:11px;padding:12px 14px;}
    .srow .s-sub{white-space:normal;}
    .mp-hero{gap:15px;}
    .mp-av{width:88px;height:88px;font-size:32px;}
    .mp-h1{font-size:26px;}
    /* mobile: UN botón a lo ancho (el primer turno) y el enlace de otros
       horarios debajo — dos botones apilados se leían como dos acciones del
       mismo peso (contraparte técnica, 19/10: «feos botones en dos»). */
    .mp-band-actions{margin-left:0;width:100%;flex-direction:column;align-items:stretch;gap:var(--space-1);}
    .mp-band-actions .mp-cta{flex:1;width:100%;}
    .mp-band-actions .mp-more{align-self:center;padding:10px 4px;}
    .mp-cta-2{min-height:50px;}
  }
"""

def _site_footer():
    """Render the one shared Jinja footer with legacy shell whitespace.

    The dynamic shell historically interpolated a triple-quoted fragment,
    whose leading and trailing newlines are observable in golden HTML.  The
    shared partial deliberately owns markup only; this wrapper owns that old
    shell boundary so public Jinja pages and dynamic pages have one source
    without changing their bytes.
    """
    return "\n" + jinja_env.render("_site_footer.html")


def _chat_bootstrap():
    """Render the one shared Jinja bootstrap for the native chat widget."""
    return jinja_env.render("_chat_bootstrap.html")


def _booking_launcher_anchor():
    """The empty slot the chat launcher moves into on a phone; without a chat
    (no AI key) it would only leave a 72px hole under the form."""
    from app.ai import ai_health
    if not ai_health.configured():
        return ""
    return '<div id="stzc-booking-launcher-anchor" class="stzc-booking-launcher-anchor"></div>'


# Only on a page that draws the block (in <head>, via head_extra): _PAGE_CSS
# ships on every page. Typography of the home page's «Explorar el sitio»
# navigation (.browse h2 / .browse a), not a 13.5px label (the product owner's review of
# PR 55, 2026-10-15: readable text).
_INFO_RELATED_CSS = (".info-related{margin:32px 0 0;}"
                     ".info-related h2{margin:0 0 12px;font-size:18px;line-height:1.3;font-weight:650;letter-spacing:-.01em;}"
                     ".info-related .info-list{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:0 24px;}"
                     ".info-related .info-list a{display:inline-flex;align-items:center;min-height:44px;font-size:16px;line-height:1.4;color:var(--link,var(--accent));}"
                     ".info-related .info-list a:hover{color:var(--link-hover,var(--accent-hover));}")


def _info_related_html(path):
    """«Guías relacionadas»: the pack's informational pages this page sends its
    reader to next (sitio.json "info_related", labels from "info_links") —
    e.g. a vacunatorio's service page to /vacunacion. Empty when the pack
    names none for this path."""
    rows = site_identity.info_related(path)
    if not rows:
        return ""
    title = site_base._esc(i18n.t_or("page.info_related", "Guías relacionadas", i18n.active()))
    links = "".join(f'<li><a href="{site_base._esc(r["href"])}">{site_base._esc(r["label"])}</a></li>' for r in rows)
    return (f'\n<nav class="info-related" aria-label="{title}">'
            f'<h2>{title}</h2><ul class="info-list">{links}</ul></nav>\n')


def _shell(title, section, body, desc="", chrome="full", contacto="",
           canonical=None, index=True, head_extra="", body_class=""):
    # chrome="focus": stepped wizard (wizard-design, 2026-07-10) — each step
    # carries ONLY the process context + what that step needs in the body.
    # Header and footer are the same as the site's; `contacto` sits right
    # before the shared footer's divider.
    #
    # chrome="chat" (2026-08-01): the pre-consult interview (modules/intake/)
    # is the only user of this today. It ships the site's OWN header and
    # footer — on a computer the conversation is a page of this site, in a
    # normal-width column, not a browser-wide app screen (the product owner, 2026-08-01:
    # "don't make the chat so wide, open it on the site — we already have all
    # HTML and design for it"). On a phone it goes back to being the whole
    # screen, and that is decided by WIDTH in entrevista.css, not here: one
    # markup, one design, two widths.
    # What never ships on this page is the floating booking assistant
    # (static/platform/chat-widget.mjs via templates/_chat_bootstrap.html) —
    # two chat surfaces stacked on one screen would confuse
    # a patient. `body_class` lets the caller pin `<body>` to 100dvh with no
    # page-level scroll on the phone (the messages pane is the only scroller).
    # The language is the request's, resolved once by routing.dispatch() —
    # `_shell()` never sees a handler (~20 call sites across five modules
    # build pages without one). Before 2026-08-04 every page built here was
    # hardcoded `<html lang="es">` with the demo bar's switcher turned off,
    # so a visitor who switched to English on the home page lost both the
    # language and the switcher itself the moment they opened a service, a
    # center or the wizard (the product owner: "the demo header isn't consistent across
    # the site — sometimes it has the language switcher, sometimes it
    # doesn't"). One resolution, every page.
    #
    # The page column is a <main> landmark (F02/F03, QA 2026-09-10): the home
    # template already wraps its column in <main class="wrap"> — every page
    # built here ships the same landmark, footer included, exactly like home.
    # No caller passes a body that already contains <main> (checked 2026-09-10),
    # so this can't duplicate the landmark anywhere.
    lang = i18n.active()
    # Tier 4 slice 6 (static_fingerprint.py): computed once, reused across
    # the three chrome variants below — same fingerprinted URL every time
    # within a single request, no reason to hash the same file three times.
    nav_js = static_fingerprint.url("platform/nav.mjs")
    chat_js = _chat_bootstrap()
    hatch = f'\n  <p class="wizard-help">{contacto}</p>\n' if contacto else ""
    if chrome == "focus":
        # The product owner 2026-07-17: the wizard carries the site's standard bar (nav +
        # logo) — same as the rest of the pages. Chat also stays: opening it
        # compresses the wizard just like any public page.
        header = (f'<header id="stznav" data-section="{section}"></header>'
                  f'<script type="module" src="{nav_js}"></script>'
                  f'{chat_js}')
        wrap_cls = "wrap wrap--focus"
        footer = hatch + _site_footer()
    elif chrome == "chat":
        header = (f'<header id="stznav" data-section="{section}"></header>'
                  f'<script type="module" src="{nav_js}"></script>')
        wrap_cls = "wrap wrap--chat"
        footer = _site_footer()
    else:
        header = (f'<header id="stznav" data-section="{section}"></header>'
                  f'<script type="module" src="{nav_js}"></script>'
                  f'{chat_js}')
        wrap_cls = "wrap"
        footer = hatch + _site_footer()
    related = _info_related_html(canonical) if chrome == "full" and canonical else ""
    if related:
        body = body + related
        head_extra = head_extra + f"\n<style>{_INFO_RELATED_CSS}</style>"
    body_attr = f' class="{body_class}"' if body_class else ""
    document = f"""<!DOCTYPE html>
<html lang="{lang}" data-theme="{theme.current()}">
<head>{theme._branding_style()}
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{theme.favicon()}
{seo.head(title, desc, path=canonical, index=index)}
{theme.fonts()}{head_extra}
<link rel="stylesheet" href="{static_fingerprint.url('human-rounds.css')}">
<style>{_PAGE_CSS}</style>
{i18n.bootstrap_script(lang=lang)}
{static_fingerprint.manifest_script()}
</head>
<body{body_attr}>
{header}
<main class="{wrap_cls}">
{body}
{footer}
</main>
</body>
</html>"""
    from app.ops import demo_installation
    return demo_installation.inject_html(
        document, "public", lang=lang, switchable=True).encode("utf-8")


def _account_or_renaper(current_user, next_path):
    """Where an auth-needed patient page should send an anonymous visitor
    (2026-08-03): straight into RENAPER when that's the site's live login
    method (accounts.auth_redirect_target — None for password-mode
    installations, or when there's already a session), else `next_path`
    itself, same as before. Used by /my-requests and /request/<id> so an
    email link never stops on the account page's login/register form first."""
    try:
        from app.cuentas import accounts
        destino = accounts.auth_redirect_target(current_user, next_path)
        if destino:
            return destino
    except Exception:  # noqa: BLE001 — never break the redirect over this
        pass
    return next_path


_CAL_ICON = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
             '<rect x="3.5" y="5" width="17" height="15" rx="2.4"></rect>'
             '<line x1="3.5" y1="9.5" x2="20.5" y2="9.5"></line>'
             '<line x1="8" y1="3" x2="8" y2="6.5"></line><line x1="16" y1="3" x2="16" y2="6.5"></line></svg>')
_ARROW_ICON = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
               '<line x1="5" y1="12" x2="18" y2="12"></line><polyline points="12 6 18 12 12 18"></polyline></svg>')
# map pin — marks the address as "this opens Google Maps" with no need for
# the word "Dirección" (the product owner, 2026-07-11: the label is obvious, drop it).
_PIN_ICON = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M21 10c0 6.5-9 12-9 12s-9-5.5-9-12a9 9 0 0 1 18 0z"></path>'
             '<circle cx="12" cy="10" r="3"></circle></svg>')
_PHONE_ICON = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">'
               '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1'
               '-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9'
               '.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c'
               '.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>')
# clock for the walk-in opening band (same strokes as _CAL_ICON, no appointment).
_CLOCK_ICON_LG = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
               '<circle cx="12" cy="12" r="8.5"></circle><path d="M12 7.5v4.8l3.2 1.9"></path></svg>')
