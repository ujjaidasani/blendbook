"""Energy from rekordbox's waveforms: relative to the library, and online data must never override it."""

import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rb_analysis  # noqa: E402
from test_security import rb, track  # noqa: E402  (points db.DB_PATH at a temp dir first)


def waveform(level, first=None, columns=1200):
    return bytes((first if first is not None else level, level, level)) * columns


class FeatureTests(unittest.TestCase):
    def test_reads_level_and_peak(self):
        self.assertEqual(rb_analysis.waveform_features(waveform(10, first=30)), (50.0, 30.0))

    def test_silent_ends_do_not_count(self):
        padded = bytes(300) + waveform(10) + bytes(300)
        self.assertEqual(rb_analysis.waveform_features(padded), rb_analysis.waveform_features(waveform(10)))

    def test_too_little_waveform_is_unknown(self):
        self.assertIsNone(rb_analysis.waveform_features(waveform(10, columns=10)))
        self.assertIsNone(rb_analysis.waveform_features(b""))

    def test_a_truncated_file_does_not_crash(self):
        self.assertIsNotNone(rb_analysis.waveform_features(waveform(10) + b"\x01"))


class RelativeEnergyTests(unittest.TestCase):
    def features(self, n):
        return {str(i): (float(i), float(i)) for i in range(n)}

    def test_spans_the_scale_in_order(self):
        e = rb_analysis.relative_energy(self.features(40))
        self.assertEqual(e["0"], rb_analysis.ENERGY_LOW)
        self.assertEqual(e["39"], rb_analysis.ENERGY_HIGH)
        self.assertEqual([e[str(i)] for i in range(40)], sorted(e.values()))

    def test_small_libraries_get_no_reading(self):
        self.assertEqual(rb_analysis.relative_energy(self.features(rb_analysis.MIN_TRACKS - 1)), {})

    def test_identical_tracks_do_not_crash(self):
        e = rb_analysis.relative_energy({str(i): (5.0, 5.0) for i in range(30)})
        self.assertEqual(len(e), 30)


class ReaderTests(unittest.TestCase):
    def test_reads_each_file_once_and_skips_what_it_cannot_use(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tracks = {}
            for i in range(25):
                f = root / "share" / "PIONEER" / "USBANLZ" / str(i) / "ANLZ0000.2EX"
                f.parent.mkdir(parents=True)
                f.write_bytes(struct.pack(">I", i))
                tracks[str(i)] = {"analysis_path": f"/PIONEER/USBANLZ/{i}/ANLZ0000.DAT", "is_sample": False}
            tracks["no-analysis"] = {"analysis_path": None, "is_sample": False}
            tracks["missing-file"] = {"analysis_path": "/PIONEER/USBANLZ/x/ANLZ0000.DAT", "is_sample": False}
            tracks["sample"] = {"analysis_path": "/PIONEER/USBANLZ/0/ANLZ0000.DAT", "is_sample": True}
            with mock.patch.object(rb_analysis, "read_features", side_effect=lambda p: (float(p.parent.name), 1.0)) as read:
                reader = rb_analysis.EnergyReader()
                first = reader.energies(root, tracks)
                reader.energies(root, tracks)
            self.assertEqual(len(first), 25)
            self.assertEqual(read.call_count, 25)  # not read again on the next reload
            self.assertGreater(first["24"], first["0"])


class PriorityTests(unittest.TestCase):
    def overlay(self, t):
        rb.RekordboxLibrary._apply_overlay({"1": t}, {"1": {"status": "matched", "energy": 0.2, "key": "4A"}})
        return t

    def test_rekordbox_energy_beats_online(self):
        t = self.overlay({**track("1", 124), "energy": 0.8, "energy_source": "rekordbox"})
        self.assertEqual((t["energy"], t["energy_source"]), (0.8, "rekordbox"))

    def test_online_fills_the_gap(self):
        t = self.overlay({**track("1", 124), "energy": None})
        self.assertEqual((t["energy"], t["energy_source"]), (0.2, "online"))

    def test_rekordbox_key_beats_online(self):
        t = self.overlay({**track("1", 124), "key": "8A", "rb_key": "8A", "key_source": "rekordbox"})
        self.assertEqual(t["key"], "8A")




class OnlyRekordboxKeysCountTests(unittest.TestCase):
    def library(self, **keys):
        """Tracks at one tempo. keys maps id to (key, rb_key)."""
        lib = rb.RekordboxLibrary(Path("/nonexistent"))
        lib.tracks = {i: {**track(i, 124), "key": k, "rb_key": r, "key_source": "rekordbox" if r else "online" if k else None}
                      for i, (k, r) in keys.items()}
        return lib

    def reasons(self, lib, tid="b"):
        return next(s for s in lib.suggest("a") if s["id"] == tid)

    def test_two_rekordbox_keys_match(self):
        pick = self.reasons(self.library(a=("8A", "8A"), b=("9A", "9A"), c=("8A", "8A"), d=("8A", "8A")))
        self.assertEqual(pick["key_relation"], "adjacent")
        self.assertTrue(any(r.startswith("harmonic") for r in pick["reasons"]))

    def test_rekordbox_keys_still_warn_of_a_clash(self):
        pick = self.reasons(self.library(a=("8A", "8A"), b=("3B", "3B"), c=("8A", "8A"), d=("8A", "8A")))
        self.assertEqual(pick["key_relation"], "clash")
        self.assertTrue(any(r.startswith("key clash") for r in pick["reasons"]))

    def test_an_online_key_on_either_side_counts_for_nothing(self):
        for a, b in ((("8A", None), ("8A", "8A")), (("8A", "8A"), ("8A", None)), (("8A", None), ("8A", None)),
                     (("8A", "8A"), ("3B", None)), (("8A", None), ("3B", None))):
            pick = self.reasons(self.library(a=a, b=b, c=("1A", None), d=("1A", None)))
            self.assertIsNone(pick["key_relation"], (a, b))
            self.assertFalse(any("harmonic" in r or "clash" in r or "key lift" in r for r in pick["reasons"]), (a, b))

    def test_online_keys_score_nothing(self):
        online_keys = self.library(a=("8A", None), b=("8A", None), c=("3B", None), d=("3B", None))
        no_keys = self.library(a=(None, None), b=(None, None), c=(None, None), d=(None, None))
        self.assertEqual({s["id"]: s["score"] for s in online_keys.suggest("a")}, {s["id"]: s["score"] for s in no_keys.suggest("a")})

    def test_online_keys_are_still_shown(self):
        self.assertEqual(self.reasons(self.library(a=("8A", None), b=("9A", None), c=("1A", None), d=("1A", None)))["key"], "9A")

    def test_a_set_with_online_keys_has_no_clash_flags(self):
        lib = self.library(a=("8A", None), b=("3B", None), c=("8A", None))
        at = datetime(2026, 10, 1, 21)
        lib.sessions = [{"id": "1", "name": "H", "started_at": at, "plays": [
            {"track_id": t, "played_at": at + timedelta(seconds=200 * i)} for i, t in enumerate("abc")]}]
        detail = lib.session_detail("1")
        self.assertFalse(any("key_clash" in t["flags"] for t in detail["transitions"]))
        self.assertEqual(detail["stats"]["keys_known"], 0)


if __name__ == "__main__":
    unittest.main()
