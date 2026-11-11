"""Phone view: what the read-only LAN server sends a paired phone (plan follow, slot clock pace), and how it answers.

server.py can only be imported once per process and its Store opens db.DB_PATH at import, so this reuses
test_security's throwaway database instead of making a second one. Never touches .cache/blendbook.db."""

import http.client
import json
import re
import sys
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # `python -m unittest tests.test_phone` finds test_security

from test_security import WEB, db, phone_mod, server, track  # noqa: E402  (points db.DB_PATH at a temp dir first)

TOKEN = "test-phone-token"


def live_session(ids, last_ago_sec=60, gap_sec=240):
    """A rekordbox history session that played ids in order, the last one last_ago_sec ago."""
    now = datetime.now()
    plays = [{"track_id": t, "played_at": now - timedelta(seconds=last_ago_sec + gap_sec * (len(ids) - 1 - i))}
             for i, t in enumerate(ids)]
    return {"id": "99", "name": "Tonight", "started_at": plays[0]["played_at"], "plays": plays}


class PhoneStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert server.store.path == db.DB_PATH, "tests must not open the real database"
        server.library.tracks = {t["id"]: t for t in [track("1", 128), track("2", 128.5), track("3", 129),
                                                       track("4", 64.2, "Half"), track("5", 140)]}

    def setUp(self):
        self.pid = server.store.add_set_plan("Warm-up", ["1", "2", "3", "5"], "warmup")
        self.play(["1", "2"])

    def tearDown(self):
        server.store.delete_set_plan(self.pid)
        server.library.sessions = []
        server.library._cache = {}

    def play(self, ids, **kw):
        server.library.sessions = [live_session(ids, **kw)]
        server.library._cache = {}

    def test_plan_follow_reaches_the_phone(self):
        st = server.phone.state()  # the instance server.py builds, so this also checks it's wired to plan_context
        self.assertEqual(st["plan"], {"name": "Warm-up", "next_position": 3, "length": 4,
                                      "next": {"title": "T3", "artist": "A", "bpm": 129, "bpm_delta_pct": 0.4}})
        # the plan's next track isn't repeated as a suggestion, and suggestions stay whitelisted
        self.assertTrue(st["suggestions"])
        self.assertNotIn("T3", [s["title"] for s in st["suggestions"]])
        for s in st["suggestions"]:
            self.assertEqual(set(s), {"title", "artist", "bpm", "bpm_delta_pct", "reasons"})

    def test_slot_clock_gets_what_it_needs(self):
        st = server.phone.state()
        self.assertEqual(set(st["session"]), {"track_count", "started_at", "track_pace_sec"})
        self.assertIsInstance(st["session"]["track_pace_sec"], int)
        self.assertEqual((st["now_playing"]["length"], st["now_playing"]["active"]), (200, True))
        self.assertIsInstance(st["now_playing"]["since_logged_sec"], int)

    def test_last_track_of_a_plan(self):
        self.play(["3", "5"])
        self.assertEqual(server.phone.state()["plan"], {"name": "Warm-up", "next_position": None, "length": 4, "next": None})

    def test_no_plan_off_plan_or_between_sets(self):
        self.play(["1", "4"])
        self.assertIsNone(server.phone.state()["plan"])
        self.play(["1", "2"], last_ago_sec=3 * 3600)  # rekordbox stopped logging: "Last set", not live
        st = server.phone.state()
        self.assertFalse(st["now_playing"]["active"])
        self.assertIsNone(st["plan"])

    def test_plan_lookup_is_optional_and_cant_break_the_view(self):
        self.assertIsNone(phone_mod.PhoneMode(server.library).state()["plan"])

        def broken(track_id, played):
            raise RuntimeError("database is locked")

        st = phone_mod.PhoneMode(server.library, plan_lookup=broken).state()
        self.assertIsNone(st["plan"])
        self.assertEqual(st["now_playing"]["title"], "T2")


class PhoneServerTests(unittest.TestCase):
    """The phone server on loopback (enable() would bind every interface)."""

    @classmethod
    def setUpClass(cls):
        server.library.tracks = {t["id"]: t for t in [track("1", 128), track("2", 128.5), track("3", 129)]}

        class Handler(phone_mod.PhoneHandler):
            phone = server.phone

        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        server.phone.tokens.add(TOKEN)

    @classmethod
    def tearDownClass(cls):
        server.phone.tokens.discard(TOKEN)
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.pid = server.store.add_set_plan("Warm-up", ["1", "2", "3"], "warmup")
        server.library.sessions = [live_session(["1", "2"])]
        server.library._cache = {}

    def tearDown(self):
        server.store.delete_set_plan(self.pid)
        server.library.sessions = []
        server.library._cache = {}

    def req(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, data, res

    def test_paired_phone_gets_the_plan_with_security_headers(self):
        st, data, res = self.req("GET", "/api/phone/state", {"X-Blendbook-Token": TOKEN})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(data)["plan"]["next"]["title"], "T3")
        self.assertIn("frame-ancestors 'none'", res.getheader("Content-Security-Policy"))
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.getheader("Cache-Control"), "no-store")

    def test_unpaired_phone_gets_nothing(self):
        st, data, _ = self.req("GET", "/api/phone/state", {"X-Blendbook-Token": "guess"})
        self.assertEqual(st, 401)
        self.assertNotIn(b"Warm-up", data)

    def test_still_read_only(self):
        for path in ("/api/phone/state", "/api/plans", "/api/slot", "/api/mixer-state"):
            st, _, _ = self.req("POST", path, {"Content-Type": "application/json", "X-Blendbook-Token": TOKEN}, "{}")
            self.assertEqual(st, 404, path)

    def test_head_says_nothing_about_files_on_the_laptop(self):
        # unpaired, and whether or not the file exists in the folder the server was started from
        for path in ("/server.py", "/tests/test_phone.py", "/README.md", "/", "/m", "/no-such-file"):
            st, data, res = self.req("HEAD", path)
            self.assertEqual(st, 405, path)
            self.assertEqual(res.getheader("Content-Length"), "0", path)
            self.assertIsNone(res.getheader("Last-Modified"), path)
            self.assertIsNone(res.getheader("Content-Type"), path)

    def test_phone_page_and_script_stay_standalone(self):
        st, page, res = self.req("GET", "/m")
        self.assertEqual(st, 200)
        self.assertIn("Content-Security-Policy", dict(res.getheaders()))
        for el in (b'id="planCard"', b'id="slotEnd"', b'id="slotLine"', b'id="slotClear"'):
            self.assertIn(el, page)
        st, js, _ = self.req("GET", "/js/mobile.js")
        self.assertEqual(st, 200)
        # the phone server only serves the files in ALLOWED_FILES: mobile.js can't import live.js or ui.js
        self.assertNotRegex(js.decode(), re.compile(r"^\s*(import|export)\b", re.M))
        self.assertEqual(self.req("GET", "/js/live.js")[0], 404)


class SlotClockCopyTests(unittest.TestCase):
    def test_phone_slot_clock_matches_the_laptop(self):
        """mobile.js carries a copy of live.js's slot clock: keep them identical so both screens agree."""
        def fn(src, name):
            m = re.search(rf"^(?:export )?(function {name}\(.*?^}})$", src, re.M | re.S)
            self.assertIsNotNone(m, name)
            return m.group(1)

        laptop = (WEB / "js" / "live.js").read_text(encoding="utf-8")
        phone = (WEB / "js" / "mobile.js").read_text(encoding="utf-8")
        for name in ("slotEndDate", "slotStatus"):
            self.assertEqual(fn(phone, name), fn(laptop, name), name)


if __name__ == "__main__":
    unittest.main()
