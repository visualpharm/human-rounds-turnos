"""Strict guard against request-shaped compatibility adapters on native routes."""

import ast
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", DATABASE_URL="")

from app.core import app_modules  # noqa: E402
from app.cuentas import accounts, accounts_router  # noqa: E402
from app.patient_web import patient_site_router, site_page_service


_ROUTERS = (
    os.path.join(ROOT, "app", "cuentas", "accounts_router.py"),
    os.path.join(ROOT, "app", "patient_web", "patient_site_router.py"),
)


def _class_fields(node):
    fields = set()
    for item in ast.walk(node):
        if isinstance(item, ast.AnnAssign):
            if isinstance(item.target, ast.Name):
                fields.add(item.target.id)
            elif (
                isinstance(item.target, ast.Attribute)
                and isinstance(item.target.value, ast.Name)
                and item.target.value.id == "self"
            ):
                fields.add(item.target.attr)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id == "__slots__":
                    values = item.value.elts if isinstance(item.value, (ast.Tuple, ast.List)) else ()
                    fields.update(
                        value.value for value in values
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    )
                elif (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    fields.add(target.attr)
    return fields


def _dict_keys(node):
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


class TestNoRequestViewAdapters(unittest.TestCase):
    def test_router_sources_define_no_path_headers_clone(self):
        for path in _ROUTERS:
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as source_file:
                    source = source_file.read()
                tree = ast.parse(source, filename=path)
                offenders = [
                    node.name for node in ast.walk(tree)
                    if isinstance(node, ast.ClassDef)
                    and {"path", "headers"}.issubset(_class_fields(node))
                ]
                request_dicts = [
                    node.lineno for node in ast.walk(tree)
                    if isinstance(node, ast.Dict)
                    and {"path", "headers"}.issubset(_dict_keys(node))
                ]
                self.assertEqual(offenders, [])
                self.assertEqual(request_dicts, [])
                self.assertNotIn("_RequestView", source)
                self.assertNotIn("SimpleNamespace", source)
                self.assertNotIn("make_dataclass", source)

    def test_account_renderer_receives_the_actual_request(self):
        app = FastAPI()
        app.include_router(accounts_router.router)
        captured = []

        def render(request):
            captured.append(request)
            return b"<html>ok</html>"

        with patch.object(accounts, "render_reset_body", side_effect=render):
            response = TestClient(app).get("/account/reset")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured), 1)
        self.assertIsInstance(captured[0], Request)

    def test_patient_wizard_receives_only_explicit_request_values(self):
        app = FastAPI()
        app.include_router(patient_site_router.router)
        user = {"dni": "30111222"}

        with (
            patch.object(app_modules, "is_enabled", return_value=True),
            patch.object(accounts, "current_user", return_value=user),
            patch.object(accounts, "auth_mode", return_value="renaper"),
            patch.object(
                site_page_service, "_page_service_appointment",
                return_value=b"<html>wizard</html>",
            ) as render,
        ):
            response = TestClient(app).get(
                "/service/cardiologia/book?slot=2026-09-01T09:00")

        self.assertEqual(response.status_code, 200)
        render.assert_called_once_with(
            "cardiologia",
            query_string="slot=2026-09-01T09:00",
            current_user=user,
            auth_mode="renaper",
        )


if __name__ == "__main__":
    unittest.main()
