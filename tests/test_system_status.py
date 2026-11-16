"""test_system_status.py — pruebas de system_status.py (sección «Sistema»:
página de setup / estado de la integración).

Correr:  python3 -m unittest test.test_system_status -v
(o vía discover, que es como corre la suite entera).

No dependen de una base viva: los chequeos externos (Mailgun, OpenRouter) se
mockean parcheando `_probe_json`, y el env-file canónico se apaga para que
`_env` NO caiga al checkout — así cada test controla el entorno por os.environ.
"""

import os
import sys
import types
import unittest
import json
import re
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.admin_panel import system_status as es  # noqa: E402
from app.admin_panel import system_status_router  # noqa: E402
from app.core import config  # noqa: E402


def _handler(host="turnos.pinamar.gob.ar", proto="https"):
    h = {}
    if host:
        h["Host"] = host
    if proto:
        h["X-Forwarded-Proto"] = proto
    return types.SimpleNamespace(headers=h)


class Base(unittest.TestCase):
    def setUp(self):
        # Aislar del env-file canónico: _env sólo mira os.environ.
        self._config_env_path = config.ENV_PATH
        config.ENV_PATH = ""
        # Guardar/limpiar las envs que tocamos.
        self._keys = [
            "OPENROUTER_API_KEY", "OPENROUTER_MANAGEMENT_KEY",
            "OPENROUTER_API_KEY_BACKUP", "AI_INFERENCE_PROFILE", "AI_INFERENCE_BASE_URL",
            "AI_INFERENCE_API_KEY", "AI_INFERENCE_API_KEY_BACKUP",
            "MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAIL_FROM",
            "TURNOS_WA_PROVIDER", "META_WA_TOKEN", "META_WA_PHONE_NUMBER_ID",
            "META_WA_APP_SECRET",
            "OTP_WAHA_URL", "OTP_WAHA_API_KEY", "OTP_WAHA_SESSION",
            "TURNOS_WA_NUMERO", "TURNOS_WA_WEBHOOK_SECRET",
            "TURNOS_DOMINIO", "TURNOS_REMINDERS_OFF", "TURNOS_RECORDATORIOS_OFF",
            "TURNOS_WAITLIST_OFF", "TURNOS_LISTA_ESPERA_OFF",
            "CONNECTOR_EMR_SYNC", "TURNOS_HSI_SYNC_OFF", "TURNOS_SKIP_HSI", "HSI_URL",
            "TURNOS_RENAPER_CLIENT_ID", "TURNOS_RENAPER_CLIENT_SECRET",
            "TURNOS_RENAPER_AUTHORIZE_URL", "TURNOS_RENAPER_TOKEN_URL",
            "TURNOS_RENAPER_USERINFO_URL", "TURNOS_SISA_URL", "TURNOS_PUCO_PUBLIC",
            "TURNOS_B2_KEY_ID", "TURNOS_B2_APP_KEY", "TURNOS_B2_BUCKET_ID",
            "TURNOS_B2_PREFIX",
            "TURNOS_PUBLIC_ORIGIN",
        ]
        _env.set_env(self, {k: None for k in self._keys})
        self._probe = es._probe_json
        self._reach = es._reachable
        # The sender-number lookup is cached per phone id and calls Graph API:
        # never let a test reach Meta or inherit another test's cached number.
        es._wa_number_cache.clear()
        es._probe_json = lambda *a, **k: (None, "stub")

    def tearDown(self):
        config.ENV_PATH = self._config_env_path
        es._probe_json = self._probe
        es._reachable = self._reach


class TestHelpers(Base):
    def test_peor_agrega_por_severidad(self):
        self.assertEqual(es._worst(["ok", "warn", "na"]), "warn")
        self.assertEqual(es._worst(["ok", "error", "warn"]), "error")
        self.assertEqual(es._worst(["ok", "ok"]), "ok")
        self.assertEqual(es._worst(["na", "na"]), "na")
        self.assertEqual(es._worst([]), "na")

    def test_es_provisorio(self):
        for host in ("localhost", "127.0.0.1", "devbox", "10.0.0.4",
                     "sana-turnos.brieflysites.com", "host", "algo.local"):
            self.assertTrue(es._is_provisional(host), host)
        self.assertFalse(es._is_provisional("turnos.pinamar.gob.ar"))
        self.assertFalse(es._is_provisional("pinamar.gob.ar"))


class TestDominio(Base):
    def test_dominio_definitivo_ok(self):
        _env.set_env(self, TURNOS_DOMINIO="turnos.pinamar.gob.ar")
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="https://turnos.pinamar.gob.ar")
        c = es._cluster_domain(_handler("turnos.pinamar.gob.ar", "https"))
        self.assertEqual(c["clave"], "dominio")
        self.assertEqual(c["estado"], "ok")

    def test_dominio_provisorio_warn(self):
        c = es._cluster_domain(_handler("127.0.0.1", proto=""))
        actual = [i for i in c["items"] if i["label"] == "Dominio actual"][0]
        self.assertEqual(actual["estado"], "warn")
        self.assertEqual(c["estado"], "warn")

    def test_dominio_esperado_pisa_por_env(self):
        _env.set_env(self, TURNOS_DOMINIO="turnos.otromunicipio.gob.ar")
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="https://turnos.otromunicipio.gob.ar")
        c = es._cluster_domain(_handler("turnos.otromunicipio.gob.ar", "https"))
        self.assertEqual(c["estado"], "ok")

    def test_without_public_origin_the_link_address_warns(self):
        _env.set_env(self, TURNOS_DOMINIO="turnos.pinamar.gob.ar")
        c = es._cluster_domain(_handler("turnos.pinamar.gob.ar", "https"))
        item = [i for i in c["items"] if i["label"] == "Dirección de los enlaces"][0]
        self.assertEqual(item["estado"], "warn")
        self.assertIn("dirección pública", item["detalle"])
        self.assertNotIn("TURNOS_", item["detalle"])
        self.assertEqual(c["estado"], "warn")
        # Healthy: the links go to the definitive domain, which the block
        # already shows twice — no third row with the same address.
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="https://turnos.pinamar.gob.ar")
        c = es._cluster_domain(_handler("turnos.pinamar.gob.ar", "https"))
        self.assertEqual([i for i in c["items"] if i["label"] == "Dirección de los enlaces"], [])
        self.assertEqual(c["estado"], "ok")

    def test_links_on_another_host_show_their_address(self):
        _env.set_env(self, TURNOS_DOMINIO="turnos.pinamar.gob.ar",
                     TURNOS_PUBLIC_ORIGIN="https://viejo.pinamar.gob.ar")
        c = es._cluster_domain(_handler("turnos.pinamar.gob.ar", "https"))
        item = [i for i in c["items"] if i["label"] == "Dirección de los enlaces"][0]
        self.assertEqual(item["valor"], "https://viejo.pinamar.gob.ar")

    def test_default_comes_from_configured_public_origin(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="https://appointments.example.org")
        c = es._cluster_domain(_handler("appointments.example.org", "https"))
        expected = [i for i in c["items"]
                    if i["label"] == "Dominio"][0]
        self.assertEqual(expected["valor"], "appointments.example.org")
        self.assertNotIn("pinamar", json.dumps(c).lower())
        c = es._cluster_domain(_handler("otro.example.org", "https"))
        expected = [i for i in c["items"]
                    if i["label"] == "Dominio definitivo"][0]
        self.assertEqual(expected["valor"], "appointments.example.org")

    def test_served_on_the_definitive_domain_it_is_named_once(self):
        # Critical review 08.11 (D10): «Dominio definitivo» and «Dominio
        # actual» both read demo.humanrounds.org.
        _env.set_env(self, TURNOS_DOMINIO="turnos.pinamar.gob.ar",
                     TURNOS_PUBLIC_ORIGIN="https://turnos.pinamar.gob.ar")
        c = es._cluster_domain(_handler("turnos.pinamar.gob.ar", "https"))
        valores = [i["valor"] for i in c["items"]]
        self.assertEqual(valores.count("turnos.pinamar.gob.ar"), 1)
        # Over HTTPS the domain is the block's only row (ui-expert 08.11).
        self.assertEqual([i["label"] for i in c["items"]], ["Dominio"])
        c = es._cluster_domain(_handler("turnos.pinamar.gob.ar", "http"))
        https = [i for i in c["items"] if i["label"] == "HTTPS"][0]
        self.assertEqual((https["valor"], https["estado"]), ("No", "warn"))


class TestWhatsapp(Base):
    def test_sin_configurar_es_na(self):
        c = es._cluster_whatsapp()
        self.assertEqual(c["estado"], "na")
        self.assertIn("sin conectar", c["resumen"])

    def test_meta_ok(self):
        _env.set_env(self, META_WA_TOKEN="x", META_WA_PHONE_NUMBER_ID="123456",
                     TURNOS_WA_WEBHOOK_SECRET="s3cret", META_WA_APP_SECRET="app-s3cret",
                     TURNOS_WA_NUMERO="+542254000000")
        c = es._cluster_whatsapp()
        proveedor = [i for i in c["items"] if i["label"] == "Proveedor activo"][0]
        self.assertEqual(proveedor["valor"], "Meta Cloud API")
        self.assertEqual(c["estado"], "ok")

    def _sender(self, c):
        return [i for i in c["items"] if i["label"] == "Número emisor"][0]

    def test_numero_emisor_se_muestra_como_numero_humano(self):
        _env.set_env(self, META_WA_TOKEN="x", META_WA_PHONE_NUMBER_ID="1309146762272339")
        calls = []

        def graph(url, headers=None, timeout=6):
            calls.append((url, headers))
            return {"display_phone_number": "+1 555-450-2857", "id": "1309146762272339"}, None

        es._probe_json = graph
        self.assertEqual(self._sender(es._cluster_whatsapp())["valor"], "+1 555-450-2857")
        self.assertIn("/1309146762272339?fields=display_phone_number", calls[0][0])
        self.assertEqual(calls[0][1], {"Authorization": "Bearer x"})
        # Cached: a second render of the status does not ask Meta again.
        self.assertEqual(self._sender(es._cluster_whatsapp())["valor"], "+1 555-450-2857")
        self.assertEqual(len(calls), 1)

    def _public(self, c):
        return [i for i in c["items"] if i["label"] == "Número público (handoff)"][0]

    def test_numero_publico_igual_al_emisor_se_lee_igual(self):
        _env.set_env(self, META_WA_TOKEN="x", META_WA_PHONE_NUMBER_ID="1309146762272339",
                     TURNOS_WA_NUMERO="15554502857")
        es._probe_json = lambda url, headers=None, timeout=6: (
            {"display_phone_number": "+1 555-450-2857"}, None)
        c = es._cluster_whatsapp()
        self.assertEqual(self._public(c)["valor"], "+1 555-450-2857")
        self.assertEqual(self._public(c)["valor"], self._sender(c)["valor"])

    def test_numero_publico_propio_pasa_por_el_formateador(self):
        _env.set_env(self, META_WA_TOKEN="x", META_WA_PHONE_NUMBER_ID="1309146762272339",
                     TURNOS_WA_NUMERO="5492254491670")
        es._probe_json = lambda url, headers=None, timeout=6: (
            {"display_phone_number": "+1 555-450-2857"}, None)
        self.assertEqual(self._public(es._cluster_whatsapp())["valor"], "+54 9 2254 49-1670")

    def test_numero_emisor_cae_al_id_si_meta_no_contesta(self):
        _env.set_env(self, META_WA_TOKEN="x", META_WA_PHONE_NUMBER_ID="1309146762272339")
        calls = []

        def graph(url, headers=None, timeout=6):
            calls.append(url)
            return None, "HTTP 400"

        es._probe_json = graph
        c = es._cluster_whatsapp()
        self.assertEqual(self._sender(c)["valor"], "1309146762272339")
        self.assertEqual(self._sender(c)["estado"], "ok")
        es._cluster_whatsapp()
        self.assertEqual(len(calls), 1)  # a failure is not retried on every view

    def test_numero_emisor_sin_token_no_consulta_a_meta(self):
        _env.set_env(self, META_WA_PHONE_NUMBER_ID="1309146762272339", TURNOS_WA_PROVIDER="meta")

        def graph(url, headers=None, timeout=6):
            raise AssertionError("no token, no call")

        es._probe_json = graph
        self.assertEqual(self._sender(es._cluster_whatsapp())["valor"], "1309146762272339")

    def test_meta_sin_webhook_secret_es_warn(self):
        _env.set_env(self, META_WA_TOKEN="x", META_WA_PHONE_NUMBER_ID="123456",
                     META_WA_APP_SECRET="app-s3cret", TURNOS_WA_NUMERO="+542254000000")
        c = es._cluster_whatsapp()
        self.assertEqual(c["estado"], "warn")

    def test_meta_sin_app_secret_es_warn(self):
        # Sin app secret los POST entrantes de Meta sólo pasan con el secreto
        # compartido en el query string de la callback URL — anda, pero deja
        # el secreto escrito en el panel y en los logs de cada proxy.
        _env.set_env(self, META_WA_TOKEN="x", META_WA_PHONE_NUMBER_ID="123456",
                     TURNOS_WA_WEBHOOK_SECRET="s3cret", TURNOS_WA_NUMERO="+542254000000")
        c = es._cluster_whatsapp()
        app_secret = [i for i in c["items"] if i["label"] == "App secret"][0]
        self.assertEqual(app_secret["valor"], "Falta")
        self.assertEqual(c["estado"], "warn")

    def test_waha_remoto_no_es_proveedor(self):
        # Contrato de transporte 2026-08-14: una URL de WAHA remota no habilita
        # nada — el cluster muestra "No configurado" y explica por qué.
        _env.set_env(self, OTP_WAHA_URL="https://waha.pinamar.gob.ar", OTP_WAHA_API_KEY="x",
                     TURNOS_WA_NUMERO="+542254000000")
        c = es._cluster_whatsapp()
        self.assertEqual(c["estado"], "na")
        proveedor = [i for i in c["items"] if i["label"] == "Proveedor"][0]
        self.assertIn("deshabilitado", proveedor["detalle"])

    def test_waha_loopback_sin_optin_no_es_proveedor(self):
        # Ni siquiera loopback alcanza sin TURNOS_WA_PROVIDER=waha explícito.
        _env.set_env(self, OTP_WAHA_URL="http://localhost:3005", OTP_WAHA_API_KEY="x")
        c = es._cluster_whatsapp()
        self.assertEqual(c["estado"], "na")

    def test_waha_dev_optin_es_warn(self):
        _env.set_env(self, TURNOS_WA_PROVIDER="waha", META_WA_TOKEN="x")  # presente pero no gana: el explícito manda
        _env.set_env(self, OTP_WAHA_URL="http://127.0.0.1:3005", OTP_WAHA_API_KEY="x")
        c = es._cluster_whatsapp()
        proveedor = [i for i in c["items"] if i["label"] == "Proveedor activo"][0]
        self.assertEqual(proveedor["valor"], "WAHA")
        gateway = [i for i in c["items"] if i["label"] == "Gateway WAHA"][0]
        self.assertEqual(gateway["estado"], "warn")
        self.assertEqual(c["estado"], "warn")

    def test_provider_explicito_waha_remoto_queda_apagado(self):
        _env.set_env(self, TURNOS_WA_PROVIDER="waha", OTP_WAHA_URL="https://waha.pinamar.gob.ar",
                     OTP_WAHA_API_KEY="x")
        c = es._cluster_whatsapp()
        self.assertEqual(c["estado"], "na")


class TestEmail(Base):
    """Los chequeos en vivo del proveedor pasan por notifications.mail.get_json
    (el mismo que usa «Probar» en Settings) — se parchea ése."""

    def setUp(self):
        super().setUp()
        from app.notifications import mail
        self._mail = mail
        self._get_json = mail.get_json
        self._db = patch.object(config, "_db_overrides", return_value={})
        self._db.start()
        _env.set_env(self, CONNECTOR_EMAIL_TRANSPORT=None, MAILGUN_API_BASE=None)

    def tearDown(self):
        self._mail.get_json = self._get_json
        self._db.stop()
        super().tearDown()

    def test_sin_key_es_error(self):
        c = es._cluster_email()
        self.assertEqual(c["estado"], "error")

    def test_dominio_activo_ok(self):
        _env.set_env(self, MAILGUN_API_KEY="key-abc", MAILGUN_DOMAIN="mg.sana-turnos.example.com")
        self._mail.get_json = lambda *a, **k: ({"domain": {"state": "active"}}, None)
        c = es._cluster_email()
        estado_mg = [i for i in c["items"] if i["label"] == "Estado en Mailgun"][0]
        self.assertEqual(estado_mg["estado"], "ok")
        self.assertEqual(c["estado"], "ok")

    def test_dominio_no_verificado_warn(self):
        _env.set_env(self, MAILGUN_API_KEY="key-abc", MAILGUN_DOMAIN="mg.example.com")
        self._mail.get_json = lambda *a, **k: ({"domain": {"state": "unverified"}}, None)
        c = es._cluster_email()
        self.assertEqual(c["estado"], "warn")

    def test_mailgun_caido_no_rompe(self):
        _env.set_env(self, MAILGUN_API_KEY="key-abc", MAILGUN_DOMAIN="mg.example.com")
        self._mail.get_json = lambda *a, **k: (None, "sin conexión")
        c = es._cluster_email()
        # Degrada a warn, nunca revienta.
        self.assertIn(c["estado"], ("warn", "error"))


class TestOpenrouter(Base):
    def test_sin_key_es_error(self):
        c = es._cluster_openrouter()
        self.assertEqual(c["estado"], "error")

    def test_key_conectada_tope_diario(self):
        _env.set_env(self, OPENROUTER_API_KEY="sk-or-x")
        es._probe_json = lambda *a, **k: ({"data": {
            "limit": 3, "limit_reset": "daily", "limit_remaining": 3,
            "usage_daily": 0, "usage_weekly": 0, "usage_monthly": 0}}, None)
        c = es._cluster_openrouter()
        self.assertEqual(c["estado"], "ok")
        labels = [i["label"] for i in c["items"]]
        self.assertIn("Días de crédito", labels)
        self.assertIn("Tope", labels)

    def test_tope_tocado_es_error(self):
        _env.set_env(self, OPENROUTER_API_KEY="sk-or-x")
        es._probe_json = lambda *a, **k: ({"data": {
            "limit": 3, "limit_reset": "daily", "limit_remaining": 0,
            "usage_daily": 3, "usage_weekly": 10, "usage_monthly": 40}}, None)
        c = es._cluster_openrouter()
        self.assertEqual(c["estado"], "error")

    def test_management_key_agrega_saldo_y_dias(self):
        _env.set_env(self, OPENROUTER_API_KEY="sk-or-x", OPENROUTER_MANAGEMENT_KEY="sk-or-mgmt")

        def fake(url, headers=None, timeout=es._TIMEOUT):
            if "credits" in url:
                return {"data": {"total_credits": 100, "total_usage": 30}}, None
            return {"data": {"limit": None, "limit_reset": None,
                             "usage_daily": 1, "usage_weekly": 7, "usage_monthly": 30}}, None
        es._probe_json = fake
        c = es._cluster_openrouter()
        labels = [i["label"] for i in c["items"]]
        self.assertIn("Saldo de la cuenta", labels)
        self.assertIn("Días de crédito (cuenta)", labels)

    def test_openrouter_caido_no_rompe(self):
        _env.set_env(self, OPENROUTER_API_KEY="sk-or-x")
        es._probe_json = lambda *a, **k: (None, "HTTP 500")
        c = es._cluster_openrouter()
        self.assertEqual(c["estado"], "warn")

    def test_hosted_profile_uses_dedicated_key_and_never_probes_openrouter(self):
        _env.set_env(self, {
            "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
            "AI_INFERENCE_BASE_URL": "https://inference.example/v1/chat/completions",
            "AI_INFERENCE_API_KEY": "dedicated-only",
            "OPENROUTER_API_KEY": "legacy-must-not-be-used",
        })
        es._probe_json = lambda *a, **k: self.fail("non-cloud must not probe OpenRouter")
        c = es._cluster_openrouter()
        self.assertEqual(c["estado"], "ok")
        self.assertEqual(c["titulo"], "Inteligencia artificial")
        self.assertEqual(c["items"][0]["valor"], "Configurada")

    def test_non_cloud_missing_dedicated_key_is_value_free(self):
        _env.set_env(self, {
            "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
            "AI_INFERENCE_BASE_URL": "https://private.example/v1/chat/completions",
            "OPENROUTER_API_KEY": "legacy-must-not-be-used",
        })
        c = es._cluster_openrouter()
        self.assertEqual(c["estado"], "error")
        rendered = json.dumps(c)
        self.assertNotIn("private.example", rendered)
        self.assertNotIn("legacy-must-not-be-used", rendered)

    def test_cloud_status_keeps_its_snapshot_key_during_a_profile_switch(self):
        _env.set_env(self, OPENROUTER_API_KEY="cloud-key")
        real_snapshot = es.ai_client.transport_snapshot

        def snapshot_then_switch():
            snapshot = real_snapshot()
            _env.set_env(self, {
                "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
                "AI_INFERENCE_BASE_URL": "https://inference.example/v1/chat/completions",
                "AI_INFERENCE_API_KEY": "dedicated-key",
            })
            return snapshot

        seen = []
        def probe(url, headers=None, timeout=es._TIMEOUT):
            seen.append((url, headers or {}))
            return {"data": {"limit": None, "usage_daily": 0,
                              "usage_weekly": 0, "usage_monthly": 0}}, None

        with patch.object(es.ai_client, "transport_snapshot", side_effect=snapshot_then_switch):
            es._probe_json = probe
            self.assertEqual(es._cluster_openrouter()["estado"], "ok")
        self.assertEqual(seen[0][1]["Authorization"], "Bearer cloud-key")


class TestStorage(Base):
    def test_carpeta_escribible(self):
        c = es._cluster_storage()
        self.assertEqual(c["clave"], "storage")
        escritura = [i for i in c["items"] if i["label"] == "Escritura"][0]
        # El repo de dev es escribible.
        self.assertEqual(escritura["estado"], "ok")


class TestRenaper(Base):
    def test_sin_credenciales_es_na_no_error(self):
        # opcional: sin RENAPER el sitio anda igual, no es un error de setup.
        c = es._cluster_renaper()
        self.assertEqual(c["clave"], "renaper")
        self.assertEqual(c["estado"], "na")
        self.assertEqual(c["items"][0]["campo"], "TURNOS_RENAPER_CLIENT_ID")

    def test_con_credenciales_ok(self):
        _env.set_env(self, TURNOS_RENAPER_CLIENT_ID="abc", TURNOS_RENAPER_CLIENT_SECRET="def")
        c = es._cluster_renaper()
        self.assertEqual(c["estado"], "ok")


class TestPuco(Base):
    def test_default_ok_con_respaldo_activo(self):
        c = es._cluster_puco()
        self.assertEqual(c["clave"], "puco")
        self.assertEqual(c["estado"], "ok")

    def test_respaldo_apagado_es_warn(self):
        _env.set_env(self, TURNOS_PUCO_PUBLIC="0")
        c = es._cluster_puco()
        self.assertEqual(c["estado"], "warn")


class TestB2(Base):
    def test_sin_credenciales_es_warn_no_error(self):
        # opcional: sin B2 el backup queda dormido, el sitio anda igual.
        c = es._cluster_b2()
        self.assertEqual(c["clave"], "b2")
        self.assertEqual(c["estado"], "warn")
        self.assertEqual(c["items"][0]["campo"], "TURNOS_B2_KEY_ID")

    def test_con_credenciales_ok(self):
        _env.set_env(self, TURNOS_B2_KEY_ID="k", TURNOS_B2_APP_KEY="a", TURNOS_B2_BUCKET_ID="b")
        with patch("app.core.db_backup.backup_purge_state", side_effect=RuntimeError("sin base")):
            c = es._cluster_b2()
        self.assertEqual(c["estado"], "ok")


class TestB2Purge(Base):
    """El bloque de B2 cuenta lo que dejó el último pase de la purga remota
    (`backup_purge_state`); la base se reemplaza, no hace falta una viva."""

    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_B2_KEY_ID="k", TURNOS_B2_APP_KEY="a", TURNOS_B2_BUCKET_ID="b")

    def _cluster(self, **state):
        base = {"last_pass_at": None, "last_success_at": None, "closed": 0, "denied": 0, "failed": 0,
                "pending": 0}
        base.update(state)
        with patch("app.core.db_backup.backup_purge_state", return_value=base):
            return es._cluster_b2()

    def test_clave_sin_deletefiles_es_warn_con_el_aviso(self):
        c = self._cluster(denied=17, pending=17)
        self.assertEqual(c["estado"], "warn")
        warn = c["items"][-1]
        self.assertEqual(warn["valor"], "Falta deleteFiles")
        self.assertEqual(warn["estado"], "warn")
        self.assertIn("17 archivos que la retención ya borró siguen guardados", warn["detalle"])
        self.assertIn("deleteFiles", warn["detalle"])
        self.assertEqual(c["items"][-3]["valor"], "17")
        self.assertEqual(c["items"][-2]["valor"], "Todavía no")

    def test_pase_limpio_muestra_la_hora_y_no_avisa(self):
        from datetime import datetime, timezone
        c = self._cluster(last_success_at=datetime(2026, 10, 26, 12, 30, tzinfo=timezone.utc), pending=0)
        self.assertEqual(c["estado"], "ok")
        self.assertEqual([i["estado"] for i in c["items"][-2:]], ["ok", "ok"])
        self.assertRegex(c["items"][-1]["valor"], r"^2026-10-2[56] \d\d:30$")

    def test_purga_remota_apagada_no_agrega_nada(self):
        _env.set_env(self, TURNOS_BACKUP_PURGE_REMOTE="0")
        self.assertEqual(len(self._cluster(denied=3)["items"]), 4)

    def test_si_la_base_no_responde_el_bloque_sigue_igual(self):
        with patch("app.core.db_backup.backup_purge_state", side_effect=RuntimeError("db")):
            c = es._cluster_b2()
        self.assertEqual((c["estado"], len(c["items"])), ("ok", 4))

    def test_el_aviso_se_traduce(self):
        from app.core import i18n
        with patch.object(i18n, "active", return_value="en"):
            c = self._cluster(denied=2)
        self.assertEqual(c["items"][-1]["valor"], "deleteFiles missing")
        self.assertIn("2 files the retention already deleted are still stored", c["items"][-1]["detalle"])
        # Russian «one» (1, 21…) and «many» (5…20); «few» (2-4) still folds
        # into the plural text, see core/i18n.py plural_category().
        with patch.object(i18n, "active", return_value="ru"):
            c = self._cluster(denied=5)
            uno = self._cluster(denied=1)
        self.assertEqual(c["items"][-1]["valor"], "\u041d\u0435\u0442 deleteFiles")
        self.assertIn("5 \u0444\u0430\u0439\u043b\u043e\u0432", c["items"][-1]["detalle"])
        self.assertIn("1 \u0444\u0430\u0439\u043b,", uno["items"][-1]["detalle"])


class TestApp(Base):
    def test_daemon_apagado_es_warn(self):
        _env.set_env(self, TURNOS_REMINDERS_OFF="1")
        c = es._cluster_app()
        rec = [i for i in c["items"] if i["label"] == "Recordatorios"][0]
        self.assertEqual(rec["valor"], "Apagado")
        self.assertEqual(rec["estado"], "warn")

    def test_app_no_incluye_sincronizacion_hsi(self):
        # La sincronización con HSI vive en su propio cluster, no en «Aplicación».
        c = es._cluster_app()
        labels = [i["label"] for i in c["items"]]
        self.assertNotIn("Sincronización con HSI", labels)


class TestHsi(Base):
    def setUp(self):
        super().setUp()
        # These are HSI-specific status tests. HSI is no longer selected by
        # package presence, so make the pre-existing test subject explicit.
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")

    def _con_db(self, futuras=2, futuras_ok=2, outbox=None, abiertos=0, ultima=None):
        """Parchea `core.db_hsi.hsi_metrics`/`hsi_open_conflicts` — lo que
        _cluster_hsi() realmente consume — con los valores de mentira."""
        m = patch("app.core.db_hsi.hsi_metrics",
                   return_value={"futuras": futuras, "futuras_ok": futuras_ok,
                                 "outbox": outbox or {}})
        c = patch("app.core.db_hsi.hsi_open_conflicts", return_value=[object()] * abiertos)
        u = patch("app.core.db_hsi.hsi_last_confirmed", return_value=ultima)
        m.start()
        c.start()
        u.start()
        self.addCleanup(m.stop)
        self.addCleanup(c.stop)
        self.addCleanup(u.stop)

    def _extension(self, install, store="unset"):
        """`install` is what the desk is offered to install; `store` is the
        address the desk's «Cargar en HSI» buttons are drawn on (by default
        there is one exactly when there is something to install)."""
        if store == "unset":
            store = "https://store.example/x" if install else None
        for target, value in (("app.core.hsi_extension.install", install),
                              ("app.core.hsi_extension.store_url", store)):
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def _centros(self, *rows):
        p = patch("app.patient_web.catalog.centers",
                  return_value=[{"slug": s, "name": n, "hsi_institution": h} for s, n, h in rows])
        p.start()
        self.addCleanup(p.stop)

    def _item(self, cluster, label):
        return [i for i in cluster["items"] if i["label"] == label][0]

    def test_skip_es_na(self):
        _env.set_env(self, TURNOS_SKIP_HSI="1")
        self._extension(None)
        c = es._cluster_hsi()
        self.assertEqual(c["clave"], "hsi")
        self.assertEqual(c["estado"], "na")  # neutro, no baja el color agregado
        modo = self._item(c, "Modo")
        self.assertEqual(modo["valor"], "Apagado")
        self.assertIn("los turnos no llegan a HSI", modo["detalle"])

    def test_skip_with_the_extension_names_the_extension(self):
        _env.set_env(self, TURNOS_SKIP_HSI="1")
        self._extension({"tienda": "https://store.example/x", "zip": None, "guia": None})
        self._centros(("hospital", "Hospital", "1086"), ("caps", "CAPS", "1068"))
        c = es._cluster_hsi()
        modo = self._item(c, "Modo")
        self.assertEqual(modo["valor"], "Carga con la extensión del navegador")
        self.assertIn("deshabilitado en esta instalación", modo["detalle"])
        self.assertEqual(c["estado"], "ok")
        # Nothing of the server-side push is shown or probed without it.
        self.assertEqual([i["label"] for i in c["items"]], ["Modo", "Instituciones en HSI"])

    def test_without_a_connector_the_card_still_names_the_state(self):
        _env.set_env(self, CONNECTOR_EMR_SYNC=None)
        self._extension({"tienda": None, "zip": "/api/admin/hsi-extension/extension.zip", "guia": None})
        self._centros(("hospital", "Hospital", "1086"), ("caps", "CAPS Norte", ""), ("sur", "CAPS Sur", ""))
        es._reachable = lambda *a, **k: self.fail("no probe without the server-side push")
        with patch("app.core.db_hsi.hsi_metrics", side_effect=AssertionError("no journal read")):
            c = es._cluster_hsi()
        modo = self._item(c, "Modo")
        self.assertEqual(modo["valor"], "Carga con la extensión del navegador")
        self.assertIn("El conector HSI no está activo", modo["detalle"])
        centros = self._item(c, "Instituciones en HSI")
        self.assertEqual(centros["valor"], "1 de 3 centros con número")
        self.assertEqual(centros["detalle"], "Hospital 1086 · Sin número: CAPS Norte, CAPS Sur")
        # The extension is what reads the number: a center without it is a warning here.
        self.assertEqual(centros["estado"], "warn")
        self.assertEqual(centros["enlace"]["href"], "/admin/hsi")
        self.assertEqual(c["estado"], "warn")

    def test_without_a_connector_nor_the_extension_it_is_off(self):
        _env.set_env(self, CONNECTOR_EMR_SYNC=None)
        self._extension(None)
        self._centros(("hospital", "Hospital", ""))
        c = es._cluster_hsi()
        self.assertEqual(self._item(c, "Modo")["valor"], "Apagado")
        centros = self._item(c, "Instituciones en HSI")
        # One center is its own sentence, never «0 de 1 centros».
        self.assertEqual((centros["valor"], centros["estado"]), ("El centro no tiene número", "na"))
        self.assertEqual(c["estado"], "na")

    def test_the_desk_buttons_alone_make_it_the_extension(self):
        # The desk draws «Cargar en HSI» on the store address even when there
        # is nothing to offer for installation (no published page, no ZIP):
        # the panel must not say «Apagado» over a desk that loads turnos.
        _env.set_env(self, CONNECTOR_EMR_SYNC=None)
        self._extension(None, store="https://store.example/x")
        self._centros(("hospital", "Hospital", "1086"))
        c = es._cluster_hsi()
        self.assertEqual(self._item(c, "Modo")["valor"], "Carga con la extensión del navegador")
        self.assertEqual(self._item(c, "Instituciones en HSI")["valor"], "El centro tiene número")

    def test_another_emr_keeps_its_own_card(self):
        _env.set_env(self, CONNECTOR_EMR_SYNC="emr_bridge")
        self.assertIsNone(es._cluster_hsi())

    def test_server_mode_names_the_last_transmission_and_the_centers(self):
        from datetime import datetime, timezone
        es._reachable = lambda *a, **k: (True, None)
        ultima = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
        self._con_db(futuras=3, futuras_ok=3, ultima=ultima)
        self._centros(("hospital", "Hospital", "1086"), ("caps", "CAPS", ""))
        c = es._cluster_hsi()
        self.assertEqual(self._item(c, "Modo")["valor"], "Envío desde el servidor")
        last = self._item(c, "Última transmisión")
        # The instant travels with the installation's offset: the screen reads
        # the day and the hour from the text, never through the reader's clock.
        self.assertEqual(last["cuando"], "2026-11-10T11:30:00-03:00")
        self.assertEqual(last["valor"], "2026-11-10 11:30")  # installation time, not UTC
        # The server-side push does not read the center's number yet: named, in neutral.
        centros = self._item(c, "Instituciones en HSI")
        self.assertEqual((centros["valor"], centros["estado"]), ("1 de 2 centros con número", "na"))
        self.assertEqual(c["estado"], "ok")

    def test_server_mode_without_a_transmission_yet(self):
        es._reachable = lambda *a, **k: (True, None)
        self._con_db()
        last = self._item(es._cluster_hsi(), "Última transmisión")
        self.assertEqual((last["valor"], last["estado"]), ("Todavía ninguna", "na"))
        self.assertNotIn("cuando", last)

    def test_activo_sincronizado_ok(self):
        es._reachable = lambda *a, **k: (True, None)
        self._con_db(futuras=3, futuras_ok=3, abiertos=0)
        c = es._cluster_hsi()
        self.assertEqual(c["estado"], "ok")
        turnos = [i for i in c["items"] if i["label"] == "Turnos futuros en HSI"][0]
        self.assertEqual(turnos["valor"], "3 de 3 (100%)")

    def test_conflictos_abiertos_warn(self):
        es._reachable = lambda *a, **k: (True, None)
        self._con_db(futuras=3, futuras_ok=2, abiertos=2)
        c = es._cluster_hsi()
        conf = [i for i in c["items"] if i["label"] == "Conflictos sin resolver"][0]
        self.assertEqual(conf["valor"], "2")
        self.assertEqual(conf["estado"], "warn")
        self.assertEqual(c["estado"], "warn")

    def test_sin_conexion_es_error(self):
        es._reachable = lambda *a, **k: (False, "sin conexión (refused)")
        self._con_db()
        c = es._cluster_hsi()
        red = [i for i in c["items"] if i["label"] == "Conectividad"][0]
        self.assertEqual(red["estado"], "error")
        self.assertEqual(c["estado"], "error")

    def test_daemon_apagado_warn(self):
        _env.set_env(self, TURNOS_HSI_SYNC_OFF="1")
        es._reachable = lambda *a, **k: (True, None)
        self._con_db()
        c = es._cluster_hsi()
        sinc = [i for i in c["items"] if i["label"] == "Sincronización"][0]
        self.assertEqual(sinc["estado"], "warn")

    def test_base_caida_degrada_a_warn(self):
        es._reachable = lambda *a, **k: (True, None)
        # db real rota al pedir métricas → warn, no 500.
        def _boom(*a, **k):
            raise RuntimeError("db down")
        m = patch("app.core.db_hsi.hsi_metrics", side_effect=_boom)
        c_patch = patch("app.core.db_hsi.hsi_open_conflicts", side_effect=_boom)
        m.start()
        c_patch.start()
        self.addCleanup(m.stop)
        self.addCleanup(c_patch.stop)
        c = es._cluster_hsi()
        met = [i for i in c["items"] if i["label"] == "Métricas"][0]
        self.assertEqual(met["estado"], "warn")


class TestPayloadYTransporte(Base):
    def test_estado_nombra_hsi_sin_conector(self):
        # Without a connector the HSI card is still there (slice 7, 2026-11-10):
        # it names the mode and reads no journal, no socket.
        es._probe_json = lambda *a, **k: (None, "sin conexión")
        _env.set_env(self, TURNOS_SKIP_HSI="1")  # HSI hermético (no red ni base)
        p = es.status(_handler())
        claves = [c["clave"] for c in p["clusters"]]
        self.assertEqual(claves, ["app", "hsi", "dominio", "whatsapp",
                                  "email", "storage", "openrouter",
                                  "renaper", "puco", "backup", "ai-register"])
        self.assertEqual(p["total"], 11)
        self.assertIn(p["estado"], ("ok", "warn", "error"))

    def test_native_response_sends_payload(self):
        es._probe_json = lambda *a, **k: (None, "sin conexión")
        _env.set_env(self, TURNOS_SKIP_HSI="1")  # HSI hermético
        response = system_status_router._status_response(_handler())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.body)["clusters"]), 11)


class TestDemoRefreshAge(unittest.TestCase):
    """The demo's refresh line read "Completed 1 minutes ago" (22/10)."""

    def _detail(self, minutes):
        from datetime import datetime, timedelta, timezone
        from app.core import tz
        now = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
        row = {"outcome": "success", "success_for": now.astimezone(tz.tzinfo()).date(),
               "last_success_at": now - timedelta(minutes=minutes)}
        from app.core import db_backup
        from app.ops import demo_installation
        with patch.object(demo_installation, "enabled", return_value=True), \
                patch.object(db_backup, "demo_refresh_completion_status", return_value=row), \
                _env.patched_env({"TURNOS_DEMO_REFRESH_OFF": ""}):
            return es._demo_refresh_item(now=now)["detalle"]

    def test_singular_and_plural(self):
        # Spanish panel, Spanish line (it read «Completed 2 hours ago», 03/11).
        self.assertEqual(self._detail(0), "Completada recién.")
        self.assertEqual(self._detail(1), "Completada hace 1 minuto.")
        self.assertEqual(self._detail(5), "Completada hace 5 minutos.")
        self.assertEqual(self._detail(61), "Completada hace 1 hora.")
        self.assertEqual(self._detail(180), "Completada hace 3 horas.")


class TestStaffReadsNoSettingNames(Base):
    """What staff read in «Estado» names the thing to fix and where, never a
    setting or variable name (03/11: «Falta META_WA_TOKEN», «revisá
    DATABASE_URL», «(GWT-RPC)»). `campo` still carries the setting for the
    «Configurar →» link; only label/valor/detalle/resumen/titulo are read."""

    _SETTING = re.compile(r"\b(?:TURNOS|META|OTP|EMR|MAILGUN|MAILJET|OPENROUTER|DATABASE)_[A-Z_]+|GWT-RPC")

    def _texts(self, cluster):
        yield cluster.get("titulo") or ""
        yield cluster.get("resumen") or ""
        for item in cluster.get("items") or []:
            for field in ("label", "valor", "detalle"):
                yield str(item.get(field) or "")

    def assertClean(self, cluster):
        for text in self._texts(cluster):
            self.assertIsNone(self._SETTING.search(text), text)

    def test_whatsapp_without_provider_and_with_missing_meta_parts(self):
        self.assertClean(es._cluster_whatsapp())
        _env.set_env(self, OTP_WAHA_URL="https://waha.example.org")
        self.assertClean(es._cluster_whatsapp())
        _env.set_env(self, OTP_WAHA_URL=None, TURNOS_WA_PROVIDER="meta")
        self.assertClean(es._cluster_whatsapp())

    def test_domain_puco_renaper_and_hsi(self):
        _env.set_env(self, TURNOS_PUCO_PUBLIC="0", TURNOS_SKIP_HSI="1", TURNOS_HSI_SYNC_OFF="1")
        self.assertClean(es._cluster_domain(_handler("turnos.example.org", proto="")))
        self.assertClean(es._cluster_puco())
        self.assertClean(es._cluster_renaper())
        puco = es._cluster_puco()["items"][1]
        self.assertEqual(puco["label"], "Consulta pública de respaldo")

    def test_ai_not_configured(self):
        _env.set_env(self, OPENROUTER_API_KEY=None, AI_INFERENCE_API_KEY=None,
                     AI_INFERENCE_PROFILE=None)
        c = es._cluster_openrouter()
        self.assertClean(c)
        self.assertEqual(c["titulo"], "Inteligencia artificial")


if __name__ == "__main__":
    unittest.main()
