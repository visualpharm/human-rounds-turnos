"""puco.py — obra social por DNI contra el padrón nacional (SISA PUCO, WS131).

PUCO = Padrón Único Consolidado Operativo (Ministerio de Salud de la Nación):
qué obra social tiene una persona según su DNI. Lo usamos en el registro: el
paciente tipea su DNI, el form dispara la consulta en background y mientras
completa el resto de los campos le mostramos TODAS las obras sociales que
figuran a su nombre (una persona puede tener más de una: IOMA por el empleo
provincial + una prepaga por desregulación de aportes) y le dejamos agregar
más a mano, igual que la prepaga directa que no figura en PUCO.

API (doc: sisa.msal.gov.ar/sisadoc/docs/0204/puco_ws_131.jsp):
  POST https://sisa.msal.gov.ar/sisa/services/rest/puco/{dni}
  body JSON {"usuario": "...", "clave": "..."}   (cuenta SISA con WS131)
  respuesta XML:
    <puco><resultado>OK</resultado>
          <coberturaSocial>O.S.P. BUENOS AIRES (IOMA)</coberturaSocial>
          <denominacion>APELLIDO NOMBRE</denominacion>
          <nrodoc>30123456</nrodoc><tipodoc>DNI</tipodoc>
          <rnos>902001</rnos></puco>
  OJO: pese a la tabla de la doc, `denominacion` trae el NOMBRE del ciudadano;
  la obra social viene en `coberturaSocial` + `rnos`. `denominacion` NUNCA se
  reexpone al cliente (el endpoint corre sin sesión — devolver el nombre de
  cualquier persona por DNI sería un leak; la obra social sola es el dato que
  la consulta pública de SISA ya muestra).

Fuentes (en orden): modo fake → WS131 REST (con creds) → consulta pública
GWT-RPC de la web de SISA (fallback sin credenciales, ver `_consultar_publico`).

Los nombres del padrón vienen en formato burocrático ("SWISS MEDICAL S.A.")
y se traducen al nombre humano ("Swiss Medical") con el diccionario
`_AMIGABLES` ANTES de cachear — así /api/puco, `cacheada()` (el match origen
'puco' de accounts.py) y lo guardado en usuarios.cobertura ven el mismo nombre.
El original queda en `nombre_padron`. Sin match, el nombre queda como vino.

Env:
  TURNOS_SISA_USER / TURNOS_SISA_PASS      cuenta SISA para WS131 REST (el
  (ex TURNOS_SISA_USUARIO / _CLAVE)        camino oficial/estable). Sin esto se
                                            usa la consulta pública si está ON.
  TURNOS_PUCO_PUBLIC                        consulta pública GWT-RPC como
                                            fallback; ON por default, "0" la
                                            apaga (queda sólo WS131/fake/manual).
  TURNOS_PUCO_GWT_PERM_DISP / _LIST         pisar los strong-names GWT si SISA
                                            redeploya y cambian (sin deploy).
  TURNOS_FAKE_PUCO=1                        sin red: último dígito del DNI
                                            0-3 → IOMA, 4-5 → IOMA + OSECAC
                                            (el caso multi), 6-7 → OSECAC,
                                            8-9 → no figura (tests/demo)
  TURNOS_PUCO_PER_HOUR                      rate limit por IP (default 30) —
                                            bucket PROPIO, no el de abuse_guard.py:
                                            una consulta PUCO no debe comerle
                                            cupo de IA a la misma IP (WiFi del
                                            hospital = muchos pacientes por IP).

Ruta nativa registrada por ``puco_router.py``:
  POST /api/puco {dni} →
    {"disponible": true,  "obras_sociales": [{"nombre", "rnos"}, ...]}  figura
    {"disponible": true,  "obras_sociales": []}                      no figura
    {"disponible": false}            sin credenciales / error de red / SISA caído
  (`obra_social` = la primera o null viaja además por compat con el JS viejo
  cacheado por Cloudflare.)

`cacheada(dni)` expone las obras del último lookup consultado (TTL 15 min)
para que accounts._register_core confirme server-side la cobertura "según el
padrón" — el cliente nunca puede inventar una cobertura con origen "puco".

Import-safe: sólo stdlib; nunca bloquea el arranque del server.

Moved here from repo-root puco.py in the Phase 6b connector extraction
(2026-07-30) — this is now the `insurance_lookup` capability's
implementation (see connectors/puco/connector.json, connectors/puco/
adapter.py). The repo-root `puco.py` compat shim was retired in Tier 2 batch L
(2026-08-07): import `connectors.puco.puco` directly.
"""

import re
import json
import time
import urllib.request
from app.core import config
from app.core import format
from app.core import data_packs
from app.core import rate_window
from app.core import text_fold
from app.core import ids as _ids

# WS131 REST base URL: env override (TURNOS_SISA_URL) > packs/ar/endpoints.json
# ("puco.base_url") > literal fallback — same precedence + read-once pattern
# as format.py's PHONE (Phase 2c, 2026-07-30, see docs/architecture/data-packs.md).
# The public GWT-RPC fallback below (_PUB_BASE/_PUB_MODULE) is a DIFFERENT path
# off the same domain, not represented in endpoints.json's schema -- left
# literal, out of this slice's scope.
_FALLBACK_SISA_URL = "https://sisa.msal.gov.ar/sisa/services/rest/puco/"


def _load_sisa_url():
    return data_packs.pack_setting("TURNOS_SISA_URL", _FALLBACK_SISA_URL, "endpoints.json", "puco", "base_url")


SISA_URL = _load_sisa_url()
TIMEOUT = 6  # segundos — el form sigue andando sin esto, no vale la pena colgar


def _env(key, default=""):
    return config.get(key, default)


def _creds():
    u = _env("TURNOS_SISA_USER") or _env("TURNOS_SISA_USUARIO")
    c = _env("TURNOS_SISA_PASS") or _env("TURNOS_SISA_CLAVE")
    return (u, c) if u and c else None


def _fake():
    return _env("TURNOS_FAKE_PUCO") == "1"


def _publico_on():
    """La consulta pública GWT-RPC como fallback. ON por default; se apaga con
    TURNOS_PUCO_PUBLIC=0 (deploy que no quiera pegarle a la web de SISA)."""
    return _env("TURNOS_PUCO_PUBLIC", "1") != "0"


def disponible():
    """Hay forma de consultar el padrón: modo fake, credenciales WS131, o la
    consulta pública GWT-RPC de la web de SISA."""
    return _fake() or _creds() is not None or _publico_on()


# ---- cache en memoria (dni -> resultado) ------------------------------------
# TTL corto: lo justo para que el POST /api/account/register que sigue al
# lookup del form encuentre el mismo resultado sin volver a pegarle a SISA
# (la cuota de WS131 es limitada). Guarda positivos Y "no figura"; los errores
# de red no se cachean (el próximo intento puede andar).

_CACHE = {}  # dni -> (epoch, [obra, ...])
_TTL = 15 * 60
_CACHE_MAX = 5000


def _cache_get(dni):
    hit = _CACHE.get(dni)
    if not hit:
        return False, []
    ts, obras = hit
    if time.time() - ts > _TTL:
        del _CACHE[dni]
        return False, []
    return True, obras


def _cache_put(dni, obras):
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()  # anti-abuso ya limita el ritmo; esto es sólo un techo
    _CACHE[dni] = (time.time(), obras or [])


def cacheada(dni):
    """Las obras sociales del último lookup consultado de ese DNI (lista, puede
    ser vacía). La usa accounts._register_core para adjuntar la cobertura con origen
    'puco' server-side."""
    _, obras = _cache_get(str(dni or ""))
    return obras


# ---- consulta ----------------------------------------------------------------

_FAKE_IOMA = {"nombre": "O.S.P. BUENOS AIRES (I.O.M.A.)", "rnos": "902001"}
_FAKE_OSECAC = {"nombre": "O.S. DE EMPLEADOS DE COMERCIO (OSECAC)", "rnos": "126205"}


def _consultar_fake(dni):
    ultimo = dni[-1] if dni else "9"
    if ultimo in "0123":
        return [dict(_FAKE_IOMA)]
    if ultimo in "45":  # el caso multi: figura en DOS padrones
        return [dict(_FAKE_IOMA), dict(_FAKE_OSECAC)]
    if ultimo in "67":
        return [dict(_FAKE_OSECAC)]
    return []


def _parse_respuesta(raw):
    """Parsea la respuesta de WS131 (XML; por las dudas tolera JSON).
    Devuelve (resultado, [obra, ...]) — la doc muestra UNA cobertura por DNI,
    pero el padrón consolidado puede tener más de una fila por persona (IOMA +
    la prepaga que recibe sus aportes), así que se levantan TODAS las
    coberturaSocial que vengan."""
    raw = raw.strip()
    docs = []
    if raw.startswith("{") or raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed = [parsed]
            docs = [{k.lower(): str(v) for k, v in d.items() if v is not None}
                    for d in parsed if isinstance(d, dict)]
        except ValueError:
            docs = []
    if not docs:
        m = re.search(r"<resultado>\s*(.*?)\s*</resultado>", raw, re.S)
        resultado = (m.group(1) if m else "").upper()
        obras = []
        for m in re.finditer(
                r"<coberturaSocial>\s*(.*?)\s*</coberturaSocial>(.*?)(?=<coberturaSocial>|$)",
                raw, re.S):
            nombre = m.group(1).strip()
            if not nombre:
                continue
            rn = re.search(r"<rnos>\s*(.*?)\s*</rnos>", m.group(2), re.S)
            obras.append({"nombre": nombre, "rnos": (rn.group(1).strip() if rn else "") or None})
        return resultado, obras

    resultado = ""
    obras = []
    for campos in docs:
        resultado = (campos.get("resultado") or resultado or "").upper() or resultado
        nombre = (campos.get("coberturasocial") or "").strip()
        if nombre:
            obras.append({"nombre": nombre, "rnos": (campos.get("rnos") or "").strip() or None})
    return resultado, obras


# ---- nombres amigables --------------------------------------------------------
# El padrón viene en formato burocrático a mayúsculas ("SWISS MEDICAL S.A.",
# "O.S.P. BUENOS AIRES (I.O.M.A.)"). Antes de cachear/responder se traduce al
# nombre que usa la gente (los mismos de OBRAS_SOCIALES/PREPAGAS en
# cuenta-form.js, frecuencia de la cartilla local). Sin match queda como vino:
# mejor burocrático que inventado. El original se conserva en `nombre_padron`.

_AMIGABLES = [(re.compile(rx), out) for rx, out in [
    (r"\bIOMA\b|OSP BUENOS AIRES", "IOMA"),
    (r"EMPRESARIOS,? PROFESIONALES Y MONOTRIBUTISTAS|\bOSDEPYM\b", "OSDEPYM"),
    (r"\bPAMI\b|\bINSSJP\b|JUBILADOS Y PENSIONADOS", "PAMI"),
    (r"\bOSECAC\b|EMPLEADOS DE COMERCIO", "OSECAC (Empleados de Comercio)"),
    (r"SWISS MEDICAL", "Swiss Medical"),
    (r"\bOSDE\b|DIRECCION DE EMPRESAS", "OSDE"),
    (r"\bGALENO\b", "Galeno"),
    (r"\bMEDIFE\b", "Medifé"),
    (r"\bMEDICUS\b", "Medicus"),
    (r"\bOMINT\b", "OMINT"),
    (r"\bSANCOR\b", "Sancor Salud"),
    (r"PREVENCION SALUD", "Prevención Salud"),
    (r"\bFEDERADA\b", "Federada Salud"),
    (r"\bAVALIAN\b|ACA SALUD", "Avalian"),
    (r"\bJERARQUICOS\b", "Jerárquicos Salud"),
    (r"LUIS PASTEUR", "Luis Pasteur"),
    (r"UNION PERSONAL|PERSONAL CIVIL DE LA NACION|\bACCORD\b", "Unión Personal"),
    (r"UTHGRA|GASTRONOMIC", "OSUTHGRA (Gastronómicos)"),
    (r"\bOSPEDYC\b|ENTIDADES DEPORTIVAS", "OSPEDYC"),
    (r"\bOSPE\b|PETROLER", "OSPE (Petroleros)"),
    (r"\bOSPRERA\b|RURAL(ES)? Y ESTIBADORES", "OSPRERA (Trabajadores Rurales)"),
    (r"LUZ Y FUERZA|FATLYF", "Luz y Fuerza (OSFATLyF)"),
    (r"PODER JUDICIAL", "Poder Judicial"),
    (r"BANCO( DE LA)? PROVINCIA", "Banco Provincia"),
    (r"\bCOMEI\b", "COMEI"),
    (r"\bAMFFA\b", "AMFFA"),
    (r"ABOGADOS", "Colegio de Abogados (CASA)"),
    (r"\bOSPECON\b|TRABAJADORES DE LA CONSTRUCCION", "OSPeCon (Construcción)"),
    (r"CHOFERES DE CAMIONES|CAMIONER", "Camioneros"),
    (r"UNION OBRERA METALURGICA|\bUOM\b", "UOM"),
]]


def _norm_txt(s):
    """MAYÚSCULAS sin tildes ni puntos ("O.S.P. Buenos Aires (I.O.M.A.)" →
    "OSP BUENOS AIRES (IOMA)") para matchear contra _AMIGABLES."""
    s = text_fold.strip_accents(str(s or ""), "NFD")
    return re.sub(r"\s+", " ", s.upper().replace(".", "")).strip()


# Palabras comunes de los nombres del padrón con su forma con tilde/mayúscula
# correcta, para el fallback Title Case de los nombres sin regla propia.
_TILDES = {
    "ASOCIACION": "Asociación", "DIRECCION": "Dirección", "ADMINISTRACION":
    "Administración", "REPUBLICA": "República", "UNION": "Unión",
    "METALURGICA": "Metalúrgica", "CONSTRUCCION": "Construcción",
    "MEDICOS": "Médicos", "MEDICA": "Médica", "TECNICOS": "Técnicos",
    "QUIMICA": "Química", "QUIMICOS": "Químicos", "MECANICOS": "Mecánicos",
    "TELEFONICOS": "Telefónicos", "FARMACEUTICOS": "Farmacéuticos",
    "PUBLICO": "Público", "PUBLICOS": "Públicos", "ATENCION": "Atención",
    "RIO": "Río", "CAJA": "Caja", "LUZ": "Luz", "GAS": "Gas", "OBRA": "Obra",
    "MAR": "Mar", "SUR": "Sur",
}
_CONECTORES = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "E", "PARA", "EN", "AL",
               "POR", "CON"}


def _title_case(crudo):
    """"ASOCIACION MUTUAL DEL CONTROL INTEGRAL" → "Asociación Mutual del
    Control Integral". Conserva siglas y abreviaturas ("O.S.", "OSPE", "S.A.":
    tokens con punto/dígito o cortos que no son palabra conocida)."""
    out = []
    for tok in re.split(r"\s+", crudo.strip()):
        base = tok.strip("(),")
        if "." in base or any(c.isdigit() for c in base):
            out.append(tok)
            continue
        if base.upper() in _CONECTORES:
            out.append(tok.replace(base, base.lower()))
        elif base.upper() in _TILDES:
            out.append(tok.replace(base, _TILDES[base.upper()]))
        elif len(base) >= 5:
            out.append(tok.replace(base, base.capitalize()))
        else:  # corto y desconocido: probable sigla (OSPE, UOM) — no tocar
            out.append(tok)
    return " ".join(out)


def amigable(nombre):
    """El nombre humano de una cobertura del padrón. Con regla en _AMIGABLES →
    ese nombre; sin regla pero descriptivo (3+ palabras) → Title Case con
    tildes comunes; corto sin regla (probable sigla) → como vino."""
    t = _norm_txt(nombre)
    for rx, out in _AMIGABLES:
        if rx.search(t):
            return out
    crudo = (nombre or "").strip()
    if len(crudo.split()) >= 3:
        return _title_case(crudo)
    return crudo


def _dedup(obras):
    """Traduce cada nombre al amigable y dedupea por ESE nombre (dos filas del
    padrón que mapean a la misma cobertura colapsan en una)."""
    vistos, out = set(), []
    for o in obras or []:
        crudo = (o.get("nombre") or "").strip()
        if not crudo:
            continue
        nombre = amigable(crudo)
        if nombre in vistos:
            continue
        vistos.add(nombre)
        traducida = dict(o, nombre=nombre)
        if nombre != crudo:
            traducida["nombre_padron"] = crudo
        out.append(traducida)
    return out


def _consultar_ws131(dni, creds):
    """WS131 REST oficial (needs SISA service account). (ok, obras)."""
    body = json.dumps({"usuario": creds[0], "clave": creds[1]}).encode("utf-8")
    req = urllib.request.Request(
        SISA_URL + dni, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — red/SISA caído: el form sigue sin padrón
        return False, []

    resultado, obras = _parse_respuesta(raw)
    # Sólo OK es una consulta respondida (con o sin cobertura). Lo demás
    # (ERROR_AUTENTICACION, NO_TIENE_QUOTA_DISPONIBLE, ERROR_INESPERADO...)
    # es "no disponible", no un falso "no figura" — y no se cachea.
    if resultado != "OK":
        return False, []
    return True, _dedup(obras)


def consultar(dni):
    """(ok, obras): ok=False -> no se pudo consultar (sin credenciales, red,
    SISA caído); ok=True + [] -> se consultó y NO figura; ok=True +
    [{'nombre','rnos'}, ...] -> figura (puede ser en más de un padrón).
    Cachea los resultados consultados.

    Orden de fuentes: modo fake (tests) → WS131 REST (si hay creds SISA) → la
    consulta pública GWT-RPC (si TURNOS_PUCO_PUBLIC no está apagado). El público
    es un fallback sin credenciales — ver `_consultar_publico`."""
    dni = _ids.dni(dni)
    if not format.dni_valid_length(dni):
        return True, []

    en_cache, obras = _cache_get(dni)
    if en_cache:
        return True, obras

    if _fake():
        obras = _dedup(_consultar_fake(dni))  # el fake también pasa por amigable()
        _cache_put(dni, obras)
        return True, obras

    creds = _creds()
    if creds:
        ok, obras = _consultar_ws131(dni, creds)
        if ok:
            _cache_put(dni, obras)
            return True, obras
        # WS131 no respondió OK (quota/auth/red): probamos el público abajo.

    if _publico_on():
        ok, obras = _consultar_publico(dni)
        if ok:
            _cache_put(dni, obras)
            return True, obras

    return False, []


# ---- consulta pública (GWT-RPC de la web de SISA, sin credenciales) ----------
# Fallback del WS131: la "Consulta de Cobertura de Salud (PUCO)" pública de
# sisa.msal.gov.ar devuelve la misma cobertura por DNI sin cuenta de servicio.
# NO es REST: es la GWT-RPC del SPA. Dos POST text/x-gwt-rpc:
#   1) LoadEnvironmentAction -> /sisa/sisa/dispatch  (mint-ea JSESSIONID, sin auth)
#   2) ListService.getPage   -> /sisa/sisa/service/list  (búsqueda por DNI)
# Reverse-engineered 2026-07-16 — receta y caveats en
# docs/solutions/sisa-puco-gwt-rpc-api.md.
#
# FRÁGIL A PROPÓSITO: los strong-names GWT (`_PUB_PERM_*`) son hashes de la
# policy de serialización compilada; cambian en cada redeploy del SPA de SISA.
# Si SISA redeploya, getPage devuelve //EX y `_consultar_publico` retorna
# ok=False -> el form degrada a "cargá la obra social a mano" (nunca un falso
# "no figura", nunca rompe el registro). Se pueden pisar los hashes por env
# (TURNOS_PUCO_GWT_PERM_DISP / _LIST) sin redeploy nuestro.

import http.cookiejar

_PUB_BASE = "https://sisa.msal.gov.ar/sisa/sisa"
_PUB_MODULE = "https://sisa.msal.gov.ar/sisa/sisa/"
_PUB_PERM_DISP = "2CB1B348CFC741E7C76AF8DA0A2C6911"
_PUB_PERM_LIST = "5CFBDB55F3DE4A47FE42E765E5AA02D3"
_PUB_ENV_BODY = (
    "7|0|6|" + _PUB_MODULE + "|{perm}|"
    "net.customware.gwt.dispatch.client.standard.StandardDispatchService|execute|"
    "net.customware.gwt.dispatch.shared.Action|"
    "ar.gob.msal.sisa.shared.rpc.action.LoadEnvironmentAction/1047162293|1|2|3|4|1|5|6|")
_PUB_LIST_BODY = (
    "7|0|14|" + _PUB_MODULE + "|{perm}|"
    "ar.gob.msal.sisa.client.commons.components.lista.service.ListService|getPage|"
    "java.lang.Integer/3438268394|java.util.List|Z|"
    "ar.gob.msal.sisa.shared.model.list.ComplexFilter/30068811|"
    "java.util.ArrayList/4159755760|"
    "ar.gob.msal.sisa.client.commons.components.lista.simple.SearchFilter/1978531670|"
    "97390|ar.gob.msal.sisa.client.entitys.list.Filter$OPERATION/3408968308|"
    "ar.gob.msal.sisa.client.entitys.list.Filter$OPERATOR/860546718|{dni}|"
    "1|2|3|4|10|5|5|5|6|6|5|7|5|6|8|5|790|5|1|-2|9|0|9|1|10|11|12|0|13|0|0|14|0|0|1|5|25|0|0|")
_PUB_DOCTYPES = {"DNI", "LC", "LE", "CI", "LI", "DNIE", "PAS", "PASAPORTE"}

_PUB_SESSION = {"opener": None, "ts": 0.0}
_PUB_SESSION_TTL = 4 * 60  # re-bootstrap antes de que SISA expire la sesión


def _pub_perm(kind, default):
    return _env("TURNOS_PUCO_GWT_PERM_" + kind) or default


def _pub_post(opener, path, body, perm):
    req = urllib.request.Request(
        _PUB_BASE + path, data=body.encode("utf-8"), method="POST",
        headers={"Content-Type": "text/x-gwt-rpc; charset=UTF-8",
                 "X-GWT-Module-Base": _PUB_MODULE,
                 "X-GWT-Permutation": perm,
                 "Referer": "https://sisa.msal.gov.ar/sisa/"})
    with opener.open(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def _pub_opener(fresh=False):
    """Opener con JSESSIONID válido. Bootstrapea (LoadEnvironment) si no hay
    sesión o venció; devuelve None si el bootstrap falla."""
    now = time.time()
    if not fresh and _PUB_SESSION["opener"] and now - _PUB_SESSION["ts"] < _PUB_SESSION_TTL:
        return _PUB_SESSION["opener"]
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    perm = _pub_perm("DISP", _PUB_PERM_DISP)
    try:
        _pub_post(opener, "/dispatch", _PUB_ENV_BODY.format(perm=perm), perm)
    except Exception:  # noqa: BLE001
        _PUB_SESSION.update(opener=None, ts=0.0)
        return None
    _PUB_SESSION.update(opener=opener, ts=now)
    return opener


def _parse_publico(raw, dni):
    """Extrae las coberturas de la respuesta GWT-RPC de ListService.getPage.
    Devuelve lista de {nombre, rnos:None}, o None si no se pudo (respuesta //EX,
    hash de policy vencido, estructura cambiada) -> el caller lo trata como
    'no disponible', jamás como 'no figura'.

    La respuesta es `//OK[<tokens>...,[pool de strings],0,7]`. Cada fila de la
    lista tiene 5 celdas string en orden de columnas
    [TipoDoc, NroDoc, Sexo, Cobertura Social, Denominación]; en el stream de
    tokens aparecen en orden inverso ([denom, cobertura, sexo, nrodoc, tipodoc]),
    cada una como `<idx del pool>, <ref de java.lang.String>`. Tomamos SÓLO la
    Cobertura (2ª); la Denominación (=NOMBRE del ciudadano) NUNCA se levanta."""
    if not raw.startswith("//OK"):
        return None
    try:
        arr = json.loads(raw[raw.index("["):])
    except (ValueError, IndexError):
        return None
    if not isinstance(arr, list) or len(arr) < 3:
        return None
    pool = arr[-3]
    if not isinstance(pool, list):
        return None
    try:
        str_ref = next(i + 1 for i, s in enumerate(pool)
                       if isinstance(s, str) and s.startswith("java.lang.String/"))
    except StopIteration:
        return None
    toks = arr[:-3]
    refs = [toks[i] for i in range(len(toks) - 1)
            if isinstance(toks[i], int) and toks[i + 1] == str_ref
            and 1 <= toks[i] <= len(pool)]
    obras, seen = [], set()
    for j in range(0, len(refs) - 4, 5):
        a, b, c, d, e = refs[j:j + 5]
        denom, cob, sexo, nrodoc, tipo = (
            pool[a - 1], pool[b - 1], pool[c - 1], pool[d - 1], pool[e - 1])
        if not all(isinstance(x, str) for x in (denom, cob, sexo, nrodoc, tipo)):
            continue
        if tipo.strip().upper() not in _PUB_DOCTYPES:
            continue
        if re.sub(r"\D", "", nrodoc) != dni:       # la fila tiene que ser ESTE dni
            continue
        if sexo.strip().upper() not in ("M", "F", "X", ""):
            continue
        nombre = cob.strip()
        if nombre and nombre not in seen and nombre != denom:
            seen.add(nombre)
            obras.append({"nombre": nombre, "rnos": None})
    return obras


def _consultar_publico(dni):
    """(ok, obras) contra la consulta pública. ok=False si no se pudo (bootstrap
    falló, //EX, hash vencido). Reintenta una vez si la sesión expiró."""
    perm = _pub_perm("LIST", _PUB_PERM_LIST)
    body = _PUB_LIST_BODY.format(perm=perm, dni=dni)
    for intento in (1, 2):
        opener = _pub_opener(fresh=(intento == 2))
        if opener is None:
            return False, []
        try:
            raw = _pub_post(opener, "/service/list", body, perm)
        except Exception:  # noqa: BLE001 — red/SISA caído
            return False, []
        if "SessionTimeOut" in raw or "SisaTimeout" in raw:
            _PUB_SESSION.update(opener=None, ts=0.0)
            continue  # re-bootstrap y reintento
        obras = _parse_publico(raw, dni)
        if obras is None:
            return False, []
        return True, _dedup(obras)
    return False, []


# ---- rate limit propio (ventana deslizante por IP) ---------------------------
# Mismo patrón que abuse_guard.allowed pero bucket separado y cupo más generoso:
# es una consulta barata de registro, no una pasada de IA.

_HITS = {}
_HOUR = 3600


def _per_hour():
    try:
        v = _env("TURNOS_PUCO_PER_HOUR")
        return int(v) if v else 30
    except ValueError:
        return 30


def _permitido(ip):
    return rate_window.allow(_HITS, ip, _per_hour(), _HOUR)


def procesar_consulta(dni_crudo, ip):
    """Domain logic pura de POST /api/puco: recibe el DNI tal cual vino en
    el body y la IP ya resuelta por el transporte (turnstile/abuse_guard-
    style header parsing) y devuelve (status, payload) — mismo orden y
    mismos mensajes que el endpoint `POST /api/puco` (que delega en esta
    función)."""
    dni = _ids.dni(dni_crudo)
    if not format.dni_valid_length(dni):
        return 400, {"error": "DNI inválido"}

    if not disponible():
        return 200, {"disponible": False}

    en_cache, _ = _cache_get(dni)
    if not en_cache and not _permitido(ip):
        return 429, {"error": "demasiadas consultas, esperá un rato"}

    ok, obras = consultar(dni)
    if not ok:
        return 200, {"disponible": False}
    # obra_social (la primera) viaja además por compat con el JS viejo cacheado
    return 200, {"disponible": True, "obras_sociales": obras,
                 "obra_social": obras[0] if obras else None}
