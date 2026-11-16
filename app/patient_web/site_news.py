"""patient_web/site_news.py — News and the informational pages of the public site.

The news items with their translation, news search results, telemedicine, zoonosis and blood donation.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import html as _html
import re

from app.core import data_packs
from app.core import format
from app.core import i18n
from app.core import site_identity
from app.core import theme
from app.core import translate

from app.patient_web import site_base
from app.patient_web import site_centers
from app.patient_web import site_preparation
from app.patient_web import site_schedule
from app.patient_web import site_shell


# Changelog history (/news, minor footer link). Editorial, curated from the
# git log: only changes a patient or the hospital NOTICE — no spacing,
# cache-bump or minor-tweak entries (the product owner, 2026-07-15).
#
# P("…") marks a news item that was born from a hospital request (the reference-deployment counterpart
# reference deployment and his team, in the July 8/15/24 meetings) — /news/pedidos shows
# only those. It's a str subclass on purpose: everything that already treated
# the news item as plain text keeps working the same (the product owner, 2026-07-27).
#
# Three more markers decide WHO sees an item (demo review 2026-11-15); the
# data stays in the file and in the translation sweep either way, they only
# filter the public page. They compose: R(S("…")), S(P("…")).
#   S("…")  staff/panel change — never on the patient page.
#   R("…")  names the reference deployment (its domain, its programs, its
#           look): shown only where that pack is active, never on the demo
#           or another installation.
#   X("…")  stale or no longer true — hidden everywhere.
# Two more say which login feature the item describes, so an installation
# where an identity portal switched that feature off does not announce it
# (2026-11-15). The panel's own staff login needs none: those items are S.
#   D("…")  our own patient sign-up and sign-in (DNI photo, RENAPER, claims on
#           a DNI that has an account, unverified accounts, password, editing
#           the DNI) — hidden where patients sign in through the portal.
#   M("…")  the account's AI-assistant connection (MCP) — hidden where the
#           municipal gateway closes it.
_FLAGS = ("requested", "staff", "reference", "retired",
          "own_signup", "assistant_link")


class _Item(str):
    # One default per name in _FLAGS (test_novedades pins the two together).
    requested = staff = reference = retired = False
    own_signup = assistant_link = False


def _mark(texto, flag):
    item = _Item(texto)
    for name in _FLAGS:
        setattr(item, name, getattr(texto, name, False))
    setattr(item, flag, True)
    return item


def P(texto):
    return _mark(texto, "requested")


def S(texto):
    return _mark(texto, "staff")


def R(texto):
    return _mark(texto, "reference")


def X(texto):
    return _mark(texto, "retired")


def D(texto):
    return _mark(texto, "own_signup")


def M(texto):
    return _mark(texto, "assistant_link")


def _portal_off_features():
    """The marker flags whose feature this installation's portal login turned
    off. Read once per page, from the installation environment."""
    from app.cuentas import portal_identity
    off = set()
    if portal_identity.patient_login_enabled():
        off.add("own_signup")
    if portal_identity.municipal_patient_login():
        off.add("assistant_link")
    return off


# The pack of the reference deployment (salud.pinamar.gob.ar and
# pinamar.humanrounds.org). Legacy single-dir mode has no chain; there the
# installation's own city decides.
REFERENCE_PACK = "ar-b-pinamar"


def _reference_deployment():
    chain = data_packs.active_chain()
    if chain is not None:
        return REFERENCE_PACK in chain
    return "Pinamar" in str(site_identity.get("city") or "")


def _public(item, reference, off=frozenset()):
    """Does this item belong on the patient page of this installation?
    `off` = the flags of `_portal_off_features()`."""
    if getattr(item, "staff", False) or getattr(item, "retired", False):
        return False
    if any(getattr(item, flag, False) for flag in off):
        return False
    return reference or not getattr(item, "reference", False)


_NEWS = [
    ("15 de noviembre de 2026", [
        R('La barra de arriba del sitio del Hospital de Pinamar dice su <b>nombre</b>, «Turnos de salud pública en Pinamar», el mismo que ves en la pestaña del navegador.'),
    ]),
    ("14 de noviembre de 2026", [
        D('Si te registrás con un <b>DNI que ya tiene cuenta</b>, tu pedido queda como reclamo pendiente. La mesa de entradas lo confirma o lo rechaza desde <b>Pacientes</b>.'),
        D('Una cuenta <b>sin verificar</b> no ve datos clínicos: ni turnos, ni calendario, ni resultados del laboratorio, hasta que se confirme que es de su titular.'),
        S('En el panel, cambiar la <b>contraseña</b> o el <b>autenticador</b> pide la contraseña actual, y al cambiar el autenticador se cierran las otras sesiones abiertas.'),
    ]),
    ("13 de noviembre de 2026", [
        'El día se ofrece <b>completo</b>, hasta la última hora de la agenda, y no se recorta más a unos pocos horarios por día.',
        'Si buscás algo en la página de un <b>centro</b> y no hay resultados, te ofrecemos la misma búsqueda en todos los centros.',
        'La tarjeta de <b>Demanda espontánea</b> y su página abren con una sola descripción, nombran los lugares donde se atiende y se leen en una lista más clara.',
        S('En los reportes, las <b>obras sociales</b> se juntan aunque estén escritas con otras mayúsculas, espacios o acentos: «Medife» y «Medifé» cuentan como una sola.'),
    ]),
    ("11 de noviembre de 2026", [
        'Si el hospital da un turno <b>directamente en su sistema</b>, esa hora ya no aparece disponible acá: el sitio y el asistente no se la ofrecen a otro paciente.',
    ]),
    ("2 de noviembre de 2026", [
        S('En <b>Pacientes</b>, la columna «Turnos» cuenta los turnos otorgados y lleva a la ficha, que ahora lista cada turno con día, servicio, lugar y estado.'),
        S('Las cuentas <b>sin nombre</b> dicen «Sin nombre» y quedan al final; las que no pidieron ningún turno ya no llenan la lista de recientes.'),
    ]),
    ("30 de octubre de 2026", [
        R('Si entrás con el <b>portal</b>, tu cuenta nace con el nombre que figura en el portal, y en Pacientes ya no aparece un «—» en su lugar.'),
        'Al <b>soltar un horario</b> que estabas por confirmar, se cancela la renovación en curso y no se vuelve a reservar solo.',
        S('En el panel, una <b>respuesta lenta</b> se muestra como «Cargando…» y no como una alerta de error; y si la sesión venció, el panel lo avisa en vez de quedar mudo.'),
    ]),
    ("29 de octubre de 2026", [
        'En <b>Mi cuenta</b>, el link de la barra dice siempre «Mi cuenta», tengas o no un turno por delante; «Mis turnos» sigue dentro de la cuenta.',
        R('Las direcciones de los <b>centros</b> llevan a Google Maps en todas las pantallas y en los correos. El <b>CAPS Móvil</b> muestra hasta cuándo está en cada lugar y se actualiza solo al vencer.'),
        R('En tu cuenta, el botón <b>«Ver resultados del laboratorio»</b> te lleva al laboratorio con un enlace seguro que vence a los 5 minutos. La lectura de la IA y el nombre del estudio se muestran en el idioma de la página.'),
        S('En el panel, se pueden <b>importar médicos</b> desde un CSV o un Excel: cada fila crea un médico con su cuenta y su invitación.'),
        'Un <b>médico</b> puede tener una segunda especialidad, que se ve en su página y en los filtros.',
        S('En la cola del panel, una fila cancelada dice <b>quién canceló</b>: «por el paciente», «por el centro» o «Cancelada».'),
        S('Al <b>derivar a otro centro</b>, la mesa de entradas ve los horarios del servicio nuevo y la tarjeta muestra el centro.'),
        R(S('En el panel, con el portal de salud.pinamar.gob.ar, <b>«Cerrar sesión»</b> sale por el portal. Y si la sesión del portal vence con la página abierta, volvés al ingreso del portal y regresás a la misma pantalla.')),
    ]),
    ("28 de octubre de 2026", [
        S('Cada <b>médico</b> puede tener su propia ventana de reserva en cada centro, que se impone a la del servicio y a la del sitio.'),
    ]),
    ("27 de octubre de 2026", [
        R('El sitio del Hospital de Pinamar estrena <b>nombre</b>, «Turnos de salud pública en Pinamar»: lo ves en la pestaña del navegador, en el chat, en los correos y en los términos y la privacidad.'),
    ]),
    ("24 de octubre de 2026", [
        'En <b>Confirmá</b>, el teléfono que escribís es el de ese turno: el de tu cuenta solo cambia si tildás «Guardar también en mi cuenta».',
        'Si elegís la <b>orden médica</b> mientras el asistente todavía está cargando, ya no se pierde: llega al asistente.',
        'Un médico que atiende un solo estudio (por ejemplo Ergometría) ya no figura en los otros servicios de su centro. Y cuando se cierra un servicio sin horario propio, también se cierran las agendas de sus <b>médicos</b>, así no se ofrecen fechas que ya estaban dadas.',
        'Un turno sacado por la web con confirmación automática guarda el <b>médico</b> del horario que elegiste.',
        S('En el panel, «Crear turno» deja elegir <b>quién atiende</b> cuando el servicio tiene varios médicos, y ya no permite cargar un turno fuera de la agenda del médico: fuera de su ventana solo puede darlo el propio médico.'),
        S('En «Crear turno», si no podés dar de alta al paciente, «Crear paciente» te explica <b>por qué</b> y qué hacer. Sin médico elegido, el botón te lleva a elegirlo.'),
        S('En «Solicitudes», debajo del servicio se lee <b>qué intentó sacar</b> el paciente: día y hora, centro y médico. Ya no aparece la fecha de recepción que se confundía con la del turno, y un médico deshabilitado conserva sus solicitudes.'),
        S('En «Pacientes de hoy», el filtro de centros ofrece <b>todos los centros</b> de la cuenta.'),
        S('En «Médicos», «Ver los turnos» lista solo los turnos por venir que cuenta el aviso, y el aviso se lee en rojo.'),
        S('En la consulta, el médico lee la <b>entrevista</b> sin marcas del asistente, y «Mis pacientes» muestra la hora del hospital en la última entrevista y el próximo turno.'),
        'Un <b>botón deshabilitado</b> se ve inactivo en todo el sitio y el panel, así se distingue de uno que se puede tocar.',
    ]),
    ("23 de octubre de 2026", [
        R('En el sitio del Hospital de Pinamar, los pacientes entran con su <b>cuenta del portal</b> de salud.pinamar.gob.ar: la cuenta se vincula o se crea sola con el DNI del portal.'),
        'Si <b>cancelás</b> un turno desde tu cuenta, el horario queda libre enseguida para otras personas, en vez de quedar oculto hasta su hora.',
        'Cuando no podemos abrir tu cuenta o un día todavía no está habilitado, el aviso te indica un <b>lugar concreto</b> adonde ir, en vez de «tu centro de salud».',
        'En tu cuenta queda registrado <b>quién abrió tus datos de salud</b>. En el panel, cada médico ve solo a sus pacientes, y la jefatura o la auditoría pueden ver a todos con un permiso especial.',
        S('El <b>resumen con IA</b> de un paciente solo lo pueden pedir los médicos de ese paciente, y un rol creado en el panel con «atención médica» queda acotado a su médico.'),
        S('En el panel, «Crear turno» y «Solicitudes» ofrecen solo a los <b>médicos</b> que atienden ese servicio, nunca a uno de otra especialidad. Los médicos y servicios deshabilitados ya no aparecen.'),
        S('En el panel, «Crear turno» y «Aprobar» llegan a <b>tres meses</b> de la agenda del médico, y el filtro de médicos de «Solicitudes» muestra el nombre de cada uno.'),
        S('En la <b>consulta</b> de la entrevista previa queda una sola vigente: al reabrirla se ve lo emitido, y el enlace de una receta reemplazada muestra el aviso y lleva al documento nuevo.'),
        S('En el panel, al <b>archivar un paciente</b> que tiene turnos por venir, se muestran esos turnos y se cancelan con tu confirmación, en vez de quedar sueltos.'),
        S('En el panel, no se puede <b>deshabilitar un médico</b> que tiene turnos por venir: «Ver los turnos» abre las solicitudes de ese médico para reasignarlas o cancelarlas, y recién después se deshabilita.'),
        S('En la ficha del <b>médico</b>, el teléfono del turno es el de la solicitud, y si la cuenta tiene otro se ve aparte, con la aclaración de a quién pertenece cada uno.'),
        S('El rol de <b>gestor de agendas</b> ve en cada ficha de médico solo lo que su permiso le deja abrir, sin pantallas que terminen en «No tenés permiso».'),
    ]),
    ("22 de octubre de 2026", [
        X('La barra del sitio del Hospital de Pinamar ahora dice <b>«Portal de turnos de salud en Pinamar»</b>, y la portada tiene un título y una bajada nuevos, a pedido de la oficina de prensa.'),
        R('Todas las páginas llevan al pie el aviso del <b>Decreto 1116/26</b> y el número de <b>Urgencias: 107</b>.'),
        'Los servicios que se atienden sin turno, por orden de llegada, están juntos en una página de <b>«Demanda espontánea»</b>, con una tabla por centro.',
        R('En la lista de especialidades, <b>Vacunas</b> aparece primero y <b>«Otras especialidades»</b> queda al final.'),
        'La portada tiene una tarjeta de <b>Telemedicina</b> con el botón «Consultá por telemedicina», y se sacó el título «Buen vecino».',
        '<b>Zoonosis</b> tiene una sola página, y los centros pueden mostrar su <b>WhatsApp</b>. Cuando te trabás en un paso, la ayuda te lleva a la línea de WhatsApp del hospital.',
        'En la ficha de cada centro, el <b>Vacunatorio</b> te lleva directo a la página de su servicio.',
    ]),
    ("18 de octubre de 2026", [
        'Cuando un servicio no tiene turnos, quedan dos opciones: cuándo vuelve a haber turnos y <b>«Contanos tu caso»</b>. Si no podés esperar, escribí tu caso y el hospital te contacta.',
        'En el buscador, <b>«urgencias»</b> y <b>«emergencia»</b> te llevan a la guardia, y <b>«hospital»</b> te muestra el hospital con su dirección. El demo también tiene la guardia del hospital.',
        S('En el panel, <b>«Mis pacientes»</b> busca por DNI o por nombre, y la frase del horario de cada servicio se entiende mejor.'),
    ]),
    ("17 de octubre de 2026", [
        X('Cuando un servicio no tiene turnos, ahora dice <b>«Ahora no hay turnos»</b> y te ofrece tres opciones: cuándo vuelve a haber turnos, el teléfono del centro si no podés esperar y «Contanos tu caso», que la mesa de entradas lee en la cola.'),
        'En el teléfono, el menú de <b>Mi cuenta</b> marca la pestaña en la que estás y siempre ofrece «Mis turnos» cuando tenés solicitudes.',
        S('La foto de un <b>centro</b> que se sube desde el panel ahora se ve en la portada, en la lista de centros y en la ficha del centro.'),
        R('Sumamos la especialidad <b>Clínica médica</b>, pedida por el hospital para marcar a sus profesionales como especialistas.'),
        R('En el sitio del hospital, la barra de arriba muestra el <b>logotipo</b> del hospital, y el pie de página quedó más compacto, con las especialidades en una sola frase.'),
    ]),
    ("16 de octubre de 2026", [
        R('El sitio del Hospital de Pinamar estrena su <b>identidad visual</b>: letra Fira Sans en todas las páginas y barra de navegación verde con el texto y el logo en blanco, a pedido de la oficina de prensa del hospital.'),
        'En la búsqueda, aparece siempre una fila <b>«Subí tu orden»</b> que te lleva directo a cargar la orden médica.',
        'Al <b>subir la orden</b>, el aviso de que la lee una IA se muestra una sola vez, y la pista para pegar la imagen ya no menciona solo un atajo de teclado.',
    ]),
    ("15 de octubre de 2026", [
        S('En el panel, si una <b>preferencia</b> de Apariencia no se puede guardar, se reintenta una vez, vuelve al valor anterior y te avisa, en vez de perderse en silencio.'),
        S('En el panel, la ficha del <b>médico</b> que entra por primera vez ya no muestra límites de turnos que no se guardaban, y «Guardar y empezar» no da por guardado un formulario incompleto. Si el email de invitación no sale, la contraseña generada se muestra una sola vez para pasarla en mano.'),
        'Cuando un servicio ya no tiene <b>turnos libres</b>, el asistente te lo dice en el paso de preferencia y te cuenta cuál es el paso siguiente, en vez de prometerte un turno que no existe.',
    ]),
    ("14 de octubre de 2026", [
        'Si entrás desde la fila de un <b>médico</b> con cierre o sin turnos libres, ya no te ofrecemos los horarios de otros médicos del servicio: el asistente te pide la franja y el centro te confirma la fecha, como dice el listado.',
        'Antes de cada charla con el <b>asistente de IA</b> te avisamos que es una IA, y el chat pide confirmación de una persona antes de avanzar. En la entrevista previa, los datos personales se filtran antes de llegar a la IA.',
        S('En el panel, el médico que entra por primera vez <b>revisa sus datos</b>, su foto y su agenda en una guía corta, y tiene una pantalla «Mis agendas». «Solicitudes» salió de su menú.'),
        S('El panel sumó <b>copias de seguridad cifradas</b>: un único archivo con todo, enviado a un almacenamiento externo, con verificación y restauración de prueba desde Sistema.'),
        S('En el panel, el <b>historial clínico de HSI</b> se puede consultar en solo lectura, a pedido, por el administrador y por el médico asignado. No se guarda ni se pasa por la IA.'),
        S('En el panel, guardar un <b>médico</b> cuyo horario lo define el servicio ya no da error, y el editor de un <b>centro</b> muestra la foto guardada y permite quitarla.'),
        S('En las <b>agendas</b>, guardar un servicio ya no ofrece reiniciar a los médicos si su horario no cambió, y el reinicio solo toca a los médicos de ese servicio y de su centro.'),
    ]),
    ("13 de octubre de 2026", [
        S('En el panel, el horario de un servicio tiene ahora <b>un solo dueño</b>: o el servicio, o sus médicos. Al cambiarlo te pedimos confirmación, así no quedan dos horarios que se pisan.'),
        S('Ahora se puede definir con cuántos <b>días de anticipación</b> se saca un turno, por médico, por servicio o por centro. Y en Pacientes de hoy, el calendario marca los días que tienen turnos.'),
        S('Los <b>médicos</b> pueden entrar al panel con una invitación por enlace personal o con usuario y contraseña. Reciben un email al crearles la cuenta, pueden recuperar la contraseña y entrar con su email.'),
        'La <b>verificación</b> «no soy un robot» ya no aparece de entrada en los formularios: solo se pide cuando hace falta.',
        S('En el panel, al <b>guardar</b> un formulario, ningún error pasa en silencio: si la sesión venció o algo no se pudo guardar, te lo avisa.'),
    ]),
    ("9 de octubre de 2026", [
        S('En el panel, al dar de alta un <b>paciente nuevo</b> ahora se piden la fecha de nacimiento y el sexo, que son los datos que HSI necesita. También se lo puede dar de alta desde «Crear turno», sin salir de la pantalla.'),
        S('El <b>lector de DNI</b> de la mesa completa de una vez el número, el apellido, el nombre, la fecha de nacimiento y el sexo, en «Crear turno» y en la ficha de paciente nueva. Antes, con algunos lectores, el dato llegaba cortado.'),
        'En la página de cada <b>médico</b>, el servicio que tiene marcado en su horario es el que manda: ya no aparece «Con orden» ni se saca el turno en otro servicio del mismo grupo.',
    ]),
    ("8 de octubre de 2026", [
        'En la portada, debajo de todo, hay ahora un bloque con enlaces a <b>Médicos</b>, <b>Centros</b> y cada especialidad. En el teléfono se muestran las primeras y el resto se despliega.',
        'La ficha de cada <b>médico</b> enlaza a la página del servicio donde atiende.',
        S('En el panel, el <b>DNI</b> es ahora una columna de Pacientes de hoy: con un clic se copia sin puntos para pegarlo en HSI, y el buscador también encuentra por DNI.'),
    ]),
    ("7 de octubre de 2026", [
        R('Sumamos páginas con información útil, siempre con sus fuentes: <b>vacunación</b> (calendario nacional y bonaerense), vacuna antigripal, chip anticonceptivo, salud reproductiva, certificado único de discapacidad y pensión por discapacidad.'),
        'Cada guía de <b>preparación</b> para un estudio (ayuno, qué llevar, qué avisar) tiene ahora su propia página, con los centros donde hacerlo.',
    ]),
    ("6 de octubre de 2026", [
        'En la página de cada médico, «<b>Cuándo atiende</b>» ahora muestra la semana entera por centro: los días con el mismo horario van juntos, los que no atiende se ven en gris y te marcamos qué día es hoy.',
    ]),
    ("3 de octubre de 2026", [
        X('El menú de arriba quedó más corto: los <b>talleres</b> ahora están junto a los centros (pestañas «Centros» y «Talleres»), el chat es un solo botón «Chat» y el selector de idioma ya no lleva caja.'),
    ]),
    ("2 de octubre de 2026", [
        'Arreglamos un caso en el que el botón de <b>chat</b> no se veía en algunas pantallas porque quedaba escondido dentro del menú: ahora el botón flotante vuelve a estar a mano.',
    ]),
    ("24 de septiembre de 2026", [
        'Arreglamos un caso en el que dos personas podían quedarse con el mismo <b>horario</b> web al confirmar a la vez: ahora se lo queda una sola y la otra vuelve a quedar pendiente.',
        S('En el panel, cuando corregís el <b>DNI</b> de un paciente, su historia clínica y su cuenta lo acompañan al DNI nuevo en vez de quedar con el viejo.'),
    ]),
    ("15 de septiembre de 2026", [
        S('En el panel, cuando un centro tiene un solo <b>médico</b>, la Cola de hoy ya no te hace elegirlo de una lista: vas directo a su agenda del día.'),
    ]),
    ("13 de septiembre de 2026", [
        X('Ajustamos el tamaño de <b>letra</b> en todo el sitio y en el panel: ya no queda texto chiquito difícil de leer, ni en las tarjetas, los formularios o los menús.'),
    ]),
    ("12 de septiembre de 2026", [
        S('En el panel, el <b>calendario</b> de la agenda ahora se lee bien en el celular: encabezados más claros y la grilla se acomoda sola sin pisarse.'),
        S('Los <b>botones</b> y casillas del panel ahora son más grandes y fáciles de tocar en el celular, y las pestañas se pueden deslizar cuando no entran todas juntas.'),
        S('Si tu sesión del panel venció, ahora te lleva directo a la pantalla de <b>login</b> en vez de mostrarte un error.'),
    ]),
    ("11 de septiembre de 2026", [
        'El <b>chat</b> ahora conversa completo en el idioma que elijas: si le escribís en portugués, italiano, francés, alemán, ruso, ucraniano, japonés o chino lo reconoce desde el primer mensaje, y te nombra especialidades, centros y secciones del sitio en ese mismo idioma.',
        'En ruso y ucraniano, el chat ya no te decía mal la <b>fecha</b> de un turno; ahora sale correcta.',
        'Empezar una charla nueva con «<b>Nuevo chat</b>» ahora abre una conversación de verdad, sin traerte de vuelta la charla anterior.',
        'Sumamos una pestaña para que subas tus propios <b>resultados de laboratorio</b>; el médico los ve marcados como no verificados hasta que los confirma.',
        'Al elegir para quién es el turno, ahora te preguntamos primero si es para vos o para otra persona (como un hijo o hija) antes de mostrarte el formulario; en pediatría vamos directo al formulario del chico o chica.',
        'Los mensajes de <b>error</b> del sitio ahora se muestran en tu idioma y con mejor letra.',
    ]),
    ("6 de septiembre de 2026", [
        'Arreglamos un caso en el que algunas <b>fotos</b> de médicos y centros se descargaban en vez de mostrarse en la pantalla.',
        'Arreglamos un caso en el que escribir en el buscador de la <b>portada</b> apenas se abría la página podía borrar lo que estabas tipeando.',
        S('En el panel, los <b>menús desplegables</b> ahora entran bien en el teléfono y se leen completos.'),
    ]),
    ("5 de septiembre de 2026", [
        'Arreglamos un caso en el que la agenda de algunos <b>médicos</b> podía mostrar un horario que ya no estaba disponible en vez del próximo turno real.',
        'En la búsqueda de <b>especialidades</b> y centros, un nombre largo ya no tapa el botón de «Sacar turno» en pantallas grandes.',
    ]),
    ("27 de agosto de 2026", [
        P('La <b>búsqueda</b> de la portada ahora también encuentra páginas de información — telemedicina, control de zoonosis, cómo donar sangre — además de los turnos de siempre.'),
        'Cuando hay un <b>aviso</b> vigente sobre un servicio (por ejemplo, que falta una vacuna puntual), ahora lo vas a ver arriba de la página de ese servicio, con qué falta y qué podés hacer.',
        R('En cualquier parte del sitio donde aparece el <b>107</b> (emergencias), el número ahora es un link: tocalo y tu celular llama directo.'),
        S(P('En el panel, <b>Pacientes</b> muestra primero a quienes tuvieron contacto reciente en vez de todo el padrón entero, y el botón de «Crear turno» y el panorama del día ya están también en Pacientes de hoy.')),
        S(P('Un profesional con rol médico ya puede subir o cambiar su propia <b>foto</b> de perfil desde el panel, sin depender de que otra persona lo haga.')),
        S('Arreglamos la pantalla para activar la verificación en dos pasos (<b>2FA</b>) para que entre bien en la pantalla del celular.'),
    ]),
    ("18 de agosto de 2026", [
        'Al elegir para quién es el turno, ahora aparece el nombre de quien tiene la <b>cuenta</b> abierta (en vez de solo «Yo»), y podés cambiar a otra cuenta ahí mismo si estás usando una computadora o un teléfono compartido, sin perder el horario que ya habías elegido.',
        'En el celular, el <b>chat</b> ya no queda tapado por el teclado: la ventana se acomoda sola para que siempre veas lo que estás escribiendo.',
        'Las listas que te manda el asistente por <b>WhatsApp</b> (por ejemplo, varias especialidades disponibles) ahora se ven prolijas, una debajo de la otra, en vez de amontonadas en el mismo renglón.',
    ]),
    ("9 de agosto de 2026", [
        'En la página de cada <b>médico</b>, el horario de atención agrupa los turnos por centro y consultorio, en vez de repetir esos datos en cada franja.',
        'En la página de un <b>servicio</b>, si la especialidad y el servicio se llaman igual, el camino de arriba ya no repite el mismo nombre dos veces.',
    ]),
    ("8 de agosto de 2026", [
        'El <b>chat</b> te muestra el saludo apenas lo abrís, sin esperar a que cargue tu historial de conversaciones anteriores.',
        'Las tarjetas de «Más buscadas» en la <b>portada</b> ahora suman una flechita que deja claro que llevan a otra pantalla.',
    ]),
    ("7 de agosto de 2026", [
        'El <b>chat</b> ya no puede mostrarte un horario inventado: si te ofrece un turno, es porque lo encontró de verdad en la agenda.',
        'Sumamos un botón de <b>chat</b> flotante, fijo en la pantalla, para abrirlo desde cualquier página sin ir al menú.',
        'El paso a <b>WhatsApp</b> desde el chat ahora vive junto a adjuntar una foto y dictar por voz, en la misma barra donde escribís.',
        R('Los <b>enlaces</b> de todo el sitio ahora se distinguen solo por color, sin subrayado, y el que ya visitaste se ve distinto al que todavía no.'),
    ]),
    ("6 de agosto de 2026", [
        S('Las <b>tablas</b> del panel de gestión (Pacientes, Mis turnos, Usuarios, Médicos, Reportes, HSI, Entrevista previa) comparten ahora la misma vista compacta y prolija de Solicitudes, sin desbordar en el celular.'),
        'En la <b>entrevista previa</b>, cuando una pregunta ya se contesta tocando un botón, el texto no repite las opciones de nuevo.',
        'El <b>chat</b> dejó de repetirse: los horarios que te ofrece aparecen una sola vez, cada uno con el centro donde es y listo para tocar.',
        'Los horarios que te muestra el chat ahora se agrupan por día y dicen arriba de todo a qué <b>especialidad</b> pertenecen, para que no haya dudas de si son de traumatología general o de otro servicio.',
        'Cuando el chat te nombra una <b>especialidad</b>, un servicio o un centro, ese nombre ahora te lleva a su página.',
        'Tu pestaña de <b>laboratorio</b> ahora te cuenta cada informe: qué estudio fue, la fecha en palabras y una lectura en una línea, escrita con cuidado — los números solos no explican nada.',
        'Nueva vista de <b>evolución</b>: tus valores a lo largo del tiempo en gráficos con su banda de referencia, empezando por los que conviene mirar.',
        'Algunos resultados delicados no se muestran en la web: vas a ver «Tu <b>médico</b> tiene tus resultados» y un acceso directo para sacar turno y verlos con él.',
        'Arreglamos un caso en el que el chat mencionaba horarios reales pero no los dejaba elegir con un toque: ahora siempre aparecen como <b>turnos</b> para reservar.',
        'Si le contás al chat un cuadro agudo del mismo día —tos, fiebre, un dolor fuerte, un golpe— ahora te dice primero que corresponde ir a la <b>guardia</b> o a demanda espontánea, antes de ofrecerte un turno programado.',
    ]),
    ("5 de agosto de 2026", [
        'Ahora podés leer todo el sitio en <b>inglés</b>: el buscador, la reserva de turnos, los mails y los WhatsApp te hablan en el idioma que elegiste.',
        S('El <b>panel de gestión</b> también quedó completo en inglés, pantalla por pantalla, para quien del personal lo prefiera.'),
        'La <b>portada</b> carga mucho más rápido: las fotos de los médicos pasaron a ser una sola imagen, y mientras cargan aparece un esqueleto en vez de dejar un hueco vacío.',
        X('Arriba de la portada quedan seis <b>especialidades</b> destacadas con foto, y el listado de abajo ya no repite las mismas.'),
        P('La barra de <b>navegación</b> queda fija arriba al hacer scroll, así el chat está a mano desde cualquier punto de la página.'),
        'Rediseñamos tu pestaña de <b>laboratorio</b>: los resultados se agrupan por informe, y arreglamos un error que dejaba un «Cargando…» flotando en la pantalla.',
        'En «Mis <b>solicitudes</b>» arreglamos un error que borraba el cartel de «¿Cancelar el turno?» a mitad de clic.',
        'El sitio estrenó una nueva paleta de <b>colores</b>, más prolija en toda la pantalla; de paso corregimos que el logo no apareciera en la barra de arriba.',
        S('Activar la verificación en dos pasos (<b>2FA</b>) es más simple: los avisos te llevan directo al botón, y la pantalla muestra primero el código QR para escanear con la cámara.'),
        S('En el panel, la <b>cola</b> de Solicitudes pasó de una pila de tarjetas a una lista más densa con el detalle al costado, para revisar más pedidos de un vistazo.'),
        S('La fila de cada paciente en la consola del médico ahora muestra lo que todavía no sabe —cada cuánto viene, el color de <b>triage</b>, la entrevista previa— en vez de repetir el mismo consultorio en cada renglón.'),
        S(P('Como pidió la contraparte técnica, el médico ya puede dar un <b>hueco</b> libre de su propia agenda con un clic sobre la fila, para ofrecerlo a quien llegó sin turno.')),
        S('La pantalla de <b>Pacientes</b> del panel ahora entra bien en el teléfono, y el filtro por estado dejó de mostrarse cortado.'),
        R('Actualizamos las fotos de DIABET-PIN y del Consultorio Móvil en el directorio de <b>centros</b>.'),
    ]),
    ("4 de agosto de 2026", [
        'Mientras el asistente busca turnos, el chat te va contando qué está <b>haciendo</b> — leyendo tu consulta, buscando en la agenda, revisando disponibilidad — en vez de quedarse en silencio.',
        'Si la conexión se corta mientras el asistente contesta, el chat <b>reintenta</b> solo y te trae la respuesta igual.',
        'Los <b>ejemplos</b> que sugiere el buscador ahora salen de lo que el hospital ofrece de verdad: cada uno encuentra algo, ninguno te deja en una página vacía.',
        S(P('Unificamos el diseño del <b>panel</b> de gestión: el menú, el encabezado y los botones son los mismos en las cerca de veinte pantallas, para que no se sienta armado por partes.')),
        S(P('La pantalla para cargar los <b>horarios</b> del médico es más compacta y clara: cada horario ahora es una sola fila, en vez de una tarjeta larga por cada uno.')),
        'Si tu <b>búsqueda</b> en la portada no encuentra nada, ahora te sugerimos el término más parecido del catálogo — como cuando te corrigen un error de tipeo.',
        'Arreglamos un problema que hacía que el sitio se viera con la <b>tipografía</b> del sistema en vez de la nuestra desde hacía unos días.',
    ]),
    ("3 de agosto de 2026", [
        'La <b>política de privacidad</b> ahora cuenta con todas las letras qué pasa con tus datos cuando interviene la inteligencia artificial: en qué servidores se procesa, qué sale y qué no.',
        'Cuando usás el chat o cuando el médico arma el resumen de tu historia, tu <b>nombre</b>, tu documento, tu teléfono y tu domicilio se reemplazan antes de salir: el servicio que responde ve el contenido, no quién sos.',
        'La <b>foto de la orden</b> sí se manda tal cual, porque leerla entera es justo lo que hay que hacer para poder darte el turno. También está explicado en la política.',
        'Los <b>términos y condiciones</b> se rehicieron y suman una sección sobre inteligencia artificial: nunca diagnostica ni decide, y lo que devuelve siempre lo revisa una persona.',
        'Sumamos una pestaña de <b>resultados de laboratorio</b> a tu cuenta, con tus valores clasificados igual que en el resumen que arma el médico antes de tu consulta.',
        'Ahora es una sola <b>conversación</b> con el asistente, la sigas por la web o por WhatsApp: lo que ya contaste en un canal aparece en el otro, sin repetirlo.',
        'Si la charla con el asistente se estira, ahora guarda <b>memoria</b> de lo primero que contaste en vez de olvidarlo.',
        'Los mails de turno aprobado y de recordatorio ahora tienen un link para sumarlo directo a tu <b>calendario</b>.',
        'La pantalla de después de reservar tu turno ahora dice «Confirmado: te esperamos» en vez de sonar a un reto, y suma un botón para <b>contarle</b> al médico cómo te sentís.',
        'El link del mail para contarle al médico cómo te sentís ahora te lleva directo al chat, sin pedirte <b>iniciar sesión</b>.',
        D('Si entrás sin cuenta a «Mis turnos» o a tu perfil, ahora vas directo a <b>identificarte</b> con tu documento, sin una pantalla extra en el medio.'),
    ]),
    ("2 de agosto de 2026", [
        'El recordatorio del día anterior ahora te pregunta si vas a poder venir, y <b>confirmás</b> con un solo toque desde el mail, sin entrar a tu cuenta.',
        'Si contestás que no vas a poder venir, damos de baja el turno en el momento y queda <b>disponible</b> para otra persona. Si después cambiás de idea, el mismo link te deja avisarnos.',
        'Podés seguir la charla por <b>WhatsApp</b> cuando quieras: el chat tiene el paso a WhatsApp arriba, desde que lo abrís, y se lleva lo que ya venías contando.',
        'Las respuestas del asistente se leen más cómodas: los <b>horarios</b> y los centros ya no van apretados adentro de un recuadro gris.',
        'Si ya estás con tu cuenta abierta en la web, un código de WhatsApp no te vuelve a pedir tu <b>identidad</b>: también te habilita a reservar desde ese teléfono.',
        'El asistente del chat ahora te contesta de a poco, como si estuviera <b>escribiendo</b>, en vez de mostrar la respuesta entera de golpe.',
        'Antes de tu consulta, el resumen que arma el médico ahora también muestra tarjetas con tus últimos valores clínicos (por ejemplo presión o glucemia) y si están dentro de lo <b>esperado</b>.',
        'Desde «Mis turnos», contar o actualizar tus <b>síntomas</b> para el médico se abre ahora en un panel al costado, sin salir de la página.',
        D(R('En Pinamar, para autorizarte ahora te identificás con tu documento a través de <b>RENAPER</b> (el sistema nacional), en vez de subir una foto del DNI.')),
    ]),
    ("1 de agosto de 2026", [
        'Los <b>nombres</b> de los profesionales ya no se muestran en mayúsculas: se escriben como se escribe un nombre.',
        'Ahora podés agregar un turno al <b>calendario</b> aunque el hospital todavía no lo haya confirmado, para bloquear el horario que pediste.',
        'En kinesiología podés tener hasta <b>diez turnos</b> a la vez, porque el tratamiento son varias sesiones. En el resto de los servicios sigue siendo uno por vez.',
        'Si ya hiciste la entrevista previa y querés <b>agregar algo</b>, ahora también podés hacerlo desde «Mis turnos», no solo desde el detalle de la solicitud.',
        'En la entrevista previa ya podés <b>adjuntar una foto</b> (por ejemplo, de una zona de la piel o de un estudio) y dictar tu respuesta por voz, además de escribirla.',
        'Cuando el asistente te hace una pregunta de sí o no (en la entrevista previa o en el chat del sitio), ahora podés tocar la <b>respuesta</b> en vez de escribirla.',
        X('El <b>menú</b> tiene las dos acciones juntas: «Subí tu orden» y «Chateá con IA», que abre el asistente sin salir de la página. En el celular las dos viven adentro del menú.'),
        'La entrevista previa deja en claro que no reemplaza la <b>consulta</b> ni da consejos médicos: lo que contás lo lee el médico durante la consulta.',
        'La entrevista previa ahora es un <b>chat</b> de verdad: burbujas, el médico virtual «escribiendo…», y la conversación queda a la vista cuando termina.',
        'La entrevista previa ahora ocupa toda la <b>pantalla</b>: nada de menú ni de pie de página compitiendo con la charla, solo vos y el chat.',
        'Al escanear el QR de la puerta ya no hace falta escribir nombre y apellido: entrás con tu <b>cuenta</b>, o solo con tu DNI.',
        'Si contás algo urgente mientras esperás en el consultorio, el chat te indica avisar al <b>personal</b> o golpear la puerta — ya no te manda a la guardia estando adentro del hospital.',
        'Los <b>mails</b> que te enviamos ahora se leen mejor: párrafos claros y un botón para la acción principal.',
        'Te ofrecemos la <b>entrevista previa</b> también en el mail de confirmación, en «Mis turnos» y con un recordatorio el día anterior al turno si todavía no la hiciste.',
        'Si algo cambió después de hacer la entrevista previa, podés <b>contarlo de nuevo</b>: desde el detalle de tu solicitud, «Contanos si algo cambió» reabre el chat sin perder lo que ya habías dicho.',
        'Al confirmar tu turno con orden médica, ahora ves la <b>foto de tu orden</b> antes de mandarla, para revisar que sea la correcta.',
        'Antes de tu consulta, el médico ahora arma un <b>resumen</b> automático con lo que ya sabemos de vos (motivo, medicación a confirmar, antecedentes), para no repetir todo de nuevo en el consultorio.',
        S('Si la entrevista previa detecta algo urgente, el personal ahora recibe una <b>alerta</b> destacada para atenderlo más rápido.'),
        'Al revisar tu orden médica, el asistente ahora también controla que tenga <b>fecha</b> y la firma o matrícula del médico; si falta algo, una persona del hospital la revisa antes de aprobarla.',
        'Desde «Mis turnos» ahora podés cancelar un turno o anotarte para que te avisemos si se libera uno antes, directamente desde la <b>tarjeta</b>, sin entrar a otra pantalla.',
        'La entrevista previa ahora te contesta en el mismo <b>idioma</b> en el que le escribís, no siempre en español.',
        'Si ya contaste tu medicación, alergias o antecedentes en una entrevista previa anterior, ahora no te los vuelve a preguntar de cero: te los <b>confirma</b>.',
        'Cuando el chat te sugiere un turno disponible, ahora es un <b>enlace</b> que te lleva directo a reservarlo.',
    ]),
    ("31 de julio de 2026", [
        'Sumamos la <b>entrevista previa</b>: después de reservar tu turno, un chat con inteligencia artificial te hace algunas preguntas sobre tu consulta, para que el médico llegue con más información.',
        S('En «Pacientes de hoy», el personal ve un color de <b>triage</b> y un resumen cuando el paciente ya completó la entrevista previa, con un botón para iniciar la consulta.'),
        S('Nueva consola de <b>consulta</b> para el médico: arma diagnósticos, estudios y medicación, y genera la receta y la orden ya listas para imprimir.'),
        'Quien llega sin haber hecho la entrevista puede completarla escaneando el <b>QR</b> de la puerta del consultorio.',
        S('Quien administra el panel ahora puede editar las <b>preguntas</b> de la entrevista previa y las instrucciones que arma la inteligencia artificial para la receta y la orden.'),
        'Si subís una orden médica emitida en otra provincia o país, te avisamos y te ofrecemos un turno con Medicina general para conseguir una <b>orden</b> válida en este centro.',
        'Los mails y los mensajes de WhatsApp que te enviamos ahora muestran siempre el <b>nombre</b> correcto de este sistema.',
        S('Corregimos un color que hacía parecer un error a algo positivo (por ejemplo, que solés venir a tus turnos); ahora se ve en <b>verde</b>.'),
    ]),
    ("30 de julio de 2026", [
        'El <b>dictado por voz</b> del chat quedó fijo en español, para que no se transcriba mal ni se traduzca lo que decís.',
        'El ícono del <b>micrófono</b> ya no se esconde por las dudas: sigue disponible hasta que el navegador rechaza el permiso de verdad.',
    ]),
    ("29 de julio de 2026", [
        R('Sumamos las instrucciones de <b>preparación</b> a los 120 servicios del catálogo (antes solo estaban en los que piden orden): vacunación, consulta, odontología y salud mental ya no aparecen sin indicaciones.'),
        'Corregimos la zona horaria del sitio: un turno de mañana ya no aparecía por error marcado como de <b>hoy</b>.',
        'El ícono de <b>micrófono</b> del chat ya no aparece si el navegador no permite dictado por voz.',
    ]),
    ("28 de julio de 2026", [
        'Al pedir un turno con orden, el <b>asistente</b> ahora cuenta bien cuántos pasos faltan (ya no dice «paso 1 de 4» si en realidad quedan menos) y solo ofrece cambiar de centro cuando existe otro realmente equivalente.',
        'La pantalla de <b>Confirmá</b> ahora arranca mostrando el turno elegido (fecha, hora y consultorio, con «Cambiar» al lado de cada dato) y ya no hay un cronómetro corriendo: te guardamos el lugar en silencio y solo avisamos, en el botón, si falta menos de un minuto.',
        'Unificamos cómo se guarda y se muestra tu <b>teléfono</b> en todo el sitio; esto corrigió casos en los que el WhatsApp del recordatorio no llegaba a algunos celulares.',
        'El asistente de <b>chat</b> se mantiene enfocado en salud y turnos (no contesta temas ajenos) y sus respuestas incluyen enlaces directos a los servicios o centros que menciona.',
        'Si se corta la conexión justo después de reservar un turno por el chat, ya no se duplica: el asistente recupera la <b>solicitud</b> que ya se había creado.',
        'En la lista de horarios para elegir turno, cada <b>médico</b> ahora muestra su propia especialidad y su propio link «Otras fechas» (antes era uno solo, compartido).',
        S('Las <b>instrucciones</b> que sigue la inteligencia artificial para leer órdenes y responder por chat se pueden editar en español desde el panel de gestión, sin depender de una actualización técnica.'),
        S('El panel de gestión ahora recuerda qué secciones del <b>menú</b> tenías abiertas y en qué pantalla estabas la última vez que entraste.'),
        'Los filtros del sitio (por ejemplo Área) ya no recortan el texto de la opción elegida cuando es larga, ni en el celular ni en el panel de gestión.',
        S('Quienes no tienen la verificación en dos pasos (<b>2FA</b>) activada ya no ven el campo de código al entrar al panel de gestión.'),
        S('Corregimos la vista previa del <b>logo</b> en Configuración, que no mostraba el emblema real que se ve en el sitio.'),
        'El sitio carga bastante más rápido, sobre todo en el <b>celular</b>.',
        S(P('Sumamos un puntaje de <b>asistencia</b> por paciente para el personal de admisión: mide qué tan probable es que la persona venga a su turno, según su historial, sin castigar a quien saca muchos turnos legítimos.')),
        S(P('La lista de <b>Pacientes</b> del panel de gestión suma filtros (estado, asistencia, edad, cobertura) y cada paciente se puede contactar por WhatsApp con un clic.')),
    ]),
    ("27 de julio de 2026", [
        S(P('Quien administra el panel ahora puede pedirle a un usuario que <b>configure de nuevo</b> su verificación en dos pasos (2FA) en su próximo ingreso — para cuando alguien cambia o pierde el teléfono.')),
        'Al elegir horario, el asistente muestra todos los <b>días</b> con turnos de una y el calendario queda abajo, para saltar a otra fecha sin perder la lista.',
        S('El <b>menú</b> del panel de gestión se reagrupó en Atención, Organización y Sistema, con Pacientes y Reportes junto al trabajo del día.'),
        P('Si tu <b>orden</b> es rechazada, el aviso por mail y WhatsApp ahora te dice qué hacer: por ejemplo, te ofrece un turno con Medicina general para sacar una orden nueva, con el horario ya elegido en el link.'),
        'Mejoramos el <b>buscador</b> del sitio: encuentra estudios que antes no aparecían (papanicolaou, hemograma, TSH, entre otros) y avisa con claridad cuando un estudio pedido no se hace en el hospital.',
        'Revisamos que los horarios de un médico correspondan al <b>servicio</b> que estás pidiendo, para no ofrecerte por error el turno de otra especialidad.',
        S(P('El personal del hospital ahora ve, en el horario de cada médico, a qué <b>consultorio</b> y centro corresponde.')),
        S(P('En «Pacientes de hoy», los horarios <b>libres</b> del día se pueden mostrar (apagado de fábrica, con un check) para ofrecerlos a quien llegó sin turno.')),
        S(P('Se puede anotar un turno con solo un nombre o una nota, sin tener la <b>ficha</b> del paciente a mano.')),
        S(P('El DNI se puede <b>escanear</b> en la mesa de admisión, con el lector de código de barras o con una foto.')),
        S(P('Una cuenta del panel de gestión se puede limitar a uno o más <b>centros</b> de salud.')),
        P('Un servicio por orden de llegada puede abrir su turno a distancia el <b>mismo día</b>, para sacarlo antes y ser atendido esa misma jornada.'),
        S('Mejoramos la <b>accesibilidad</b> de «Pacientes de hoy» para el personal que usa lector de pantalla.'),
        'Al elegir el horario del turno, ahora se indica en qué <b>consultorio</b> vas a ser atendido.',
        S('Arreglamos que el panel de gestión mandara a la pantalla de <b>ingreso</b> a alguien que ya tenía la sesión iniciada.'),
        'Al pedir un turno con el horario ya elegido, el asistente llega directo a Confirmá, sin pantallas intermedias que <b>titilaban</b>.',
        S(P('Sumamos el ingreso con <b>passkeys</b> (huella, cara o PIN del dispositivo) al panel de gestión, sin contraseña ni código.')),
        S('Cada usuario del panel de gestión ya puede editar su propio <b>email</b> desde Cuenta.'),
        S(P('Rediseñamos «Pacientes de hoy»: ahora es una sola tabla del día, con el buscador primero (también busca por fecha).')),
        D(R('Si entrás con RENAPER durante el pedido de turno, ya no te vuelve a pedir la <b>orden</b> ni el horario que habías elegido.')),
        'Si el horario que habías elegido ya no está disponible, ahora te avisamos por qué en vez de mandarte de nuevo a elegir sin <b>explicación</b>.',
    ]),
    ("26 de julio de 2026", [
        P('Si tocás «Más horarios» desde la página de un <b>médico</b>, el asistente de turno te lo sigue mostrando primero a él, en vez de la lista completa.'),
        'En los servicios sin turno (por orden de llegada), la página ya no repite el mismo <b>horario</b> dos veces: queda una sola grilla semanal, más clara.',
        'Corregimos que un horario con solo la hora de <b>inicio</b> pareciera tener también una hora de cierre que en realidad no existía.',
        'El selector de turnos por <b>calendario</b> ahora muestra dos meses a la vez, con más lugar para elegir el horario.',
    ]),
    ("25 de julio de 2026", [
        S(P('El panel de gestión suma un perfil de acceso exclusivo para <b>reportes</b>: solo ve los informes y estadísticas, sin acceso a las solicitudes.')),
        S(P('Un servicio, médico o centro con turnos ya dados ya no se borra del catálogo: se <b>deshabilita</b> y deja de ofrecerse para turnos nuevos, sin perder el historial de los turnos que ya tiene.')),
        S(P('En «Pacientes de hoy», el personal ahora ve si el paciente <b>confirmó</b> su asistencia al recordatorio del día anterior.')),
        S(P('El color y el <b>logo</b> del sitio ahora se pueden cambiar desde el panel de gestión, sin depender de una actualización técnica.')),
        S(P('Sumamos una sección «<b>Cuenta</b>» al panel de gestión: cualquier usuario cambia su contraseña y activa la verificación en dos pasos (2FA) sin depender de otra persona, con aviso si todavía falta hacerlo.')),
        S('El <b>menú</b> del panel de gestión se reorganizó en grupos (Atención, Catálogo, Personas, Gestión) para encontrar cada sección más rápido, y sumó «Cerrar sesión» directo ahí.'),
        'Los <b>filtros</b> del buscador (Área, Para) ya no se amontonan: entran en una sola línea en cualquier tamaño de pantalla.',
        '«Subí tu orden» pasa a ser un <b>botón</b> bien visible en el menú, en vez de una tarjeta de texto.',
    ]),
    ("24 de julio de 2026", [
        'Rediseñamos la página «<b>Acerca de</b>» del sitio.',
        P('En las páginas de especialidad, cada médico ahora muestra su <b>especialidad</b> puntual (antes solo se veía en Médicos), y ajustamos cómo se acomodan estas listas en tablet y celular.'),
        S('Nueva sección «Hoja de ruta» en el panel de gestión, con el estado y los próximos pasos del <b>proyecto</b>.'),
        R(S(P('Sumamos cuatro <b>reportes</b> nuevos al panel de gestión: Recupero de costos, SUMAR+, Estadística mensual y Capacidad y temporada.'))),
        S(P('Nueva sección «<b>Pacientes de hoy</b>» en el panel de gestión: el personal ve los turnos del día agrupados por médico y marca «Atendió» o «No vino» con un clic, con buscador y filtro por servicio.')),
        S(P('En el editor de servicios, ahora se puede evitar que un consultorio aparezca en <b>búsquedas</b> que no le corresponden (por ejemplo, que no salga al buscar «PAP» si no lo atiende).')),
        P('Algunos turnos, los que no necesitan revisión del hospital, quedan <b>confirmados</b> al instante apenas elegís el horario, sin esperar aprobación.'),
        X(P('Los turnos ahora se pueden sacar con hasta un mes de <b>anticipación</b> como máximo.')),
        X(P('El recordatorio del día anterior también sale por <b>WhatsApp</b> y te pregunta si vas a poder venir: si contestás que no, el turno se libera solo y se le ofrece a quien esté esperando (falta habilitar el número oficial del municipio).')),
        S('En el panel de gestión, las <b>columnas</b> de cualquier tabla se ensanchan arrastrando el borde del encabezado, y cada persona se queda con el ancho que eligió.'),
        S(P('Sumamos un perfil de acceso para la mesa de <b>admisión</b>, que solo ve los turnos del día y marca presentes.')),
        R(P('Sacamos el Centro de <b>Hidroterapia</b> del catálogo, que ya no está en funcionamiento.')),
        S(P('El personal del hospital ahora puede sacar un <b>turno</b> directamente para un paciente (por DNI o nombre) desde el panel de gestión, incluido un sobreturno cuando hace falta, o desde el calendario del médico.')),
        S('En la ficha de cada paciente, se puede marcar cuál <b>cobertura</b> es la principal y editar los chicos a cargo, incluso si dependen de dos personas.'),
        S(P('El panel de gestión suma una sección de «<b>Pacientes</b>» (alta, edición y archivo), la agenda de cada médico puede indicar qué servicio atiende en cada franja, y ahora también se puede subir una foto para cada centro de salud.')),
    ]),
    ("23 de julio de 2026", [
        'Sumamos un <b>asistente</b> de chat en el sitio para buscar un servicio, ver horarios disponibles y subir tu orden sin salir de la conversación.',
        'Desde el chat podés seguir la conversación por <b>WhatsApp</b> con un link, sin tener que repetir lo que ya contaste.',
        'Al subir tu <b>orden</b> médica, ahora ves una animación mientras la leemos, y «Cómo prepararte» solo aparece cuando tenemos una indicación concreta para tu estudio.',
        'En Médicos sumamos la columna Centro, los <b>retratos</b> se ven más grandes y ya no se repite ninguno; los ejemplos de búsqueda ahora son propios de cada página.',
        S('La sección «Sistema» del panel de gestión también muestra el estado de conexión con <b>HSI</b>, cuántos turnos futuros ya están agendados ahí y los conflictos pendientes.'),
    ]),
    ("22 de julio de 2026", [
        S('Nueva sección «<b>Sistema</b>» en el panel de gestión: muestra qué está conectado (dominio, WhatsApp, email, almacenamiento, inteligencia artificial) y qué falta configurar.'),
        S('En el panel de gestión, los <b>servicios</b> del catálogo se pueden filtrar por centro y por modo (con o sin turno), y los horarios repetidos en varios días se ven agrupados como uno solo.'),
        S('El reporte de <b>Centros</b> deja las estrellas: ahora colorea cada tema según qué tan bien se habla de él y reconoce por nombre a quienes las reseñas agradecen.'),
        S('Las <b>búsquedas</b> de los pacientes pasan a tener su propio reporte, agrupadas por lo que realmente encuentran (especialidades, estudios o personas).'),
        S('Los conflictos de turnos con el sistema del hospital (<b>HSI</b>) ahora se resuelven solos en la mayoría de los casos; la sección «HSI» del panel de gestión solo muestra, en una tabla, los que necesitan que alguien decida.'),
        S('La sesión del panel de <b>gestión</b> dura 30 días y se renueva sola mientras la usás (antes duraba 12 horas).'),
        S('Los reportes de Gestión y Médicos muestran ahora la distribución real de los datos en vez de compararla con un corte inventado, y <b>Pacientes</b> suma la pirámide de edad y el mapa de coberturas.'),
    ]),
    ("21 de julio de 2026", [
        'Ajustamos las tarjetas de «<b>Mis turnos</b>»: todas tus solicitudes vigentes (aprobadas y pendientes) se ven igual, con el médico, el centro y la dirección en la misma línea.',
        S('El panel de gestión del hospital vuelve a llamarse <b>Pinamar Turnos</b> (había pasado a «Admin de Turnos» el día anterior).'),
        S('En el panel de gestión, la <b>selección</b> de varias solicitudes a la vez ya no arranca con casilleros tildados de entrada, para evitar aprobar o rechazar algo por error.'),
        S('El botón «Reintentar» del envío al sistema del hospital (<b>HSI</b>) ahora sí reintenta de verdad, y si vuelve a fallar explica el motivo real en vez de no hacer nada.'),
        S('Al sugerir un <b>médico</b> disponible para una solicitud, el panel de gestión ahora tiene en cuenta si el paciente prefiere mañana, tarde o noche.'),
        S('El reporte de <b>Gestión</b> ahora muestra cuánto tiempo llevan esperando en promedio las solicitudes pendientes, y los de Médicos y Pacientes suman un gráfico de carga y de tendencia mensual.'),
        S('En el login del panel de gestión, tocar Entrar apenas carga la página ya no rebota con un error de verificación anti-bot, y el código de <b>2FA</b> se pide en su propia pantalla.'),
    ]),
    ("20 de julio de 2026", [
        S('El panel de gestión de solicitudes para el personal del hospital pasa a llamarse <b>Admin de Turnos</b>.'),
        S('Los <b>reportes</b> ahora abren en un Resumen con una tarjeta de acceso a cada sub-reporte (Gestión, Médicos, Pacientes, Centros, Costos de IA y Panel de login).'),
        S('Sumamos un nuevo sub-reporte de <b>Centros</b>, con el análisis de las reseñas reales de Google de cada centro de salud.'),
        S('Los reportes de <b>Gestión</b> ahora muestran en qué franja y centro rendiría más una hora de atención, cuándo llega más gente y cuántas horas de personal se ahorran.'),
        S('En el panel de gestión, el horario de cada solicitud se elige de una lista simple que ya tiene en cuenta la franja que prefiere el paciente, y el botón «Resolver según <b>sugerencia</b>» aprueba o rechaza de una las solicitudes que ya la tienen.'),
        S('El <b>calendario</b> de cada médico en el panel de gestión se ve coloreado según qué tan ocupado está cada día.'),
        'Arreglamos que a veces la verificación <b>anti-bot</b> de «Subí tu orden» se trabara sin decir el motivo real de la falla.',
        'Si el análisis de tu <b>orden</b> tarda demasiado, ahora te avisamos en vez de dejarte esperando para siempre.',
    ]),
    ("19 de julio de 2026", [
        'Arreglamos que el <b>DNI</b>, la fecha de nacimiento o el teléfono se vieran cortados en los formularios de la cuenta.',
        'Rediseñamos «Mis turnos»: tu próximo turno aprobado se destaca arriba con la foto del médico, la dirección con mapa y los botones para agregarlo al <b>calendario</b>, reprogramarlo o cancelarlo; el resto de tus solicitudes se agrupa en tarjetas según su estado.',
        S('En el panel de gestión, cada dato de la solicitud aparece una sola vez, «Derivar a» y «<b>Médico</b>» se completan escribiendo (con la lista filtrada por especialidad), y un laboratorio ya no pide asignar médico.'),
        S(P('El hospital ahora puede elegir, desde su panel de Configuración y sin depender de un redeploy técnico, cómo los pacientes confirman su identidad para sacar turno (contraseña o RENAPER) y si la verificación <b>anti-bot</b> está activa.')),
    ]),
    ("18 de julio de 2026", [
        D('Arreglamos un error que podía impedir crear una cuenta nueva al <b>registrarte</b>.'),
        P('Si acumulás varias <b>ausencias</b> a turnos aprobados, tu próxima solicitud la tenés que hacer presencialmente en el hospital o el centro de salud, no por la web.'),
        S(P('El hospital ahora registra si te presentaste o no a cada turno aprobado, con el historial de quién lo marcó y cuándo.')),
        'Corregimos que a veces se mostrara en «Mis turnos» una <b>hora</b> de turno distinta a la real.',
        'Si ya elegiste un horario pero tu solicitud sigue pendiente de aprobación, también aparece en tu <b>calendario</b> (marcada como «pendiente»).',
        'Si estás en la <b>lista de espera</b>, ahora te podemos ofrecer un turno libre hasta la hora misma en que empieza (antes el corte era media hora antes).',
        'Al pedir un turno, el paso de «Confirmá» se lee como una <b>frase</b> (servicio, centro, paciente y DNI) en vez de una lista de datos sueltos, y tu DNI se escribe con puntos para que sea más fácil de leer.',
        D('La <b>contraseña</b> de tu cuenta pasa a tener un mínimo de 8 caracteres, al registrarte o al cambiarla.'),
        D('Ya podés corregir tu <b>DNI</b> desde Mi cuenta si lo cargaste mal (antes quedaba fijo).'),
        'Cada sección de Mi cuenta (Datos, Cobertura, Contacto) tiene su botón «<b>Guardar cambios</b>» con confirmación.',
        'La fecha de nacimiento de «Personas a cargo» se completa igual que la tuya: en palabras, con calendario.',
        'Si tenés una foto asociada a tu email (<b>Gravatar</b>), tu cuenta la muestra sola; y al subir o sacar tu foto de perfil, el avatar del menú se actualiza al toque.',
        'En «Mis turnos», el estado de cada solicitud se resume en una frase clara y se ve el <b>médico</b> asignado en la fila del turno.',
        'Cada solicitud en «Mis turnos» tiene el botón que corresponde (Cambiar horario, Elegir horario o Reprogramar) y «Agregar al calendario» junto a la fecha.',
        X('El menú del sitio ahora empieza con <b>Médicos</b> (antes Estudios), y «Mi cuenta» pasa a ser solo tu foto o tus iniciales.'),
        'En Estudios, el <b>centro</b> de cada fila es ahora un link directo, y «+N» muestra los demás centros donde también se hace.',
        'Médicos ya se puede filtrar por especialidad y para quién <b>atiende</b>, igual que Estudios.',
        'Las páginas de Acerca de, Privacidad y Términos ahora tienen el mismo <b>menú</b> y colores que el resto del sitio.',
        S('Si el sistema del hospital (<b>HSI</b>) está caído cuando se aprueba un turno, ahora se sincroniza solo apenas vuelve, sin que el personal tenga que reintentarlo a mano.'),
        S(P('El personal que arma las <b>agendas</b> ahora puede bloquear solo un rango de horas puntual, cerrar varios días seguidos de una vez (vacaciones, licencias) y reservar cupos internos que no se ofrecen por la web.')),
        S('El panel de gestión de solicitudes tiene una barra única con los filtros por estado y el buscador, y sugiere un <b>médico</b> disponible para asignar según el horario elegido.'),
        S('Los datos que leyó la inteligencia artificial en la orden (nombre, DNI, obra social) se pueden corregir ahí mismo si hace falta, sin rechazar la solicitud.'),
        S('El inicio de sesión del panel de gestión (personal del hospital) suma un costado con <b>humor</b> médico curado y consejos de uso, distinto cada día.'),
    ]),
    ("17 de julio de 2026", [
        'El link para agregar tu turno al <b>calendario</b> ahora te guía según tu aplicación (Google, Apple, Outlook) y copia la dirección para pegar, en vez de descargar un archivo.',
        P('Si todavía no tenés la <b>orden</b>, te mostramos todos los consultorios de Clínica y Medicina general disponibles para elegir dónde sacar el turno, no uno solo.'),
        'La <b>foto de perfil</b> de tu cuenta se ve más grande y no hace falta volver a subirla cada vez que entrás.',
        S('Sacamos la opción de avisos por <b>SMS</b>, que nunca llegaba a mandarse.'),
        'Tu fecha de <b>nacimiento</b> se muestra escrita en palabras («28 de octubre de 1978») en vez de números.',
        'Al <b>reprogramar</b> un turno, ahora solo elegís el nuevo horario — no hace falta volver a subir la orden.',
        R('Corregimos la <b>foto</b> de un médico del directorio que no le correspondía.'),
        'En Mi cuenta, «<b>Mis turnos</b>» pasa a ser lo primero que ves si ya tenés turnos, con Reprogramar y Cancelar en cada fila.',
        M('Cobertura, Contacto y el <b>Asistente de IA</b> ahora son secciones separadas dentro de Mi cuenta, en vez de estar todas juntas.'),
        S(P('El personal que arma las <b>agendas</b> ya puede editar servicios y médicos del catálogo desde el panel de gestión, sin depender de la Secretaría para cada cambio.')),
        'En la lista de <b>médicos</b> ya se ve el link «Más horarios» al lado del turno, como en el resto de las listas.',
        'Cada <b>búsqueda</b> que hacés en Médicos o Estudios queda guardada en el link, así la podés compartir o volver a abrirla tal cual.',
        'Al registrarte, tu <b>obra social</b> se muestra con el nombre que usás vos (Swiss Medical, IOMA, PAMI...) en vez del nombre completo del padrón oficial.',
        S('La cola de solicitudes del panel de gestión quedó más clara, con menos repetición y accesos rápidos para aprobar o rechazar.'),
    ]),
    ("16 de julio de 2026", [
        D('Podés agregar una <b>foto de perfil</b> a tu cuenta — recortada de tu DNI o una que subas vos.'),
        S('El panel de gestión del hospital sumó <b>Reportes</b>: turnos y demanda por especialidad, rechazos, búsquedas más frecuentes y el costo de la IA.'),
        'Si el asistente de <b>IA</b> no anda un rato, podés seguir subiendo tu orden igual — queda para que la revise el hospital a mano.',
        '<b>Talleres y grupos</b> se ven ahora como tarjetas con toda la info (horario, lugar, para quién) sin tener que entrar a otra página.',
        'Arreglamos que en el <b>celular</b> no se viera el link «Más horarios» al lado del turno.',
        'La página de cada <b>centro de salud</b> se reorganizó: fotos y consejos de vecinos arriba, y una sola lista de servicios con buscador.',
        S('El panel de gestión mejoró la ficha de cada <b>médico</b>: sus horarios, calendario de turnos aprobados y datos, todo en pestañas.'),
        P('Si cancelás un turno aprobado, se lo ofrecemos a quien esté en la <b>lista de espera</b> — y si esa oferta por email ya venció, te mostramos el próximo turno libre en vez de un error.'),
        P('Al registrarte buscamos tu <b>obra social</b> automáticamente por tu DNI (podés declarar otras a mano o tu prepaga, y cargar tu número de afiliado).'),
        'El turno que elegís en el asistente queda <b>reservado unos minutos</b> mientras completás la solicitud, para que no te lo saque otra persona.',
        D('Te podés registrar sacándole una <b>foto al DNI</b>: leemos tus datos automáticamente, frente y dorso, incluida tu dirección.'),
        S(P('Los <b>médicos</b> ya pueden iniciar sesión y ver sus propios turnos aprobados.')),
        '«<b>Subí tu orden</b>» en el menú: subís la orden que te dio tu médico y te decimos qué turno sacar y cómo prepararte — y el turno sale con la orden ya cargada.',
        P('Al enviar una solicitud te llega un <b>email con el detalle</b> (turno elegido, centro y preparación), y otro de recordatorio <b>el día antes del turno</b>.'),
        '<b>Grupos y talleres</b> pasa a ser una tarjeta de «Más buscadas» en la portada.',
        R('El buscador muestra <b>ejemplos para probar</b> (Cardiología, Ecografía, Ostende…): un toque y ves qué encuentra.'),
        'Esta página de <b>novedades</b>, linkeada desde el pie del sitio.',
        'El buscador ya entiende <b>profesiones</b>: buscá «cardiólogo» y aparece Cardiología, aunque el catálogo diga distinto.',
        R(P('Sumamos la <b>matrícula profesional</b> verificada a más médicos.')),
        R('Se dieron de baja los médicos de demostración: en el directorio quedan solo <b>profesionales reales</b>.'),
        S('El panel de gestión del hospital estrena <b>menú lateral</b> y una pantalla de inicio de sesión propia.'),
    ]),
    ("15 de julio de 2026", [
        '«<b>Más horarios</b>» lleva directo a elegir el turno; la orden se sube después.',
        'Al tocar un horario el asistente <b>avanza solo</b>, sin botón «Continuar» de más.',
        R(P('Actualizamos las agendas con la <b>planilla completa</b> de la Secretaría de Salud (versión final del 14 de julio).')),
        R(P('Se sumaron <b>nuevos servicios</b> al catálogo: nutrición, psicomotricidad, psicopedagogía y salud mental.')),
        'El listado se ve prolijo en el <b>celular</b>: cada fila queda ordenada, sin desplazar la pantalla de un lado a otro.',
        'El botón <b>Atrás</b> del navegador ya funciona bien en cualquier paso del asistente de turno.',
    ]),
    ("14 de julio de 2026", [
        '<b>Un solo botón de turno</b> en todo el sitio: dice la acción y la fecha («Sacar mañana, 13:00») y el color avisa qué tan pronto es.',
        'El turno que tocás en un listado <b>llega ya elegido</b> al asistente: no hay que volver a buscarlo.',
        'Los listados ordenan por <b>popularidad</b>: primero lo que más horas se atiende por semana.',
    ]),
    ("13 de julio de 2026", [
        X('<b>Estudios</b> pasa a ser lo primero del menú.'),
        R('Un estudio que se hace en varios centros se elige por <b>espera</b>, no por cercanía: están a 10 minutos entre sí, conviene el que atiende antes.'),
        'El turno que muestra cada página es <b>el primero, no el único</b>: al lado, «Más horarios».',
    ]),
    ("12 de julio de 2026", [
        R(P('Cargamos el <b>Detalle de turnos oficial</b> de la Secretaría de Salud: 145 prestaciones con sus profesionales, consultorios y agendas reales.')),
        P('<b>Turnos programados sin orden</b>: los servicios que no piden papeles se solicitan directo.'),
        '<b>Hijos a cargo</b>: desde tu cuenta sacás turnos pediátricos para tus hijos.',
        D('Si te olvidás la contraseña, la <b>recuperás por email</b>.'),
        P('Cada servicio tiene su <b>página informativa</b> (dónde, cuándo, preparación); el pedido de turno va aparte, paso a paso.'),
    ]),
    ("11 de julio de 2026", [
        '<b>Cómo prepararte para tu estudio</b>: subís la orden y te decimos la preparación que corresponde (ayuno, vejiga llena, estudios previos).',
        '<b>Calendario</b>: agregá tu turno aprobado a Google Calendar o al iPhone, o suscribite al feed con todos tus turnos.',
        'Tu cuenta: <b>datos editables</b> que se guardan solos.',
        S(P('El hospital puede <b>editar el contenido</b> del sitio (centros, servicios, médicos y agendas) sin tocar código.')),
    ]),
    ("10 de julio de 2026", [
        '<b>Página propia de cada médico</b>, con su foto y cuándo atiende.',
        P('Servicios sin turno: grilla semanal de atención y estado en vivo («<b>Atiende ahora</b> hasta las 12:00»).'),
        'Páginas de centros con <b>fotos, reseñas y consejos</b> de vecinos.',
        'Pedido de turno <b>rediseñado para mayores</b>: texto grande, una acción por pantalla, y la foto de la orden se puede pegar con Ctrl+V.',
    ]),
    ("9 de julio de 2026", [
        R('<b>Turnos en vivo de Mi Salud Digital</b> al elegir el horario.'),
        '<b>Espera aproximada</b> en todas las listas; con varios centros, primero el que atiende antes.',
        R('<b>Fotos reales</b> de los profesionales en el directorio.'),
    ]),
    ("8 de julio de 2026", [
        P('<b>Nace <a href="https://humanrounds.org/" target="_blank" rel="noopener">Human Rounds</a></b>, la plataforma detrás de Pinamar Turnos: los servicios del Hospital de Pinamar y los CAPS, con solicitud de turno por orden médica.'),
        P('Tu cuenta se crea con el <b>DNI</b>, la misma clave que usa el hospital.'),
        P('La orden que subís se <b>lee automáticamente</b> para que el hospital la revise más rápido.'),
        P('Los turnos aprobados quedan en la <b>historia clínica</b> del hospital.'),
    ]),
]


# Machine translation of the changelog entries (translate.py, not i18n.py —
# this is long-tail editorial prose, not a short curated UI string). Entries
# carry inline <b>/<a href> markup; asking a model to reproduce it verbatim
# drifts sooner or later, so every tag is swapped for an opaque [[N]] token
# before the text goes to the translator and swapped back after. A response
# that lost or reordered a token is easy to detect and never ships as broken
# markup — it degrades to the Spanish original, same as a cold cache
# (the product owner's decision, 2026-08-04: translate everything, machine + cache, never
# hide the page).
NEWS_SOURCE_LANG = "es"  # _NEWS is literal Spanish in the code, independent
# of whatever language the active data pack itself happens to be written in.

_NEWS_TAG_RE = re.compile(r"<[^>]+>")


def _news_tokenize(html_text):
    """'<b>Hola</b>' -> ('[[1]]Hola[[2]]', ['<b>', '</b>'])."""
    tags = []

    def _stash(m):
        tags.append(m.group(0))
        return f"[[{len(tags)}]]"

    return _NEWS_TAG_RE.sub(_stash, html_text), tags


def _news_detokenize(text, tags):
    for n, tag in enumerate(tags, 1):
        text = text.replace(f"[[{n}]]", tag)
    return text


_NEWS_TOKEN_RE = re.compile(r"\[\[(\d+)\]\]")


def _news_tokens_intact(text, count):
    """Every token present EXACTLY once, in the SAME order as the original
    — not just "each number appears somewhere". A model that drops a token
    is the obvious failure; one that duplicates or reorders one is just as
    dangerous (a duplicated `[[4]]` would detokenize into two closing
    `</b>`s; a swapped `[[1]]`/`[[4]]` would open a tag where it should
    close one) and a simple membership check would miss both."""
    found = [int(n) for n in _NEWS_TOKEN_RE.findall(text)]
    return found == list(range(1, count + 1))


def news_translation_entries():
    """[(source_key, tokenized_text)] for every _NEWS item — swept by
    scripts/translate_pack.py and translate_warmup.py to fill translate.py's
    cache. Keyed by date slug + position within that date's list: stable
    across a text EDIT (the row gets replaced, not orphaned), which holds
    because novedades entries are appended, never reordered within a date —
    see the novedades-changelog skill; the 2026-07-08 entry is explicitly
    frozen."""
    out = []
    for fecha, items in _NEWS:
        slug = site_base._slug(fecha)
        for idx, item in enumerate(items):
            tokenized, _tags = _news_tokenize(item)
            out.append((f"news.entry.{slug}.{idx}", tokenized))
    return out


def _news_localized(fecha, idx, item, lang):
    """One changelog entry in the visitor's language. Reads ONLY the cache
    (translate.cached_text — never localized_text: ~150 sequential model
    calls would sit in front of the first byte). A request for the source
    language, a cold cache, or a translation that lost a token all degrade
    to the Spanish original — never to broken markup."""
    def deployment_text(text):
        # This changelog predates white-label packs and retains three
        # historical reference-deployment nouns. Render those through the
        # active installation instead of publishing Pinamar metadata from a
        # fresh checkout.
        city = _html.escape(str(site_identity.get("city") or "la localidad"))
        hospital = _html.escape(str(
            site_identity.get("hospital") or "el hospital"
        ))
        brand = theme.brand_name()
        if brand.strip().lower() == "human rounds":
            # The site IS branded with the platform's name: «la plataforma
            # detrás de Human Rounds» says nothing, so the site gets named
            # by what it is instead.
            brand = i18n.t_or("news.site_phrase", "los turnos de salud de {city}",
                              lang, city=city)
        else:
            brand = _html.escape(brand)
        # Last line: whatever translation spelled the place differently
        # («Pinamar Hospital») still never names it on another installation.
        return (str(text)
                .replace("Pinamar Turnos", brand)
                .replace("Hospital de Pinamar", hospital)
                .replace("Pinamar", city))

    if lang == NEWS_SOURCE_LANG:
        return deployment_text(item)
    key = f"news.entry.{site_base._slug(fecha)}.{idx}"
    tokenized, tags = _news_tokenize(item)
    translated = translate.cached_text(key, tokenized, lang, pack_lang=NEWS_SOURCE_LANG)
    if translated == tokenized or not _news_tokens_intact(translated, len(tags)):
        return deployment_text(item)
    return deployment_text(_news_detokenize(translated, tags))


# "4 de agosto de 2026" -> "August 4, 2026" for a non-Spanish visitor. Reuses
# the SAME date.month_full.<n> catalog i18n.py already keeps for
# _friendly_date() (one formatter per datum — no second month-name table),
# and the Spanish page never calls this at all (early return below), so its
# date headers stay the literal cache key, byte for byte.
_NEWS_FECHA_RE = re.compile(r"(\d{1,2}) de (\w+) de (\d{4})")


def _news_fecha_localized(fecha, lang):
    if lang == NEWS_SOURCE_LANG:
        return fecha
    m = _NEWS_FECHA_RE.match(fecha)
    if not m:
        return fecha
    day, mes, year = m.groups()
    try:
        month_num = site_schedule._MONTH_FULL.index(mes) + 1
    except ValueError:
        return fecha
    month = i18n.t_or(f"date.month_full.{month_num}", mes, lang)
    # Default here is dead in practice (the `lang == NEWS_SOURCE_LANG` early
    # return above always wins for Spanish), but kept as "<castellano>" per
    # every other t_or() call site in this file.
    return i18n.t_or("news.date_long", "{day} de {month} de {year}", lang,
                      month=month, day=day, year=year)


def _news_tabs(solo_pedidos, lang):
    """Todas | A pedido: two real pages (/news, /news/pedidos), so two plain
    links with aria-current, the same `.subtabs` the centers page uses —
    not a checkbox that navigates (demo review 2026-11-15)."""
    tabs = ((False, "/news", i18n.t_or("news.filter.all", "Todas", lang)),
            (True, "/news/pedidos", i18n.t_or("news.filter.requested", "A pedido", lang)))
    links = "".join(
        f'<a href="{href}"' + (' aria-current="page"' if key == solo_pedidos else "")
        + f">{site_base._esc(label)}</a>" for key, href, label in tabs)
    return (f'<nav class="subtabs nv-tabs" aria-label="'
            f'{site_base._esc(i18n.t_or("news.tabs", "Filtrar novedades", lang))}">'
            f"{links}</nav>")


def _news_section(fecha, indexed, lang):
    """One date. A single item is a paragraph, not a one-bullet list."""
    texts = [_news_localized(fecha, idx, item, lang) for idx, item in indexed]
    if len(texts) == 1:
        inner = f"<p>{texts[0]}</p>"
    else:
        inner = "<ul>" + "".join(f"<li>{t}</li>" for t in texts) + "</ul>"
    return f'<section class="nv"><h2>{_news_fecha_localized(fecha, lang)}</h2>{inner}</section>'


# How many items the open part of /news shows at least before the older
# months fold away. The whole history open was ~40,000 px on a phone: a
# full-page capture of it at 3x came out garbled (demo review 2026-11-15,
# P9/D4), and nobody scrolls four months of entries to reach the footer.
_NEWS_OPEN_MIN_ITEMS = 8


def _news_month_label(fecha, lang):
    m = _NEWS_FECHA_RE.match(fecha)
    if not m:
        return fecha
    _day, mes, year = m.groups()
    try:
        month_num = site_schedule._MONTH_FULL.index(mes) + 1
    except ValueError:
        return fecha
    month = i18n.t_or(f"date.month_full.{month_num}", mes, lang)
    label = i18n.t_or("news.month_long", "{month} de {year}", lang,
                      month=month, year=year)
    return label[:1].upper() + label[1:]


def _news_months(fechas, lang, open_all=False):
    """Dates grouped by month, newest first. The newest month stays open
    (and the next ones while fewer than _NEWS_OPEN_MIN_ITEMS items show);
    each older month is one closed <details> the visitor opens."""
    months = []
    for fecha, indexed in fechas:
        key = _NEWS_FECHA_RE.match(fecha)
        key = key.group(2, 3) if key else fecha
        if not months or months[-1][0] != key:
            months.append((key, fecha, []))
        months[-1][2].append((fecha, indexed))
    out = []
    shown = 0
    for _key, first_fecha, dates in months:
        html = "".join(_news_section(f, ix, lang) for f, ix in dates)
        if open_all or shown < _NEWS_OPEN_MIN_ITEMS:
            out.append(html)
            shown += sum(len(ix) for _f, ix in dates)
        else:
            label = site_base._esc(_news_month_label(first_fecha, lang))
            out.append(f'<details class="nv-mes"><summary>{label}</summary>{html}</details>')
    return "".join(out)


def _page_news(solo_pedidos=False):
    # «A pedido» has its own URL (/news/pedidos) so the link can be shared
    # already filtered. Deliberately terse: says what it filters without
    # explaining who the request came from (the product owner, 2026-07-27).
    lang = i18n.active()
    reference = _reference_deployment()
    off = _portal_off_features()
    fechas = []
    any_requested = False
    for fecha, items in _NEWS:
        # Keep the ORIGINAL position: it is the item's translation key.
        indexed = [(i, it) for i, it in enumerate(items) if _public(it, reference, off)]
        any_requested = any_requested or any(
            getattr(it, "requested", False) for _i, it in indexed)
        if solo_pedidos:
            indexed = [(i, it) for i, it in indexed if getattr(it, "requested", False)]
        if indexed:
            fechas.append((fecha, indexed))
    secs = _news_months(fechas, lang, open_all=solo_pedidos)
    # A lone tab is not a choice: no requested item, no tab bar.
    tabs = _news_tabs(solo_pedidos, lang) if any_requested else ""
    body = (
        '<section class="sec nv-page">'
        f'<h1>{i18n.t_or("news.h1", "Novedades", lang)}</h1>'
        f"{tabs}{secs}</section>"
    )
    return site_shell._shell(
        f'{i18n.t_or("news.title", "Novedades", lang)} | {theme.brand_name()}', "", body,
        desc=i18n.t_or(
            "news.desc",
            "Historia de actualizaciones de {brand}: qué agregamos y mejoramos, semana a semana.",
            lang, brand=theme.brand_name()),
        canonical="/news/pedidos" if solo_pedidos else "/news",
        # Never indexed (SEO audit 2026-10-01, Pinamar item 4): a changelog
        # has no search demand. Reachable from the footer, out of the index
        # and of the sitemap. Panel-only entries are marked S(...) and stay
        # off this page (demo review 2026-11-15).
        index=False,
    )


# ---- informational search results (2026-08-27 hospital meeting) -----------
#
# Telemedicina / Zoonosis / Donar sangre: plain informational pages the
# search box can lead to (catalogo-y-datos.md's "enlaces" pack file). Each
# page's CONTENT (what the service is, who it's for) comes from that entry's
# `pagina` object in the active pack — the reference-deployment counterpart can edit the words with no
# deploy, same spirit as estudios-preparacion.json. Chrome strings (headings,
# the "not for emergencies" label, generic CTA text) go through i18n with a
# Spanish fallback, same as every other page in this file; a pack that hasn't
# supplied a `pagina` yet still renders a sensible page off those fallbacks.

def _facts_html(items):
    return '<ul class="facts">' + "".join(f"<li>{site_base._esc(p)}</li>" for p in items) + "</ul>"


def _emergency_notice_html(lang, text=""):
    """«No es para emergencias», with the installation's ONE emergency number
    (`site_identity.emergency_number()`, the footer's) as a tap-to-call link.
    `text` is the pack's own wording (`no_es`, placeholders already resolved
    by `_links`); without it the engine's sentence carries `{number}`. The
    notice is in the theme's own text colour on a panel — never brown on
    yellow (demo review 2026-11-15, D10/D32)."""
    number = site_identity.emergency_number()
    if not text:
        text = i18n.t_or("telemedicine.emergency_call",
                         "Si es una urgencia, andá a la guardia del hospital o llamá al {number}.",
                         lang, number=number)
    body = site_base._esc(text)
    if number:
        esc_number = site_base._esc(number)
        link = f'<a class="stz-link" href="tel:{site_base._esc(format.tel_href(number))}">{esc_number}</a>'
        body = re.sub(r"(?<!\d)" + re.escape(esc_number) + r"(?!\d)", lambda _m: link, body, count=1)
    label = i18n.t_or("telemedicine.not_for_label", "No es para emergencias.", lang)
    return f'<p class="info-alert"><b>{site_base._esc(label)}</b> {body}</p>'


def _real_target(href):
    """An absolute https target that is not a placeholder domain (`.example`,
    RFC 2606), or a `tel:` with digits: the only kinds of link a page may
    turn into a button (plain http is refused: a patient's consultation does
    not go over an unencrypted link)."""
    href = str(href or "").strip()
    if href.startswith("tel:"):
        digitos = format.tel_href(href[len("tel:"):])
        return "tel:" + digitos if len(digitos.lstrip("+")) >= 3 else ""
    if not href.startswith("https://"):
        return ""
    host = href[len("https://"):].split("/", 1)[0].split(":", 1)[0].lower()
    return "" if (not host or host.endswith(".example")) else href


def _page_telemedicine():
    """/telemedicine. Order (demo review 2026-11-15, D16/D32): what it is in
    one sentence, then «No es para emergencias» with the footer's number —
    above the fold on a phone, before any action — then who may use it and
    what it costs when the pack says so, then the button (only when the pack
    names a real target: the demo has none, so it shows none), then when to
    use it and when not. One name, «telemedicina»; no footnote under the
    button — a requirement the pack states (`cta_nota`) is one more fact."""
    lang = i18n.active()
    pagina = (site_centers._link_by_id("telemedicina") or {}).get("pagina") or {}
    h1 = i18n.t_or("telemedicine.h1", "Telemedicina", lang)
    lede = pagina.get("lede") or i18n.t_or(
        "telemedicine.lede_default",
        "Una consulta por videollamada con un médico generalista, las 24 horas.",
        lang)
    para = [p for p in (pagina.get("para") or []) if p]
    no_para = [p for p in (pagina.get("no_para") or []) if p]
    pasos = [p for p in (pagina.get("pasos") or []) if p]
    datos = [str(pagina.get(k) or "").strip() for k in ("quien", "costo", "cta_nota")]
    datos = [d for d in datos if d]
    cta_href = _real_target(pagina.get("cta_href"))
    cta_texto = pagina.get("cta_texto") or i18n.t_or("telemedicine.cta", "Consultá por telemedicina", lang)

    secs = ""
    if para:
        secs += (f'<section class="campaign-section"><h2>{i18n.t_or("telemedicine.for_h", "Cuándo usarla", lang)}</h2>'
                 + _facts_html(para) + "</section>")
    if no_para:
        secs += (f'<section class="campaign-section"><h2>{i18n.t_or("telemedicine.not_for_h", "Cuándo necesitás atención presencial", lang)}</h2>'
                 + _facts_html(no_para) + "</section>")
    if pasos:
        secs += (f'<section class="campaign-section"><h2>{i18n.t_or("telemedicine.how_h", "Cómo funciona", lang)}</h2>'
                 '<ol class="campaign-steps">' + "".join(f"<li>{site_base._esc(p)}</li>" for p in pasos) + "</ol></section>")
    notice = _emergency_notice_html(lang, str(pagina.get("no_es") or "").strip())
    datos_html = _facts_html(datos) if datos else ""
    cta = (f'<div class="campaign-action"><a class="stz-cta" href="{site_base._esc(cta_href)}" target="_blank" rel="noopener">{site_base._esc(cta_texto)}</a></div>'
           if cta_href else "")

    body = f"""
  <div class="campaign-page">
  <h1 class="h1">{site_base._esc(h1)}</h1>
  <p class="lede">{site_base._esc(lede)}</p>
  {notice}{datos_html}{cta}
{secs}
  </div>
"""
    return site_shell._shell(f"{h1} | {theme.brand_name()}", "", body, desc=lede, canonical="/telemedicine")


def _page_zoonosis():
    lang = i18n.active()
    enlace = site_centers._link_by_id("zoonosis") or {}
    pagina = enlace.get("pagina") or {}
    # The center this page stands for (`"centro"` in enlaces.json): its
    # /center/ page answers 301 here, so its address (with the map), email
    # and photo live on this page. The page's own contact data wins; the
    # center fills what the page does not say.
    centro = site_centers._centers_by_slug().get(str(enlace.get("centro") or "").strip()) or {}
    h1 = i18n.t_or("zoonosis.h1", "Zoonosis", lang)
    lede = pagina.get("lede") or i18n.t_or(
        "zoonosis.lede_default",
        "El Centro de Salud Animal y Zoonosis de la Municipalidad.", lang)
    servicios = [p for p in (pagina.get("servicios") or []) if p]
    castracion = [p for p in (pagina.get("castracion") or []) if p]
    roedores = [p for p in (pagina.get("roedores") or []) if p]
    mordedura = pagina.get("mordedura") or ""
    nota = pagina.get("nota") or i18n.t_or(
        "zoonosis.nota_default",
        "Es un servicio sanitario municipal — los turnos para mascotas no se sacan por este portal.",
        lang)
    whatsapp = str(pagina.get("whatsapp") or centro.get("whatsapp") or "").strip()
    telefono = str(pagina.get("telefono") or centro.get("phone") or "").strip()
    direccion = str(pagina.get("direccion") or centro.get("address") or "").strip()
    email = str(centro.get("email") or "").strip()
    oficiales = [e for e in (pagina.get("enlaces_oficiales") or []) if e.get("href")]

    secs = ""
    if servicios:
        secs += (f'<section class="campaign-section"><h2>{i18n.t_or("zoonosis.services_h", "Servicios para animales y vecinos", lang)}</h2>'
                  '<ul class="facts">' + "".join(f"<li>{site_base._esc(p)}</li>" for p in servicios) + "</ul></section>")
    if castracion:
        secs += (f'<section class="campaign-section"><h2>{i18n.t_or("zoonosis.neuter_h", "Castrar también es cuidar el barrio", lang)}</h2>'
                  '<ul class="facts">' + "".join(f"<li>{site_base._esc(p)}</li>" for p in castracion) + "</ul></section>")
    if roedores:
        secs += (f'<section class="campaign-section"><h2>{i18n.t_or("zoonosis.rodent_h", "Prevení enfermedades transmitidas por roedores", lang)}</h2>'
                  '<ul class="facts">' + "".join(f"<li>{site_base._esc(p)}</li>" for p in roedores) + "</ul></section>")
    if mordedura:
        # The same notice as telemedicine's: theme text on a panel, never
        # brown on yellow; the closing note is body text, not a boxed field
        # (demo review 2026-11-15, D10).
        secs += f'<p class="info-alert"><b>{i18n.t_or("zoonosis.bite_h", "Si te mordieron o arañaron", lang)}</b> {site_base._esc(mordedura)}</p>'
    secs += f'<section class="campaign-section"><p>{site_base._esc(nota)}</p></section>'

    contact_bits = []
    if whatsapp:
        wa_link = format.tel_wa(whatsapp)
        if wa_link:
            contact_bits.append(
                f'<a class="wa-action" href="https://wa.me/{wa_link}">'
                f'<img src="/static/icons/whatsapp-sf-regular.svg" alt="">'
                f'{site_base._esc(i18n.t_or("zoonosis.whatsapp_lead", "Escribir por WhatsApp", lang))} · '
                f'{site_base._esc(format.tel_text(whatsapp))}</a>')
    # The landline says it is one: next to a WhatsApp number a bare number
    # read as a second WhatsApp (Pinamar press office, 2026-10-22).
    datos = []
    if telefono:
        datos.append(f'{site_base._esc(i18n.t_or("zoonosis.landline", "Teléfono fijo", lang))}: '
                     f'<a class="stz-link" href="tel:{site_base._esc(site_centers._phone_href(telefono))}">{site_base._esc(format.tel_text(telefono))}</a>')
    if direccion:
        datos.append(f'<a class="stz-link" href="{site_base._esc(site_centers._maps_url(centro))}" target="_blank" rel="noopener">{site_base._esc(direccion)}</a>'
                     if centro.get("address") or centro.get("maps_url") else site_base._esc(direccion))
    if email:
        datos.append(f'<a class="stz-link" href="mailto:{site_base._esc(email)}">{site_base._esc(email)}</a>')
    foto = site_centers._center_photo(centro["slug"]) if centro.get("slug") else ""
    if contact_bits or datos:
        secs += (f'<section class="campaign-section"><h2>{i18n.t_or("zoonosis.contact_h", "Pedí información o coordiná", lang)}</h2>'
                  + (f'<div class="campaign-action">{" ".join(contact_bits)}</div>' if contact_bits else '')
                  + (f'<p>{" · ".join(datos)}</p>' if datos else '')
                  + (f'<figure class="hero-photo zoo-photo"><img src="{site_base._esc(foto)}" alt="{site_base._esc(centro.get("name") or h1)}" loading="lazy"></figure>'
                     if foto else '')
                  + '</section>')

    if oficiales:
        links_html = "".join(
            f'<li><a class="stz-link" href="{site_base._esc(e["href"])}" target="_blank" rel="noopener">{site_base._esc(e.get("texto") or e["href"])}</a></li>'
            for e in oficiales)
        secs += (f'<section class="campaign-section"><h2>{i18n.t_or("zoonosis.official_h", "Información oficial", lang)}</h2>'
                  f'<ul class="facts">{links_html}</ul></section>')

    body = f"""
  <div class="campaign-page"><h1 class="h1">{site_base._esc(h1)}</h1>
  {site_preparation._link_announcements_html("zoonosis")}
  <p class="lede">{site_base._esc(lede)}</p>
{secs}
  </div>
"""
    return site_shell._shell(f"{h1} | {theme.brand_name()}", "", body, desc=lede, canonical="/zoonosis")


def _page_blood_donation():
    """/blood-donation (demo review 2026-11-15, D2/D10/D11/D28): what it is,
    then WHERE and WHEN — the center the pack names (`lugar`, else the
    installation's main center) with its address, the hours when the pack
    knows them, otherwise the next step in plain text — and the buttons to
    call or write, each showing exactly the number it dials, both written by
    the ONE phone formatter (`format.tel_text` / `format.tel_href`), so the
    hospital's phone reads the same here as on its center page. One body
    size; no boxed note that looks like an empty field."""
    lang = i18n.active()
    pagina = (site_centers._link_by_id("donar-sangre") or {}).get("pagina") or {}
    h1 = i18n.t_or("blood_donation.h1", "Donar sangre", lang)
    lede = pagina.get("lede") or i18n.t_or(
        "blood_donation.lede_default",
        "Cada donación puede ayudar a salvar una vida.", lang)
    cuerpo = [p for p in (pagina.get("cuerpo") or []) if p]
    antes = [p for p in (pagina.get("antes") or []) if p]
    quienes = [p for p in (pagina.get("quienes") or []) if p]
    lugar = str(pagina.get("lugar") or "").strip()
    centro = (site_centers._centers_by_slug().get(lugar) if lugar else site_centers.main_center()) or {}
    horario = str(pagina.get("horario") or "").strip()
    telefono = str(pagina.get("telefono") or centro.get("phone") or "").strip()
    cta_href = str(pagina.get("cta_href") or "").strip()
    # A pack whose own button is a WhatsApp link (Pinamar's Hemoterapia line)
    # names the number to write to: it outranks the center's.
    wa_pack = re.match(r"https://wa\.me/(\d{8,15})/?$", cta_href)
    whatsapp = str(pagina.get("whatsapp") or ("+" + wa_pack.group(1) if wa_pack else "")
                   or centro.get("whatsapp") or "").strip()
    if wa_pack:
        cta_href = ""

    parrafos = "".join(f'<p>{site_base._esc(p)}</p>' for p in cuerpo)
    cuerpo_html = f'<section class="campaign-section">{parrafos}</section>' if parrafos else ""

    donde = []
    if centro.get("name") and centro.get("slug"):
        sitio_html = f'<a class="stz-link" href="{site_base._esc(site_centers.center_href(centro["slug"]))}">{site_base._esc(centro["name"])}</a>'
        # a temporary address: the place in the link, its date as its own
        # plain line — as on every other center surface
        lugar_dir = centro.get("address_place") if centro.get("address_until") else centro.get("address")
        if lugar_dir:
            sitio_html += (f' · <a class="stz-link" href="{site_base._esc(site_centers._maps_url(centro))}" target="_blank" rel="noopener">'
                           f'{site_base._esc(lugar_dir)}</a>')
        donde.append(f'<p>{sitio_html}</p>')
        if centro.get("address_until") and centro.get("address_place"):
            hasta = str(centro["address_until"])
            donde.append(f'<p>{site_base._esc(hasta[:1].upper() + hasta[1:])}</p>')
    if horario:
        donde.append(f'<p>{site_base._esc(horario)}</p>')
    else:
        nota = pagina.get("nota") or i18n.t_or(
            "blood_donation.nota_default",
            "Consultá en el hospital por el horario de donación.", lang)
        donde.append(f'<p>{site_base._esc(nota)}</p>')

    botones = []
    if telefono and format.tel_href(telefono):
        # the button dials ONE number, so it shows that one (a field with
        # «49-1670 / 49-1671» dials the first)
        uno = telefono.split("/")[0].strip()
        botones.append(
            f'<a class="stz-cta" href="tel:{site_base._esc(format.tel_href(uno))}">'
            f'{site_base._esc(i18n.t_or("blood_donation.call", "Llamar al {phone}", lang, phone=format.tel_text(uno)))}</a>')
    wa = format.tel_wa(whatsapp) if whatsapp else ""
    if wa:
        botones.append(
            f'<a class="wa-action" href="https://wa.me/{wa}">'
            f'<img src="/static/icons/whatsapp-sf-regular.svg" alt="">'
            f'WhatsApp · {site_base._esc(format.tel_text(whatsapp))}</a>')
    cta_href = _real_target(cta_href) or (cta_href if cta_href.startswith("/") else "")
    if cta_href:
        # The pack's own button (a sign-up form for a campaign day) always
        # stays; next to a call button it is the plain link, one filled
        # button per row.
        cta_texto = pagina.get("cta_texto") or i18n.t_or("blood_donation.cta", "Consultar antes de ir", lang)
        clase = "stz-link" if botones else "stz-cta"
        botones.append(f'<a class="{clase}" href="{site_base._esc(cta_href)}">{site_base._esc(cta_texto)}</a>')
    if botones:
        donde.append(f'<div class="campaign-action">{"".join(botones)}</div>')
    donde_html = (f'<section class="campaign-section"><h2>{i18n.t_or("blood_donation.where_h", "Dónde y cuándo donar", lang)}</h2>'
                  + "".join(donde) + '</section>')

    antes_html = (f'<section class="campaign-section"><h2>{i18n.t_or("blood_donation.before_h", "Antes de ir", lang)}</h2>'
                  + _facts_html(antes) + '</section>') if antes else ""
    quienes_html = (f'<section class="campaign-section"><h2>{i18n.t_or("blood_donation.who_h", "Quiénes pueden donar", lang)}</h2>'
                    + _facts_html(quienes) + '</section>') if quienes else ""
    body = f"""
  <div class="campaign-page"><h1 class="h1">{site_base._esc(h1)}</h1>
  <p class="lede">{site_base._esc(lede)}</p>
  {cuerpo_html}{donde_html}{antes_html}{quienes_html}
  </div>
"""
    return site_shell._shell(f"{h1} | {theme.brand_name()}", "", body, desc=lede, canonical="/blood-donation")

