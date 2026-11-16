"""test_lab_results.py — «Resultados de laboratorio» en la cuenta del
paciente (lab_results.py + lab_analysis.py, rediseño 2026-08-06,
docs/architecture/laboratorio-paciente.md).

Cubre lab_results.py: la categorización (tipo=="laboratorio"), el gate por
sesión (sin sesión 401, la sesión de UN paciente nunca ve la historia de
otro), el orden más nuevo primero, el estado vacío (nunca un error), y el
link a un clínico.

Cubre lab_analysis.py (el motor de IA, cacheado): el cache hit (segunda
lectura = cero llamadas), la invalidación por prompt editado, la redacción
server-side de un informe sensible (tampoco en la serie), el piso de
sensibilidad por palabra clave, el fallback determinístico (sólo valores
catalogados, nunca texto OCR sin vetear), los topes de lote, y que un valor
catalogado extraído por la IA sí entra a la serie.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_lab_results -v
"""

import os
import sys
import json
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_ai  # noqa: E402
from app.cuentas import accounts      # noqa: E402
from app.ai import lab_analysis  # noqa: E402
from app.ai import lab_results  # noqa: E402
from app.ai import lab_results_router  # noqa: E402
from app.ai import prompts  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_labresults", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _cookie_header(token):
    return {"Cookie": "%s=%s" % (accounts.COOKIE_NAME, token)}


def _truncate():
    import psycopg
    with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE users, sessions, dependents, clinical_history_records, "
                       "lab_report_analysis, patient_lab_uploads, ai_prompts "
                       "RESTART IDENTITY CASCADE")


def _informe_batch(body, **_kwargs):
    """Modelo falso genérico: ecoa un informe 'ok' sin valores por cada idx
    que le mandaron — para tests que sólo miden CUÁNTAS veces se llamó, no
    qué contestó."""
    contenido = body["messages"][0]["content"]
    datos = json.loads(contenido.split("INFORMES:\n", 1)[1])
    salida = [{"idx": item["idx"], "tipo_estudio": "Análisis de laboratorio",
              "resumen": "Todos los valores dentro del rango de referencia.",
              "tono": "ok", "sensible": False, "valores": []}
             for item in datos["informes"]]
    return {"choices": [{"message": {"content": json.dumps({"informes": salida})}}]}


_LAB_RECORD = {
    "fecha": "2024-02-15", "tipo": "laboratorio", "servicio": "Laboratorio Central",
    "texto": "LABORATORIO CENTRAL\nGlucemia: 138 mg/dl (VR 70-110)",
    "valores": {"glucemia_mg_dl": 138, "hba1c_pct": 7.2},
}
_LAB_RECORD_VIEJO = {
    "fecha": "2020-04-20", "tipo": "laboratorio", "servicio": "Laboratorio Alepo",
    "texto": "Col total 210", "valores": {"colesterol_total_mg_dl": 210},
}
_EVOLUCION_RECORD = {
    "fecha": "2024-02-16", "tipo": "evolucion", "servicio": "Clínica Médica",
    "texto": "Control.", "valores": {"ta_sistolica": 138, "ta_diastolica": 85},
}


class TestValorItemUnit(unittest.TestCase):
    def test_clave_catalogada_dentro_y_fuera_de_rango(self):
        dentro = lab_analysis._valor_item("glucemia_mg_dl", 95)
        self.assertFalse(dentro["fuera"])
        self.assertEqual(dentro["unidad"], "mg/dL")
        fuera = lab_analysis._valor_item("glucemia_mg_dl", 138)
        self.assertTrue(fuera["fuera"])

    def test_clave_no_catalogada_none(self):
        self.assertIsNone(lab_analysis._valor_item("campo_inventado", 1))

    def test_peso_sin_rango_nunca_fuera_de_rango(self):
        item = lab_analysis._valor_item("peso_kg", 999)
        self.assertFalse(item["fuera"])


class TestReportHash(unittest.TestCase):
    def test_mismo_contenido_mismo_hash(self):
        self.assertEqual(lab_analysis.report_hash(_LAB_RECORD),
                         lab_analysis.report_hash(dict(_LAB_RECORD)))

    def test_informe_corregido_es_otro_hash(self):
        corregido = dict(_LAB_RECORD, texto=_LAB_RECORD["texto"] + " (corregido)")
        self.assertNotEqual(lab_analysis.report_hash(_LAB_RECORD),
                            lab_analysis.report_hash(corregido))


class TestInformesDeFallback(unittest.TestCase):
    """Sin IA (TURNOS_FAKE_AI=1, tal cual quedó seteado al importar el
    módulo): cubre lo que no necesita mockear ai_client — orden, scoping por
    sesión, estado vacío, y el fallback determinístico en sí."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _truncate()

    def test_todo_informe_de_laboratorio_se_muestra_mas_nuevo_primero(self):
        db_clinical.clinical_history_replace(
            "40111222", [_LAB_RECORD, _LAB_RECORD_VIEJO, _EVOLUCION_RECORD], "local_history")
        informes, estados, series, pendiente = lab_results.informes_de("40111222")
        self.assertEqual([i["fecha"] for i in informes], ["2024-02-15", "2020-04-20"])
        self.assertEqual(estados.get("local_history"), "ok")
        self.assertEqual(pendiente, 0)
        nuevo = informes[0]
        claves = {it["clave"] for it in nuevo["items"]}
        self.assertEqual(claves, {"glucemia_mg_dl", "hba1c_pct"})
        glucemia = next(it for it in nuevo["items"] if it["clave"] == "glucemia_mg_dl")
        self.assertTrue(glucemia["fuera"])
        self.assertEqual(glucemia["rango"], [70, 110])
        self.assertIn(nuevo["tono"], ("ok", "revisar"))

    def test_dni_sin_historia_lista_vacia(self):
        informes, estados, series, pendiente = lab_results.informes_de("40999888")
        self.assertEqual(informes, [])
        self.assertEqual(series, [])
        self.assertEqual(pendiente, 0)

    def test_dni_vacio_lista_vacia_sin_explotar(self):
        informes, estados, series, pendiente = lab_results.informes_de("")
        self.assertEqual(informes, [])

    def test_fallback_expone_solo_valores_catalogados(self):
        # El texto trae un hallazgo que asusta y NO está en el catálogo — el
        # fallback determinístico nunca lo puede mostrar (nadie lo vetó).
        peligroso = dict(_LAB_RECORD,
                         texto="LABORATORIO\nGlucemia: 138 mg/dl\n"
                               "SOSPECHA DE NEOPLASIA MALIGNA — urgente",
                         valores={"glucemia_mg_dl": 138})
        db_clinical.clinical_history_replace("40111222", [peligroso], "local_history")
        informes, estados, series, pendiente = lab_results.informes_de("40111222")
        self.assertEqual(len(informes), 1)
        crudo = json.dumps(informes[0], ensure_ascii=False).lower()
        self.assertNotIn("neoplasia", crudo)
        self.assertNotIn("maligna", crudo)
        claves = {it["clave"] for it in informes[0]["items"]}
        self.assertEqual(claves, {"glucemia_mg_dl"})


class TestHandleHttp(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _truncate()
        app = FastAPI()
        app.include_router(lab_results_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_ruta_ajena_false(self):
        self.assertEqual(self.client.get("/api/account/otra-cosa").status_code, 404)
        self.assertEqual(self.client.post("/api/account/lab-results").status_code, 405)

    def test_sin_sesion_401(self):
        response = self.client.get("/api/account/lab-results")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "auth_requerida")

    def test_paciente_ve_solo_su_propia_historia(self):
        db_accounts.create_user("40111222", "Ana", "Gómez", "1990-05-05", "F", "2254550001", "clave1234")
        db_accounts.create_user("40111333", "Otro", "Paciente", "1985-01-01", "M", "2254550002", "clave5678")
        db_clinical.clinical_history_replace("40111222", [_LAB_RECORD], "local_history")
        db_clinical.clinical_history_replace("40111333", [_LAB_RECORD_VIEJO], "local_history")
        token = db_accounts.create_session("40111222")

        response = self.client.get("/api/account/lab-results", headers=_cookie_header(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["informes"]), 1)
        self.assertEqual(response.json()["informes"][0]["fecha"], "2024-02-15")
        self.assertIn("series", response.json())
        self.assertIn("analisis_pendiente", response.json())

    def test_estado_vacio_nunca_error(self):
        db_accounts.create_user("40111222", "Ana", "Gómez", "1990-05-05", "F", "2254550001", "clave1234")
        token = db_accounts.create_session("40111222")
        response = self.client.get("/api/account/lab-results", headers=_cookie_header(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["informes"], [])

    def test_link_a_un_clinico_nunca_numeral(self):
        # El pie "sacá turno con un clínico" tiene que llevar a una ruta
        # real (cluster de medicina general, o /doctors si el pack no tiene
        # ninguno configurado) — nunca un "#" muerto.
        db_accounts.create_user("40111222", "Ana", "Gómez", "1990-05-05", "F", "2254550001", "clave1234")
        token = db_accounts.create_session("40111222")
        href = self.client.get("/api/account/lab-results", headers=_cookie_header(token)).json()["clinico_href"]
        self.assertTrue(href)
        self.assertNotEqual(href, "#")
        self.assertTrue(href.startswith("/"))


class TestAnalysisEngine(unittest.TestCase):
    """Con un modelo real mockeado (TURNOS_FAKE_AI="" para la duración de
    cada test) — cache, invalidación, sensibilidad, topes de lote."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _truncate()
        prompts.invalidate_cache()

    def tearDown(self):
        prompts.invalidate_cache()

    def test_segunda_lectura_cache_hit_cero_llamadas(self):
        db_clinical.clinical_history_replace("40111222", [_LAB_RECORD], "local_history")
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_informe_batch) as llamada:
            informes1, _e, _s, pend1 = lab_results.informes_de("40111222", sync=True)
            self.assertEqual(llamada.call_count, 1)
            self.assertEqual(pend1, 0)
            informes2, _e, _s, pend2 = lab_results.informes_de("40111222", sync=True)
            self.assertEqual(llamada.call_count, 1)  # sin segunda llamada
            self.assertEqual(informes1, informes2)

    def test_prompt_editado_dispara_relectura(self):
        db_clinical.clinical_history_replace("40111222", [_LAB_RECORD], "local_history")
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_informe_batch) as llamada:
            lab_results.informes_de("40111222", sync=True)
            self.assertEqual(llamada.call_count, 1)
            db_ai.prompt_save("laboratorio_paciente", texto_en="Edited instructions.",
                           texto_es="Instrucciones editadas.", editado=True)
            prompts.invalidate_cache()
            lab_results.informes_de("40111222", sync=True)
            self.assertEqual(llamada.call_count, 2)  # el hash cambió → relee

    def test_informe_sensible_se_redacta_y_no_entra_a_la_serie(self):
        registros = [
            dict(_LAB_RECORD, fecha=f"2024-0{i}-01",
                texto=f"Glucemia: {100 + i} mg/dl", valores={"glucemia_mg_dl": 100 + i})
            for i in range(1, 4)
        ]

        def _sensible_call(body, **_kwargs):
            contenido = body["messages"][0]["content"]
            datos = json.loads(contenido.split("INFORMES:\n", 1)[1])
            salida = []
            for item in datos["informes"]:
                salida.append({
                    "idx": item["idx"], "tipo_estudio": "Serología",
                    "resumen": "Resultado registrado.", "tono": "revisar",
                    "sensible": True,
                    "valores": [{"clave": "glucemia_mg_dl", "nombre": "Glucemia",
                                "valor": 105, "unidad": "mg/dL",
                                "rango": [70, 110], "fuera": False}],
                })
            return {"choices": [{"message": {"content": json.dumps({"informes": salida})}}]}

        db_clinical.clinical_history_replace("40111222", registros, "local_history")
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_sensible_call):
            informes, _e, series, _p = lab_results.informes_de("40111222", sync=True)
        self.assertEqual(len(informes), 3)
        for informe in informes:
            # `tercero` (provenance only, no clinical content) viaja incluso en
            # el informe redactado: la cuenta marca "Subido por vos" sin revelar.
            self.assertEqual(set(informe.keys()), {"fecha", "sensible", "tercero"})
            self.assertTrue(informe["sensible"])
        self.assertEqual(series, [])  # nada catalogado llega a graficarse

    def test_piso_de_palabra_clave_fuerza_sensible(self):
        con_biopsia = dict(_LAB_RECORD,
                           texto="Biopsia de piel: informe de anatomía patológica adjunto.")

        def _dice_no_sensible(body, **_kwargs):
            contenido = body["messages"][0]["content"]
            datos = json.loads(contenido.split("INFORMES:\n", 1)[1])
            salida = [{"idx": item["idx"], "tipo_estudio": "Anatomía patológica",
                      "resumen": "Resultado registrado.", "tono": "ok",
                      "sensible": False, "valores": []}
                     for item in datos["informes"]]
            return {"choices": [{"message": {"content": json.dumps({"informes": salida})}}]}

        db_clinical.clinical_history_replace("40111222", [con_biopsia], "local_history")
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_dice_no_sensible):
            informes, _e, _s, _p = lab_results.informes_de("40111222", sync=True)
        self.assertEqual(len(informes), 1)
        self.assertEqual(set(informes[0].keys()), {"fecha", "sensible", "tercero"})
        self.assertTrue(informes[0]["sensible"])

    def test_topes_de_lote_25_informes_dos_llamadas(self):
        registros = [
            dict(_LAB_RECORD, fecha=f"2024-01-{i:02d}",
                texto=f"Informe numero {i}: Glucemia {90 + i} mg/dl",
                valores={"glucemia_mg_dl": 90 + i})
            for i in range(1, 26)
        ]
        db_clinical.clinical_history_replace("40111222", registros, "local_history")
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_informe_batch) as llamada:
            informes, _e, _s, pendiente = lab_results.informes_de("40111222", sync=True)
        self.assertEqual(llamada.call_count, 2)  # _BATCH=10 * _MAX_BATCHES=2
        self.assertEqual(pendiente, 5)           # 25 - 20 procesados
        self.assertEqual(len(informes), 20)

    def test_valor_extraido_por_ia_entra_a_la_serie(self):
        registros = [
            dict(fecha=f"2024-0{i}-01", tipo="laboratorio", servicio="Laboratorio Central",
                texto=f"Creatinina: {0.7 + i / 10:.1f} mg/dl")
            for i in range(1, 4)
        ]  # sin `valores` estructurados: la clave sólo la extrae la IA

        def _extrae_creatinina(body, **_kwargs):
            contenido = body["messages"][0]["content"]
            datos = json.loads(contenido.split("INFORMES:\n", 1)[1])
            salida = []
            for item in datos["informes"]:
                i = item["idx"]
                salida.append({
                    "idx": i, "tipo_estudio": "Función renal",
                    "resumen": "Todos los valores dentro del rango de referencia.",
                    "tono": "ok", "sensible": False,
                    "valores": [{"clave": "creatinina_mg_dl", "nombre": "Creatinina",
                                "valor": 0.7 + (i + 1) / 10, "unidad": "mg/dL",
                                "rango": [0.6, 1.2], "fuera": False}],
                })
            return {"choices": [{"message": {"content": json.dumps({"informes": salida})}}]}

        db_clinical.clinical_history_replace("40111222", registros, "local_history")
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_extrae_creatinina):
            informes, _e, series, _p = lab_results.informes_de("40111222", sync=True)
        self.assertEqual(len(informes), 3)
        for informe in informes:
            claves = {it["clave"] for it in informe["items"]}
            self.assertEqual(claves, {"creatinina_mg_dl"})
        ids = {s["id"] for s in series}
        self.assertIn("creatinina_mg_dl", ids)
        creat = next(s for s in series if s["id"] == "creatinina_mg_dl")
        self.assertEqual(len(creat["puntos"]), 3)

    def test_get_no_espera_al_modelo(self):
        """El camino del paciente (sync=False) siembra fallback y devuelve
        sin llamar al modelo en el hilo del request — la IA va en fondo.

        DNI propio (no el 40111222 compartido): con base real, los tests
        anteriores del módulo ya persistieron una lectura FRESCA para ese
        DNI y este GET la encontraría en cache (pendiente=0) en vez de
        ejercer el camino de siembra. `ai_client.available` va parcheado
        abierto por lo mismo que `primary_key`: la resolución real de
        claves (config/env) no ve el parche del módulo."""
        import time
        db_clinical.clinical_history_replace("40111299", [_LAB_RECORD], "local_history")

        def _modelo(body, **_kwargs):
            # Any call to the model from the request thread is counted below
            # (`llamada.call_count`) and fails the test; no need to make it slow.
            return _informe_batch(body)

        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.backup_key", return_value=""), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_modelo) as llamada, \
             mock.patch.object(lab_analysis, "_schedule_ia",
                               return_value=True) as sched:
            # No arrancar el hilo de verdad: sólo verificar que se programó
            # y que el GET ya devolvió con fallback + pendiente. (No
            # patchear threading.Thread — rompe el ThreadPoolExecutor de
            # _historia_clinica.)
            t0 = time.monotonic()
            informes, _e, _s, pendiente = lab_results.informes_de("40111299")
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.5)
        self.assertEqual(llamada.call_count, 0)
        self.assertEqual(pendiente, 1)
        self.assertEqual(len(informes), 1)
        self.assertTrue(informes[0]["items"])  # valores del conector YA
        sched.assert_called_once_with("40111299")


class TestFechasDeLaboratorioSonDate(unittest.TestCase):
    """problems.md №13: `lab_report_analysis.fecha` and
    `patient_lab_uploads.fecha` are `date`; an impossible or foreign-format
    date is stored as «no date», never raises, and readers still get ISO text."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _truncate()

    def test_las_columnas_son_date(self):
        import psycopg
        with psycopg.connect(_db.test_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT table_name, data_type FROM information_schema.columns "
                        "WHERE column_name='fecha' AND table_name IN "
                        "('lab_report_analysis', 'patient_lab_uploads') ORDER BY 1")
            self.assertEqual(cur.fetchall(), [("lab_report_analysis", "date"),
                                              ("patient_lab_uploads", "date")])

    def test_la_base_rechaza_una_fecha_imposible(self):
        import psycopg
        with psycopg.connect(_db.test_url, connect_timeout=5) as c, c.cursor() as cur:
            with self.assertRaises(psycopg.errors.DatetimeFieldOverflow):
                cur.execute("INSERT INTO patient_lab_uploads (dni, uploaded_by_dni, fecha) "
                            "VALUES ('1', '1', '2024-13-45')")

    def test_subidas_ordenadas_por_fecha_y_sin_fecha_al_final(self):
        _dbtest.ensure_user("40111222")   # patient_lab_uploads.uploaded_by_dni is a foreign key
        db_clinical.patient_lab_upload_insert("40111222", "40111222", fecha="2024-10-02")
        db_clinical.patient_lab_upload_insert("40111222", "40111222", fecha="2024-13-45")
        db_clinical.patient_lab_upload_insert("40111222", "40111222", fecha="2023-01-09")
        db_clinical.patient_lab_upload_insert("40111222", "40111222", fecha="9/1/2024")
        filas = db_clinical.patient_lab_uploads_by_dni("40111222")
        self.assertEqual([f["fecha"] for f in filas], ["2023-01-09", "2024-10-02", None, None])

    def test_la_fecha_que_lee_el_modelo_tiene_que_ser_un_dia_real(self):
        from app.ai import lab_upload
        self.assertEqual(lab_upload._fecha_ok("2024-02-29"), "2024-02-29")
        self.assertEqual(lab_upload._fecha_ok("2024-02-29T10:00"), "2024-02-29")
        for malo in ("2024-13-45", "2023-02-29", "15/02/2024", "", None):
            self.assertIsNone(lab_upload._fecha_ok(malo), malo)

    def test_la_lectura_del_informe_se_guarda_con_su_fecha_o_sin_ella(self):
        for rhash, fecha in (("h1", "2024-02-15"), ("h2", "2024-02-15T10:30:00"), ("h3", "15/02/2024"),
                             ("h4", "2024-02-30"), ("h5", None)):
            db_clinical.lab_analysis_upsert("40111222", rhash, "p", fecha, "alepo", "Lab", "ok", "ok",
                                   False, [], "modelo")
        por_hash = {f["report_hash"]: f["fecha"] for f in db_clinical.lab_analysis_for("40111222")}
        self.assertEqual(por_hash, {"h1": "2024-02-15", "h2": "2024-02-15", "h3": None,
                                    "h4": None, "h5": None})


class TestResultadosSubidosPorElPaciente(unittest.TestCase):
    """El paciente sube su propio resultado (conector patient_uploads):
    aparece en su cuenta marcado como aportado por él, el médico lo lee como
    no verificado, y nadie puede subirlo ni borrarlo por otra persona."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _truncate()
        app = FastAPI()
        app.include_router(lab_results_router.router)
        self.client = TestClient(app)
        db_accounts.create_user("40111222", "Ana", "Gómez", "1990-05-05", "F",
                       "2254550001", "clave1234")
        self.token = db_accounts.create_session("40111222")

    def tearDown(self):
        self.client.close()

    def _subir(self, **kwargs):
        return self.client.post(
            "/api/account/lab-results/upload",
            files={"archivo": ("laboratorio.jpg", b"\xff\xd8\xff-bytes", "image/jpeg")},
            headers=_cookie_header(self.token), **kwargs)

    def test_sin_sesion_401(self):
        response = self.client.post(
            "/api/account/lab-results/upload",
            files={"archivo": ("laboratorio.jpg", b"x", "image/jpeg")})
        self.assertEqual(response.status_code, 401)

    def test_con_sesion_y_sin_archivo_es_el_422_de_siempre(self):
        response = self.client.post("/api/account/lab-results/upload",
                                    data={"patient_dni": ""}, headers=_cookie_header(self.token))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"detail": [{
            "type": "missing", "loc": ["body", "archivo"],
            "msg": "Field required", "input": None}]})

    def test_subida_aparece_en_la_cuenta_marcada_como_aportada(self):
        response = self._subir()
        self.assertEqual(response.status_code, 200)
        # Sin IA (TURNOS_FAKE_AI=1) el archivo queda sin transcribir: la
        # cuenta lo muestra igual y la lectura llega después.
        self.assertTrue(response.json()["sin_lectura"])

        informes, estados, _series, _pend = lab_results.informes_de("40111222")
        self.assertEqual(len(informes), 1)
        self.assertTrue(informes[0]["tercero"])
        self.assertEqual(estados.get("patient_uploads"), "ok")

    def test_formato_no_soportado_y_archivo_muy_pesado(self):
        malo = self.client.post(
            "/api/account/lab-results/upload",
            files={"archivo": ("virus.exe", b"MZ", "application/x-msdownload")},
            headers=_cookie_header(self.token))
        self.assertEqual(malo.status_code, 415)
        pesado = self.client.post(
            "/api/account/lab-results/upload",
            files={"archivo": ("grande.pdf",
                               b"%PDF" + b"0" * (lab_results.MAX_UPLOAD_BYTES + 1),
                               "application/pdf")},
            headers=_cookie_header(self.token))
        self.assertEqual(pesado.status_code, 413)

    def test_dni_ajeno_rechazado_y_persona_a_cargo_aceptada(self):
        ajeno = self.client.post(
            "/api/account/lab-results/upload",
            files={"archivo": ("laboratorio.jpg", b"x", "image/jpeg")},
            data={"patient_dni": "40999888"},
            headers=_cookie_header(self.token))
        self.assertEqual(ajeno.status_code, 403)
        self.assertEqual(ajeno.json()["error"], "paciente_no_vinculado")

        db_slots.save_dependent("40111222", "50222333", "Juan", "Gómez",
                          "2015-03-03", "M", "hijo")
        propio = self.client.post(
            "/api/account/lab-results/upload",
            files={"archivo": ("laboratorio.jpg", b"x", "image/jpeg")},
            data={"patient_dni": "50222333"},
            headers=_cookie_header(self.token))
        self.assertEqual(propio.status_code, 200)
        informes, _e, _s, _p = lab_results.informes_de("50222333")
        self.assertEqual(len(informes), 1)
        self.assertEqual(lab_results.informes_de("40111222")[0], [])

    def test_borrar_lo_esconde_y_solo_para_quien_lo_subio(self):
        upload_id = self._subir().json()["id"]
        db_accounts.create_user("40111333", "Otro", "Paciente", "1985-01-01", "M",
                       "2254550002", "clave5678")
        otro = db_accounts.create_session("40111333")
        ajeno = self.client.delete(
            "/api/account/lab-results/upload/%s" % upload_id,
            headers=_cookie_header(otro))
        self.assertEqual(ajeno.status_code, 404)
        self.assertEqual(len(lab_results.informes_de("40111222")[0]), 1)

        propio = self.client.delete(
            "/api/account/lab-results/upload/%s" % upload_id,
            headers=_cookie_header(self.token))
        self.assertEqual(propio.status_code, 200)
        self.assertEqual(lab_results.informes_de("40111222")[0], [])

    def test_el_conector_esta_en_el_fan_out_y_en_la_procedencia(self):
        from app.ai import chart_summary
        self.assertIn("patient_uploads", chart_summary._history_connector_ids())
        self._subir()
        historia, estados = chart_summary._historia_clinica("40111222")
        propias = [e for e in historia if e.get("sistema") == "patient_uploads"]
        self.assertEqual(len(propias), 1)
        self.assertTrue(propias[0]["tercero"])
        partes = chart_summary._fuentes_linea([], [], historia, estados)
        self.assertTrue(any(p.startswith("Aportado por el paciente: 1 registros")
                            for p in partes))


def _traducciones(prefijo="labs.reading."):
    """Las filas de la caché de traducción que cuelgan de `prefijo`."""
    import psycopg
    with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("SELECT source_key, lang FROM translations "
                        "WHERE source_key LIKE %s ORDER BY source_key, lang",
                        (prefijo + "%",))
            return cur.fetchall()


class TestIdiomaDeLaLectura(unittest.TestCase):
    """TASK-20: la lectura se guarda en castellano y el paciente la lee en
    el idioma de su página — el fallback se vuelve a armar, la lectura de IA
    sale de la caché de traducción, y el GET nunca llama al modelo."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        import psycopg
        from app.core import translate
        _truncate()
        prompts.invalidate_cache()
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c:
            c.execute("DELETE FROM translations")
        translate.invalidate_rows()
        translate.reset_call_count()
        self.translate = translate
        self._traductor("EN {t}")

    def tearDown(self):
        prompts.invalidate_cache()
        self.translate.invalidate_rows()

    def _en(self):
        from app.core import i18n
        _lang, token = i18n.bind_lang("en")
        self.addCleanup(i18n.unbind, token)

    def _informe(self, **valores):
        registro = dict(_LAB_RECORD, valores=valores)
        db_clinical.clinical_history_replace("40111222", [registro], "local_history")
        return registro

    # ---- fallback sin IA: se rearma con el catálogo -------------------------

    def test_fallback_en_ingles_segun_cuantos_valores_estan_fuera(self):
        casos = [
            ({"glucemia_mg_dl": 138, "hba1c_pct": 7.2},
             "There are 2 values to go over with your doctor at your next visit."),
            ({"glucemia_mg_dl": 138},
             "There is 1 value to go over with your doctor at your next visit."),
            ({"glucemia_mg_dl": 90},
             "All values are within the reference range."),
            ({}, "We couldn't read the values in this report yet."),
        ]
        for valores, esperado in casos:
            with self.subTest(valores=valores):
                self._informe(**valores)
                es = lab_results.informes_de("40111222")[0][0]["resumen"]
                self.assertNotEqual(es, esperado)
                from app.core import i18n
                _lang, token = i18n.bind_lang("en")
                try:
                    en = lab_results.informes_de("40111222")[0][0]
                finally:
                    i18n.unbind(token)
                self.assertEqual(en["resumen"], esperado)

    def test_fallback_en_ingles_traduce_tipo_y_nombres_de_determinaciones(self):
        self._informe(glucemia_mg_dl=138, hba1c_pct=7.2)
        self._en()
        informes, _e, series, _p = lab_results.informes_de("40111222")
        informe = informes[0]
        self.assertIn("Blood glucose", informe["tipo_estudio"])
        self.assertNotIn("Glucemia", informe["tipo_estudio"])
        nombres = {it["clave"]: it["nombre"] for it in informe["items"]}
        self.assertEqual(nombres["glucemia_mg_dl"], "Blood glucose")
        self.assertEqual(nombres["hba1c_pct"], "HbA1c")
        etiquetas = {s["id"]: s["label"] for s in series}
        if "glucemia_mg_dl" in etiquetas:
            self.assertEqual(etiquetas["glucemia_mg_dl"], "Blood glucose")

    def test_en_castellano_el_fallback_queda_como_siempre(self):
        self._informe(glucemia_mg_dl=138, hba1c_pct=7.2)
        informe = lab_results.informes_de("40111222")[0][0]
        self.assertEqual(informe["resumen"],
                         "Hay 2 valores para revisar con tu médico en la próxima consulta.")
        self.assertIn("Glucemia", informe["tipo_estudio"])

    # ---- lectura de IA: se guarda en castellano, se traduce al mostrar ------

    def _con_ia(self):
        return (_env.patched_env({"TURNOS_FAKE_AI": "",
                                  "TURNOS_FAKE_TRANSLATE": "1",
                                  "LANGUAGES": "es,en"}),
                mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"),
                mock.patch("app.ai.ai_client.available", return_value=True),
                mock.patch("app.ai.ai_client.call", side_effect=_informe_batch))

    def _traductor(self, formato):
        """El traductor falso de `core/translate.py` antepone «[en] »; salvo en
        la prueba de la marca, el «traductor» de esta clase devuelve texto sin
        marca, como el modelo real."""
        patcher = mock.patch.object(self.translate, "_fake_translate",
                                    side_effect=lambda t, lang: formato.format(t=t, l=lang))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lectura_de_ia_se_traduce_al_mostrar_sin_llamar_al_modelo(self):
        db_clinical.clinical_history_replace("40111222", [_LAB_RECORD], "local_history")
        env, clave, disp, llamada = self._con_ia()
        with env, clave, disp, llamada as modelo:
            es = lab_results.informes_de("40111222", sync=True)[0][0]
            self.assertEqual(es["resumen"],
                             "Todos los valores dentro del rango de referencia.")
            self.assertEqual(modelo.call_count, 1)
            self.translate.reset_call_count()   # el pase de fondo ya tradujo
            self._en()
            en = lab_results.informes_de("40111222", sync=True)[0][0]
            self.assertEqual(
                en["resumen"],
                "EN Todos los valores dentro del rango de referencia.")
            self.assertEqual(en["tipo_estudio"], "EN Análisis de laboratorio")
            # El GET en inglés no volvió a leer el informe ni a traducirlo.
            self.assertEqual(modelo.call_count, 1)
            self.assertEqual(self.translate.call_count(), 0)

    def test_la_marca_del_traductor_falso_nunca_llega_al_paciente(self):
        # Con el proveedor falso la caché guarda «[en] <texto>»: es prueba de
        # la caché, no texto de paciente. Se lee el original, sin marca.
        self._traductor("[{l}] {t}")
        db_clinical.clinical_history_replace("40111222", [_LAB_RECORD], "local_history")
        env, clave, disp, llamada = self._con_ia()
        with env, clave, disp, llamada:
            lab_results.informes_de("40111222", sync=True)
            self.assertEqual(len(_traducciones()), 2)   # la marca sí quedó en caché
            self._en()
            en = lab_results.informes_de("40111222", sync=True)[0][0]
        self.assertEqual(en["resumen"],
                         "Todos los valores dentro del rango de referencia.")
        self.assertEqual(en["tipo_estudio"], "Análisis de laboratorio")
        self.assertNotIn("[en]", json.dumps(en, ensure_ascii=False))

    def test_la_traduccion_no_se_guarda_en_la_lectura(self):
        db_clinical.clinical_history_replace("40111222", [_LAB_RECORD], "local_history")
        env, clave, disp, llamada = self._con_ia()
        with env, clave, disp, llamada:
            lab_results.informes_de("40111222", sync=True)
            self._en()
            lab_results.informes_de("40111222", sync=True)
            guardada = db_clinical.lab_analysis_lecturas()
        self.assertEqual(len(guardada), 1)
        self.assertEqual(guardada[0]["resumen"],
                         "Todos los valores dentro del rango de referencia.")
        self.assertNotIn("[en]", json.dumps(guardada, ensure_ascii=False))
        rhash = guardada[0]["report_hash"]
        self.assertEqual(
            _traducciones(),
            [("labs.reading.%s.resumen" % rhash, "en"),
             ("labs.reading.%s.tipo_estudio" % rhash, "en")])

    def test_lectura_aun_sin_traducir_se_muestra_en_castellano(self):
        # Sin segundo idioma habilitado nadie calienta la caché: el GET en
        # inglés degrada al castellano y no llama al modelo.
        db_clinical.clinical_history_replace("40111222", [_LAB_RECORD], "local_history")
        env, clave, disp, llamada = self._con_ia()
        with env, clave, disp, llamada as modelo, \
             _env.patched_env(LANGUAGES="es"):
            lab_results.informes_de("40111222", sync=True)
            self.assertEqual(_traducciones(), [])
            self._en()
            en = lab_results.informes_de("40111222", sync=True)[0][0]
            self.assertEqual(en["resumen"],
                             "Todos los valores dentro del rango de referencia.")
            self.assertEqual(modelo.call_count, 1)
            self.assertEqual(self.translate.call_count(), 0)

    def test_informe_sensible_y_fallback_nunca_se_mandan_a_traducir(self):
        from app.ai import lab_language
        filas = [
            {"report_hash": "h-sensible", "tipo_estudio": "Serología",
             "resumen": "Resultado registrado.", "sensible": True, "modelo": "m"},
            {"report_hash": "h-fallback", "tipo_estudio": "Glucemia",
             "resumen": "Hay 1 valor.", "sensible": False, "modelo": "fallback"},
            {"report_hash": "h-ia", "tipo_estudio": "Hemograma",
             "resumen": "Todo en orden.", "sensible": False, "modelo": "m"},
        ]
        with _env.patched_env(TURNOS_FAKE_TRANSLATE="1"):
            hechas = lab_language.calentar(filas, idiomas=["en"])
        self.assertEqual(hechas, 2)
        self.assertEqual(
            [k for k, _l in _traducciones()],
            ["labs.reading.h-ia.resumen", "labs.reading.h-ia.tipo_estudio"])

    def test_el_barrido_periodico_lleva_las_lecturas_de_ia_sin_dni(self):
        from app.ai import lab_language
        db_clinical.clinical_history_replace("40111222", [_LAB_RECORD], "local_history")
        env, clave, disp, llamada = self._con_ia()
        with env, clave, disp, llamada:
            lab_results.informes_de("40111222", sync=True)
        rhash = db_clinical.lab_analysis_lecturas()[0]["report_hash"]
        # Una lectura de fallback no entra al barrido.
        db_clinical.clinical_history_replace(
            "40111223", [dict(_LAB_RECORD_VIEJO, texto="otro")], "local_history")
        lab_results.informes_de("40111223", sync=True)
        entradas = lab_language.entradas_barrido()
        self.assertEqual(
            sorted(k for k, _t in entradas),
            ["labs.reading.%s.resumen" % rhash,
             "labs.reading.%s.tipo_estudio" % rhash])
        self.assertNotIn("40111222", json.dumps(entradas))

    def test_borrar_la_lectura_borra_sus_traducciones(self):
        from app.ai import lab_language
        filas = [{"report_hash": "h-uno", "tipo_estudio": "Hemograma",
                  "resumen": "Todo en orden.", "sensible": False, "modelo": "m"},
                 {"report_hash": "h-dos", "tipo_estudio": "Orina",
                  "resumen": "Sin hallazgos.", "sensible": False, "modelo": "m"}]
        with _env.patched_env(TURNOS_FAKE_TRANSLATE="1"):
            lab_language.calentar(filas, idiomas=["en"])
        self.assertEqual(len(_traducciones()), 4)
        lab_language.olvidar("h-uno")
        self.assertEqual([k for k, _l in _traducciones()],
                         ["labs.reading.h-dos.resumen",
                          "labs.reading.h-dos.tipo_estudio"])


if __name__ == "__main__":
    unittest.main()
