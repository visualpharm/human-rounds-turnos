"""test_analytics_events.py — the contract of the anonymous analytics stream (analytics_events.py).

Pure functions, no database. The allowlists pinned here are the privacy promise: a key that is not listed
never reaches a row.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.modules.turnos import analytics_events as ev  # noqa: E402

VOCAB = frozenset(("cardiologia", "pediatria", "perez", "pinamar", "kinesiologia"))
JID = "a1b2c3d4e5f6"
PL = "p1l2o3a4d5x6"
CHROME = "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"


class TestContract(unittest.TestCase):
    def test_allowlists_are_pinned(self):
        self.assertEqual(ev.TYPES, ("page_view", "search", "step"))
        self.assertEqual(ev.FIELDS["page_view"], ("route", "rid", "ref", "lang", "device", "jid", "pl"))
        self.assertEqual(ev.FIELDS["search"], ("q", "n", "origin", "jid", "pl"))
        self.assertEqual(ev.FIELDS["step"], ("step", "service", "jid", "pl"))
        self.assertEqual(ev.STEPS, ("slots_shown", "submit", "done", "error"))

    def test_no_field_name_can_carry_a_person(self):
        banned = ("dni", "name", "nombre", "email", "mail", "phone", "tel", "ip", "ua", "user", "cuit")
        for kind, fields in ev.FIELDS.items():
            for f in fields:
                self.assertNotIn(f, banned, (kind, f))

    def test_id_is_23_chars_and_sortable(self):
        a = ev.new_id()
        self.assertEqual(len(a), 23)
        int(a, 16)
        self.assertLessEqual(a[:13], ev.new_id()[:13])

    def test_conforms_needs_exactly_the_keys_of_the_type(self):
        e = ev.step({"step": "done", "service": "cardiologia", "jid": JID, "pl": PL}, device="desktop")
        self.assertTrue(ev.conforms(e))
        self.assertFalse(ev.conforms(dict(e, extra="x")))
        broken = dict(e)
        broken.pop("jid")
        self.assertFalse(ev.conforms(broken))
        self.assertFalse(ev.conforms({"type": "other"}))


class TestRoutes(unittest.TestCase):
    def test_static_and_templated(self):
        self.assertEqual(ev.route_of("/"), ("/", None))
        self.assertEqual(ev.route_of("/doctors/"), ("/doctors", None))
        self.assertEqual(ev.route_of("/doctor/dr-perez"), ("/doctor/:id", "dr-perez"))
        self.assertEqual(ev.route_of("/center/pinamar?x=1#y"), ("/center/:slug", "pinamar"))

    def test_unknown_or_private_paths_are_refused(self):
        self.assertIsNone(ev.route_of("/admin/pacientes"))
        self.assertIsNone(ev.route_of("/api/requests"))
        self.assertIsNone(ev.route_of("/x" * 100))

    def test_slug_that_looks_like_a_document_has_no_rid(self):
        self.assertEqual(ev.route_of("/doctor/30123456"), ("/doctor/:id", None))
        self.assertEqual(ev.route_of("/service/Ana Perez"), ("/service/:id", None))


class TestDeviceAndRef(unittest.TestCase):
    def test_devices(self):
        self.assertEqual(ev.device_of(CHROME), "desktop")
        self.assertEqual(ev.device_of(IPHONE), "mobile")
        self.assertEqual(ev.device_of("Mozilla/5.0 (iPad; CPU OS 17_0)"), "tablet")

    def test_robots_have_no_device(self):
        self.assertIsNone(ev.device_of(""))
        self.assertIsNone(ev.device_of("Googlebot/2.1"))
        self.assertIsNone(ev.device_of("curl/8.0"))

    def test_referrer_is_a_host_only(self):
        self.assertEqual(ev.ref_of(""), "direct")
        self.assertEqual(ev.ref_of("https://www.google.com/search?q=dni+30123456"), "www.google.com")
        self.assertEqual(ev.ref_of("https://salud.example/doctors", "salud.example"), "internal")
        self.assertEqual(ev.ref_of("not a url"), "direct")

    def test_the_beacons_bare_host_is_read_like_a_url(self):
        # analytics.mjs sends `new URL(document.referrer).host`: no scheme, maybe a port. All of these were «direct».
        self.assertEqual(ev.ref_of("www.google.com"), "www.google.com")
        self.assertEqual(ev.ref_of("Salud.Example:8443", "salud.example"), "internal")
        self.assertEqual(ev.ref_of("https://salud.example:8443/doctors", "salud.example"), "internal")
        self.assertEqual(ev.ref_of("l.instagram.com", "salud.example"), "l.instagram.com")
        events, _ = ev.clean_batch({"events": [{"t": "page_view", "path": "/", "jid": JID, "pl": PL,
                                                "ref": "www.google.com"}]}, user_agent=CHROME, own_host="")
        self.assertEqual(events[0]["ref"], "www.google.com")


class TestSearchText(unittest.TestCase):
    def test_public_words_stay(self):
        self.assertEqual(ev.sanitize_query("Cardiología", VOCAB), "cardiologia")

    def test_other_words_are_masked(self):
        out = ev.sanitize_query("turno cardiologia para Juan Gomez", VOCAB)
        self.assertNotIn("juan", out)
        self.assertNotIn("gomez", out)
        self.assertIn("cardiologia", out)
        self.assertIn("·", out)

    def test_documents_phones_and_addresses_are_dropped(self):
        self.assertIsNone(ev.sanitize_query("30123456", VOCAB))
        self.assertIsNone(ev.sanitize_query("ana@example.com cardiologia", VOCAB))
        self.assertIsNone(ev.sanitize_query("http://x.test cardiologia", VOCAB))
        self.assertNotIn("1155551234", ev.sanitize_query("cardiologia 1155551234", VOCAB))

    def test_no_vocabulary_means_nothing_is_stored(self):
        self.assertIsNone(ev.sanitize_query("cardiologia", frozenset()))

    def test_search_event_shape(self):
        e = ev.search("pediatria", 4, "/doctors", JID, PL, vocab=VOCAB)
        self.assertTrue(ev.conforms(e))
        self.assertEqual((e["q"], e["n"], e["origin"]), ("pediatria", 4, "/doctors"))
        self.assertIsNone(ev.search("Juan Gomez", 1, "/", JID, PL, vocab=VOCAB))
        self.assertIsNone(ev.search("pediatria", "x", "/", JID, PL, vocab=VOCAB))


class TestBatch(unittest.TestCase):
    def _pv(self, **kw):
        base = {"t": "page_view", "path": "/doctors", "jid": JID, "pl": PL, "lang": "es", "ref": ""}
        base.update(kw)
        return base

    def test_page_view_keeps_only_the_allowlist(self):
        raw = self._pv(dni="30123456", name="Ana", extra="x")
        events, bots = ev.clean_batch({"events": [raw]}, user_agent=CHROME, own_host="")
        self.assertEqual(bots, 0)
        self.assertEqual(len(events), 1)
        self.assertTrue(ev.conforms(events[0]))
        self.assertNotIn("dni", events[0])
        self.assertEqual(events[0]["device"], "desktop")

    def test_query_string_never_travels(self):
        events, _ = ev.clean_batch({"events": [self._pv(path="/doctor/dr-perez?dni=30123456")]},
                                   user_agent=CHROME, own_host="")
        self.assertEqual(events[0]["rid"], "dr-perez")
        self.assertNotIn("30123456", str(events[0]))

    def test_robot_and_webdriver_are_dropped(self):
        self.assertEqual(ev.clean_batch({"events": [self._pv()]}, user_agent="Googlebot/2.1", own_host=""), ([], 1))
        events, _ = ev.clean_batch({"events": [self._pv(wd=1)]}, user_agent=CHROME, own_host="")
        self.assertEqual(events, [])

    def test_bad_identifiers_and_shapes_give_nothing(self):
        for body in ({"events": [self._pv(jid="x")]}, {"events": "no"}, [], None, {"events": [5]}):
            self.assertEqual(ev.clean_batch(body, user_agent=CHROME, own_host="")[0], [], body)

    def test_unknown_step_is_refused_and_batch_is_capped(self):
        raw = {"t": "step", "step": "dni_typed", "jid": JID, "pl": PL}
        self.assertEqual(ev.clean_batch({"events": [raw]}, user_agent=CHROME, own_host="")[0], [])
        many = {"events": [self._pv() for _ in range(ev.MAX_BATCH + 15)]}
        self.assertEqual(len(ev.clean_batch(many, user_agent=CHROME, own_host="")[0]), ev.MAX_BATCH)

    def test_unknown_language_falls_back_to_spanish(self):
        events, _ = ev.clean_batch({"events": [self._pv(lang="xx")]}, user_agent=CHROME, own_host="")
        self.assertEqual(events[0]["lang"], "es")


if __name__ == "__main__":
    unittest.main()
