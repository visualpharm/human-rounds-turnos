"""Adversarial contracts for the last native ASGI route."""

import asyncio
import fcntl
import http.client
import os
import pathlib
import socket
import tempfile
import unittest
from unittest.mock import patch

import _env  # noqa: E402
import _wait  # noqa: E402
_env.process_set(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")
_env.process_set(TURNOS_FAKE_AI="1")

from starlette.requests import Request  # noqa: E402

from app.core import static_fingerprint  # noqa: E402
from app.patient_web import availability_pages  # noqa: E402
from app.routers import native_terminal  # noqa: E402
from app.services import static_assets  # noqa: E402


def _fd_path(fd):
    """The path an open descriptor points at. Linux exposes it as the
    /dev/fd symlink; macOS's /dev/fd entries are not symlinks, so readlink
    fails there and the path comes from fcntl(F_GETPATH)."""
    if hasattr(fcntl, "F_GETPATH"):
        return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0"))
    return os.readlink(os.path.join("/dev/fd", str(fd)))


def _request(method, path, query=b"", headers=()):
    return Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query,
        "headers": list(headers),
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })


async def _body(response):
    iterator = getattr(response, "body_iterator", None)
    if iterator is None:
        return response.body
    chunks = []
    async for chunk in iterator:
        chunks.append(chunk)
    return b"".join(chunks)


class TestNativeTerminal(unittest.IsolatedAsyncioTestCase):

    def _fixture_fds(self):
        """Descriptors this process holds on the fixture tree. Counting every
        entry of /dev/fd also counted other threads' sockets and pipes (DB
        pools, daemons of earlier modules) and failed at random."""
        root = os.path.realpath(self.root)
        held = set()
        for name in os.listdir("/dev/fd"):
            try:
                target = _fd_path(int(name))
            except (OSError, ValueError):
                continue
            if target == root or target.startswith(root + os.sep):
                held.add((name, target))
        return held

    def _wait_fixture_fds(self, expected, timeout=10.0):
        """The fixture descriptors once the streaming response has let go of
        them. Closing a Starlette streaming body does not close the sync
        generator behind it: that generator is released by the AnyIO worker
        thread that last ran it, which drops its reference to it only AFTER it
        has reported the chunk to the event loop (``del item, args`` at the end
        of ``WorkerThread.run``). On a loaded machine (five test lanes beside
        the database) the loop thread can finish ``aclose()`` and read the
        descriptors before the worker gets its turn, so the first reading is
        not the final one. A real leak never settles and still fails after
        ``timeout``."""
        held = self._fixture_fds()

        def settled():
            nonlocal held
            held = self._fixture_fds()
            return held == expected

        try:
            _wait.until(settled, "the streaming response lets go of the fixture descriptors", timeout=timeout)
        except AssertionError:
            pass   # a real leak never settles: the caller compares what is still held
        return held

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "static").mkdir()
        (self.root / "static" / "plain.js").write_bytes(b"plain")
        (self.root / "static" / "font.woff2").write_bytes(b"font")
        (self.root / "asgi_main.py").write_bytes(b"checkout-source-sentinel")
        (self.root / "admin.html").write_bytes(b"retired-shell-sentinel")
        (self.root / "ui-core.js").write_bytes(b"retired-ui-core-sentinel")
        (self.root / "specialty-browser.js").write_bytes(b"retired-specialty-sentinel")
        (self.root / "stz-ui.js").write_bytes(b"retired-stz-ui-sentinel")
        (self.root / "rubricador.js").write_bytes(b"retired-rubricador-sentinel")
        (self.root / "admin-queue.js").write_bytes(b"retired-queue-sentinel")
        (self.root / "admin-cola.js").write_bytes(b"retired-queue-alias-sentinel")
        (self.root / "admin-content.js").write_bytes(b"retired-catalog-sentinel")
        (self.root / "static" / "admin-content.js").write_bytes(b"retired-catalog-static-sentinel")

    def tearDown(self):
        self.tmp.cleanup()

    async def _terminal(self, method, path, query=b""):
        with patch.object(static_assets, "APP_ROOT", str(self.root)):
            response = await native_terminal.terminal(
                path.lstrip("/"),
                _request(method, path, query),
            )
        return response

    async def test_static_get_and_head_share_metadata(self):
        get = await self._terminal("GET", "/static/plain.js")
        get_body = await _body(get)
        head = await self._terminal("HEAD", "/static/plain.js")
        head_body = await _body(head)
        self.assertEqual(get.status_code, 200)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(get_body, b"plain")
        self.assertEqual(head_body, b"")
        for key in ("content-type", "content-length", "cache-control"):
            self.assertEqual(get.headers[key], head.headers[key])
        self.assertEqual(get.headers["content-length"], "5")
        self.assertEqual(get.headers["cache-control"], "public, max-age=300")

    async def test_retired_admin_shell_is_never_a_static_document(self):
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                response = await self._terminal(method, "/admin.html")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(
                    await _body(response),
                    b'{"error": "not found"}',
                )

    async def test_retired_client_scripts_are_never_static_documents(self):
        for path, query in (
            ("/ui-core.js", b""),
            ("/ui-core.js", b"v=1"),
            ("/specialty-browser.js", b""),
            ("/specialty-browser.js", b"v=1"),
            ("/stz-ui.js", b""),
            ("/rubricador.js", b""),
            ("/admin-queue.js", b""),
            ("/admin-queue.js", b"v=1"),
            ("/admin-cola.js", b""),
            ("/admin-cola.js", b"v=31"),
            ("/admin-cola.js/", b""),
            ("/admin-content.js", b""),
            ("/admin-content.js", b"v=1"),
            ("/admin-content.js/", b""),
            ("/static/admin-content.js", b""),
            ("/static/admin-content.js/", b""),
        ):
            for method in ("GET", "HEAD"):
                with self.subTest(method=method, path=path, query=query):
                    with patch.object(static_assets, "APP_ROOT", str(self.root)):
                        response = await native_terminal.terminal(
                            path.lstrip("/"),
                            _request(method, path, query),
                        )
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertNotIn("location", response.headers)
                    self.assertEqual(
                        await _body(response),
                        b'{"error": "not found"}',
                    )

    async def test_versioned_cache_and_woff2_type_are_exact(self):
        versioned = await self._terminal("GET", "/static/plain.js", b"v=abc")
        await _body(versioned)
        legacy_substring = await self._terminal(
            "GET",
            "/static/plain.js",
            b"source=v=abc",
        )
        await _body(legacy_substring)
        font = await self._terminal("GET", "/static/font.woff2")
        await _body(font)
        self.assertEqual(
            versioned.headers["cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            legacy_substring.headers["cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(font.headers["content-type"], "font/woff2")

    async def test_if_modified_since_keeps_the_existing_304_contract(self):
        first = await self._terminal("GET", "/static/plain.js")
        await _body(first)
        with patch.object(static_assets, "APP_ROOT", str(self.root)):
            second = await native_terminal.terminal(
                "static/plain.js",
                _request(
                    "GET",
                    "/static/plain.js",
                    headers=((
                        b"if-modified-since",
                        first.headers["last-modified"].encode("ascii"),
                    ),),
                ),
            )
        self.assertEqual(second.status_code, 304)
        self.assertEqual(await _body(second), b"")
        self.assertEqual(second.headers["cache-control"], "public, max-age=300")

    async def test_fingerprints_and_relative_import_passthrough_use_real_files(self):
        hashed_path = static_fingerprint.url("platform/nav.mjs")
        hashed = await native_terminal.terminal(
            hashed_path.lstrip("/"),
            _request("GET", hashed_path),
        )
        hashed_body = await _body(hashed)
        with open(os.path.join(static_assets.APP_ROOT, "static/platform/nav.mjs"), "rb") as fh:
            self.assertEqual(hashed_body, fh.read())
        self.assertEqual(hashed.status_code, 200)
        self.assertEqual(
            hashed.headers["cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertNotIn("last-modified", hashed.headers)

        # Tier 4 slice 6, batch 3: the client-loaded cohort (the URLs the
        # shell publishes as window.SANA_STATIC) serves through the SAME
        # native terminal with the same immutable contract — no transport
        # code was added for it, which is exactly what this pins.
        for name in sorted(static_fingerprint.CLIENT_LOADED):
            with self.subTest(name=name):
                client_path = static_fingerprint.manifest()[name]
                self.assertEqual(client_path, static_fingerprint.url(name))
                served = await native_terminal.terminal(
                    client_path.lstrip("/"),
                    _request("GET", client_path),
                )
                served_body = await _body(served)
                # Platform modules physically live below static/, unlike the
                # remaining root-level clients. Keep this expected disk path
                # independent of the production fingerprint resolver.
                relative_path = os.path.join("static", name) if name.startswith("platform/") else name
                with open(os.path.join(static_assets.APP_ROOT, relative_path), "rb") as fh:
                    self.assertEqual(served_body, fh.read())
                self.assertEqual(served.status_code, 200)
                self.assertEqual(
                    served.headers["cache-control"],
                    "public, max-age=31536000, immutable",
                )

        for name in sorted(static_fingerprint._module_assets()):
            with self.subTest(module_asset=name):
                module_path = static_fingerprint.module_url(name)
                served = await native_terminal.terminal(
                    module_path.lstrip("/"), _request("GET", module_path),
                )
                self.assertEqual(served.status_code, 200)
                self.assertEqual(served.headers["cache-control"],
                                 "public, max-age=31536000, immutable")

        retired = await native_terminal.terminal(
            "static/format.js",
            _request("GET", "/static/format.js"),
        )
        self.assertEqual(retired.status_code, 404)

        for name in ("format", "dates", "hours", "calendar-grid", "islands",
                     "directory", "tiles"):
            with self.subTest(retired_root_primitive=name):
                closed = await native_terminal.terminal(
                    f"{name}.mjs",
                    _request("GET", f"/{name}.mjs"),
                )
                self.assertEqual(closed.status_code, 404)
                self.assertEqual(closed.headers["cache-control"], "no-store")

        forged = hashed_path.replace(hashed_path.split(".")[-2], "00000000")
        missing = await native_terminal.terminal(
            forged.lstrip("/"),
            _request("GET", forged),
        )
        self.assertEqual(missing.status_code, 404)

    async def test_raw_module_asset_redirect_is_get_only_and_query_free(self):
        target = static_fingerprint.module_url("modules/intake/entrevista.js")
        redirected = await native_terminal.terminal(
            "entrevista.js", _request("GET", "/entrevista.js", b"stale=1"),
        )
        self.assertEqual(redirected.status_code, 301)
        self.assertEqual(redirected.headers["location"], target)
        self.assertEqual(redirected.headers["cache-control"], "no-store")
        self.assertEqual(await _body(redirected), b"")
        not_redirected = await native_terminal.terminal(
            "entrevista.js", _request("HEAD", "/entrevista.js"),
        )
        self.assertNotEqual(not_redirected.status_code, 301)

    async def test_broken_module_alias_never_falls_through_to_raw_source(self):
        from unittest.mock import patch

        with patch.object(static_fingerprint, "_MODULE_ASSETS_CACHE", frozenset()):
            response = await native_terminal.terminal(
                "modules/intake/entrevista.js",
                _request("GET", "/modules/intake/entrevista.js"),
            )
            head = await native_terminal.terminal(
                "modules/intake/entrevista.js",
                _request("HEAD", "/modules/intake/entrevista.js"),
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["content-length"], "0")
        self.assertEqual(await _body(response), b"")
        self.assertEqual(head.status_code, 404)
        self.assertEqual(head.headers["cache-control"], "no-store")
        self.assertEqual(head.headers["content-length"], "0")

    async def test_dynamic_result_is_emitted_without_static_resolution(self):
        result = availability_pages.HtmlResult(b"dynamic")
        with (
            patch.object(availability_pages, "match", return_value=result),
            patch.object(static_assets, "resolve_static_request") as resolve,
        ):
            response = await self._terminal("GET", "/cardiology/example")
        self.assertEqual(await _body(response), b"dynamic")
        self.assertEqual(response.headers["cache-control"], "no-cache")
        resolve.assert_not_called()

    async def test_api_unknown_is_json_and_checkout_source_is_not_found(self):
        api = await self._terminal("GET", "/api/unknown")
        # A source file that exists in every checkout is not on the static allowlist
        # (tests/test_static_allowlist.py): 404, the same answer as a file that does not exist.
        source = await self._terminal("GET", "/asgi_main.py")
        self.assertEqual(api.status_code, 404)
        self.assertEqual(await _body(api), b'{"error": "not found"}')
        self.assertEqual(api.headers["cache-control"], "no-store")
        self.assertEqual(source.status_code, 404)
        self.assertEqual(source.headers["cache-control"], "no-store")

    async def test_path_replacement_fails_closed_without_serving_replacement(self):
        response = await self._terminal("GET", "/static/plain.js")
        original = self.root / "static" / "plain.js"
        moved = self.root / "static" / "held.js"
        original.rename(moved)
        original.write_bytes(b"poison")
        with self.assertRaises(OSError):
            await _body(response)

    async def test_same_inode_mutation_fails_closed(self):
        response = await self._terminal("GET", "/static/plain.js")
        (self.root / "static" / "plain.js").write_bytes(b"other")
        with self.assertRaises(OSError):
            await _body(response)

    async def test_parent_symlink_and_hard_link_are_denied(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "leak.js").write_bytes(b"secret")
        # inside a served tree: outside it the allowlist answers 404 before any open
        (self.root / "static" / "linked").symlink_to(outside, target_is_directory=True)
        linked = await self._terminal("GET", "/static/linked/leak.js")
        hard = self.root / "static" / "hard.js"
        os.link(self.root / "static" / "plain.js", hard)
        hard_response = await self._terminal("GET", "/static/hard.js")
        self.assertEqual(linked.status_code, 403)
        self.assertEqual(hard_response.status_code, 403)

    async def test_symlink_root_fifo_socket_and_directory_are_denied(self):
        linked_root = self.root.parent / (self.root.name + "-link")
        linked_root.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(linked_root.unlink)
        with patch.object(static_assets, "APP_ROOT", str(linked_root)):
            root_response = await native_terminal.terminal(
                "static/plain.js",
                _request("GET", "/static/plain.js"),
            )
        fifo = self.root / "static" / "pipe.js"
        os.mkfifo(fifo)
        fifo_response = await self._terminal("GET", "/static/pipe.js")
        socket_path = self.root / "static" / "socket.js"
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(unix_socket.close)
        unix_socket.bind(str(socket_path))
        socket_response = await self._terminal("GET", "/static/socket.js")
        directory_response = await self._terminal("GET", "/static/")
        for response in (
            root_response,
            fifo_response,
            socket_response,
            directory_response,
        ):
            self.assertEqual(response.status_code, 403)

    async def test_concurrent_streams_do_not_mix(self):
        (self.root / "static" / "two.js").write_bytes(b"second")
        first = await self._terminal("GET", "/static/plain.js")
        second = await self._terminal("GET", "/static/two.js")
        bodies = await asyncio.gather(_body(first), _body(second))
        self.assertEqual(bodies, [b"plain", b"second"])

    async def test_result_close_is_idempotent_and_does_not_leak(self):
        before = self._fixture_fds()
        with patch.object(static_assets, "APP_ROOT", str(self.root)):
            result = static_assets.resolve_static_request("/static/plain.js")
        self.assertIsInstance(result, static_assets.StaticFile)
        result.close()
        result.close()
        self.assertEqual(self._fixture_fds(), before)

    async def test_stream_close_after_first_chunk_releases_every_descriptor(self):
        payload = b"a" * (1024 * 1024 + 1)
        (self.root / "static" / "large.js").write_bytes(payload)
        before = self._fixture_fds()
        response = await self._terminal("GET", "/static/large.js")
        first = await response.body_iterator.__anext__()
        self.assertEqual(len(first), 1024 * 1024)
        self.assertNotEqual(self._fixture_fds(), before)  # the probe sees the open file
        await response.body_iterator.aclose()
        self.assertEqual(self._wait_fixture_fds(before), before)

    def test_private_verification_revocation_before_read_fails_and_closes(self):
        before = self._fixture_fds()
        result = static_assets._file_result(
            str(self.root),
            "static/plain.js",
            "/webdata/plain.js",
            "",
            private_root=True,
        )
        with patch.object(
            static_assets.data_packs,
            "private_root_is_verified",
            return_value=False,
        ):
            with self.assertRaises(OSError):
                list(result.chunks())
        self.assertEqual(self._fixture_fds(), before)

    def test_read_and_fstat_failures_release_every_descriptor(self):
        for operation in ("read", "fstat"):
            with self.subTest(operation=operation):
                before = self._fixture_fds()
                result = static_assets._file_result(
                    str(self.root),
                    "static/plain.js",
                    "/static/plain.js",
                    "",
                )
                with patch.object(
                    static_assets.os,
                    operation,
                    side_effect=OSError("synthetic descriptor failure"),
                ):
                    with self.assertRaises(OSError):
                        list(result.chunks())
                self.assertEqual(self._fixture_fds(), before)

    def test_dynamic_match_preserves_late_path_rules(self):
        doctor = {"slug": "example"}
        with (
            patch.object(availability_pages.availability_directory, "medico_by_url", return_value=doctor) as lookup,
            patch.object(availability_pages, "render_doctor_page", return_value=b"doctor"),
            patch.object(availability_pages, "render_specialty_page", return_value=None) as specialty,
        ):
            result = availability_pages.match(
                "GET",
                "//cardiolog%C3%ADa///example//",
            )
        self.assertEqual(result, availability_pages.HtmlResult(b"doctor"))
        lookup.assert_called_once_with("cardiología", "example")
        specialty.assert_not_called()
        for method, path in (
            ("HEAD", "/cardiologia/example"),
            ("GET", "/api/example"),
            ("GET", "/one/two/three"),
            ("GET", "/"),
        ):
            with self.subTest(method=method, path=path):
                self.assertIsNone(availability_pages.match(method, path))


class TestDarkNativeTerminalParity(unittest.TestCase):
    def test_terminal_status_body_and_selected_headers_match_dark_mode(self):
        from _livehttp import LiveServer
        from fastapi.testclient import TestClient
        from app import main as app

        cases = (
            ("GET", static_fingerprint.url("platform/admin-queue.mjs")),
            ("HEAD", static_fingerprint.url("platform/admin-queue.mjs")),
            # Tier 4 slice 6, batch 3: a client-loaded fingerprint (what
            # window.SANA_STATIC publishes) and its stale twin, both transports.
            ("GET", static_fingerprint.url("platform/listing.mjs")),
            ("GET", "/static/platform/listing.deadbeef.mjs"),
            ("GET", static_fingerprint.url("platform/format.mjs")),
            ("GET", "/format.mjs"),
            ("GET", "/dates.mjs"),
            ("GET", "/hours.mjs"),
            ("GET", "/calendar-grid.mjs"),
            ("GET", "/islands.mjs"),
            ("GET", "/admin-session.mjs"),
            ("GET", "/admin-preferences.mjs"),
            ("GET", "/admin-table.mjs"),
            ("GET", "/admin-combo.mjs"),
            ("GET", "/admin-filter-controls.mjs"),
            ("GET", "/admin-control-fit.mjs"),
            ("GET", "/admin-patient-summary.mjs"),
            ("GET", "/server.py"),
            ("HEAD", "/server.py"),
            ("GET", "/terminal-parity-missing"),
            ("HEAD", "/terminal-parity-missing"),
            ("POST", "/terminal-parity-missing"),
            ("PATCH", "/terminal-parity-missing"),
            ("OPTIONS", "/terminal-parity-missing"),
        )
        selected = ("content-type", "content-length", "cache-control")
        with (
            patch("app.ops.web_runtime.start_background_services"),
            patch("app.ops.web_runtime.stop_app_modules"),
            LiveServer() as base_url,
            TestClient(app.app, client=("127.0.0.1", 12345)) as native,
        ):
            port = int(base_url.rsplit(":", 1)[1])
            for method, path in cases:
                with self.subTest(method=method, path=path):
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    try:
                        connection.request(method, path)
                        dark = connection.getresponse()
                        dark_body = dark.read()
                        dark_headers = {key.lower(): value for key, value in dark.getheaders()}
                    finally:
                        connection.close()
                    current = native.request(method, path)
                    current_headers = {
                        key.lower(): value for key, value in current.headers.items()
                    }
                    self.assertEqual(current.status_code, dark.status)
                    self.assertEqual(current.content, dark_body)
                    self.assertEqual(
                        {key: current_headers.get(key) for key in selected},
                        {key: dark_headers.get(key) for key in selected},
                    )


if __name__ == "__main__":
    unittest.main()
