"""Live must keep following rekordbox: through a long outro, past a failing job, and with no WAL file."""

import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_security import rb, server, track  # noqa: E402  (points db.DB_PATH at a temp dir first)


def library_playing(seconds_ago):
    lib = rb.RekordboxLibrary(Path("/nonexistent"))
    lib.tracks = {"1": track("1", 124), "2": track("2", 125)}
    played = datetime.now() - timedelta(seconds=seconds_ago)
    lib.sessions = [{"id": "s", "name": "HISTORY", "started_at": played, "plays": [{"track_id": "1", "played_at": played}]}]
    return lib


class ActiveWindowTests(unittest.TestCase):
    def test_a_long_outro_is_still_live(self):
        # the track is 200 s long and was logged about 60 s in, so it "ended" 140 s after logging
        self.assertTrue(library_playing(150).live()["active"])
        self.assertTrue(library_playing(300).live()["active"])  # looped past its end, next track not logged yet

    def test_a_finished_set_stops_being_live(self):
        self.assertFalse(library_playing(200 + rb.ENDED_GRACE_SEC + 5).live()["active"])


class BackgroundLoopTests(unittest.TestCase):
    def test_a_failing_job_keeps_running(self):
        runs = []
        done = threading.Event()

        def job():
            runs.append(1)
            if len(runs) == 3:
                done.set()
                threading.Event().wait()  # enough: park this thread rather than keep logging failures
            raise OSError("disk hiccup")

        threading.Thread(target=server.every, args=(0.001, job), daemon=True).start()
        self.assertTrue(done.wait(5), "the loop stopped after the first failure")


class WalTests(unittest.TestCase):
    def test_refresh_without_a_wal_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "master.db").write_bytes(b"")
            lib = rb.RekordboxLibrary(Path(d))
            lib._mtime = (Path(d) / "master.db").stat().st_mtime  # already loaded: no reload needed
            lib.refresh()
            self.assertIsNone(lib.error)


if __name__ == "__main__":
    unittest.main()
