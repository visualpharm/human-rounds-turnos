"""test_puco.py — obra social por DNI (puco.py) + cobertura en el registro.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_puco.py"

Sigue el patrón de test_accounts.py: base dedicada `sana_turnos_test_puco` para
las pruebas que persisten (si Postgres no está, se saltean); el resto corre en
memoria con TURNOS_FAKE_PUCO=1 (sin red).
"""

import io
import os
import sys
import json
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_FAKE_PUCO="1")

from app.core import db_accounts  # noqa: E402
from app.connectors.puco import puco      # noqa: E402
from app.connectors.puco import puco_router  # noqa: E402
from app.cuentas import accounts   # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.core import config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

turnstile.ENV_PATH = ""  # determinístico (no leer el .env del dev)
puco.ENV_PATH = ""       # ídem: sin credenciales SISA del .env en los tests
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_puco", seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


class FakeHandler:
    def __init__(self, path, body=None, headers=None):
        self.path = path
        if body is None:
            self._body = b""
        elif isinstance(body, (bytes, bytearray)):
            self._body = bytes(body)
        else:
            self._body = json.dumps(body).encode("utf-8")
        self.headers = headers or {}
        self.status = None
        self.wfile = io.BytesIO()
        self.header_list = []
        self.client_address = ("127.0.0.1", 54321)

    def _read_body(self):
        return self._body

    def _read_json(self):
        return json.loads(self._body.decode("utf-8")) if self._body else {}

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.header_list.append((k, v))

    def end_headers(self):
        pass

    def json_body(self):
        raw = self.wfile.getvalue()
        return json.loads(raw.decode("utf-8")) if raw else None


def _limpiar(case):
    puco._CACHE.clear()
    puco._HITS.clear()
    _env.set_env(case, TURNOS_FAKE_PUCO="1", TURNOS_PUCO_PER_HOUR=None)


class TestConsultarFake(unittest.TestCase):
    def setUp(self):
        _limpiar(self)

    def test_dni_terminado_en_0_figura_ioma(self):
        ok, obras = puco.consultar("30123450")
        self.assertTrue(ok)
        self.assertEqual(len(obras), 1)
        # el nombre burocrático del padrón sale traducido al humano
        self.assertEqual(obras[0]["nombre"], "IOMA")
        self.assertEqual(obras[0]["nombre_padron"], "O.S.P. BUENOS AIRES (I.O.M.A.)")
        self.assertTrue(obras[0]["rnos"])

    def test_dni_terminado_en_5_figura_en_dos_padrones(self):
        ok, obras = puco.consultar("30123455")
        self.assertTrue(ok)
        self.assertEqual(len(obras), 2)
        self.assertEqual(obras[0]["nombre"], "IOMA")
        self.assertIn("OSECAC", obras[1]["nombre"])

    def test_dni_terminado_en_8_no_figura(self):
        ok, obras = puco.consultar("30123458")
        self.assertTrue(ok)
        self.assertEqual(obras, [])

    def test_sin_credenciales_ni_fake_ni_publico_no_disponible(self):
        # sin fake, sin creds y con la consulta pública apagada: no hay fuente.
        _env.set_env(self, TURNOS_FAKE_PUCO="0", TURNOS_PUCO_PUBLIC="0")
        ok, obras = puco.consultar("30123450")
        self.assertFalse(ok)
        self.assertEqual(obras, [])
        self.assertFalse(puco.disponible())

    def test_cacheada_expone_el_ultimo_lookup(self):
        self.assertEqual(puco.cacheada("30123450"), [])
        puco.consultar("30123450")
        self.assertEqual(puco.cacheada("30123450")[0]["nombre"], "IOMA")
        # los "no figura" también se cachean; cacheada() da lista vacía
        puco.consultar("30123458")
        self.assertEqual(puco.cacheada("30123458"), [])

    def test_dni_invalido_no_consulta(self):
        ok, obras = puco.consultar("123")
        self.assertTrue(ok)
        self.assertEqual(obras, [])


# Respuesta real (recortada) de ListService.getPage de la web pública de SISA.
# El pool 1-indexado: 5=java.lang.String, 6=TipoDoc, 7=NroDoc, 8=Sexo,
# 9..=Cobertura(s), y la Denominación = NOMBRE del ciudadano ("PEREZ, JUAN"),
# que NUNCA debe salir. Cada fila = 5 refs `<idx>,5` en orden inverso de columna.
_PUB_OK_1 = ('//OK[10,5,9,5,8,5,7,5,6,5,2,1,'
             '["PageList/1","Boolean/2","ArrayList/3","Row/4",'
             '"java.lang.String/2004016611","DNI","30111222","M",'
             '"SWISS MEDICAL S.A.","PEREZ, JUAN"],0,7]')
# Dos coberturas (dos filas, mismo DNI). El nombre "GOMEZ, ANA" es la
# Denominación de ambas filas y el pool GWT lo deduplica (aparece una vez, ref 10).
_PUB_OK_2 = ('//OK[10,5,9,5,8,5,7,5,6,5,10,5,11,5,8,5,7,5,6,5,2,1,'
             '["PageList/1","Boolean/2","ArrayList/3","Row/4",'
             '"java.lang.String/2004016611","DNI","30111222","F",'
             '"O.S.P. BUENOS AIRES (I.O.M.A.)","GOMEZ, ANA","OSDE"],0,7]')
_PUB_EX = ('//EX[2,1,["ar.gob.msal.sisa.client.callback.exception.'
           'SessionTimeOutException/824486946","Su sesión ha expirado."],0,7]')


class TestAmigable(unittest.TestCase):
    """El diccionario padrón-burocrático → nombre humano (los de la cartilla)."""

    def test_los_mas_comunes(self):
        casos = {
            "SWISS MEDICAL S.A.": "Swiss Medical",
            "O.S.P. BUENOS AIRES (I.O.M.A.)": "IOMA",
            "INSTITUTO NACIONAL DE SERVICIOS SOCIALES PARA JUBILADOS Y PENSIONADOS": "PAMI",
            "O.S. DE EMPLEADOS DE COMERCIO Y ACTIVIDADES CIVILES": "OSECAC (Empleados de Comercio)",
            "OBRA SOCIAL DE EJECUTIVOS Y DEL PERSONAL DE DIRECCION DE EMPRESAS": "OSDE",
            "GALENO ARGENTINA S.A.": "Galeno",
            "MEDIFE ASOCIACION CIVIL": "Medifé",
            "ASOCIACION MUTUAL SANCOR": "Sancor Salud",
            "OBRA SOCIAL UNION PERSONAL DE LA UNION DEL PERSONAL CIVIL DE LA NACION": "Unión Personal",
            "O.S. DEL PERSONAL RURAL Y ESTIBADORES DE LA REPUBLICA ARGENTINA": "OSPRERA (Trabajadores Rurales)",
            "O.S. DE LOS TRABAJADORES DE LA CONSTRUCCION (OSPECON)": "OSPeCon (Construcción)",
        }
        for crudo, esperado in casos.items():
            self.assertEqual(puco.amigable(crudo), esperado, crudo)

    def test_ospe_no_pisa_ospedyc(self):
        # \bOSPE\b no debe matchear OSPEDYC (y viceversa)
        self.assertEqual(puco.amigable("O.S. DE PETROLEROS (OSPE)"), "OSPE (Petroleros)")
        self.assertEqual(
            puco.amigable("O.S. DEL PERSONAL DE ENTIDADES DEPORTIVAS Y CIVILES (OSPEDYC)"),
            "OSPEDYC")

    def test_sin_regla_descriptivo_va_a_title_case(self):
        # sin regla propia pero con nombre descriptivo → Title Case con tildes;
        # las abreviaturas ("O.S.") quedan intactas (caso real: la mutual de
        # control integral del padrón del responsable del producto, 2026-07-17)
        self.assertEqual(puco.amigable("ASOCIACION MUTUAL DEL CONTROL INTEGRAL"),
                         "Asociación Mutual del Control Integral")
        self.assertEqual(puco.amigable("O.S. DEL VIDRIO PLANO"),
                         "O.S. del Vidrio Plano")

    def test_sin_regla_corto_queda_como_vino(self):
        # una sigla suelta no se "titlecasea" (Ospif sería inventado)
        self.assertEqual(puco.amigable("OSPIF"), "OSPIF")

    def test_osdepym(self):
        self.assertEqual(
            puco.amigable("OBRA SOCIAL  DE EMPRESARIOS, PROFESIONALES Y MONOTRIBUTISTAS"),
            "OSDEPYM")

    def test_dedup_colapsa_dos_filas_de_la_misma_cobertura(self):
        obras = puco._dedup([
            {"nombre": "SWISS MEDICAL S.A.", "rnos": "127605"},
            {"nombre": "SWISS MEDICAL SA", "rnos": None},
        ])
        self.assertEqual(len(obras), 1)
        self.assertEqual(obras[0]["nombre"], "Swiss Medical")
        self.assertEqual(obras[0]["nombre_padron"], "SWISS MEDICAL S.A.")
        self.assertEqual(obras[0]["rnos"], "127605")

    def test_dedup_sin_match_no_agrega_nombre_padron(self):
        obras = puco._dedup([{"nombre": "IOMA", "rnos": None}])
        self.assertEqual(obras, [{"nombre": "IOMA", "rnos": None}])


class TestParsePublico(unittest.TestCase):
    def test_una_cobertura_y_el_nombre_no_sale(self):
        obras = puco._parse_publico(_PUB_OK_1, "30111222")
        self.assertEqual(obras, [{"nombre": "SWISS MEDICAL S.A.", "rnos": None}])
        # el nombre del ciudadano NUNCA se levanta
        self.assertNotIn("PEREZ", json.dumps(obras))

    def test_dos_coberturas(self):
        obras = puco._parse_publico(_PUB_OK_2, "30111222")
        nombres = [o["nombre"] for o in obras]
        self.assertIn("O.S.P. BUENOS AIRES (I.O.M.A.)", nombres)
        self.assertIn("OSDE", nombres)
        self.assertNotIn("GOMEZ", json.dumps(obras))

    def test_dni_de_otra_fila_se_ignora(self):
        # una fila cuyo NroDoc no es el consultado no cuenta (defensa anti-ruido)
        self.assertEqual(puco._parse_publico(_PUB_OK_1, "99999999"), [])

    def test_ex_es_no_disponible_no_no_figura(self):
        self.assertIsNone(puco._parse_publico(_PUB_EX, "30111222"))

    def test_basura_es_none(self):
        self.assertIsNone(puco._parse_publico("no-json", "30111222"))
        self.assertIsNone(puco._parse_publico("//OK[1,2,3]", "30111222"))


class TestConsultarPublico(unittest.TestCase):
    def setUp(self):
        _limpiar(self)
        _env.set_env(self, TURNOS_FAKE_PUCO="0")   # forzar el camino real → público
        puco._PUB_SESSION.update(opener=object(), ts=puco.time.time())  # sesión "viva"

    def tearDown(self):
        puco._PUB_SESSION.update(opener=None, ts=0.0)

    def test_consultar_usa_el_publico_sin_credenciales(self):
        calls = []

        def fake_post(opener, path, body, perm):
            calls.append(path)
            return _PUB_OK_1

        orig = puco._pub_post
        puco._pub_post = fake_post
        try:
            ok, obras = puco.consultar("30111222")
        finally:
            puco._pub_post = orig
        self.assertTrue(ok)
        esperada = {"nombre": "Swiss Medical", "rnos": None,
                    "nombre_padron": "SWISS MEDICAL S.A."}
        self.assertEqual(obras, [esperada])
        self.assertIn("/service/list", calls)
        # y quedó cacheado para el registro que sigue
        self.assertEqual(puco.cacheada("30111222"), [esperada])

    def test_ex_reintenta_y_degrada_a_no_disponible(self):
        def always_ex(opener, path, body, perm):
            return _PUB_EX if path == "/service/list" else "//OK[1,[],0,7]"

        orig = puco._pub_post
        puco._pub_post = always_ex
        try:
            ok, obras = puco.consultar("30111222")
        finally:
            puco._pub_post = orig
        self.assertFalse(ok)      # nunca un falso "no figura"
        self.assertEqual(obras, [])


class TestParseRespuesta(unittest.TestCase):
    def test_xml_ok(self):
        raw = ("<puco><resultado>OK</resultado>"
               "<coberturaSocial>O.S.P. BUENOS AIRES (I.O.M.A.)</coberturaSocial>"
               "<denominacion>PEREZ JUAN</denominacion>"
               "<nrodoc>30123456</nrodoc><tipodoc>DNI</tipodoc>"
               "<rnos>902001</rnos></puco>")
        resultado, obras = puco._parse_respuesta(raw)
        self.assertEqual(resultado, "OK")
        self.assertEqual(len(obras), 1)
        self.assertEqual(obras[0]["rnos"], "902001")
        self.assertIn("I.O.M.A", obras[0]["nombre"])

    def test_xml_con_dos_coberturas(self):
        # el padrón consolidado puede tener más de una fila por persona
        raw = ("<pucos><resultado>OK</resultado>"
               "<puco><coberturaSocial>O.S.P. BUENOS AIRES (I.O.M.A.)</coberturaSocial>"
               "<rnos>902001</rnos></puco>"
               "<puco><coberturaSocial>SWISS MEDICAL S.A.</coberturaSocial>"
               "<rnos>127605</rnos></puco></pucos>")
        resultado, obras = puco._parse_respuesta(raw)
        self.assertEqual(resultado, "OK")
        self.assertEqual([o["nombre"] for o in obras],
                         ["O.S.P. BUENOS AIRES (I.O.M.A.)", "SWISS MEDICAL S.A."])
        self.assertEqual([o["rnos"] for o in obras], ["902001", "127605"])

    def test_error_autenticacion_no_es_un_no_figura(self):
        raw = "<puco><resultado>ERROR_AUTENTICACION</resultado></puco>"
        resultado, obras = puco._parse_respuesta(raw)
        self.assertNotEqual(resultado, "OK")
        self.assertEqual(obras, [])

    def test_ok_sin_cobertura_es_no_figura(self):
        raw = "<puco><resultado>OK</resultado><nrodoc>30123458</nrodoc></puco>"
        resultado, obras = puco._parse_respuesta(raw)
        self.assertEqual(resultado, "OK")
        self.assertEqual(obras, [])


class TestEndpoint(unittest.TestCase):
    def setUp(self):
        _limpiar(self)
        self.app = FastAPI()
        self.app.include_router(puco_router.router)
        self.client = TestClient(self.app)
        self._ip_clients = {}

    def tearDown(self):
        self.client.close()
        for c in self._ip_clients.values():
            c.close()

    def _client_for_ip(self, ip):
        # proxy_trust.real_peer_from() (core/proxy_trust.py) fails closed on
        # a spoofable X-Forwarded-For header outside signed-proxy mode and
        # resolves the peer from the ASGI connection's client host instead —
        # every TestClient defaults to the SAME fixed host, so a header alone
        # can't distinguish "IP"s for the rate limiter. Bind a distinct
        # TestClient per fake peer so `abuse_guard.client_ip`/`puco._HITS`
        # actually key on different identities, one per test.
        if ip not in self._ip_clients:
            self._ip_clients[ip] = TestClient(self.app, client=(ip, 12345))
        return self._ip_clients[ip]

    def _post(self, dni, ip="1.2.3.4"):
        return self._client_for_ip(ip).post("/api/puco", json={"dni": dni})

    def test_hit(self):
        h = self._post("30.123.450")  # normaliza puntos
        self.assertEqual(h.status_code, 200)
        j = h.json()
        self.assertTrue(j["disponible"])
        self.assertEqual(len(j["obras_sociales"]), 1)
        self.assertEqual(j["obras_sociales"][0]["nombre"], "IOMA")
        # compat con el JS viejo cacheado: obra_social = la primera
        self.assertEqual(j["obra_social"]["nombre"], "IOMA")

    def test_hit_multiple(self):
        h = self._post("30123454")
        j = h.json()
        self.assertTrue(j["disponible"])
        self.assertEqual(len(j["obras_sociales"]), 2)
        self.assertEqual(j["obra_social"]["nombre"], "IOMA")  # compat: la primera

    def test_miss(self):
        h = self._post("30123458")
        j = h.json()
        self.assertTrue(j["disponible"])
        self.assertEqual(j["obras_sociales"], [])
        self.assertIsNone(j["obra_social"])

    def test_sin_credenciales_disponible_false(self):
        _env.set_env(self, TURNOS_FAKE_PUCO="0")
        h = self._post("30123450")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json(), {"disponible": False})

    def test_dni_invalido_400(self):
        h = self._post("123")
        self.assertEqual(h.status_code, 400)

    def test_rate_limit_por_ip_y_cache_no_consume(self):
        _env.set_env(self, TURNOS_PUCO_PER_HOUR="2")
        self._post("30123450", ip="9.9.9.9")
        self._post("30123451", ip="9.9.9.9")
        h = self._post("30123452", ip="9.9.9.9")
        self.assertEqual(h.status_code, 429)
        # un DNI ya cacheado responde igual, sin consumir cupo
        h2 = self._post("30123450", ip="9.9.9.9")
        self.assertEqual(h2.status_code, 200)
        # otra IP no está limitada
        h3 = self._post("30123453", ip="8.8.8.8")
        self.assertEqual(h3.status_code, 200)

    def test_solo_post(self):
        self.assertEqual(self.client.get("/api/puco").status_code, 405)


class TestNormCobertura(unittest.TestCase):
    def setUp(self):
        _limpiar(self)

    def test_el_nombre_del_padron_se_marca_puco_lo_demas_declarada(self):
        # el cliente manda la lista (padrón pre-llenado + lo tipeado); el server
        # marca 'puco' sólo lo que matchea su cache, el resto 'declarada'.
        puco.consultar("30123450")  # cache: IOMA
        out = accounts._norm_coverage(
            {"obras_sociales": [
                {"nombre": "IOMA"},   # el cliente ecoa el nombre amigable de /api/puco
                {"nombre": "OSDE BINARIO"}],
             "prepaga": "OSDE"},
            dni="30123450")
        obras = out["obras_sociales"]
        self.assertEqual(len(obras), 2)
        self.assertEqual(obras[0]["origen"], "puco")
        self.assertEqual(obras[0]["nombre"], "IOMA")
        self.assertEqual(obras[1], {"nombre": "OSDE BINARIO", "origen": "declarada"})
        self.assertEqual(out["prepaga"], {"nombre": "OSDE"})  # string viejo -> dict

    def test_padron_multiple_enviado_se_marca_puco(self):
        puco.consultar("30123455")  # figura en DOS padrones (IOMA + OSECAC)
        out = accounts._norm_coverage(
            {"obras_sociales": [
                {"nombre": "IOMA"},
                {"nombre": "OSECAC (Empleados de Comercio)"}]},
            dni="30123455")
        obras = out["obras_sociales"]
        self.assertEqual(len(obras), 2)
        self.assertTrue(all(o["origen"] == "puco" for o in obras))
        self.assertIn("OSECAC", obras[1]["nombre"])

    def test_quitar_una_obra_del_padron_la_borra(self):
        # el cliente es la fuente de verdad: si NO manda una obra del padrón,
        # se borra (aunque siga en el cache) — el responsable del producto 2026-07-16, obras removibles.
        puco.consultar("30123455")  # cache: IOMA + OSECAC
        out = accounts._norm_coverage(
            {"obras_sociales": [{"nombre": "IOMA"}]},
            dni="30123455")
        obras = out["obras_sociales"]
        self.assertEqual(len(obras), 1)
        self.assertEqual(obras[0]["nombre"], "IOMA")

    def test_editar_el_nombre_del_padron_lo_degrada_a_declarada(self):
        puco.consultar("30123450")  # cache: IOMA
        out = accounts._norm_coverage(
            {"obras_sociales": [{"nombre": "IOMA CORREGIDO A MANO"}]},
            dni="30123450")
        self.assertEqual(out["obras_sociales"][0]["origen"], "declarada")

    def test_body_viejo_con_obra_social_singular(self):
        out = accounts._norm_coverage({"obra_social": {"nombre": "IOMA"}}, dni="30123458")
        self.assertEqual(out["obras_sociales"],
                         [{"nombre": "IOMA", "origen": "declarada"}])

    def test_afiliado_y_plan(self):
        puco.consultar("30123450")
        out = accounts._norm_coverage(
            {"obras_sociales": [{"nombre": "IOMA",
                                 "afiliado": "123-456"}],
             "prepaga": {"nombre": "OSDE", "afiliado": "62 865209 0 01", "plan": "210"}},
            dni="30123450")
        # el nº de afiliado viene del body aunque la obra social sea del padrón
        obra = out["obras_sociales"][0]
        self.assertEqual(obra["origen"], "puco")
        self.assertEqual(obra["afiliado"], "123-456")
        self.assertEqual(out["prepaga"],
                         {"nombre": "OSDE", "afiliado": "62 865209 0 01", "plan": "210"})

    def test_afiliado_vacio_se_borra_del_registro_previo(self):
        existente = {"obras_sociales": [{"nombre": "IOMA", "origen": "puco", "afiliado": "999"}]}
        out = accounts._norm_coverage(
            {"obras_sociales": [{"nombre": "IOMA", "afiliado": ""}]},
            dni="30123458", existente=existente)
        self.assertNotIn("afiliado", out["obras_sociales"][0])
        self.assertEqual(out["obras_sociales"][0]["origen"], "puco")

    def test_nombre_igual_conserva_el_registro_previo(self):
        existente = {"obras_sociales": [{"nombre": "IOMA", "rnos": "902001", "origen": "puco"}]}
        out = accounts._norm_coverage({"obras_sociales": [{"nombre": "IOMA"}]},
                                      dni="30123458", existente=existente)
        self.assertEqual(out["obras_sociales"][0]["origen"], "puco")
        self.assertEqual(out["obras_sociales"][0]["rnos"], "902001")

    def test_existente_viejo_singular_tambien_se_conserva(self):
        existente = {"obra_social": {"nombre": "IOMA", "rnos": "902001", "origen": "puco"}}
        out = accounts._norm_coverage({"obras_sociales": [{"nombre": "IOMA"}]},
                                      dni="30123458", existente=existente)
        self.assertEqual(out["obras_sociales"][0]["rnos"], "902001")

    def test_lista_vacia_mencionada_borra(self):
        existente = {"obras_sociales": [{"nombre": "IOMA", "origen": "declarada"}]}
        out = accounts._norm_coverage({"obras_sociales": []},
                                      dni="30123458", existente=existente)
        self.assertIsNone(out)

    def test_sin_mencionar_conserva_lo_guardado(self):
        existente = {"obras_sociales": [{"nombre": "IOMA", "origen": "declarada"}]}
        out = accounts._norm_coverage({"prepaga": "OSDE"},
                                      dni="30123458", existente=existente)
        self.assertEqual(out["obras_sociales"], existente["obras_sociales"])

    def test_vacio_es_none(self):
        self.assertIsNone(accounts._norm_coverage({}, dni="30123458"))
        self.assertIsNone(accounts._norm_coverage(None, dni="30123458"))

    def test_strings_largos_se_recortan(self):
        out = accounts._norm_coverage({"prepaga": "x" * 500}, dni="30123458")
        self.assertEqual(len(out["prepaga"]["nombre"]), 120)

    def test_prepaga_string_suelto_sigue_valiendo(self):
        # Cliente viejo que manda la prepaga como string → {nombre}.
        out = accounts._norm_coverage({"prepaga": "Medicus"}, dni="30123458")
        self.assertEqual(out["prepaga"], {"nombre": "Medicus"})

    def test_norm_avisos(self):
        self.assertEqual(accounts._norm_notifications({"whatsapp": True, "email": False}),
                         {"whatsapp": True, "email": False})
        # sin la key en el body → conserva lo guardado
        self.assertEqual(accounts._norm_notifications(None, existente={"email": False}),
                         {"email": False})
        self.assertIsNone(accounts._norm_notifications(None))


class TestRegistroConCobertura(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        _limpiar(self)

    def _registrar(self, dni, cobertura=None):
        body = {"dni": dni, "password": "clave123", "nombre": "Ana", "apellido": "Prueba",
                "contacto": "2254550000"}
        if cobertura is not None:
            body["cobertura"] = cobertura
        return accounts._register_core(body, "127.0.0.1")

    def test_registro_adjunta_obras_del_padron(self):
        # el form consultó PUCO al tipear el DNI (termina en 4: DOS padrones) y
        # manda la lista completa: los dos del padrón + una declarada.
        puco.consultar("40111224")
        h = self._registrar("40111224",
                            {"obras_sociales": [
                                {"nombre": "IOMA", "afiliado": "555777"},
                                {"nombre": "OSECAC (Empleados de Comercio)"},
                                {"nombre": "OTRA DECLARADA"}],
                             "prepaga": {"nombre": "Swiss Medical",
                                         "afiliado": "800.006-3055088", "plan": "SMG20"}})
        self.assertEqual(h.status, 200, h.json_body)
        u = db_accounts.get_user("40111224")
        obras = u["cobertura"]["obras_sociales"]
        self.assertEqual(len(obras), 3)  # 2 del padrón + 1 declarada
        self.assertEqual(obras[0]["origen"], "puco")
        self.assertEqual(obras[0]["nombre"], "IOMA")
        self.assertEqual(obras[0]["afiliado"], "555777")
        self.assertEqual(obras[1]["origen"], "puco")
        self.assertIn("OSECAC", obras[1]["nombre"])
        self.assertEqual(obras[2], {"nombre": "OTRA DECLARADA", "origen": "declarada"})
        self.assertEqual(u["cobertura"]["prepaga"],
                         {"nombre": "Swiss Medical", "afiliado": "800.006-3055088",
                          "plan": "SMG20"})

    def test_registro_sin_padron_guarda_lo_declarado(self):
        h = self._registrar("40111228", {"obra_social": "PAMI"})
        self.assertEqual(h.status, 200, h.json_body)
        u = db_accounts.get_user("40111228")
        self.assertEqual(u["cobertura"]["obras_sociales"],
                         [{"nombre": "PAMI", "origen": "declarada"}])
        self.assertNotIn("prepaga", u["cobertura"])

    def test_registro_sin_cobertura_queda_null(self):
        h = self._registrar("40111238")
        self.assertEqual(h.status, 200, h.json_body)
        self.assertIsNone(db_accounts.get_user("40111238")["cobertura"])

    def test_actualizar_sin_la_key_conserva_la_cobertura(self):
        puco.consultar("40111240")  # cache: IOMA
        h = self._registrar("40111240",
                            {"obras_sociales": [{"nombre": "IOMA"}],
                             "prepaga": "OSDE"})
        # autosave viejo sin `cobertura` en el body → no borra lo guardado
        h2 = accounts._update_core(
            db_accounts.get_user("40111240"), {"nombre": "Ana María", "contacto": "2254550000"})
        self.assertEqual(h2.status, 200, h2.json_body)
        u = db_accounts.get_user("40111240")
        self.assertEqual(u["nombre"], "Ana María")
        self.assertEqual(u["cobertura"]["prepaga"], {"nombre": "OSDE"})
        self.assertEqual(u["cobertura"]["obras_sociales"][0]["origen"], "puco")

    def test_actualizar_edita_la_prepaga(self):
        h = self._registrar("40111248", {"prepaga": "OSDE"})
        h2 = accounts._update_core(
            db_accounts.get_user("40111248"),
            {"nombre": "Ana", "contacto": "2254550000",
             "coverage_revision": accounts._user_json(db_accounts.get_user("40111248"))["coverage_revision"],
             "cobertura": {"prepaga": {"nombre": "Galeno", "plan": "220"}}})
        self.assertEqual(h2.status, 200, h2.json_body)
        u = db_accounts.get_user("40111248")
        self.assertEqual(u["cobertura"]["prepaga"], {"nombre": "Galeno", "plan": "220"})
        # desde 2026-07-24 lo guardado dice además cuál cobertura manda
        # (health_coverage.py): con una sola, esa.
        self.assertEqual(u["cobertura"]["principal"], "prepaga")


if __name__ == "__main__":
    unittest.main()
