---
title: Los avatares chicos van en UN sprite, nombrado por su propio contenido
scope: repo
area: design
tags: [performance, imagenes, cache, home, avatar_sprite]
created: 2026-08-05
refs:
  - avatar_sprite.py
  - patient_web/site_*.py
  - test/test_avatar_sprite.py
  - docs/architecture/listados-y-busqueda.md
status: active
---

## Problem

The product owner: *"it's loading but veery slow… the page loads up to the search bar and
then it's stuck."*

Medido en vivo contra `demo.humanrounds.org`, con caché frío: **79 pedidos y
2,02 MB**. La home pintaba el hero y el buscador a los ~1,7 s y ahí se
quedaba; el resto seguía llegando hasta los **11,5 s**.

El grueso: **46 pedidos a `/doctor-photo/<id>`, 1,56 MB**, todos retratos a
resolución completa, para dibujar círculos de **44 px**. El origen está en
Alemania y los pacientes en Argentina (~0,8 s de ida y vuelta por pedido), y
el server rinde ~180 KB/s repartidos entre los 50 streams simultáneos.

## Root cause

Nadie eligió pedir 46 imágenes. Salió de una regla razonable aplicada a una
escala para la que no se pensó: `site_doctors._photo_style()` resuelve el
retrato de UN médico, y las tarjetas del directorio lo llamaban una vez por
cara. Con 13 clusters × hasta 6 caras, la home terminaba pidiendo casi todo
el padrón, de a una foto por vez y a tamaño ficha.

Ya existía un sprite offline (`medico-photo-sprite.webp`, por pack), pero
sólo se usa cuando el médico NO tiene foto subida desde el panel — y la
demo tiene las 50 subidas, así que en la práctica nunca entraba.

Dos hallazgos laterales de la misma medición:

- La home pedía `/api/directory` **dos veces**. La segunda copia no se leía
  nunca (`res[2]` sin usar) y era la que demoraba las tiles de centros.
- `server.py:end_headers` pisaba el `Cache-Control` que un módulo ya había
  mandado, mandando DOS cabeceras. El browser se queda con la más
  restrictiva.

## Solution

`avatar_sprite.py`: **un solo atlas WebP** con todos los retratos del
catálogo, en mosaicos de 96 px (el doble del avatar más grande que dibuja el
sitio), y cada lugar de uso recibe un `background-position`. El recorte lo
hace CSS. Las 70 caras de Pinamar: **84 KB en UN pedido**.

Resultado en la demo: **79 pedidos / 2,02 MB → 52 pedidos / 972 KB**, con 0
pedidos a `/doctor-photo` y 1 al atlas.

El atlas se arma sobre el padrón ENTERO a propósito, no sobre las caras de
una página: así `/medicos` referencia el MISMO archivo y sus 70 avatares
salen del caché que la home ya llenó.

`_photo_style(mid, chico=True)` es el que va al atlas. El retrato grande de
la ficha del médico queda en `chico=False` con su archivo propio — recortarlo
de un mosaico de 96 px se vería horrible.

## How to apply

**La regla que hay que llevarse: el nombre de un artefacto derivado es el
hash de SU CONTENIDO, nunca el de los mtimes que lo produjeron.**

La primera versión nombraba el atlas con el hash de los mtimes de las fotos.
Parece equivalente y no lo es: `demo_refresh.py` vuelve a copiar los 50
retratos todas las noches — bytes idénticos, mtimes nuevos. Cada noche nacía
una URL nueva para una imagen que no había cambiado en un pixel. **En vivo,
en la demo, la home quedó sirviendo 404 en todas las caras.**

Tres cosas tienen que darse juntas para que un recurso content-addressed sea
seguro. Faltando una, se cae:

1. **Nombrar por el contenido de salida.** Se arma el atlas en memoria, se
   hashean esos bytes, y recién ahí se lo escribe. Entradas idénticas → misma
   URL. El mtime queda sólo para DETECTAR el cambio, que es para lo único
   que sirve.
2. **No borrar el anterior en el acto** (`RETENCION_S = 6 h`). Las páginas ya
   abiertas, el caché del browser y el contenedor viejo durante un deploy
   siguen pidiendo la URL con la que se los sirvió. Un atlas de más cuesta
   60 KB de disco; un 404 le cuesta a la página todos sus retratos.
3. **Poder responder sin el archivo.** El atlas vigente queda en memoria, así
   la ruta contesta aunque abajo se haya limpiado el volumen o otro
   contenedor haya purgado.

Lo demás que conviene conservar si se toca esto:

- **Degradar, siempre.** Sin Pillow, con una fuente ilegible o con un
  catálogo mayor a `MAX_TILES`, `styles()` devuelve `{}` y `patient_web/site_*.py`
  vuelve a las URLs por foto. Una foto rota le cuesta la cara a ESE médico,
  no a la página.
- **El encuadre es un contrato, no una preferencia.** `ImageOps.fit`
  centrado es exactamente lo que hacían `background-size:cover` +
  `background-position:center`. Cambia cómo se ENTREGA la foto, no cómo se
  ve. Si algún día conviene subir el encuadre, es una decisión de diseño
  aparte.
- **El recorte se testea contra los píxeles.** `background-position` es un
  porcentaje de la DIFERENCIA entre imagen y caja, no de la imagen. Un
  off-by-one pasa cualquier assertion de forma y muestra a cada médico con la
  cara de otro. `test_cada_medico_recorta_SU_cara` arma retratos de colores
  planos y verifica qué color cae en cada celda.
- **Las fotos de los centros entran al mismo atlas, recortadas (2026-10-27).**
  Los círculos de la tarjeta «Demanda espontánea» son edificios: enteros en
  44 px se leían como cielo y vereda. `styles(ids, extra=[("center:<slug>",
  archivo, (x, y, zoom))])` recorta un cuadrado alrededor del foco que el
  pack nombra en `centros-info.json` (`"avatar"`), atado con `for` al hash
  de la foto sobre la que se eligió; otra foto cae al foco por defecto
  (`site_centers._CENTER_FOCUS_DEFAULT`). El encuadre de los MÉDICOS no
  cambia — esa es la decisión de diseño aparte del punto anterior, tomada
  solo para los centros. Test: `test_la_foto_de_un_centro_se_recorta_en_su_foco`
  (foto en cuadrantes de colores).
- **Todo círculo de un centro sale del atlas, no de la foto (2026-10-28).**
  `_center_faces()` entrega al atlas TODOS los centros con foto (antes solo los
  de demanda espontánea) y `site_doctors.center_circle_style(slug)` es el único
  lugar que pide el corte. Lo usan la tarjeta de la home y la vista previa de
  64 px del editor de centros del panel (`doctor_photos.preview_payload`); la
  foto entera (una subida del panel puede pesar varios MB) queda solo para donde
  se muestra grande — tiles 4:3 de `/centers`, el héroe de la página del
  centro, la galería — y como respaldo cuando el atlas no se puede armar
  («degradar, nunca fallar»). Medido en vivo (pinamar.humanrounds.org,
  2026-10-28): los 6 círculos de la home pedían 6 fotos, 1 096 812 B; los 6
  cortes ocupan unos 14 KB dentro del atlas que la home ya descarga, y 0
  pedidos más. Tests: `TestCenterCirclesNeverLoadTheFullPhoto`
  (`test_press_office_requests.py`, atlas real sobre fotos de prueba) y
  `test_preview_con_upload_devuelve_el_corte_del_atlas` (`test_doctor_photos.py`).
  Un círculo NUEVO de un centro: `center_circle_style`, nunca `_center_photo`.
  La vista previa de 64 px del médico en el editor también va al atlas
  (`_photo_style(mid, chico=True)`); el retrato entero solo si el atlas falla.
