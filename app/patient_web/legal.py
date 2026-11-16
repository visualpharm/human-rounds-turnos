"""legal.py — los dos documentos legales del sitio (términos y privacidad),
editables desde el panel.

Hasta ahora el texto legal vivía partido en ~40 claves de `locales/es.json`
(`terms.s3.p1`, `privacy.s1.p2_bold`, …). Eso servía para traducirlo, pero
hacía imposible editarlo: cambiar una cláusula era tocar el JSON, la página
estática y desplegar. Un texto legal lo escribe y lo corrige el hospital, no
el programador — así que acá vive como UN cuerpo de texto por documento, con
el mismo patrón de superposición que usa `catalog.py` y `prompts.py`:

    el original vive en el código  ->  la base lo pisa  ->  "restaurar" borra la fila

Formato del cuerpo (mini-markdown, deliberadamente chico — quien edita esto
es una persona del hospital, no alguien que sabe HTML):

    párrafo suelto antes del primer título   -> la bajada (.lead)
    ## Título de sección                     -> <h2>
    - ítem                                   -> <ul><li>
    > texto                                  -> el recuadro destacado (.box)
    **negrita**  `código`  [texto](/ruta)    -> <b> <code> <a>

Idiomas: el cuerpo canónico está en el idioma del paquete de datos (español
en las instalaciones argentinas) y se traduce a pedido con
`translate.localized_text`, la misma caché de contenido de paquete que usan
las preparaciones. Si la traducción vuelve sin los `{{sitio.*}}` que tenía el
original, se descarta y se sirve el canónico: preferimos una página en
español a una página que dice "el hospital" donde debería decir el nombre
real.

Nombre de la instalación: NUNCA se escribe literal. `{{sitio.brand}}` rinde
"Turnos de salud pública en Pinamar" en Pinamar y "Human Rounds" en la demo — es el mismo
documento en las dos, que es exactamente lo que pidió el responsable del producto (2026-08-03).
Sobre la demo: el cuerpo trae su propia cláusula ("no se aplica a las
instalaciones de demostración") y, cuando `TURNOS_DEMO_MODE=1`, la página
además abre con un aviso que lo dice de frente.

Versión institucional (2026-10-27): el texto por defecto es el del prototipo
("un prototipo en desarrollo", sin responsabilidad por pérdida o filtración).
Una instalación que nombra a su responsable de los datos
(`SITE_ID_DATA_CONTROLLER`, p. ej. salud.pinamar.gob.ar) recibe
`_TERMINOS_INSTITUCIONAL` / `_PRIVACIDAD_INSTITUCIONAL`: el responsable responde
conforme a la Ley 25.326, se citan las leyes 25.326 y 26.529, y lo que decide la
Asesoría Letrada no se publica (`PENDIENTES_ASESORIA`, `sin_marcas`). Las secciones
fácticas (datos, registro de accesos, IA, registros técnicos) se copian del
prototipo por número: un solo texto por hecho.
"""

import html as _html
import re

from app.ops import demo_installation
from app.core import ai_register, i18n
from app.core import translate
from app.core import tz
from app.core.status_error import BodyStatusError

# id del documento -> archivo público que lo muestra (sólo informativo: la
# página la sigue sirviendo site_identity.py, acá está para la API del panel)
DOCS = ("terminos", "privacidad")

_PAGINA = {"terminos": "/terms.html", "privacidad": "/privacy.html"}

MAX_CHARS = 40_000

# Los meses en castellano ya están duplicados en notifications.py, reports.py,
# remedies.py y patient_web/site_*.py; importar cualquiera de esos acá arrastraría
# medio sitio (Mailgun, la base) para formatear una fecha.
_MESES = {
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
}


# =============================================================================
# Los originales. El canónico es el castellano rioplatense que lee el paciente.
# =============================================================================

_TERMINOS = """\
Al usar {{sitio.brand}} aceptás estos términos. Si no estás de acuerdo, no la uses.

## 1. Qué es {{sitio.brand}}

{{sitio.brand}} es una app de bien público, gratuita y sin fines de lucro, para ver los servicios del {{sitio.network}} y pedir turnos con menos vueltas. Es un prototipo en desarrollo, pensado junto con la Secretaría de Salud de {{sitio.city}}.

## 2. Urgencias

> **{{sitio.brand}} no es un servicio de emergencias.** Ante una urgencia médica, llamá al **{{sitio.emergency_number}}** o acercate a la guardia del {{sitio.hospital}}. No uses la app para situaciones que necesiten atención inmediata.

## 3. Se ofrece "tal cual está"

El servicio se brinda "tal cual está" y "según disponibilidad", sin garantías de ningún tipo. No garantizamos que funcione siempre, que esté libre de errores, ni que los horarios y la información de servicios sean exactos o estén actualizados.

Los horarios y modalidades de atención pueden cambiar. **Confirmá siempre con el centro de salud** antes de presentarte.

## 4. La solicitud no es un turno confirmado

Enviar una solicitud desde la app **no garantiza** un turno. El hospital revisa cada solicitud y su orden de derivación, y confirma (o no) el turno según su disponibilidad y sus criterios. **La confirmación responde a las reglas del centro de salud.** En servicios configurados para confirmación automática, tu confirmación explícita del horario en la web o en el chat puede confirmar un horario disponible. La opinión de la IA no activa esa confirmación.

## 5. Inteligencia artificial

{{sitio.brand}} usa inteligencia artificial para **leer la orden de derivación**, **responder en el chat**, **resumir tu historia clínica** y las funciones enumeradas al final de este documento. Esa lista se genera desde el registro del producto.

La instalación configura dónde se procesa: puede usar un servicio alojado o un modelo local. Cuando usa servidores de proveedores externos, pueden estar fuera de la Argentina. Antes de mandar texto hacemos un esfuerzo razonable por sacarle los datos que te identifican; las imágenes completas y el audio del dictado son las excepciones indicadas en la [política de privacidad](/privacy.html). Un asistente externo que conectes con tu token MCP puede recibir tu identidad y los datos autorizados de tu cuenta sin ese filtro; elegí y revisá ese cliente antes de conectarlo.

> **La inteligencia artificial no diagnostica ni decide turnos.** Las lecturas son preliminares y pueden equivocarse: consultalas con el equipo de salud. El profesional confirma los documentos de atención; el paciente confirma las cancelaciones en la web. No tomes una decisión médica a partir de lo que dice la app.

## 6. Lo usás bajo tu propia responsabilidad

**Usás {{sitio.brand}} bajo tu exclusiva responsabilidad y riesgo.** En la máxima medida permitida por la ley, no aceptamos responsabilidad por nada que surja del uso (o de la imposibilidad de uso) de la app, incluida la pérdida o el acceso no autorizado a datos, o turnos mal mostrados, vencidos o no confirmados.

## 7. Uso correcto

Usá {{sitio.brand}} con tus propios datos y para gestionar tus propios turnos (o los de una persona a tu cargo). No intentes vulnerar, sobrecargar ni abusar del servicio.

## 8. Cambios y baja del servicio

Al ser un proyecto en desarrollo, podemos modificar, suspender o dar de baja {{sitio.brand}} en cualquier momento y sin aviso, sin que eso genere responsabilidad alguna.

## 9. A qué instalación se aplica

Estos términos rigen para la instalación **{{sitio.brand}}**. No se aplican a las instalaciones de demostración de Human Rounds, que funcionan con datos ficticios, no atienden pacientes reales y existen sólo para mostrar cómo anda el sistema.
"""


_PRIVACIDAD = """\
{{sitio.brand}} es una app de bien público, gratuita y sin fines de lucro. Esta política explica, en criollo, qué hacemos y qué no hacemos con tus datos.

## 1. Qué datos manejamos

Cuando te registrás y pedís un turno guardamos los datos que cargás: **DNI**, nombre y apellido, fecha de nacimiento, sexo, un teléfono y/o email de contacto, tu **domicilio** y localidad, tu **obra social o prepaga** (si tenés), y la imagen o el PDF de la orden médica de derivación que subas.

**Foto del DNI:** si para completar el registro sacás o subís una foto del frente o el dorso del DNI, la leemos **en el momento** para extraer esos datos (nombre, DNI, fecha de nacimiento, domicilio) y **no guardamos la foto**: sólo los datos que completás en el formulario. Lo único que puede quedar de ella es el retrato recortado como foto de perfil, que se explica a continuación y que podés descartar.

**Foto de perfil:** si subís una foto de perfil, o dejás la que recortamos del retrato de tu DNI (te la mostramos antes de crear la cuenta y podés descartarla), la guardamos en el servidor de esta instalación, asociada a tu DNI. Sólo la ve tu cuenta, no tiene una dirección pública y no se borra sola con el tiempo. Podés cambiarla o quitarla cuando quieras desde Mi cuenta.

**Gravatar:** si no subiste una foto propia y tu cuenta tiene un email, la app puede buscar la foto que ese email tenga en Gravatar, un servicio externo de fotos de perfil. Para eso le enviamos a Gravatar un código calculado a partir de tu email, no el email escrito: ese código no dice tu nombre, pero quien ya conozca tu email puede calcularlo. La consulta sale desde el servidor de la instalación, no desde tu dispositivo, y no guardamos en nuestra base la foto que devuelve. Si no querés que se haga, subí una foto propia; la instalación también puede desactivar esta consulta.

**Foto en la entrevista previa:** si en la entrevista previa adjuntás una foto (por ejemplo, de una lesión), se guarda junto con la conversación de esa entrevista, la ve el profesional que la atiende y se envía sin modificar al proveedor de inteligencia artificial (sección 5). No se borra sola con el tiempo.

**Obra social:** para sugerirte tu cobertura consultamos tu DNI contra el padrón nacional de salud (SISA/PUCO, del Ministerio de Salud). Sólo enviamos el DNI; guardamos la obra social que figure únicamente si registrás la cuenta.

También guardamos registros técnicos (logs) básicos: fecha y hora, dirección IP y qué página se pidió. Sirven para que la app funcione, para depurar errores y para prevenir abuso. Qué dicen y qué no dicen esos registros, y los reportes de errores, está en la sección 6.

## 2. Para qué usamos tus datos

Usamos tus datos **únicamente** para:

- Gestionar tu solicitud de turno y que el hospital pueda revisarla y confirmarte el turno.
- Que el centro de salud pueda atenderte e incorporar tu atención al circuito de salud (HSI, la Historia de Salud Integrada).
- Mostrar tus resultados, recoger la entrevista previa y preparar lecturas y documentos para que los revises con el equipo de salud.
- Conservar lo que relatás sobre tu salud para próximas consultas, sólo cuando el profesional revisa y confirma ese perfil.
- Depurar la app y arreglarla cuando algo falla.

## 3. Para qué NO los usamos

No usamos tus datos para publicidad, analítica comercial de comportamiento, estudios de mercado ni perfiles comerciales, y no los vendemos o cedemos para esos fines. La memoria de salud relatada para tu atención se describe en la sección de inteligencia artificial; no es un perfil comercial.

## 4. Datos de salud

La orden médica y el motivo de tu solicitud son datos sensibles de salud. Se comparten con el personal del {{sitio.network}} a cargo de tu atención, y con los sistemas de salud del municipio (incluida la Historia de Salud Integrada, HSI), con el único fin de darte el turno y atenderte.

**Quién abrió tus datos de salud.** Cada vez que alguien del personal abre en el panel tus datos de salud —tu ficha, la entrevista previa, la conversación en la que contaste tu caso al pedir un sobreturno, el resumen de tu historia, la orden médica, la consulta o una lista que los muestra— queda registrado quién fue, cuándo, qué abrió y si el sistema se lo permitió. En las listas que se actualizan solas, como los pacientes del día, se registra la primera vez de cada día. En ese registro tu DNI no se guarda tal cual: se guarda un código calculado a partir de él con una clave del sistema, que sirve para encontrar tus registros. El registro no se puede modificar; sólo se borra al vencer el plazo de conservación que fija la instalación, que es de diez años salvo que la instalación lo cambie. Un médico sólo abre los datos de salud de quien tiene con él un turno, una solicitud o una entrevista previa. Sólo quien recibe de la instalación el permiso expreso de ver los pacientes de todos los médicos —por ejemplo, la jefatura de un servicio o una auditoría— abre los de cualquier paciente, y esos accesos también quedan registrados.

## 5. La inteligencia artificial corre en servidores externos

La instalación configura el proveedor y el modelo. Cuando usa un servicio alojado, el texto o la imagen **sale del hospital y se procesa en servidores de proveedores externos**, que **pueden estar fuera de la Argentina**. También admite inferencia local. Las funciones se enumeran al final de este documento desde el registro del producto; no se garantiza la jurisdicción de un proveedor sin verificar su contrato.

**Qué sale sin tus datos personales.** Antes de mandar texto a un proveedor externo lo pasamos por un filtro automático que reemplaza lo que te identifica por etiquetas genéricas: el nombre y el apellido quedan como `[nombre]`, el DNI como `[documento]`, y lo mismo con el teléfono, el email, el CUIL y el domicilio. Ese filtro se aplica al **chat**, a la **entrevista previa**, a la conversación en la que **contás tu caso** cuando no hay turnos y a su síntesis para la mesa, al **resumen de tu historia clínica**, al texto de laboratorio, al borrador de perfil y a la redacción de documentos de consulta. El resumen y la prioridad sugerida de esa entrevista también se seudonimizan con el mismo filtro. Las traducciones e instrucciones son contenido institucional/público, no deben incluir datos de pacientes; se envían sin ese filtro para conservar los contactos públicos.

**Qué sale tal cual.** La **imagen o el PDF de la orden de derivación** se envía **sin modificar**, igual que la foto del DNI, las imágenes/PDF de laboratorio y una foto adjunta a la entrevista. Pueden contener tu nombre, DNI, diagnóstico, domicilio y datos del profesional. La lectura utiliza el documento completo; el filtro de texto no borra datos dentro de una imagen.

**Dictado por voz.** El audio completo se envía al proveedor de transcripción Fal y a su almacenamiento temporal; puede incluir tu identidad y lo que contás sobre tu salud. No se puede filtrar lo que identifica dentro del audio antes de transcribirlo. La app no conserva ese archivo como un registro de voz; no podemos garantizar la eliminación o retención del proveedor.

> **Hacemos un esfuerzo razonable por anonimizar, no podemos garantizar el resultado.** El filtro es automático: si escribís tu nombre con un error de tipeo, o si aparece adentro de un texto escaneado, puede pasar igual. Si hay un dato que preferís que no salga del hospital, no lo escribas en el chat.

Estos proveedores procesan el texto para devolver una respuesta. No les mandamos tus datos para armar perfiles ni para publicidad, y no controlamos sus políticas internas. Nada de esto es obligatorio para atenderte: podés pedir el turno desde la web y consultar en el mostrador o por teléfono, sin pasar por el asistente.

**Asistente externo conectado por MCP.** Si lo conectás con tu token, autorizás que reciba los datos de las herramientas de tu cuenta, incluida tu identidad y los resultados permitidos. Esa respuesta a tu cliente externo no pasa por el filtro de texto del proveedor de IA del producto. El modelo y proveedor de ese asistente los elegís en el cliente; el hospital no los verifica ni controla. No compartas el token y revisá qué asistente conectás.

## 6. Registros técnicos y reportes de errores

**Registros del servidor (logs).** Anotan la fecha y hora, la dirección IP, qué página se pidió y cómo respondió el servidor. De la dirección de la página se borra todo lo que va después del signo «?» y se tapan los números y códigos que identifican a una persona o a un trámite. Cuando la app anota un aviso sobre un caso, el DNI, el teléfono y el email aparecen tapados: sólo quedan los últimos dígitos (por ejemplo `*****222`) o el dominio del email (`***@gmail.com`). Cuando algo falla, el registro anota sólo el tipo de error, su código y en qué parte del programa pasó, nunca el mensaje de error: así no repite un dato que la base de datos o un proveedor recibió. Estos registros los lee sólo quien opera el servidor de esta instalación, y cuánto tiempo se guardan depende de la configuración de ese servidor.

**Reportes de errores.** Si la instalación tiene activado un servicio de seguimiento de errores (Sentry), cada vez que algo falla se manda un reporte **con los datos que te identifican quitados o tapados**. El reporte se arma desde cero con una lista cerrada de campos, no copiando lo que había en el momento del error:

- Lo que **sí** va: el tipo de error y su mensaje (con los emails, DNI, CUIL, teléfonos y cualquier número largo tapados, y recortado si pasa de 300 caracteres), el archivo, la función y la línea del programa donde falló, el método y la dirección de la página sin nada después del «?» y con los números y códigos tapados, el sistema operativo y la versión de Python del servidor, y qué versión de la app estaba corriendo.
- Lo que **no** va: tu dirección IP, tus cookies o tu sesión, tu usuario, los encabezados y el contenido de lo que enviaste (formularios, archivos, imágenes) y los valores que el programa tenía en memoria.
- Del navegador sólo se reportan los errores de las pantallas del personal del {{sitio.network}}, nunca los de las páginas que usás vos.

Ese servicio lo contrata y lo administra quien opera esta instalación, y puede estar alojado fuera de la Argentina; los reportes se conservan según su configuración. El filtro es automático, con el mismo límite que el de la inteligencia artificial: un nombre metido dentro del texto de un error no se puede detectar con certeza, y por eso no prometemos que un reporte nunca lleve un dato tuyo.

## 7. Conservación y seguridad

Guardamos lo mínimo necesario para que la app ande y para gestionar tu turno. La foto del DNI no se guarda nunca. Otros datos se borran solos con los plazos que trae la app (la instalación puede ajustarlos, y el borrado se hace en una pasada periódica, no en el minuto exacto):

- El archivo de la orden de derivación, a los 180 días de que la solicitud se cerró (rechazada, cancelada o con el turno ya pasado). Queda el registro de la solicitud, sin el archivo.
- Los mensajes del chat, a los 90 días, una vez que la conversación quedó resumida; el resumen se conserva para próximas consultas.
- La sesión de tu cuenta, después de 30 días sin uso.

Tu cuenta, tu foto de perfil, la foto que hayas adjuntado a la entrevista previa, tus solicitudes y turnos y lo que el equipo de salud registró de tu atención no tienen un plazo de borrado automático. Podés pedir que los borremos, salvo lo que la ley obliga a conservar de una atención de salud (sección 8).

Como guía de seguridad tomamos las medidas que recomienda la Resolución AAIP 47/2018 para tratar y conservar datos personales en sistemas informáticos. Ponemos cuidado razonable, pero al ser un prototipo ningún sistema es 100% seguro. Como decimos en los términos, no asumimos responsabilidad por la pérdida ni por la filtración de datos.

## 8. Tus derechos

Podés pedir ver tus datos (acceso), corregirlos o actualizarlos, o que los borremos (supresión). El pedido es gratuito.

**Cómo pedirlo.** Acercate al {{sitio.hospital}} o a cualquier centro de {{sitio.network}}, o usá los canales de contacto (teléfono, correo y dirección) que cada centro publica en la página de [centros de salud](/centers). Para cuidar tus datos verificamos tu identidad, por ejemplo con tu DNI, antes de entregarte o cambiar algo; si pedís por una persona a tu cargo, también el vínculo.

**Plazos.** Si pedís ver tus datos, te respondemos dentro de los 10 días corridos de recibido el pedido. Si pedís corregirlos, actualizarlos o borrarlos, dentro de los 5 días hábiles (no cuentan los fines de semana ni los feriados). Cuando no podamos borrar algo porque la ley obliga a conservarlo, te decimos qué conservamos y por qué.

**Lo que no se borra a pedido.**

- El registro de quién abrió tus datos de salud (sección 4) no se borra a pedido: es la prueba de quién los vio.
- **La copia en la historia de salud de la provincia (HSI).** Lo que se cargó en la Historia de Salud Integrada es un registro de la provincia, no una copia nuestra: sólo puede corregirse o borrarse a través de quien administra el HSI. Nosotros no podemos hacerlo desde acá; te ayudamos pasándole tu pedido.
- **Las copias de seguridad cifradas.** Guardamos copias de seguridad cifradas de la instalación, y lo que borramos queda en las copias anteriores hasta que se renuevan por rotación; el plazo depende de cómo está configurada la instalación. Si alguna vez hay que restaurar una copia, volvemos a aplicar los borrados que ya se habían hecho.
- Los proveedores externos que mencionamos en esta política (inteligencia artificial, correo, WhatsApp, Gravatar) pueden tener sus propias copias, que no controlamos.

Lo que forma parte de tu historia clínica se rige además por la Ley 26.529 de Derechos del Paciente: entre otras cosas, tenés derecho a pedir una copia.

El titular de los datos personales tiene la facultad de ejercer el derecho de acceso a los mismos en forma gratuita a intervalos no inferiores a seis meses, salvo que se acredite un interés legítimo al efecto conforme lo establecido en el artículo 14, inciso 3 de la Ley N° 25.326. La Agencia de Acceso a la Información Pública, en su carácter de Órgano de Control de la Ley N° 25.326, tiene la atribución de atender las denuncias y reclamos que interpongan quienes resulten afectados en sus derechos por incumplimiento de las normas vigentes en materia de protección de datos personales.

## 9. Cambios

Podemos actualizar esta política en cualquier momento. La versión vigente es siempre la que está publicada en esta página.

## 10. A qué instalación se aplica

Esta política rige para la instalación **{{sitio.brand}}**. No se aplica a las instalaciones de demostración de Human Rounds, que funcionan con datos ficticios, no atienden pacientes reales y existen sólo para mostrar cómo anda el sistema.
"""


# =============================================================================
# La versión institucional: una instalación con responsable de los datos
# =============================================================================
#
# Los dos textos de arriba son los de un prototipo: dicen «prototipo en
# desarrollo» y no aceptan responsabilidad por la pérdida o la filtración de
# datos. Una instalación que opera un organismo público con pacientes reales
# (salud.pinamar.gob.ar) no puede publicar eso. Cuando la instalación nombra a
# su responsable de la base de datos (`data_controller`: sitio.json del paquete
# o `SITE_ID_DATA_CONTROLLER`), los originales pasan a ser estos.
#
# Lo que describe el código (qué datos, el registro de accesos, la IA, los
# registros técnicos, los plazos de borrado, los derechos) NO se reescribe: se
# toma, sección por sección, del texto de arriba, así las dos versiones nunca
# dicen cosas distintas sobre lo mismo (legal.md: cambia el código, cambia la
# frase — una sola vez). Los números de sección son los mismos en las dos
# versiones: `document()` refresca la sección 5 por su número y el texto se
# cita a sí mismo («sección 5», «sección 8»).
#
# Las decisiones que no son del programa sino del asesor legal del organismo
# NO se escriben en el texto: una página pública nunca muestra una marca
# «[Asesoría Letrada: …]» (c1cd1a13, 29.10). El texto dice lo que sabemos y
# calla lo demás; las preguntas abiertas viven en `PENDIENTES_ASESORIA`, que no
# se publica. `sin_marcas()` quita cualquier marca que llegue (una fila de la
# base guardada con el texto de antes, una edición del panel).

PENDIENTES_ASESORIA = (
    "alcance de la responsabilidad del Municipio por interrupciones del servicio y por información de horarios o servicios desactualizada",
    "jurisdicción competente y norma municipal que aprueba el uso del sistema",
    "si ante un incidente de seguridad que afecte datos personales se avisa a los titulares y a la AAIP, en qué plazo y por qué canal",
    "domicilio y correo de la Secretaría de Salud para recibir los pedidos de acceso, rectificación y supresión",
    "inscripción de la base de datos en el Registro Nacional de Bases de Datos de la AAIP (número, o si corresponde)",
    "base legal de la transferencia a proveedores de inteligencia artificial fuera de la Argentina (sección 5) según el artículo 12 de la Ley 25.326, y los contratos con esos proveedores",
    "qué datos de esta base forman parte de la historia clínica y cuánto tiempo se conservan (Ley 26.529, artículo 18)",
)

MARCA_ASESORIA = "[Asesoría Letrada:"
# La marca entera, con su negrita y el guion de lista si la tenía; la línea
# se va con ella (un ítem vacío o un párrafo de asteriscos tampoco se publican).
_MARCA_RE = re.compile(r"(?m)^[ \t]*(?:- )?(?:\*\*)?\[Asesor[ií]a Letrada:[^\]]*\](?:\*\*)?[ \t]*\n?")
_MARCA_EN_LINEA_RE = re.compile(r"(?:\*\*)?\[Asesor[ií]a Letrada:[^\]]*\](?:\*\*)?")


def _seccion(texto, numero):
    """`## <numero>. …` entera (título incluido) hasta la próxima sección."""
    m = re.search(r"(?ms)^## %d\. .*?(?=^## |\Z)" % numero, texto)
    if not m:
        raise ValueError(f"falta la sección {numero}")
    return m.group(0).rstrip("\n") + "\n\n"


def _sin_titulo(seccion):
    return seccion.split("\n", 1)[1].lstrip("\n")


def _reemplazar(texto, viejo, nuevo):
    """Reemplazo que falla al importar si el párrafo de origen cambió: un
    cambio en el texto del prototipo no puede dejar la versión institucional
    diciendo lo viejo sin que nadie se entere."""
    if texto.count(viejo) != 1:
        raise ValueError("el párrafo a reemplazar no está una sola vez: " + viejo[:60])
    return texto.replace(viejo, nuevo)


_TERMINOS_INSTITUCIONAL = (
    """\
Al usar {{sitio.brand}} aceptás estos términos. Si no estás de acuerdo, podés pedir tu turno en el mostrador de cualquier centro de salud o por teléfono.

## 1. Qué es {{sitio.brand}}

{{sitio.brand}} es el sistema de turnos en línea de **{{sitio.data_controller}}**, gratuito, para ver los servicios del {{sitio.network}} y pedir turnos. Es una vía más para pedir turno: el mostrador y el teléfono de cada centro siguen funcionando.

"""
    + _seccion(_TERMINOS, 2)
    + """\
## 3. La información de los servicios

Mantenemos actualizados los horarios, los profesionales y los requisitos de cada servicio, pero pueden cambiar de un día para otro. **Confirmá siempre con el centro de salud** antes de presentarte.

El sistema puede no estar disponible por mantenimiento o por una falla. Si no funciona, pedí el turno en el mostrador o por teléfono.

"""
    + _seccion(_TERMINOS, 4)
    + _seccion(_TERMINOS, 5)
    + """\
## 6. Responsabilidades

{{sitio.data_controller}} trata tus datos conforme a la Ley 25.326 de Protección de Datos Personales y a la Ley 26.529 de Derechos del Paciente, y responde por ese tratamiento según esas leyes. Nada de estos términos limita los derechos que te dan.

Vos respondés por la veracidad de los datos que cargás y por el cuidado de tu cuenta: no compartas tu contraseña, tu acceso al portal ni tu token.

"""
    + _seccion(_TERMINOS, 7)
    + """\
## 8. Cambios

Podemos actualizar estos términos. La versión vigente es siempre la publicada en esta página, con la fecha de su última actualización.

## 9. Ley aplicable

Estos términos se rigen por las leyes de la República Argentina.

## 10. A qué instalación se aplica

"""
    + _sin_titulo(_seccion(_TERMINOS, 9))
)


_SEGURIDAD_PROTOTIPO = (
    "Como guía de seguridad tomamos las medidas que recomienda la Resolución AAIP 47/2018 "
    "para tratar y conservar datos personales en sistemas informáticos. Ponemos cuidado "
    "razonable, pero al ser un prototipo ningún sistema es 100% seguro. Como decimos en "
    "los términos, no asumimos responsabilidad por la pérdida ni por la filtración de datos.")

_SEGURIDAD_INSTITUCIONAL = """\
Como guía de seguridad tomamos las medidas que recomienda la Resolución AAIP 47/2018 para tratar y conservar datos personales en sistemas informáticos: acceso del personal con usuario propio y permisos por rol, registro de quién abre tus datos de salud (sección 4), conexiones cifradas y copias de seguridad cifradas. Quienes acceden a tus datos están obligados a guardar secreto (artículo 10 de la Ley 25.326) y, el personal de salud, el secreto profesional.

{{sitio.data_controller}} responde por la seguridad de tus datos conforme a la Ley 25.326."""

_PEDIDO_PROTOTIPO = "**Cómo pedirlo.** Acercate al {{sitio.hospital}}"
_PEDIDO_INSTITUCIONAL = ("**Cómo pedirlo.** El pedido se hace ante {{sitio.data_controller}}, "
                         "responsable de la base de datos (sección 9). Acercate al {{sitio.hospital}}")


_PRIVACIDAD_INSTITUCIONAL = (
    """\
{{sitio.brand}} es el sistema de turnos en línea de **{{sitio.data_controller}}**. Esta política explica qué datos tuyos tratamos, para qué, quién es responsable de ellos y cómo ejercer tus derechos, según la Ley 25.326 de Protección de Datos Personales y la Ley 26.529 de Derechos del Paciente.

"""
    + "".join(_seccion(_PRIVACIDAD, n) for n in range(1, 7))
    + _reemplazar(_seccion(_PRIVACIDAD, 7), _SEGURIDAD_PROTOTIPO, _SEGURIDAD_INSTITUCIONAL)
    + _reemplazar(_seccion(_PRIVACIDAD, 8), _PEDIDO_PROTOTIPO, _PEDIDO_INSTITUCIONAL)
    + """\
## 9. Responsable de los datos y marco legal

El responsable de la base de datos es **{{sitio.data_controller}}**. Trata tus datos de salud como establecimiento sanitario público, según el artículo 8 de la Ley 25.326, con secreto profesional; lo que forma parte de tu historia clínica se rige además por la Ley 26.529 (confidencialidad e intimidad, artículo 2; tu derecho a una copia, artículo 14; su custodia, artículo 18).

**¿Es obligatorio darnos tus datos?** Para pedir un turno en línea necesitamos tu DNI, tu nombre y apellido, tu fecha de nacimiento y un teléfono o email: sin ellos no podemos identificarte ni avisarte del turno. La foto de perfil y la entrevista previa son opcionales. Si un dato es incorrecto, el turno puede no quedar a tu nombre. Si preferís no cargarlos acá, podés pedir el turno en el mostrador de cualquier centro.

Los pedidos de acceso, rectificación y supresión de tus datos se hacen como explica la sección 8.

## 10. Cambios

Podemos actualizar esta política. La versión vigente es siempre la publicada en esta página, con la fecha de su última actualización. Un cambio no reduce los derechos que te da la ley.

## 11. A qué instalación se aplica

"""
    + _sin_titulo(_seccion(_PRIVACIDAD, 10))
)


_ORIGINALES = {
    "terminos": {
        "cuerpo": _TERMINOS,
        # Fecha del original. Una edición desde el panel la reemplaza por la
        # fecha real del guardado (la fila de la base manda).
        "actualizado": "2026-10-14",
        "titulo": "Términos y condiciones",
        "pagina": _PAGINA["terminos"],
    },
    "privacidad": {
        "cuerpo": _PRIVACIDAD,
        "actualizado": "2026-10-25",
        "titulo": "Política de privacidad",
        "pagina": _PAGINA["privacidad"],
    },
}

_INSTITUCIONALES = {
    "terminos": dict(_ORIGINALES["terminos"], cuerpo=_TERMINOS_INSTITUCIONAL,
                     actualizado="2026-10-27"),
    "privacidad": dict(_ORIGINALES["privacidad"], cuerpo=_PRIVACIDAD_INSTITUCIONAL,
                       actualizado="2026-10-27"),
}


# =============================================================================
# resolución (original + override de la base)
# =============================================================================

def responsable():
    """El responsable de la base de datos que la instalación declara
    (`data_controller`), o "" — una instalación de prototipo no nombra
    ninguno. Nunca voltea la página: sin identidad, el texto del prototipo."""
    try:
        from app.core import site_identity  # perezoso: site_identity importa este módulo
        return str(site_identity.get("data_controller") or "").strip()
    except Exception:  # noqa: BLE001 — la identidad nunca voltea la página
        return ""


def institucional():
    """¿Esta instalación publica la versión institucional?"""
    return bool(responsable())


def original(doc):
    """El texto que vive en el código, sin mirar la base: el institucional
    cuando la instalación nombra a su responsable, si no el del prototipo."""
    return dict((_INSTITUCIONALES if institucional() else _ORIGINALES)[doc])


def marcas_asesoria(cuerpo):
    """Las marcas de asesor legal que quedan en `cuerpo` (ninguna, en un texto
    sano; `sin_marcas` las quita antes de publicar)."""
    return re.findall(r"\[Asesor[ií]a Letrada:[^\]]*\]", cuerpo or "")


def sin_marcas(cuerpo):
    """`cuerpo` sin ninguna marca de asesor legal ni corchete de relleno: lo
    que sale a la página pública es texto terminado."""
    texto = _MARCA_RE.sub("", cuerpo or "")
    return _MARCA_EN_LINEA_RE.sub("", texto)


def document(doc):
    """{cuerpo, actualizado, titulo, pagina, editado} del documento.

    Sin base (o con la base caída) devuelve el original: una página legal no
    puede depender de que Postgres esté arriba."""
    base = original(doc)
    base["editado"] = False
    base["actualizado_por"] = None
    try:
        from app.core import db_catalog
        fila = db_catalog.legal_get(doc)
    except Exception:  # noqa: BLE001 — base caída o tabla ausente
        fila = None
    # Columnas en inglés (convención de las tablas nuevas, como `translations`);
    # el wire de la API sigue en castellano como el resto del panel.
    if fila and (fila.get("body") or "").strip():
        base["cuerpo"] = fila["body"]
        base["editado"] = True
        base["actualizado_por"] = fila.get("updated_by")
        if fila.get("updated"):
            # A la zona horaria de la instalación antes de quedarnos con la
            # fecha: la del proceso no es la del hospital, y una edición de
            # las 22:30 no puede publicarse fechada al día siguiente.
            base["actualizado"] = (
                fila["updated"].astimezone(tz.tzinfo()).date().isoformat())
    # Institutional prose remains editable; the standard AI section is a
    # factual product statement and follows the current implementation.
    ai_section = re.search(r'(?ms)^## 5\..*?(?=^## 6\.|\Z)', original(doc)['cuerpo'])
    if ai_section:
        base['cuerpo'] = re.sub(r'(?ms)^## 5\..*?(?=^## 6\.|\Z)',
                               lambda _match: ai_section.group(0), base['cuerpo'])
    if doc == "privacidad":
        base["cuerpo"] = _con_registros_tecnicos(base["cuerpo"])
    return base


# Same rule as the AI section: what the logs and the error reports carry is a
# statement about the code (`core/log_pii.py`, `core/error_tracking.py`), not
# hospital prose. An edited body gets the current section — replaced where it
# has one (its own heading and number kept), inserted after the AI section
# where it predates it (unnumbered, so it never repeats a number).
_TECNICOS_TITULO = "Registros técnicos y reportes de errores"
_TECNICOS_RE = re.compile(r"(?ms)^## [^\n]*" + _TECNICOS_TITULO + r"[^\n]*\n.*?(?=^## |\Z)")


def _con_registros_tecnicos(cuerpo):
    canon = _TECNICOS_RE.search(_PRIVACIDAD).group(0)
    texto = canon.split("\n", 1)[1].rstrip("\n") + "\n\n"
    actual = _TECNICOS_RE.search(cuerpo)
    if actual:
        titulo = actual.group(0).split("\n", 1)[0]
        return cuerpo[:actual.start()] + titulo + "\n" + texto + cuerpo[actual.end():]
    ia = re.search(r"(?ms)^## 5\..*?(?=^## |\Z)", cuerpo)
    pos = ia.end() if ia else len(cuerpo)
    antes = cuerpo[:pos].rstrip("\n") + "\n\n"
    return antes + "## " + _TECNICOS_TITULO + "\n" + texto + cuerpo[pos:].lstrip("\n")


def _fecha_larga(iso, lang=None):
    """'2026-08-03' -> '3 de agosto de 2026' ('August 3, 2026' en inglés).
    Una fecha que no parsea se devuelve tal cual: es texto de una fila
    editada, no vale romper la página por eso."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(iso or "").strip())
    if not m:
        return str(iso or "")
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not 1 <= month <= 12:
        return str(iso)
    code = (lang or "es").split("-", 1)[0].lower()
    if code == "en":
        return f"{_MESES['en'][month - 1]} {day}, {year}"
    return f"{day} de {_MESES['es'][month - 1]} de {year}"


# =============================================================================
# mini-markdown -> HTML
# =============================================================================

_PLACEHOLDER_RE = re.compile(r"\{\{sitio\.[a-zA-Z0-9_.]+\}\}")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
# Sólo esquemas inertes: un `javascript:` (o `\tjavascript:`) en un texto legal
# editable desde el panel ejecutaría en la sesión de quien lo lea.
_SAFE_URL_RE = re.compile(r"^(https?:|mailto:|tel:|/|#)", re.I)


def _safe_href(url):
    limpio = re.sub(r"[\s\x00-\x1f]+", "", url or "")
    return limpio if _SAFE_URL_RE.match(limpio) else "#"


def _inline(texto):
    out = _html.escape(texto, quote=False)
    out = _CODE_RE.sub(lambda m: "<code>" + m.group(1) + "</code>", out)
    out = _BOLD_RE.sub(lambda m: "<b>" + m.group(1) + "</b>", out)
    out = _LINK_RE.sub(
        lambda m: '<a href="' + _html.escape(_safe_href(m.group(2)), quote=True) + '">'
                  + m.group(1) + "</a>", out)
    return out


def _bloques(cuerpo):
    """Parte el cuerpo en bloques separados por una línea en blanco."""
    partes = re.split(r"\n\s*\n", (cuerpo or "").strip())
    return [p.strip("\n") for p in partes if p.strip()]


def markdown_html(cuerpo):
    """El cuerpo editable convertido al HTML que ya sabe maquetar la página
    (mismas clases que tenían terms.html/privacy.html a mano)."""
    html_out = []
    primero = True
    for bloque in _bloques(cuerpo):
        lineas = [ln.strip() for ln in bloque.split("\n") if ln.strip()]
        if lineas[0].startswith("## "):
            html_out.append("<h2>" + _inline(lineas[0][3:].strip()) + "</h2>")
            resto = lineas[1:]
            if resto:
                html_out.append("<p>" + _inline(" ".join(resto)) + "</p>")
            primero = False
            continue
        if all(ln.startswith("- ") for ln in lineas):
            items = "".join("<li>" + _inline(ln[2:].strip()) + "</li>"
                            for ln in lineas)
            html_out.append("<ul>" + items + "</ul>")
            primero = False
            continue
        if lineas[0].startswith("> "):
            texto = " ".join(ln[2:].strip() if ln.startswith("> ") else ln
                             for ln in lineas)
            html_out.append('<div class="box"><p>' + _inline(texto) + "</p></div>")
            primero = False
            continue
        clase = ' class="lead"' if primero else ""
        html_out.append("<p" + clase + ">" + _inline(" ".join(lineas)) + "</p>")
        primero = False
    return "".join(html_out)


# =============================================================================
# idioma
# =============================================================================

def _traducido(doc, cuerpo, lang):
    """`cuerpo` en `lang`, o el canónico si la traducción perdió algún
    `{{sitio.*}}` (un documento legal que pierde el nombre de la institución
    a mitad de camino es peor que un documento en el idioma equivocado)."""
    if not lang:
        return cuerpo
    try:
        salida = translate.localized_text("legal." + doc, cuerpo, lang)
    except Exception:  # noqa: BLE001 — el traductor nunca voltea la página
        return cuerpo
    if not salida or not salida.strip():
        return cuerpo
    if sorted(_PLACEHOLDER_RE.findall(salida)) != sorted(_PLACEHOLDER_RE.findall(cuerpo)):
        return cuerpo
    return salida


def _aviso_demo(lang):
    """El recuadro que sólo ve la demo. Lo dice de frente: el documento es el
    mismo que el de la instalación real, pero acá no rige."""
    if not demo_installation.enabled():
        return ""
    texto = i18n.t("legal.demo_notice", lang)
    return '<div class="box"><p>' + _inline(texto) + "</p></div>"


def body_html(doc, lang=None):
    """El fragmento completo que se inyecta en terms.html / privacy.html:
    línea de "última actualización", aviso de demo (si corresponde) y cuerpo."""
    d = document(doc)
    # Antes de traducir: el traductor no debe ver (ni cachear) una marca.
    cuerpo = _traducido(doc, sin_marcas(d["cuerpo"]), lang)
    etiqueta = i18n.t("legal.updated", lang)
    fecha = _fecha_larga(d["actualizado"], lang)
    return ('<p class="updated">' + _html.escape(f"{etiqueta} {fecha}", quote=False)
            + "</p>" + _aviso_demo(lang) + markdown_html(cuerpo) + ai_uses_html(lang))


def ai_uses_html(lang=None):
    """Non-editable factual annex: both documents always agree with inventory.

    Editable prose remains hospital-owned. Overrides cannot remove the live
    annex or turn an old three-use description into the current inventory.
    """
    title = i18n.t_or('ai.legal.functions', 'Funciones de inteligencia artificial', lang)
    return '<section data-ai-inventory><h2>' + _html.escape(title) + '</h2><ul>' + ''.join(
        '<li data-ai-function="' + _html.escape(f['id'], quote=True) + '">' +
        _html.escape(i18n.t_or('ai.function.' + f['id'], f['purpose'], lang)) +
        '</li>' for f in ai_register.FUNCTIONS) + '</ul></section>'


_TOKEN_RE = re.compile(r"\{\{legal\.([a-z]+)\}\}")


def render(text, lang=None):
    """Reemplaza cada `{{legal.<doc>}}` por su fragmento. Mismo contrato que
    `site_identity.render` / `i18n.render`: se corre sobre la página entera y
    un token de otro dueño queda intacto."""
    def _sub(m):
        doc = m.group(1)
        if doc not in _ORIGINALES:
            return m.group(0)
        return body_html(doc, lang)
    return _TOKEN_RE.sub(_sub, text)


# =============================================================================
# API del panel — /api/admin/legal
# =============================================================================


def _item(doc):
    d = document(doc)
    return {
        "id": doc,
        "titulo": d["titulo"],
        "pagina": d["pagina"],
        "cuerpo": d["cuerpo"],
        "original": original(doc)["cuerpo"],
        "actualizado": d["actualizado"],
        "actualizado_por": d.get("actualizado_por"),
        "editado": d["editado"],
    }


class LegalError(BodyStatusError):
    """Carries the exact `(status, body)` these domain functions raise.
    `legal_router.py`'s native endpoints catch this and build their
    response bytes from it — `db.DbUnavailable` is deliberately NOT wrapped
    here: both call sites keep their own outer 503 guard around the domain
    call (see the module's list/document read path, which
    already degrades internally and never raises it)."""


# ---- pure domain functions (the native router's entry points) ------

def list_payload():
    return {"items": [_item(d) for d in DOCS]}


def item_payload(doc):
    """`_item(doc)`, or raises `LegalError(404, ...)` for an unknown doc."""
    if doc not in _ORIGINALES:
        raise LegalError(404, {"error": "no existe ese documento"})
    return _item(doc)


def save_payload(doc, cuerpo, usuario):
    """Validates + persists `cuerpo` for `doc`. Raises `LegalError` on an
    unknown doc or an invalid body; may raise `db.DbUnavailable` from
    `db_catalog.legal_save` (caller's guard, unchanged)."""
    if doc not in _ORIGINALES:
        raise LegalError(404, {"error": "no existe ese documento"})
    if not cuerpo.strip():
        raise LegalError(400, {"error": "el texto no puede quedar vacío"})
    if len(cuerpo) > MAX_CHARS:
        raise LegalError(400, {"error": "el texto es demasiado largo"})
    from app.core import db_catalog
    db_catalog.legal_save(doc, cuerpo, usuario=usuario)
    _invalidar(doc)
    return {"ok": True, "item": _item(doc)}


def delete_payload(doc):
    """Deletes the override for `doc`, returns `{"ok": True, "item": ...}`.
    Raises `LegalError(404, ...)` for an unknown doc; may raise
    `db.DbUnavailable` from `db_catalog.legal_delete` (caller's guard, unchanged)."""
    if doc not in _ORIGINALES:
        raise LegalError(404, {"error": "no existe ese documento"})
    from app.core import db_catalog
    db_catalog.legal_delete(doc)
    _invalidar(doc)
    return {"ok": True, "item": _item(doc)}


def _invalidar(doc):
    """Un cuerpo nuevo invalida su traducción cacheada: la próxima visita en
    otro idioma la rehace contra el texto nuevo (translate.py compara por
    hash, así que esto es sólo por prolijidad para el override manual)."""
    try:
        from app.core import db_catalog
        for lang in i18n.enabled_languages():
            if lang != translate.pack_language():
                db_catalog.translation_clear_override("legal." + doc, lang)
                translate.invalidate_rows(lang)
    except Exception:  # noqa: BLE001 — la caché se rehace sola por hash
        pass
