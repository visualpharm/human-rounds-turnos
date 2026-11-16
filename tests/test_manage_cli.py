"""Tests for manage.py — the Phase 6 mod/conn operator CLI. Calls the
command functions directly (per the task's own "subprocess or direct" note)
rather than shelling out. `conn enable`/`disable`/`list`/`info` run the REAL
registry (`connectors.enable/disable/resolve/active_connector_id`) against a
fake CONFIG layer: `connectors.config.get`/`guardar` are monkeypatched onto
an in-memory dict, so no reachable DATABASE_URL, ambient environment or
checkout .env ever participates — same spirit as config.py's own test suite.
"""
import argparse
import io
import os
import re
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import manage  # noqa: E402
from app import connectors  # noqa: E402
import _env  # noqa: E402


def _run(func, **kwargs):
    ns = argparse.Namespace(**kwargs)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = func(ns)
    return code, out.getvalue(), err.getvalue()


_STATUS_RE = re.compile(r"\s(active \((?:explicit|default)\)|off|invalid:\S+|no)(?=\s|$)")


def _status(out, connector_id):
    """The `active` cell of one connector's `conn list` row — the status
    vocabulary is closed (`active (explicit)`, `active (default)`, `off`,
    `invalid:<value>`, `no`), so it is matched by shape rather than by column
    (an id longer than its 12-char column shifts the padding)."""
    for line in out.splitlines():
        if line.startswith(connector_id + " "):
            m = _STATUS_RE.search(line)
            if not m:
                raise AssertionError(f"no status cell in row: {line!r}")
            return m.group(1)
    raise AssertionError(f"no row for {connector_id!r} in:\n{out}")


class FakeConfigMixin:
    """`connectors.config.get` reads ONLY `self._store`; `guardar` writes it
    with config.guardar's own empty-deletes-the-key semantics."""

    def setUp(self):
        self._store = {}

        def fake_get(key, default=None, legacy=None):
            if os.environ.get("TURNOS_CONFIG_LOCK") == "1":
                value = os.environ.get(key)
                return value if value not in (None, "") else default
            v = self._store.get(key)
            return v if v not in (None, "") else default

        def fake_guardar(overrides):
            for k, v in overrides.items():
                if v in (None, ""):
                    self._store.pop(k, None)
                else:
                    self._store[k] = v
            return dict(self._store)

        for target, value in (("get", fake_get), ("guardar", fake_guardar)):
            patcher = mock.patch.object(connectors.config, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class TestModList(unittest.TestCase):
    def test_lists_the_turnos_module(self):
        code, out, _ = _run(manage.cmd_mod_list)
        self.assertEqual(code, 0)
        self.assertIn("turnos", out)
        self.assertIn("enabled", out)


class TestConnList(FakeConfigMixin, unittest.TestCase):
    def test_lists_every_connector_with_capability_and_country(self):
        code, out, _ = _run(manage.cmd_conn_list)
        self.assertEqual(code, 0)
        for connector_id in ("renaper", "puco", "refeps", "hsi", "msd", "webturno", "waha", "meta"):
            self.assertIn(connector_id, out)
        self.assertIn("emr_sync", out)
        self.assertIn("identity_verification", out)

    def test_shows_config_key_status_without_a_value(self):
        self._store["TURNOS_SISA_USER"] = "some-secret-user"
        code, out, _ = _run(manage.cmd_conn_list)
        self.assertEqual(code, 0)
        self.assertIn("TURNOS_SISA_USER=set", out)
        self.assertNotIn("some-secret-user", out)

    def test_unconfigured_installation_shows_the_defaults_per_row(self):
        # Nothing stored: the flagged defaults read `active (default)`;
        # hsi and emr_bridge are institutional EMR choices and stay off;
        # webturno — the other
        # slot_source — reads `no`; the two unflagged OTP transports and the
        # four clinical-history archives read `no` (no default, by design).
        code, out, _ = _run(manage.cmd_conn_list)
        self.assertEqual(code, 0)
        for connector_id in ("renaper", "puco", "refeps", "msd"):
            self.assertEqual(_status(out, connector_id), "active (default)", connector_id)
        for connector_id in ("hsi", "emr_bridge", "webturno", "waha", "meta",
                             "alepo", "marqui", "local_history", "patient_uploads"):
            self.assertEqual(_status(out, connector_id), "no", connector_id)
        self.assertNotIn("TURNOS_CONFIG_LOCK", out)

    def test_lock_note_when_config_is_locked(self):
        with _env.patched_env({"TURNOS_CONFIG_LOCK": "1"}):
            code, out, _ = _run(manage.cmd_conn_list)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "(TURNOS_CONFIG_LOCK=1: DB values are ignored)")


class TestConnInfo(FakeConfigMixin, unittest.TestCase):
    def test_unknown_connector_errors_cleanly(self):
        code, out, err = _run(manage.cmd_conn_info, id="not-a-real-connector")
        self.assertEqual(code, 1)
        self.assertIn("desconocido", err)

    def test_known_connector_shows_full_detail(self):
        code, out, _ = _run(manage.cmd_conn_info, id="refeps")
        self.assertEqual(code, 0)
        self.assertIn("capability:  license_lookup", out)
        self.assertIn("country:     AR", out)
        self.assertIn("active:      active (default)", out)
        self.assertIn("config_keys: (none required)", out)

    def test_other_implementer_names_the_routed_one(self):
        code, out, _ = _run(manage.cmd_conn_info, id="webturno")
        self.assertEqual(code, 0)
        self.assertIn("active:      no (active for this capability: msd, default)", out)
        self._store["CONNECTOR_SLOT_SOURCE"] = "webturno"
        code, out, _ = _run(manage.cmd_conn_info, id="msd")
        self.assertIn("active:      no (active for this capability: webturno, explicit)", out)

    def test_lock_note_when_config_is_locked(self):
        with _env.patched_env({"TURNOS_CONFIG_LOCK": "1"}):
            code, out, _ = _run(manage.cmd_conn_info, id="msd")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "(TURNOS_CONFIG_LOCK=1: DB values are ignored)")


class TestConnEnableDisable(FakeConfigMixin, unittest.TestCase):
    """The real connectors.enable/disable run against the fake config store —
    proves manage.py calls them with the right arguments AND that what they
    persist is what `conn list` then reports, without needing a DB."""

    def test_enable_unknown_id_errors_cleanly(self):
        code, out, err = _run(manage.cmd_conn_enable, id="nope", set=None)
        self.assertEqual(code, 1)
        self.assertIn("desconocido", err)
        self.assertEqual(self._store, {})

    def test_enable_the_reserved_off_name_errors_cleanly(self):
        code, out, err = _run(manage.cmd_conn_enable, id="off", set=None)
        self.assertEqual(code, 1)
        self.assertIn("desconocido", err)

    def test_enable_sets_active_and_reports_missing_keys(self):
        code, out, err = _run(manage.cmd_conn_enable, id="renaper", set=None)
        self.assertEqual(code, 0)
        self.assertIn("renaper habilitado para identity_verification", out)
        self.assertIn("TURNOS_FAKE_RENAPER", err)  # still missing, flagged
        self.assertEqual(self._store["CONNECTOR_IDENTITY_VERIFICATION"], "renaper")
        self.assertNotIn("TURNOS_CONFIG_LOCK", err)
        _, out, _ = _run(manage.cmd_conn_list)
        self.assertEqual(_status(out, "renaper"), "active (explicit)")

    def test_enable_with_set_values_persists_them(self):
        code, out, _ = _run(
            manage.cmd_conn_enable, id="puco",
            set=["TURNOS_SISA_USER=u", "TURNOS_SISA_PASS=p"])
        self.assertEqual(code, 0)
        self.assertEqual(self._store["TURNOS_SISA_USER"], "u")
        self.assertEqual(self._store["TURNOS_SISA_PASS"], "p")
        self.assertEqual(self._store["CONNECTOR_INSURANCE_LOOKUP"], "puco")

    def test_enable_rejects_malformed_set(self):
        code, out, err = _run(manage.cmd_conn_enable, id="puco", set=["not-a-kv-pair"])
        self.assertEqual(code, 1)
        self.assertIn("KEY=VALUE", err)

    def test_disable_by_capability_writes_the_off_sentinel(self):
        self._store["CONNECTOR_EMR_SYNC"] = "hsi"
        code, out, _ = _run(manage.cmd_conn_disable, id="emr_sync")
        self.assertEqual(code, 0)
        self.assertIn("emr_sync: off", out)
        self.assertEqual(self._store["CONNECTOR_EMR_SYNC"], "off")

    def test_disable_by_connector_id_resolves_its_capability(self):
        self._store["CONNECTOR_EMR_SYNC"] = "hsi"
        code, out, _ = _run(manage.cmd_conn_disable, id="hsi")
        self.assertEqual(code, 0)
        self.assertIn("emr_sync", out)
        self.assertEqual(self._store["CONNECTOR_EMR_SYNC"], "off")

    def test_disable_unknown_id_errors_cleanly(self):
        code, out, err = _run(manage.cmd_conn_disable, id="not-a-thing")
        self.assertEqual(code, 1)

    def test_disabled_capability_reads_off_on_every_row_and_enable_reverts(self):
        code, _, _ = _run(manage.cmd_conn_disable, id="slot_source")
        self.assertEqual(code, 0)
        _, out, _ = _run(manage.cmd_conn_list)
        self.assertEqual(_status(out, "msd"), "off")
        self.assertEqual(_status(out, "webturno"), "off")
        self.assertIsNone(connectors.adapter_for(connectors.SLOT_SOURCE))
        _, out, _ = _run(manage.cmd_conn_info, id="webturno")
        self.assertIn("active:      off\n", out)
        code, _, _ = _run(manage.cmd_conn_enable, id="msd", set=None)
        self.assertEqual(code, 0)
        _, out, _ = _run(manage.cmd_conn_list)
        self.assertEqual(_status(out, "msd"), "active (explicit)")
        self.assertEqual(_status(out, "webturno"), "no")
        self.assertEqual(type(connectors.adapter_for(connectors.SLOT_SOURCE)).__name__, "MsdConnector")

    def test_invalid_stored_value_reads_invalid_on_every_row_and_is_not_the_default(self):
        self._store["CONNECTOR_SLOT_SOURCE"] = "nope"
        _, out, _ = _run(manage.cmd_conn_list)
        self.assertEqual(_status(out, "msd"), "invalid:nope")
        self.assertEqual(_status(out, "webturno"), "invalid:nope")
        self.assertIsNone(connectors.adapter_for(connectors.SLOT_SOURCE))
        _, out, _ = _run(manage.cmd_conn_info, id="msd")
        self.assertIn("active:      invalid:nope\n", out)

    def test_falsey_non_string_value_is_reported_without_becoming_absent(self):
        for raw in (False, 0):
            with self.subTest(raw=raw):
                self._store["CONNECTOR_SLOT_SOURCE"] = raw
                _, out, _ = _run(manage.cmd_conn_list)
                self.assertEqual(_status(out, "msd"), f"invalid:{raw}")

    def test_lock_note_after_enable_and_disable(self):
        with _env.patched_env({"TURNOS_CONFIG_LOCK": "1"}):
            code, out, err = _run(manage.cmd_conn_enable, id="msd", set=None)
            self.assertEqual(code, 0)
            self.assertIn("msd habilitado para slot_source", out)
            self.assertIn("saved, but TURNOS_CONFIG_LOCK=1: env/.env values take precedence "
                          "until the lock is removed", err)
            # The write landed (not refused)...
            self.assertEqual(self._store["CONNECTOR_SLOT_SOURCE"], "msd")
            _, listed, _ = _run(manage.cmd_conn_list)
            self.assertEqual(_status(listed, "msd"), "active (default)")
            code, out, err = _run(manage.cmd_conn_disable, id="slot_source")
            self.assertEqual(code, 0)
            self.assertIn("saved, but TURNOS_CONFIG_LOCK=1", err)
            self.assertEqual(self._store["CONNECTOR_SLOT_SOURCE"], "off")
            _, listed, _ = _run(manage.cmd_conn_list)
            self.assertEqual(_status(listed, "msd"), "active (default)")


class TestArgparseWiring(unittest.TestCase):
    def test_mod_list_parses(self):
        parser = manage.build_parser()
        args = parser.parse_args(["mod", "list"])
        self.assertIs(args.func, manage.cmd_mod_list)

    def test_conn_enable_parses_with_repeated_set(self):
        parser = manage.build_parser()
        args = parser.parse_args(["conn", "enable", "puco", "--set", "A=1", "--set", "B=2"])
        self.assertEqual(args.id, "puco")
        self.assertEqual(args.set, ["A=1", "B=2"])

    def test_conn_requires_a_subcommand(self):
        parser = manage.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["conn"])

    def test_auth_password_inventory_parses_require_zero(self):
        parser = manage.build_parser()
        args = parser.parse_args(["auth", "password-inventory", "--require-zero"])
        self.assertIs(args.func, manage.cmd_auth_password_inventory)
        self.assertTrue(args.require_zero)


class TestAuthPasswordInventory(unittest.TestCase):
    @mock.patch("app.core.db_accounts.legacy_password_hash_inventory")
    def test_reports_only_counts_and_passes_when_zero(self, inventory):
        inventory.return_value = {"patients": 0, "staff": 0, "owners": 0}
        code, out, err = _run(manage.cmd_auth_password_inventory, require_zero=True)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out.splitlines(),
            ["patients: 0", "staff: 0", "owners: 0", "total: 0"],
        )

    @mock.patch("app.core.db_accounts.legacy_password_hash_inventory")
    def test_require_zero_blocks_without_exposing_rows(self, inventory):
        inventory.return_value = {"patients": 2, "staff": 1, "owners": 0}
        code, out, err = _run(manage.cmd_auth_password_inventory, require_zero=True)
        self.assertEqual(code, 1)
        self.assertIn("total: 3", out)
        self.assertIn("compatibility is still required", err)

    @mock.patch("app.core.db_accounts.legacy_password_hash_inventory", side_effect=RuntimeError("db"))
    def test_database_failure_is_value_free(self, _inventory):
        code, out, err = _run(manage.cmd_auth_password_inventory, require_zero=False)
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "password inventory unavailable (check DATABASE_URL)\n")


class TestBackupCommandReadsFreshConfig(unittest.TestCase):
    """A fresh CLI process reads config once while importing (timezone -> pack chain), before the
    database answers, and kept that empty answer for the 3 s cache: `backup run` then saw an
    installation with no backup settings. The command drops the cache before it reads them."""

    def test_the_config_cache_is_dropped_before_the_backup_operation(self):
        from app.core import config
        from app.ops import backup_full
        order = []
        with mock.patch.object(config, "_invalidate_db_cache", side_effect=lambda: order.append("invalidate")), \
                mock.patch.object(backup_full, "run", side_effect=lambda: order.append("run") or {"id": "x"}):
            code, out, err = _run(manage.cmd_backup, backup_command="run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(order, ["invalidate", "run"])


if __name__ == "__main__":
    unittest.main()
