"""Offline transport-contract tests for the F2 native migration
(native-migration campaign, 2026-08-26): `patient_web.patient_site_router`
builds every response itself; these tests pin the native endpoints' own
status/headers/body contract directly."""

import asyncio
import datetime
import gzip
import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env  # noqa: E402
_env.process_set(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.core import app_modules  # noqa: E402
from app import connectors  # noqa: E402
from app.core import gzip_bridge  # noqa: E402
from app.patient_web import (
    site_availability,
    site_base,
    site_catalog,
    site_centers,
    site_listing,
    site_page_doctors,
    site_shell,
)
from app.patient_web import patient_site_router  # noqa: E402


def _request(path, query=b"", accept_encoding=""):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode("utf-8"), "query_string": query,
        "headers": [(b"accept-encoding", accept_encoding.encode())],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    })


class ApiJsonContractTests(unittest.TestCase):
    def test_api_services_json_cache_and_gzip_contract(self):
        data = [{"id": "svc1", "nombre": "Servicio Uno"}] * 50
        with (
            patch.object(site_listing, "_services_enriched", return_value=data),
            patch.object(gzip_bridge, "_MIN_SIZE", 1),
        ):
            native = asyncio.run(patient_site_router._api_services(
                _request("/api/services", accept_encoding="gzip")))

        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(native.headers["cache-control"], "no-store")
        self.assertEqual(native.headers["content-encoding"], "gzip")
        self.assertEqual(native.headers["vary"], "Accept-Encoding")
        self.assertEqual(json.loads(gzip.decompress(native.body)), {"servicios": data})

    def test_api_service_slots_query_parsing_blank_value_semantics(self):
        # `?hold=` (present but empty) must be dropped exactly like
        # `urllib.parse.parse_qs`'s default `keep_blank_values=False` drops it
        # — Starlette's own `QueryParams` keeps blank values, so the native
        # side must parse the raw query string itself.
        captured = {}

        def _fake_appointments_for_service(sv, exclude_token=None):
            captured["exclude_token"] = exclude_token
            return {"slots": []}

        with (
            patch.object(site_catalog, "_service_by_id", return_value={"id": "svc1"}),
            patch.object(site_availability, "_appointments_for_service",
                         side_effect=_fake_appointments_for_service),
        ):
            native = asyncio.run(patient_site_router._api_service_slots(
                _request("/api/service-slots/svc1", query=b"hold="), sid="svc1"))
        self.assertEqual(native.status_code, 200)
        self.assertIsNone(captured["exclude_token"])

    def test_public_services_and_service_slots_do_not_serve_past_connector_times(self):
        now = datetime.datetime(2026, 9, 5, 9, 0)
        service = {
            "id": "svc-past", "nombre": "Servicio de prueba", "modo": "turno",
            "cluster": "none", "centro": "none",
            "agenda": {"dias": [1], "desde": "08:00", "hasta": "09:00", "cada_min": 20},
        }

        class StaleSource:
            def slots_for_service(self, _service):
                return {"medicos": [{"turnos": [
                    {"dia": "2026-07-17", "hora": "09:40"},
                    {"dia": "2026-09-05", "hora": "08:40"},
                ]}]}

        source = StaleSource()
        with (
            patch.object(site_base, "_now_art", return_value=now),
            patch.object(site_catalog, "_services", return_value=[service]),
            patch.object(site_catalog, "_doctors", return_value=[]),
            patch.object(site_centers, "_centers_by_slug", return_value={}),
            patch.object(site_catalog, "_service_by_id", return_value=service),
            patch.object(connectors, "adapter_for", return_value=source),
        ):
            services = asyncio.run(patient_site_router._api_services(_request("/api/services")))
            slots = asyncio.run(patient_site_router._api_service_slots(
                _request("/api/service-slots/svc-past"), sid="svc-past"))

        service_next = json.loads(services.body)["servicios"][0]["proximo"]
        offered = json.loads(slots.body)["medicos"][0]["turnos"]
        self.assertEqual((service_next["dia"], service_next["hora"]), ("2026-09-07", "08:00"))
        self.assertTrue(all(slot["dia"] >= "2026-09-05" for slot in offered))
        self.assertNotIn("2026-07-17", json.dumps({"next": service_next, "slots": offered}))


class HtmlPageContractTests(unittest.TestCase):
    def test_doctors_page_html_and_gzip_contract(self):
        body = ("<html>" + "x" * 2000 + "</html>").encode("utf-8")
        with (
            patch.object(site_page_doctors, "_page_doctors", return_value=body),
            patch.object(gzip_bridge, "_MIN_SIZE", 1),
        ):
            native = asyncio.run(patient_site_router._doctors_page(
                _request("/doctors", accept_encoding="gzip")))

        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.headers["content-type"], "text/html; charset=utf-8")
        self.assertNotIn("cache-control", native.headers)
        self.assertEqual(native.headers["content-encoding"], "gzip")
        self.assertEqual(native.headers["vary"], "Accept-Encoding")
        self.assertEqual(gzip.decompress(native.body), body)

    def test_missing_doctor_redirects_to_doctors_index(self):
        with patch.object(site_page_doctors, "_page_doctor", return_value=None):
            native = asyncio.run(patient_site_router._doctor_page(
                _request("/doctor/nadie"), mid="nadie"))
        self.assertEqual(native.status_code, 302)
        self.assertEqual(native.headers["location"], "/doctors")


class RedirectGroupContractTests(unittest.TestCase):
    def test_delinking_redirect_contract(self):
        native = asyncio.run(patient_site_router._redirect_home(_request("/clinicas")))
        self.assertEqual(native.status_code, 302)
        self.assertEqual(native.headers["location"], "/")
        self.assertEqual(native.headers["content-length"], "0")
        self.assertEqual(native.body, b"")

    def test_my_requests_delegates_to_account_or_renaper(self):
        with patch.object(
            site_shell, "_account_or_renaper",
            return_value="/auth/renaper?next=%2Faccount%23turnos",
        ) as mock_target:
            native = asyncio.run(
                patient_site_router._my_requests_redirect(_request("/my-requests")))

        self.assertEqual(native.status_code, 302)
        self.assertEqual(native.headers["location"], "/auth/renaper?next=%2Faccount%23turnos")
        mock_target.assert_called_once()
        current_user, next_path = mock_target.call_args.args
        self.assertEqual(next_path, "/account#turnos")
        self.assertIsNone(current_user)


class LangSwitchAndModuleGateOrderingTests(unittest.TestCase):
    def test_lang_switch_answers_before_the_turnos_gate(self):
        with (
            patch.object(
                patient_site_router.i18n, "lang_switch_redirect",
                return_value=("/doctors", "turnos_lang=en; Max-Age=1; Path=/; SameSite=Lax"),
            ),
            patch.object(app_modules, "is_enabled", side_effect=AssertionError("gate must not run")),
        ):
            native = asyncio.run(patient_site_router._doctors_page(
                _request("/doctors", query=b"lang=en")))
        self.assertEqual(native.status_code, 302)
        self.assertEqual(native.headers["location"], "/doctors")
        self.assertEqual(
            native.headers["set-cookie"], "turnos_lang=en; Max-Age=1; Path=/; SameSite=Lax")
        self.assertEqual(native.headers["content-length"], "0")

    def test_disabled_turnos_module_answers_404_like_the_shared_gate(self):
        with (
            patch.object(patient_site_router.i18n, "lang_switch_redirect", return_value=None),
            patch.object(app_modules, "is_enabled", return_value=False),
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(patient_site_router._doctors_page(_request("/doctors")))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
