"""The built-in coach (no API key) must answer the question asked, not the same thing every time."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import coach_local  # noqa: E402
import rb  # noqa: E402


def sugg(title, bpm, pct, key=None, rel=None):
    return {"id": title, "title": title, "artist": "A", "bpm": bpm, "key": key, "bpm_delta_pct": pct,
            "key_relation": rel, "reasons": [f"{bpm} BPM"]}


LIVE = {"rekordbox": {
    "active": True,
    "session": {"track_count": 9, "elapsed_sec": 2400, "track_pace_sec": 240, "played_ids": ["1"]},
    "now_playing": {"id": "1", "title": "Now", "artist": "A", "bpm": 124, "key": "8A", "key_source": "rekordbox", "length": 300,
                    "since_logged_sec": 120},
    "bpm_path": [120, 122, 124],
    "last_transition": {"from": {"title": "Before"}, "to": {"title": "Now"}, "bpm_delta_pct": 1.6, "flags": []},
    "suggestions": [sugg("Far", 131, 5.6, "3B", "clash"), sugg("Near", 124, 0, "9A", "adjacent")],
}, "controller": {"bassClash": False, "recentTransitions": [{"durationSec": 31, "bassOverlapSec": 7, "verdict": "rushed"}]}}


class CoachTests(unittest.TestCase):
    def test_different_questions_get_different_answers(self):
        qs = ["What should I play next?", "How's my set going?", "How do I get out of this track?", "what key am I in",
              "how much time is left", "how was that last blend", "what's the bpm", "next in my plan?"]
        answers = [coach_local.answer(q, LIVE, "live") for q in qs]
        self.assertEqual(len(set(answers)), len(qs), answers)

    def test_energy_direction(self):
        self.assertEqual(coach_local.live_direction("bring it down a bit"), "down")
        self.assertEqual(coach_local.live_direction("need a closer"), "down")
        self.assertEqual(coach_local.live_direction("lift the energy"), "up")
        self.assertEqual(coach_local.live_direction("time to peak"), "up")
        self.assertEqual(coach_local.live_direction("what should I play next"), "hold")

    def test_answers_use_the_data(self):
        self.assertIn("8A", coach_local.answer("what key", LIVE, "live"))
        self.assertIn("Near", coach_local.answer("what key", LIVE, "live"))
        self.assertNotIn("Far", coach_local.answer("what key", LIVE, "live"))  # a clash isn't a harmonic pick
        self.assertIn("2:00", coach_local.answer("how much time is left", LIVE, "live"))  # 300 - (120 + 60 lag)
        self.assertIn("7 s", coach_local.answer("how was that last blend", LIVE, "live"))
        self.assertIn("loop", coach_local.answer("how do i get out of this track", {**LIVE, "rekordbox": {
            **LIVE["rekordbox"], "suggestions": LIVE["rekordbox"]["suggestions"][:1]}}, "live"))

    def test_an_online_key_gets_no_harmonic_picks(self):
        rekordbox = LIVE["rekordbox"]
        ctx = {**LIVE, "rekordbox": {**rekordbox, "now_playing": {**rekordbox["now_playing"], "key_source": "online"}}}
        answer = coach_local.answer("what key", ctx, "live")
        self.assertIn("≈8A", answer)
        self.assertIn("Analyze Track", answer)
        self.assertNotIn("Harmonic picks", answer)

    def test_leaning_on_the_same_tracks(self):
        ctx = {"totals": {"sessions": 5}, "leaning_on": [{"title": "Crutch", "artist": "A", "sets": 4, "of": 5}]}
        self.assertIn("Crutch", coach_local.answer("Am I relying on the same tracks?", ctx, "insights"))
        self.assertIn("fresh", coach_local.answer("Am I relying on the same tracks?", {**ctx, "leaning_on": []}, "insights"))

    def test_unknown_question_says_what_it_can_do(self):
        self.assertIn("I can answer", coach_local.answer("tell me a joke", LIVE, "live"))
        self.assertNotIn("I can answer", coach_local.answer("what should I play next", LIVE, "live"))

    def test_long_durations_show_hours(self):
        self.assertEqual(coach_local._mmss(3725), "1:02:05")
        self.assertEqual(coach_local._mmss(65), "1:05")

    def test_same_song_twice_counts_once(self):
        self.assertEqual(rb._norm_title("Endlied (Original Mix)"), rb._norm_title("Endlied"))
        self.assertEqual(rb._norm_title("Strobe - Radio Edit"), "strobe")
        self.assertNotEqual(rb._norm_title("Opus (Four Tet Remix)"), "opus")  # a remix is a different track


if __name__ == "__main__":
    unittest.main()
