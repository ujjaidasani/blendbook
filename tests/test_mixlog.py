import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import mixlog  # noqa: E402


def ms(dt):
    return int(dt.timestamp() * 1000)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = db.Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_migrate_is_idempotent(self):
        self.assertEqual(db.migrate(self.store.conn), len(db.MIGRATIONS))
        v = self.store.conn.execute("SELECT version FROM schema_version").fetchone()[0]
        self.assertEqual(v, len(db.MIGRATIONS))

    def test_mix_events_dedupe_and_transition_row(self):
        ev = [{"uid": "a", "ts": 10_000, "type": "transition_start", "from": 1, "to": 2},
              {"uid": "b", "ts": 40_000, "type": "transition_end", "from": 1, "to": 2, "durationSec": 30,
               "bassOverlapSec": 6.5, "eqMoves": 3, "verdict": "Clean 30s blend"}]
        self.assertEqual(self.store.add_mix_events("s1", ev), 2)
        self.assertEqual(self.store.add_mix_events("s1", ev), 0)  # retry is a no-op
        t = self.store.mix_transitions_between(0, 100_000)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["started_ts"], 10_000)
        self.assertEqual(t[0]["bass_overlap_sec"], 6.5)
        self.assertEqual(self.store.mix_log_status()["events"], 2)

    def test_bad_events_are_skipped(self):
        self.assertEqual(self.store.add_mix_events("s1", [{"ts": 1, "type": "x"}, {"uid": "z", "type": "x"}]), 0)

    def test_goals_plans_recordings_round_trip(self):
        self.store.add_goal("gaps", 1, focus=True)
        self.store.add_goal("bpm_jumps", 0, focus=True)
        goals = self.store.goals()
        self.assertEqual([g["focus"] for g in goals], [0, 1])
        self.store.add_set_plan("Friday", ["1", "2"], shape=[0.2, 0.8])
        self.assertEqual(self.store.set_plans()[0]["track_ids"], ["1", "2"])
        self.store.add_recording("/x.wav", "abc", session_id="9", offset_sec=1.5)
        self.assertEqual(self.store.recordings()[0]["offset_sec"], 1.5)


class MatchTests(unittest.TestCase):
    """rekordbox logs the incoming track LOG_LAG_SEC after the blend that brought it in started."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = db.Store(Path(self.tmp.name) / "t.db")
        self.start = datetime(2026, 10, 16, 20, 0, 0)
        # tracks logged at 0, 300, 600, 900 s; transitions bring in tracks 2..4
        self.detail = {
            "started_at": self.start.isoformat(),
            "tracks": [{"played_at": (self.start + timedelta(seconds=s)).isoformat()} for s in (0, 300, 600, 900)],
            "transitions": [{"n": i + 1, "at_sec": s, "to": {"title": f"T{i + 2}"}} for i, s in enumerate((300, 600, 900))],
            "observations": [],
        }

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def blend(self, uid, start_sec, dur, bass=0, eq=2):
        t0 = self.start + timedelta(seconds=start_sec)
        self.store.add_mix_events("s", [{"uid": uid, "ts": ms(t0) + dur * 1000, "type": "transition_end", "from": 1,
                                         "to": 2, "durationSec": dur, "bassOverlapSec": bass, "eqMoves": eq,
                                         "verdict": "v"}])

    def test_blends_land_on_the_right_transitions(self):
        lag = mixlog.LOG_LAG_SEC
        self.blend("a", 300 - lag + 5, 30, bass=8)    # -> transition 0, high confidence
        self.blend("b", 600 - lag - 100, 20)            # -> transition 1, low confidence (100 s off)
        self.blend("c", 1500, 10)                       # inside the window, matches nothing
        d = mixlog.enrich(self.detail, self.store)
        c = [t["controller"] for t in d["transitions"]]
        self.assertEqual(c[0]["confidence"], "high")
        self.assertEqual(c[0]["bass_overlap_sec"], 8)
        self.assertEqual(c[1]["confidence"], "low")
        self.assertIsNone(c[2])
        self.assertEqual(d["controller"]["matched"], 2)
        self.assertEqual(len(d["controller"]["unmatched"]), 1)
        self.assertEqual(d["controller"]["heavy_bass_blends"], 1)
        self.assertTrue(any(o.get("source") == "controller" for o in d["observations"]))

    def test_one_to_one(self):
        lag = mixlog.LOG_LAG_SEC
        self.blend("a", 300 - lag, 30)
        self.blend("b", 300 - lag + 10, 30)  # competes for the same transition and loses
        d = mixlog.enrich(self.detail, self.store)
        # the next transition is too far away for the loser, so it stays unmatched rather than doubling up
        self.assertEqual(d["controller"]["matched"], 1)
        self.assertEqual(len(d["controller"]["unmatched"]), 1)
        self.assertEqual(d["transitions"][0]["controller"]["confidence"], "high")

    def test_no_controller_data(self):
        d = mixlog.enrich(self.detail, self.store)
        self.assertFalse(d["controller"]["seen"])
        self.assertTrue(all(t["controller"] is None for t in d["transitions"]))


if __name__ == "__main__":
    unittest.main()


class ConcurrencyTests(unittest.TestCase):
    def test_parallel_reads_and_writes(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            store = db.Store(Path(d) / "t.db")
            errors = []

            def work(i):
                try:
                    for j in range(50):
                        store.add_mix_events("s", [{"uid": f"{i}-{j}", "ts": j, "type": "play"}])
                        store.mix_transitions_between(0, 10 ** 12)
                        store.goals()
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(errors, [])
            self.assertEqual(store.mix_log_status()["events"], 400)
            store.close()
