"""Request guards, input hardening, and the live features built on them (plan follow, half-time labels).

Runs a real server on a free port against a throwaway database: never touches .cache/blendbook.db."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import security  # noqa: E402

TMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows can't delete a database that's still open
db.DB_PATH = Path(TMP.name) / "server.db"  # before server is imported: its Store() opens DB_PATH

import phone as phone_mod  # noqa: E402
import recordings  # noqa: E402
import rb  # noqa: E402
import server  # noqa: E402

WEB = Path(server.WEB_DIR)


def track(tid, bpm, title=None, artist="A", genre="Techno"):
    return {"id": tid, "title": title or f"T{tid}", "artist": artist, "bpm": bpm, "key": None, "genre": genre,
            "length": 200, "source": "file", "rating": 0, "year": None, "added": None, "is_sample": False}


class GuardTests(unittest.TestCase):
    def test_allowed_hosts(self):
        for host in ("localhost:8080", "127.0.0.1:8080", "[::1]:8080", "LOCALHOST:8080"):
            self.assertTrue(security.allowed_host(host, 8080), host)
        for host in ("evil.example:8080", "localhost:9999", "localhost", "", None, "127.0.0.1.evil.example:8080"):
            self.assertFalse(security.allowed_host(host, 8080), host)

    def test_post_needs_json_and_same_origin(self):
        ok = {"Content-Type": "application/json; charset=utf-8", "Origin": "http://localhost:8080"}
        self.assertIsNone(security.same_origin_post(ok, 8080))
        self.assertIsNone(security.same_origin_post({"Content-Type": "application/json"}, 8080))  # curl, tests
        self.assertIsNotNone(security.same_origin_post({**ok, "Content-Type": "text/plain"}, 8080))
        self.assertIsNotNone(security.same_origin_post({**ok, "Origin": "https://evil.example"}, 8080))
        self.assertIsNotNone(security.same_origin_post({**ok, "Origin": "null"}, 8080))
        self.assertIsNotNone(security.same_origin_post({**ok, "Sec-Fetch-Site": "cross-site"}, 8080))
        self.assertIsNone(security.same_origin_post({**ok, "Sec-Fetch-Site": "same-origin"}, 8080))

    def test_csp_allows_exactly_the_inline_scripts(self):
        csp = security.content_security_policy(WEB)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("unsafe-inline' 'self'", csp)
        self.assertIn("media-src 'self' blob: https://audio-ssl.itunes.apple.com;", csp)
        script_src = next(d for d in csp.split("; ") if d.startswith("script-src"))
        self.assertNotIn("unsafe", script_src)
        inline_pages = [p for p in WEB.glob("*.html") if security._INLINE_SCRIPT.search(p.read_text(encoding="utf-8"))]
        self.assertGreaterEqual(script_src.count("'sha256-"), 1 if inline_pages else 0)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert server.store.path == db.DB_PATH, "tests must not open the real database"
        server.library.tracks = {t["id"]: t for t in [track("1", 128), track("2", 128.5), track("3", 129),
                                                       track("4", 64.2, "Half"), track("5", 140)]}
        server.library.sessions = []
        server.library._cache = {}
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": f"localhost:{self.port}"}
        if body is not None or raw is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=raw if raw is not None else (json.dumps(body) if body is not None else None),
                     headers=hdrs)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        try:
            return res.status, json.loads(data or b"null"), res
        except json.JSONDecodeError:
            return res.status, data, res

    def test_cross_site_post_is_refused_and_changes_nothing(self):
        before = len(server.store.goals())
        st, _, _ = self.req("POST", "/api/goals", raw='{"metric":"gaps","target":1}', headers={"Content-Type": "text/plain"})
        self.assertEqual(st, 403)
        st, _, _ = self.req("POST", "/api/goals", {"metric": "gaps", "target": 1}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        self.assertEqual(len(server.store.goals()), before)

    def test_dns_rebinding_host_is_refused(self):
        for path in ("/api/status", "/index.html", "/api/live"):
            st, _, _ = self.req("GET", path, headers={"Host": f"evil.example:{self.port}"})
            self.assertEqual(st, 421, path)

    def test_security_headers_on_pages_and_api(self):
        for path in ("/", "/api/status"):
            _, _, res = self.req("GET", path)
            self.assertIn("frame-ancestors 'none'", res.getheader("Content-Security-Policy"))
            self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(res.getheader("Cross-Origin-Resource-Policy"), "same-origin")

    def test_junk_input_gets_a_400_not_a_dropped_connection(self):
        cases = [
            ("/api/coach", "[1, 2]"),
            ("/api/coach", "{not json"),
            ("/api/goals", '{"metric": "bpm_jumps", "target": "nan"}'),
            ("/api/goals", '{"metric": ["gaps"], "target": 1}'),
            ("/api/plan", '{"start_id": "1", "include_ids": "abc"}'),
            ("/api/listen", '{"client_session": "../../etc/x", "start_ms": 1760000000000}'),
            ("/api/listen", '{"client_session": "abc", "start_ms": 1e400}'),
            ("/api/mixlog", '{"client_session": "a/b", "events": []}'),
            ("/api/settings", '{"anthropic_api_key": {"k": 1}}'),
        ]
        for path, raw in cases:
            st, data, _ = self.req("POST", path, raw=raw)
            self.assertEqual(st, 400, f"{path} {raw} -> {st} {data}")
        st, _, _ = self.req("POST", "/api/coach", headers={"Content-Length": "-5", "Content-Type": "application/json"})
        self.assertEqual(st, 400)
        st, data, _ = self.req("POST", "/api/plan/alternatives", {"prev_id": "1", "target_bpm": "abc"})
        self.assertEqual(st, 200)

    def test_plan_follow(self):
        pid = server.store.add_set_plan("Warm-up", ["1", "2", "3", "5"], "warmup")
        try:
            ctx = server.plan_context("2", played=["1", "2"])
            self.assertEqual((ctx["name"], ctx["position"], ctx["next"]["id"], ctx["next_position"]), ("Warm-up", 2, "3", 3))
            self.assertEqual([t["id"] for t in ctx["then"]], ["5"])
            # a planned track you already played is skipped over
            ctx = server.plan_context("2", played=["1", "2", "3"])
            self.assertEqual((ctx["next"]["id"], ctx["next_position"]), ("5", 4))
            # the last track of a plan: no next, but you still know you're in it
            self.assertIsNone(server.plan_context("5", played=[])["next"])
            self.assertIsNone(server.plan_context("4", played=[]))
            st, data, _ = self.req("GET", "/api/suggest?track_id=2")
            self.assertEqual(st, 200)
            self.assertEqual(data["plan"]["next"]["id"], "3")
        finally:
            server.store.delete_set_plan(pid)

    def test_half_time_matches_say_so(self):
        sugg = {s["id"]: s for s in server.library.suggest("1", limit=10)}
        self.assertIn("half-time, feels like 128.4", sugg["4"]["reasons"][0])
        self.assertNotIn("time", sugg["2"]["reasons"][0])

    @unittest.skipIf(sys.platform == "win32", "Windows has no owner-only mode bits; %APPDATA% is already per user")
    def test_config_file_is_owner_only(self):
        orig = server.CONFIG_PATH
        server.CONFIG_PATH = Path(TMP.name) / "cfg" / "config.json"
        try:
            server.save_config({"anthropic_api_key": "sk-ant-test"})
            self.assertEqual(server.CONFIG_PATH.stat().st_mode & 0o777, 0o600)
            self.assertEqual(server.load_config()["anthropic_api_key"], "sk-ant-test")
        finally:
            server.CONFIG_PATH = orig


class InputHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = db.Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_mix_events_coerce_junk(self):
        ev = [{"uid": "a", "ts": float("inf"), "type": "t"},
              {"uid": {"x": 1}, "ts": 5, "type": "t"},
              {"uid": "b", "ts": 1_760_000_000_000, "type": "t", "big": "x" * 10_000},
              {"uid": "c", "ts": 1_760_000_000_000, "type": "transition_end", "deck": {"a": 1}, "from": 99, "to": 2,
               "durationSec": "lots", "eqMoves": "x", "bassOverlapSec": float("nan"), "verdict": ["no"]}]
        self.assertEqual(self.store.add_mix_events("s1", ev), 1)
        t = self.store.mix_transitions_between(0, 2_000_000_000_000)[0]
        self.assertEqual((t["from_deck"], t["to_deck"], t["duration_sec"], t["eq_moves"], t["verdict"]),
                         (None, 2, 0.0, None, None))

    def test_mic_features_reject_paths_and_junk_values(self):
        orig = recordings.audio.FEATURES_DIR
        recordings.audio.FEATURES_DIR = Path(self.tmp.name) / "features"
        try:
            with self.assertRaises(ValueError):
                recordings.add_mic_features(self.store, "../x", 1_760_000_000_000, {})
            res = recordings.add_mic_features(self.store, "abc-123", 1_760_000_000_000,
                                              {"loudness_db": [-20, "x", None, 5, -99], "low": "nope", "mid": [0.5] * 5})
            self.assertEqual(res["seconds"], 5)
            feat = json.loads((recordings.audio.FEATURES_DIR / "mic-abc-123.json").read_text())
            self.assertEqual(feat["loudness_db"], [-20.0, -60.0, -60.0, 0.0, -60.0])
            self.assertEqual(feat["bands"]["low"], [0.0] * 5)
        finally:
            recordings.audio.FEATURES_DIR = orig

    def test_phone_mixer_relay_keeps_only_clean_fields(self):
        m = phone_mod.clean_mixer({"bassClash": 1, "decks": {"1": "x", "2": {"eq": 5, "fader": "a", "playing": 1},
                                                             "9": {}, "evil": {"x": 1}},
                                   "currentTransition": {"from": 1, "to": "2", "elapsedSec": 1e9, "eqMoves": -3},
                                   "extra": "<script>"})
        self.assertEqual(m, {"bassClash": True,
                             "currentTransition": {"from": 1, "to": None, "elapsedSec": 3600.0, "bassOverlapSec": 0.0, "eqMoves": 0},
                             "decks": {"2": {"playing": True, "audible": False, "fader": None, "low": None}}})
        self.assertIsNone(phone_mod.clean_mixer("junk"))
        pm = phone_mod.PhoneMode(rb.RekordboxLibrary(Path(self.tmp.name)))
        pm.set_mixer({"decks": {"1": {"fader": 2}}})
        self.assertEqual(pm.mixer["decks"]["1"]["fader"], 1.0)


if __name__ == "__main__":
    unittest.main()
