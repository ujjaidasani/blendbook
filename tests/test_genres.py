"""Blendbook has to work for every kind of DJ, not just the house and techno it was first used with."""

import random
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_security import rb, track  # noqa: E402  (points db.DB_PATH at a temp dir first)

import planner  # noqa: E402

GENRES = {  # label as rekordbox / Apple Music shows it: typical BPM range
    "Techno": (125, 140), "Hard Techno": (145, 160), "House": (120, 128), "Deep House": (118, 124), "Trance": (132, 142),
    "Drum & Bass": (170, 178), "Dubstep": (138, 145), "UK Garage": (130, 136), "Amapiano": (110, 115),
    "Hip-Hop/Rap": (75, 100), "Trap": (130, 160), "R&B/Soul": (68, 105), "Pop": (95, 128), "K-Pop": (100, 135),
    "Latin": (88, 100), "Salsa": (160, 200), "Afrobeats": (98, 112), "Dancehall": (92, 108), "Reggae": (65, 85),
    "Disco": (110, 124), "Funk": (95, 118), "Rock": (100, 145), "Country": (80, 120), "Bollywood": (90, 130),
    "Jazz": (70, 170), "Classical": (60, 140),
}


def library(tracks):
    lib = rb.RekordboxLibrary(Path("/nonexistent"))
    lib.tracks = {t["id"]: t for t in tracks}
    return lib


def t(tid, bpm, genre):
    return track(str(tid), bpm, title=f"{genre} {tid}", artist=f"Artist {tid}", genre=genre)


def set_of(lib, seconds_each):
    at, plays = datetime(2026, 10, 1, 21), []
    for tid in lib.tracks:
        plays.append({"track_id": tid, "played_at": at})
        at += timedelta(seconds=seconds_each)
    lib.sessions = [{"id": "1", "name": "HISTORY", "started_at": plays[0]["played_at"], "plays": plays}]
    return lib.session_detail("1")


class EveryGenreTests(unittest.TestCase):
    def test_suggests_and_plans_a_full_set(self):
        for genre, (lo, hi) in GENRES.items():
            with self.subTest(genre):
                r = random.Random(genre)
                lib = library([t(i, round(r.uniform(lo, hi), 1), genre) for i in range(60)])
                self.assertGreaterEqual(len(lib.suggest("0", limit=10)), 5)
                for shape in planner.SHAPES:
                    plan = planner.plan_set(lib, "0", 12, shape)
                    self.assertEqual(len(plan["tracks"]), 12, shape)


class SoundTests(unittest.TestCase):
    def test_labels_for_one_sound_are_related(self):
        for a, b in (("Hip-Hop/Rap", "Rap"), ("Hip-Hop/Rap", "Trap"), ("Drum & Bass", "DnB"), ("Drum & Bass", "Jungle"),
                     ("Deep House", "Tech House"), ("House", "Dance"), ("Minimal, Deep Tech", "Techno"),
                     ("Afro-fusion", "Afrobeats"), ("R&B/Soul", "Funk"), ("Reggae", "Dancehall")):
            self.assertTrue(rb.same_sound(a, b), (a, b))

    def test_different_sounds_are_not(self):
        for a, b in (("Hip-Hop/Rap", "Rock"), ("Hip-Hop/Rap", "Country"), ("Dubstep", "Reggae"), ("Salsa", "Techno"),
                     ("Pop", "Jazz"), (None, "Rock"), ("Rock", None)):
            self.assertFalse(rb.same_sound(a, b), (a, b))

    def test_a_related_genre_beats_an_unrelated_one(self):
        lib = library([t(0, 90, "Hip-Hop/Rap"), t(1, 90, "Country"), t(2, 90, "Rap"), t(3, 90, "Rock")])
        self.assertEqual(lib.suggest("0")[0]["genre"], "Rap")


class TempoTests(unittest.TestCase):
    def open_format(self):
        return library([t(i, b, g) for i, (b, g) in enumerate(
            [(75, "Hip-Hop/Rap"), (100, "Latin"), (128, "House"), (105, "Pop"), (120, "Disco"), (96, "Afrobeats")])])

    def test_wide_tempo_gaps_still_get_suggestions(self):
        picks = self.open_format().suggest("0")
        self.assertEqual(len(picks), 5)
        self.assertTrue(any("cut or echo out" in r for r in picks[0]["reasons"]), picks[0])

    def test_wide_tempo_gaps_still_get_a_full_plan(self):
        self.assertEqual(len(planner.plan_set(self.open_format(), "0", 5, "build")["tracks"]), 5)

    def test_close_matches_keep_far_tracks_out(self):
        lib = library([t(0, 124, "House"), t(1, 124, "House"), t(2, 125, "House"), t(3, 123, "House"), t(4, 90, "House")])
        self.assertNotIn("4", [s["id"] for s in lib.suggest("0")])

    def test_half_time(self):
        reason = library([t(0, 87, "Hip-Hop/Rap"), t(1, 174, "Drum & Bass")]).suggest("0")[0]["reasons"][0]
        self.assertIn("double-time", reason)

    def test_track_without_a_bpm_still_gets_suggestions(self):
        picks = library([t(0, None, "Classical"), t(1, 90, "Classical"), t(2, 90, "Rock")]).suggest("0")
        self.assertEqual(picks[0]["id"], "1")
        self.assertIn("by ear", picks[0]["reasons"][0])


class FeedbackTests(unittest.TestCase):
    def kinds(self, detail):
        return {o["title"]: o["kind"] for o in detail["observations"]}

    def test_open_format_quick_mix_is_not_told_off(self):
        sounds = [("Hip-Hop/Rap", 95), ("Pop", 120), ("Latin", 96), ("House", 126), ("Afrobeats", 104)]
        detail = set_of(library([t(i, sounds[i % 5][1], sounds[i % 5][0]) for i in range(20)]), 70)
        self.assertNotIn("warn", self.kinds(detail).values(), detail["observations"])
        self.assertTrue(any(k.startswith("Open-format") for k in self.kinds(detail)))
        self.assertFalse(any("channel-surfing" in o["detail"] for o in detail["observations"]))

    def test_a_single_sound_set_still_gets_real_warnings(self):
        tracks = [t(i, 124, "House") for i in range(8)]
        tracks[4] = t(4, 140, "House")
        detail = set_of(library(tracks), 200)
        self.assertEqual(self.kinds(detail).get("2 big tempo jumps"), "warn")

    def test_a_couple_of_short_plays_are_still_flagged(self):
        lib = library([t(i, 124, "House") for i in range(8)])
        at, plays = datetime(2026, 10, 1, 21), []
        for i, tid in enumerate(lib.tracks):
            plays.append({"track_id": tid, "played_at": at})
            at += timedelta(seconds=40 if i in (2, 3) else 200)
        lib.sessions = [{"id": "1", "name": "H", "started_at": plays[0]["played_at"], "plays": plays}]
        self.assertEqual(self.kinds(lib.session_detail("1")).get("2 tracks got less than 75s"), "warn")


if __name__ == "__main__":
    unittest.main()
