"""What public users lean on: safe update checks, private diagnostics, the folder picker and the taste lists."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_security import rb, server, track  # noqa: E402  (points db.DB_PATH at a temp dir first)

import diagnostics  # noqa: E402
import paths  # noqa: E402
import updates  # noqa: E402

GOOD_URL = updates.RELEASES_PREFIX + "tag/v2.0.0"


class UpdateTests(unittest.TestCase):
    def test_a_newer_release_is_offered(self):
        self.assertEqual(updates.newer_release({"tag_name": "v2.0.0", "html_url": GOOD_URL}, "1.0.1"),
                         {"version": "2.0.0", "url": GOOD_URL})

    def test_the_same_or_an_older_release_is_not(self):
        for tag in ("v1.0.1", "v1.0.0", "0.9.9"):
            self.assertIsNone(updates.newer_release({"tag_name": tag, "html_url": GOOD_URL}, "1.0.1"), tag)

    def test_versions_compare_as_numbers(self):
        self.assertIsNotNone(updates.newer_release({"tag_name": "v1.0.10", "html_url": GOOD_URL}, "1.0.9"))

    def test_the_answer_is_not_trusted(self):
        for release in ({"tag_name": "v9.0.0", "html_url": "https://evil.example/blendbook.dmg"},
                        {"tag_name": "v9.0.0", "html_url": "http://github.com/ujjaidasani/blendbook/releases/x"},
                        {"tag_name": "<script>", "html_url": GOOD_URL}, {"tag_name": 9, "html_url": GOOD_URL},
                        {"tag_name": "v9.0.0"}, {}, [], None, "v9.0.0"):
            self.assertIsNone(updates.newer_release(release, "1.0.1"), release)

    def test_being_offline_is_quiet_and_rechecks_only_daily(self):
        with mock.patch.dict(updates._last, {"at": 0.0, "result": None}), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("offline")) as call:
            self.assertIsNone(updates.check("1.0.1"))
            self.assertIsNone(updates.check("1.0.1"))
            self.assertEqual(call.call_count, 1)


class DiagnosticsTests(unittest.TestCase):
    def report(self, log="", error=None):
        status = {"rekordbox_version": "7.2.19", "tested_version": "7.2.19", "found": True, "tracks": 10, "sessions": 2,
                  "loaded_at": "now", "error": error, "rekordbox_dir": str(Path.home() / "Library/Pioneer/rekordbox")}
        online = {"coverage": {"rekordbox_keys": 5, "from_online": 2, "no_rekordbox_key": {"spotify": 3}}}
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "server.log"
            log_path.write_text(log, encoding="utf-8")
            with mock.patch.object(paths, "LOG_PATH", log_path):
                return diagnostics.report("1.0.1", status, online, ai=True)

    def test_says_what_a_bug_report_needs(self):
        text = self.report()
        for want in ("Blendbook 1.0.1", "7.2.19", "10 tracks", "AI coach: on"):
            self.assertIn(want, text)

    def test_hides_the_home_folder_everywhere(self):
        home = str(Path.home())
        text = self.report(log=f"Traceback in {home}/Music/x.mp3", error=f"Could not read {home}/lib")
        self.assertNotIn(home, text)
        self.assertIn("~/Music/x.mp3", text)

    def test_a_missing_log_is_fine(self):
        with mock.patch.object(paths, "LOG_PATH", Path("/no/such/server.log")):
            self.assertIn("Blendbook", diagnostics.report("1.0.1", {
                "rekordbox_version": None, "tested_version": "7.2.19", "found": False, "tracks": 0, "sessions": 0,
                "loaded_at": None, "error": None, "rekordbox_dir": "/x"},
                {"coverage": {"rekordbox_keys": 0, "from_online": 0, "no_rekordbox_key": {}}}, ai=False))


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_accepts_the_folder_or_its_parent(self):
        lib = self.root / "rekordbox"
        lib.mkdir()
        (lib / "master.db").write_bytes(b"")
        self.assertEqual(server.valid_rekordbox_folder(str(lib)), lib)
        self.assertEqual(server.valid_rekordbox_folder(str(self.root)), lib)

    def test_refuses_a_folder_without_a_library(self):
        with self.assertRaises(server.BadRequest):
            server.valid_rekordbox_folder(str(self.root))

    def test_a_saved_folder_that_has_gone_is_ignored(self):
        before = server.library.rb_dir
        with mock.patch.object(server, "load_config", return_value={"rekordbox_dir": str(self.root / "gone")}):
            server.apply_saved_folder()
        self.assertEqual(server.library.rb_dir, before)


class VersionTests(unittest.TestCase):
    def status(self, settings):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "rekordbox3.settings"
            f.write_text(settings, encoding="utf-8")
            with mock.patch.object(paths, "RB_SETTINGS", [Path(d) / "missing.settings", f]):
                return rb.RekordboxLibrary(Path(d)).status()

    def test_reads_rekordbox_version(self):
        self.assertEqual(self.status('<VALUE name="LaunchedVersion" val="70219"/>')["rekordbox_version"], "7.2.19")

    def test_flags_a_newer_rekordbox(self):
        self.assertFalse(self.status('<VALUE name="LaunchedVersion" val="70219"/>')["newer_rekordbox"])
        self.assertTrue(self.status('<VALUE name="LaunchedVersion" val="70300"/>')["newer_rekordbox"])

    def test_an_unknown_version_is_not_an_alarm(self):
        st = self.status("nothing useful")
        self.assertIsNone(st["rekordbox_version"])
        self.assertFalse(st["newer_rekordbox"])


class TasteTests(unittest.TestCase):
    def test_leaning_on_means_most_of_your_recent_sets(self):
        lib = rb.RekordboxLibrary(Path("/nonexistent"))
        lib.tracks = {str(i): track(str(i), 124) for i in range(8)}
        lib.tracks["7"]["is_sample"] = True
        at = datetime(2026, 10, 1, 21)
        lib.sessions = []
        for n in range(5):
            ids = ["0", "7", str(n + 1)] + (["6"] if n < 2 else [])  # 0 is in all 5 sets, 6 in only 2
            lib.sessions.append({"id": str(n), "name": "H", "started_at": at + timedelta(days=n), "plays": [
                {"track_id": t, "played_at": at + timedelta(days=n, seconds=200 * i)} for i, t in enumerate(ids)]})
        leaning = lib.insights()["leaning_on"]
        self.assertEqual([(t["id"], t["sets"], t["of"]) for t in leaning], [("0", 5, 5)])  # no samples, no rare tracks


if __name__ == "__main__":
    unittest.main()
