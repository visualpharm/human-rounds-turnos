# Ficha de Chrome Web Store — es-AR

**Nombre:** HSI Humanizador

**Descripción corta (132 máx.):**
Pasa turnos y pacientes de Human Rounds a HSI cuando no hay acceso a la API.

**Categoría:** Trabajo y planificación

**Ícono de la tienda:** `icono-tienda-128.png` (dibujo de 96 px con 16 px transparentes por lado)

**Idioma:** Español (Argentina) · **Visibilidad:** No listada (se instala por enlace)

## Descripción

Texto plano: la tienda no interpreta Markdown (unos `**` se ven tal cual).

Una extensión de navegador que conecta Human Rounds (utilizado, por ejemplo, en Pinamar) con la Historia de Salud Integrada (HSI) de Argentina.

Con un solo clic, transfiere los datos del paciente y completa su turno en HSI.

Ahorrá hasta 5 minutos por paciente, sin cargar dos veces la misma información.

## Propósito único

Completar en la pantalla de turnos de HSI los datos de un turno que ya está en «Pacientes de hoy» de Human Rounds Turnos.

## Justificación de permisos

- **storage:** guarda tus ajustes, cuántos turnos guardaste y la lista reducida de las últimas cargas.
- **scripting:** agrega el completado a la pestaña de HSI que vos elegiste en los ajustes.
- **clipboardWrite:** copia el DNI cuando tocás «Copiar DNI» o «Abrir en HSI».
- **Acceso a sitios (HSI de prueba) y acceso opcional a otros sitios:** la extensión solo actúa en «Pacientes de hoy» y en la dirección de HSI que vos habilitás; Chrome te pide permiso para esa dirección y para ninguna otra.
- **Código remoto:** no usa.

## Uso de datos

- Maneja datos personales y de salud (nombre, DNI, sexo, fecha de nacimiento, contacto, cobertura y turno) únicamente dentro del navegador, para completar HSI.
- No envía datos a ningún servidor, no registra nada, no usa analíticas, no vende ni comparte datos.
- Política de privacidad: `privacidad.md` (publicarla en una dirección pública y pegar el enlace en la ficha).

## Capturas

Tomadas de una corrida contra la HSI de prueba con pacientes ficticios: `extensions/hsi-humanizador/guia/img/pacientes-de-hoy.png`, `hsi-completo.png`, `fila-guardada.png`, `guardar-solo.png`, `ajustes.png` (llevarlas a 1280×800).
