"""The public demo is opt-in, fictional and isolated from live integrations."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _lifespan  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import config  # noqa: E402
from app.ops import demo_installation  # noqa: E402
from app.ops import demo_installation_router  # noqa: E402
from app.notifications import otp_send  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.responses import Response  # noqa: E402
from app.routers import platform as platform_routers  # noqa: E402
from app import main as app_module  # noqa: E402
import _env  # noqa: E402


def _client():
    app = FastAPI()
    app.include_router(demo_installation_router.router)
    return TestClient(app, client=("127.0.0.1", 12345))


def setUpModule():
    _lifespan.no_background_services()


class DemoEnvironment:
    def setUp(self):
        # The app lifespan entered below would start the daily demo-refresh
        # daemon for good; it outlived this module and reseeded the NEXT
        # module's database concurrently (test_manage_demo_seed: duplicate
        # doctor+slot). The same lifespan starts the cache warm-up, reminders,
        # waitlist and housekeeping threads, which never stop either: setUpModule
        # keeps them out (tests/_lifespan.py).
        # patched_env (not set_env): the tests call apply_safety_defaults(), which
        # writes other keys, and the whole environment must come back.
        self.enterContext(_env.patched_env({
            "TURNOS_DEMO_MODE": "1",
            "TURNOS_DEMO_REFRESH_OFF": "1",
            "TURNOS_ADMIN_SECRET": "test-demo-secret",
        }))


class TestDisabled(unittest.TestCase):
    def test_routes_and_banner_do_not_exist_without_explicit_mode(self):
        with _env.patched_env({"TURNOS_DEMO_MODE": "0"}, clear=False):
            with _client() as client:
                response = client.get("/admin/demo-access", follow_redirects=False)
            self.assertEqual(response.status_code, 404)
            source = "<html><body><main>Normal</main></body></html>"
            self.assertEqual(demo_installation.inject_html(source), source)


class TestSafetyDefaults(DemoEnvironment, unittest.TestCase):
    def test_demo_disables_every_live_side_effect(self):
        keys = tuple(demo_installation._SAFETY_DEFAULTS)
        with _env.patched_env(dict.fromkeys(keys)):
            demo_installation.apply_safety_defaults()
            self.assertEqual(os.environ["TURNOS_SKIP_HSI"], "1")
            self.assertEqual(os.environ["TURNOS_HSI_SYNC_OFF"], "1")
            self.assertEqual(os.environ["TURNOS_REMINDERS_OFF"], "1")
            self.assertEqual(os.environ["TURNOS_WAITLIST_OFF"], "1")
            self.assertEqual(os.environ["TURNOS_FAKE_AI"], "1")
            self.assertEqual(os.environ["TURNOS_FAKE_CHAT_AI"], "0")
            self.assertEqual(os.environ["TURNOS_FAKE_PUCO"], "1")
            self.assertEqual(os.environ["TURNOS_FAKE_ALEPO"], "1")
            self.assertEqual(os.environ["TURNOS_FAKE_MARQUI"], "1")
            self.assertEqual(os.environ["MAIL_ENABLED"], "0")

    def test_an_explicit_unsafe_setting_is_overwritten(self):
        with _env.patched_env({"TURNOS_WAITLIST_OFF": "0"}, clear=False):
            demo_installation.apply_safety_defaults()
            self.assertEqual(os.environ["TURNOS_WAITLIST_OFF"], "1")

    def test_demo_lock_beats_a_database_configuration_override(self):
        with mock.patch.object(config, "_db_overrides",
                               return_value={"MAIL_ENABLED": "1",
                                             "TURNOS_SKIP_HSI": "0"}):
            self.assertEqual(config.get("MAIL_ENABLED"), "0")
            self.assertEqual(config.get("TURNOS_SKIP_HSI"), "1")


class TestPublicAccess(DemoEnvironment, unittest.TestCase):
    def test_native_router_is_mounted_as_platform_core(self):
        self.assertIn(demo_installation_router.router, platform_routers.ORDERED)
        self.assertEqual(
            [(route.path, sorted(route.methods))
             for route in demo_installation_router.router.routes],
            [
                ("/api/demo", ["GET"]),
                ("/account/demo-access", ["GET"]),
                ("/admin/demo-doctor-access", ["GET"]),
                ("/admin/demo-access", ["GET"]),
            ],
        )

    def test_metadata_declares_fictional_data(self):
        with _client() as client:
            response = client.get("/api/demo")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["data"], "ficticios")

    def test_one_click_admin_entry_sets_signed_secure_cookie(self):
        with _client() as client:
            response = client.get(
                "/admin/demo-access",
                headers={"X-Forwarded-Proto": "https"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/admin/solicitudes")
        cookie = response.headers["set-cookie"]
        self.assertIn("st_admin=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)
        token = cookie.split("=", 1)[1].split(";", 1)[0]
        self.assertEqual(
            admin_auth._verify_token(token),
            {"usuario": "demo", "rol": "admin"},
        )

    def test_one_click_patient_entry_opens_the_curated_account(self):
        user = {"dni": "99000001", "nombre": "Sofía"}
        with mock.patch("app.core.db_accounts.get_user", return_value=user), \
                mock.patch("app.core.db_accounts.create_session", return_value="patient-token"), \
                mock.patch("app.cuentas.accounts._set_cookie", return_value="st_ses=patient-token"), \
                mock.patch(
                    "app.cuentas.accounts_router.render_account",
                    return_value=Response(
                        b"<html>account</html>",
                        headers={"Set-Cookie": "st_ses=patient-token"},
                    ),
                ) as render:
            with _client() as client:
                response = client.get("/account/demo-access", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("location", response.headers)
        self.assertEqual(response.headers["set-cookie"], "st_ses=patient-token")
        self.assertEqual(render.call_args.kwargs["bootstrap_user"], user)
        self.assertEqual(render.call_args.kwargs["set_cookie"], "st_ses=patient-token")
        self.assertEqual(render.call_args.kwargs["canonical_path"], "/account")

    def test_one_click_doctor_entry_opens_the_doctors_day(self):
        with mock.patch("app.core.db_operators.operator_get", return_value={"usuario": "gonzalez", "rol": "medico"}), \
                mock.patch("app.core.db_operators.operator_mark_access") as mark:
            with _client() as client:
                response = client.get(
                    "/admin/demo-doctor-access",
                    headers={"X-Forwarded-Proto": "https"},
                    follow_redirects=False,
                )
        # the click is the doctor's login: the visit is recorded like any login's,
        # so the admin no longer reads «Invitado» (audit 2026-11-05). The demo
        # doctor lands on «Hoy» — his own day of work, the screen the demo is
        # for (2026-11-10), the same place a real doctor's login opens.
        mark.assert_called_once_with("gonzalez")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/admin/cola")
        token = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
        self.assertEqual(admin_auth._verify_token(token),
                         {"usuario": "gonzalez", "rol": "medico"})

    def test_native_router_serves_all_four_entry_points(self):
        # Native-migration campaign, family W2c (docs/roadmap/native-
        # migration-campaign-20260826.md): `/account/demo-access` no longer
        # calls `cuentas.accounts_router.render_account` (family F6), which
        # renders through the shared `accounts.render_account_body` domain
        # function without a handler-shaped compatibility layer.
        #
        # This pins the native router's four routes' exact
        # status/redirect/cookie contract directly.
        app = FastAPI()
        app.include_router(demo_installation_router.router)

        patches = (
            mock.patch("app.core.db_accounts.get_user", return_value={
                "dni": "99000001", "nombre": "Demo Patient"}),
            mock.patch("app.core.db_accounts.create_session", return_value="patient-token"),
            mock.patch("app.cuentas.accounts._set_cookie", return_value="st_ses=patient-token"),
            mock.patch("app.core.db_operators.operator_get", return_value={
                "usuario": "gonzalez", "rol": "medico"}),
            mock.patch("app.core.db_operators.operator_mark_access"),
            mock.patch("app.auth.admin_auth._mint_token",
                       side_effect=lambda user, role: f"{user}-{role}"),
            mock.patch("app.auth.admin_auth._set_cookie_from",
                       side_effect=lambda _headers, token: f"st_admin={token}"),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            metadata = client.get(
                "/api/demo", headers={"X-Forwarded-Proto": "https"},
                follow_redirects=False,
            )
            self.assertEqual(metadata.status_code, 200)
            self.assertTrue(metadata.json()["demo"])

            account = client.get(
                "/account/demo-access", headers={"X-Forwarded-Proto": "https"},
                follow_redirects=False,
            )
            self.assertEqual(account.status_code, 200)
            self.assertEqual(account.headers["set-cookie"], "st_ses=patient-token")

            doctor = client.get(
                "/admin/demo-doctor-access", headers={"X-Forwarded-Proto": "https"},
                follow_redirects=False,
            )
            self.assertEqual(doctor.status_code, 302)
            self.assertEqual(doctor.headers["location"], "/admin/cola")
            self.assertEqual(doctor.headers["set-cookie"], "st_admin=gonzalez-medico")

            admin = client.get(
                "/admin/demo-access", headers={"X-Forwarded-Proto": "https"},
                follow_redirects=False,
            )
            self.assertEqual(admin.status_code, 302)
            self.assertEqual(admin.headers["location"], "/admin/solicitudes")
            self.assertEqual(admin.headers["set-cookie"], "st_admin=demo-admin")

    def test_demo_doctor_lands_on_the_account_when_no_screen_is_open_to_the_role(self):
        from app.admin_panel import admin_navigation
        app = FastAPI()
        app.include_router(demo_installation_router.router)
        with TestClient(app, client=("127.0.0.1", 12345)) as client, \
                mock.patch.object(demo_installation, "doctor_access_identity",
                                  return_value=("gonzalez", "medico")), \
                mock.patch.object(admin_auth, "marcar_acceso"), \
                mock.patch.object(admin_auth, "_mint_token", return_value="t"), \
                mock.patch.object(admin_auth, "_set_cookie_from", return_value="st_admin=t"), \
                mock.patch.object(admin_navigation, "default_path_for_role", return_value=None):
            doctor = client.get("/admin/demo-doctor-access", follow_redirects=False)
        self.assertEqual(doctor.status_code, 302)
        self.assertEqual(doctor.headers["location"], "/admin/account")

    def test_native_router_returns_404_without_exposing_disabled_demo(self):
        app = FastAPI()
        app.include_router(demo_installation_router.router)
        with _env.patched_env({"TURNOS_DEMO_MODE": "0"}, clear=False), \
                TestClient(app, client=("127.0.0.1", 12345)) as client:
            for path in (
                "/api/demo",
                "/account/demo-access",
                "/admin/demo-doctor-access",
                "/admin/demo-access",
            ):
                with self.subTest(path=path):
                    response = client.get(path, follow_redirects=False)
                    self.assertEqual(response.status_code, 404)

    def test_native_router_preserves_missing_seed_failures(self):
        app = FastAPI()
        app.include_router(demo_installation_router.router)
        with TestClient(app, client=("127.0.0.1", 12345)) as client, \
                mock.patch("app.core.db_accounts.get_user", return_value=None), \
                mock.patch("app.core.db_operators.operator_get", return_value=None):
            patient = client.get("/account/demo-access", follow_redirects=False)
            doctor = client.get("/admin/demo-doctor-access", follow_redirects=False)
        self.assertEqual(patient.status_code, 503)
        self.assertEqual(patient.json(), {"error": "demo_patient_not_seeded"})
        self.assertEqual(doctor.status_code, 503)
        self.assertEqual(doctor.json(), {"error": "demo_doctor_not_seeded"})

    def test_demo_session_never_enters_the_real_2fa_enrolment_policy(self):
        status = admin_auth.account_security({"usuario": "demo", "rol": "admin"},
                                             ensure_deadline=True)
        self.assertTrue(status["tiene_2fa"])
        self.assertFalse(status["2fa_vencido"])
        self.assertIsNone(status["2fa_deadline"])

    def test_truthy_demo_mode_never_enters_real_2fa_policy(self):
        with _env.patched_env({"TURNOS_DEMO_MODE": "true"}, clear=False):
            status = admin_auth.account_security(
                {"usuario": "demo", "rol": "admin"},
                ensure_deadline=True,
            )
        self.assertTrue(status["tiene_2fa"])
        self.assertIsNone(status["2fa_deadline"])

    def test_legacy_webturno_pages_and_booking_api_are_blocked(self):
        # These retired WebTurno routes are absent from the composed native
        # app altogether. Demo mode cannot make either legacy surface live.
        with TestClient(app_module.app) as client:
            page = client.get("/app", follow_redirects=False)
            booking = client.post("/api/book", follow_redirects=False)
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["location"], "/")
        self.assertEqual(booking.status_code, 404)

    def test_owner_coverage_api_is_blocked_for_every_mutating_method(self):
        with TestClient(app_module.app) as client:
            for method in ("POST", "PUT", "DELETE"):
                with self.subTest(method=method):
                    response = client.request(
                        method,
                        "/api/owner/coverage/doctor-1",
                        follow_redirects=False,
                    )
                    self.assertEqual(response.status_code, 404)

    def test_demo_blocks_whatsapp_and_otp_at_the_transport_boundary(self):
        with _env.patched_env({
            "META_WA_TOKEN": "configured",
            "META_WA_PHONE_NUMBER_ID": "configured",
            "OTP_PROVIDER": "whatsapp",
        }, clear=False), \
                mock.patch.object(otp_send, "_enviar_meta",
                                  return_value=True) as send_meta, \
                mock.patch.object(otp_send, "_enviar_whatsapp",
                                  return_value=True) as send_waha:
            self.assertFalse(otp_send.enviar_texto("5492255111222", "hola"))
            self.assertFalse(otp_send.enviar_otp("5492255111222", "123456"))
        send_meta.assert_not_called()
        send_waha.assert_not_called()

    def test_banner_explains_fictional_data_and_links_to_admin(self):
        out = demo_installation.inject_html(
            "<!doctype html><html><body><main>Inicio</main></body></html>",
            "public",
        )
        self.assertIn('demo-installation__lead">Demo.</strong>', out)
        self.assertIn("Todos los datos son ficticios", out)
        self.assertIn('href="/admin/demo-access" data-demo-destination="admin">Gestión</a>', out)
        self.assertLess(out.index("demo-installation"), out.index("<main>"))

    def test_banner_shows_only_the_three_demo_destinations_on_the_public_surface(self):
        out = demo_installation.inject_html(
            "<html><body><main>Inicio</main></body></html>", "public")
        self.assertNotIn("Sitio público", out)
        self.assertIn('href="/account/demo-access" data-demo-destination="patient">Paciente</a>', out)
        self.assertIn('href="/admin/demo-doctor-access" data-demo-destination="doctor">Consola médica</a>', out)
        self.assertIn('href="/admin/demo-access" data-demo-destination="admin">Gestión</a>', out)

    def test_public_url_is_not_exposed_even_when_configured(self):
        with _env.patched_env({"TURNOS_DEMO_PUBLIC_URL": "https://otro.example/"}, clear=False):
            out = demo_installation.inject_html(
                "<html><body><main>Inicio</main></body></html>", "public")
        self.assertNotIn("https://otro.example/", out)

    def test_admin_banner_links_back_to_patient_site(self):
        out = demo_installation.inject_html(
            b"<html><body><main>Panel</main></body></html>",
            "admin",
        ).decode("utf-8")
        self.assertIn('demo-installation__lead">Demo.</strong>', out)
        self.assertIn("Datos ficticios", out)
        self.assertIn('href="/account/demo-access" data-demo-destination="patient">Paciente</a>', out)

    def test_banner_shows_only_the_three_demo_destinations_on_the_admin_surface(self):
        out = demo_installation.inject_html(
            b"<html><body><main>Panel</main></body></html>", "admin").decode("utf-8")
        self.assertNotIn("Sitio público", out)
        self.assertIn('href="/account/demo-access" data-demo-destination="patient">Paciente</a>', out)
        self.assertIn('href="/admin/demo-doctor-access" data-demo-destination="doctor">Consola médica</a>', out)
        self.assertIn('href="/admin/demo-access" data-demo-destination="admin">Gestión</a>', out)

    # ---- idioma del cartel (responsable del producto, 2026-08-01: cartel en inglés arriba de un
    # panel escrito entero en español) --------------------------------------
    def test_banner_renders_in_the_language_the_page_says_not_its_own(self):
        # Aunque el visitante pida inglés, una superficie que se rindió en
        # español muestra el cartel en español: el cartel no resuelve idioma.
        with _env.patched_env({"LANGUAGES": "es,en",
                                          "LANG_SWITCHER_MODE": "nav"}, clear=False), \
                mock.patch.object(config, "_db_overrides", return_value={}):
            out = demo_installation.inject_html(
                "<html><body><main>Inicio</main></body></html>",
                "public", lang="es", switchable=True)
        self.assertIn("Todos los datos son ficticios", out)
        self.assertIn(
            '<span class="demo-installation__detail">Todos los datos son ficticios.</span>',
            out,
        )
        self.assertNotIn(
            '<span class="demo-installation__detail">All data is fictional.</span>',
            out,
        )
        # …y en modo «nav» el conmutador es el del encabezado, no uno segundo
        # en la banda (revisión de la demo 2026-11-15).
        self.assertNotIn('<nav class="demo-installation__lang"', out)
        # Con el conmutador propio de la banda (modo «secret», sin conmutador
        # en el encabezado), el idioma en uso es el marcado, no el otro.
        with _env.patched_env({"LANGUAGES": "es,en",
                                          "LANG_SWITCHER_MODE": "secret"}, clear=False), \
                mock.patch.object(config, "_db_overrides", return_value={}):
            out = demo_installation.inject_html(
                "<html><body><main>Inicio</main></body></html>",
                "public", lang="es", switchable=True)
        self.assertIn('<nav class="demo-installation__lang"', out)
        self.assertIn('<option value="es" lang="es" selected>Español</option>', out)
        self.assertIn('<option value="en" lang="en">English</option>', out)

    def test_switcher_only_where_the_page_can_actually_switch(self):
        base = b"<html><body><main>Panel</main></body></html>"
        with _env.patched_env({"LANGUAGES": "es,en",
                                          "LANG_SWITCHER_MODE": "nav"}, clear=False), \
                mock.patch.object(config, "_db_overrides", return_value={}):
            admin = demo_installation.inject_html(base, "admin", lang="es",
                                                  switchable=False).decode("utf-8")
        # El bloque de estilos siempre nombra la clase; lo que no puede
        # aparecer es el nav del conmutador.
        self.assertNotIn('<nav class="demo-installation__lang"', admin)

    def test_admin_banner_can_show_the_shared_language_switcher(self):
        base = b"<html><body><main>Panel</main></body></html>"
        with _env.patched_env({"LANGUAGES": "es,en",
                                          "LANG_SWITCHER_MODE": "nav"}, clear=False), \
                mock.patch.object(config, "_db_overrides", return_value={}):
            admin = demo_installation.inject_html(
                base, "admin", lang="es", switchable=True).decode("utf-8")
        self.assertIn('<nav class="demo-installation__lang"', admin)
        self.assertIn('<select data-demo-lang-select', admin)
        self.assertIn('<option value="en" lang="en">English</option>', admin)
        self.assertIn('data-demo-surface="admin"', admin)
        self.assertIn("if(bar.dataset.demoSurface==='admin')", admin)
        self.assertIn("document.cookie='lang='", admin)
        self.assertIn("applyAdminLanguage(code)", admin)
        self.assertIn("location.assign(location.pathname+'?lang='", admin)
        self.assertIn("+location.hash", admin)

    def test_mobile_banner_is_one_row_without_rules(self):
        # Revisión de la demo 2026-11-15: en el celular la banda pública es
        # una sola fila (la línea de detalle se cae); el panel conserva su
        # grilla de tres columnas.
        out = demo_installation.inject_html(
            "<html><body><main>Inicio</main></body></html>", "public")
        self.assertNotIn("grid-template-columns:repeat(3,minmax(0,1fr))", out)
        self.assertIn(".demo-installation__detail{display:none}", out)
        admin = demo_installation.inject_html(
            "<html><body><main>Panel</main></body></html>", "admin")
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", admin)
        self.assertNotIn("border-top", out)
        self.assertNotIn("border-bottom", out)

    def test_the_banner_never_underlines_a_link(self):
        out = demo_installation.inject_html(
            "<html><body><main>Inicio</main></body></html>", "public")
        self.assertNotIn("text-decoration:underline", out)


class TestDemoIsNotIndexed(DemoEnvironment, unittest.TestCase):
    """demo.humanrounds.org is a fictional sandbox: search engines must not
    index it next to the real installation (SEO audit 2026-10-01, Human
    Rounds item 3 — live demo answered `index,follow`)."""

    def test_public_page_robots_meta_becomes_noindex(self):
        out = demo_installation.inject_html(
            '<html><head><meta name="robots" content="index,follow">'
            '<link rel="canonical" href="https://demo.example/"></head>'
            '<body><main>Inicio</main></body></html>', "public")
        self.assertIn('<meta name="robots" content="noindex,follow">', out)
        self.assertNotIn('content="index,follow"', out)
        self.assertEqual(out.count('name="robots"'), 1)

    def test_page_without_robots_meta_gets_one(self):
        out = demo_installation.inject_html(
            b"<html><head><title>x</title></head><body><main>Panel</main></body></html>",
            "admin").decode("utf-8")
        self.assertIn('<meta name="robots" content="noindex,follow">', out)
        self.assertLess(out.index('name="robots"'), out.index("<body>"))

    def test_rendered_patient_page_is_noindex(self):
        from app.patient_web import site_page_centers
        page = site_page_centers._page_centers().decode("utf-8")
        self.assertIn('<meta name="robots" content="noindex,follow">', page)
        self.assertNotIn('content="index,follow"', page)

    def test_production_pages_stay_indexable(self):
        with _env.patched_env({"TURNOS_DEMO_MODE": "0"}, clear=False):
            from app.patient_web import site_page_centers
            page = site_page_centers._page_centers().decode("utf-8")
        self.assertIn('<meta name="robots" content="index,follow">', page)

    # The meta tag only reaches HTML pages; the sitemap, robots.txt and
    # every non-HTML response need their own signal (review 2026-10-07).

    def test_sitemap_lists_no_url(self):
        from app.patient_web import sitemap
        body = sitemap.xml().decode("utf-8")
        self.assertIn("<urlset", body)
        self.assertNotIn("<loc>", body)

    def test_robots_txt_does_not_point_to_the_sitemap(self):
        from app.core import site_identity
        body = site_identity.render_page("robots.txt", lang="es")[0].decode("utf-8")
        self.assertNotIn("Sitemap:", body)
        self.assertNotIn("sitemap.xml", body)
        self.assertIn("User-agent: *", body)

    def test_every_response_carries_x_robots_tag(self):
        wrapped = demo_installation.NoindexHeaderApp(app_module.app)
        with TestClient(wrapped) as client:
            for path in ("/centers", "/sitemap.xml", "/robots.txt", "/llms.txt"):
                with self.subTest(path=path):
                    r = client.get(path, follow_redirects=False)
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(r.headers.get("x-robots-tag"), "noindex")

    def test_asgi_mode_wraps_the_app_only_on_the_demo(self):
        import sys
        import types
        from app import asgi_main
        fake_app = types.SimpleNamespace(app=object())
        fake_uvicorn = types.SimpleNamespace(run=lambda *a, **kw: None)
        with mock.patch.dict(sys.modules, {"app.main": fake_app, "uvicorn": fake_uvicorn}), \
                mock.patch.object(asgi_main, "_verify_required_packs"), \
                mock.patch.object(demo_installation, "apply_safety_defaults"), \
                mock.patch.object(fake_uvicorn, "run") as run, \
                _env.patched_env({"TURNOS_PORTAL_IDENTITY_MODE": "off"}):
            asgi_main._asgi_mode()
            served = run.call_args.args[0]
            self.assertIsInstance(served, demo_installation.NoindexHeaderApp)
            self.assertIs(served.app, fake_app.app)
            run.reset_mock()
            with _env.patched_env({"TURNOS_DEMO_MODE": "0"}):
                asgi_main._asgi_mode()
            self.assertIs(run.call_args.args[0], fake_app.app)

    def test_production_sitemap_robots_and_headers_unchanged(self):
        with _env.patched_env({"TURNOS_DEMO_MODE": "0"}, clear=False):
            from app.core import site_identity
            from app.patient_web import sitemap
            self.assertIn(b"<loc>", sitemap.xml())
            robots = site_identity.render_page("robots.txt", lang="es")[0]
            self.assertIn(b"Sitemap:", robots)
            with TestClient(app_module.app) as client:
                r = client.get("/centers")
            self.assertIsNone(r.headers.get("x-robots-tag"))


if __name__ == "__main__":
    unittest.main()
