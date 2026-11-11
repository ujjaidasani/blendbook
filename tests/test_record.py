"""Reading recordings: a long silence is a pause, and short ones are dead air."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audio  # noqa: E402


class PauseTests(unittest.TestCase):
    def feat(self, silences):
        return {"loudness_db": [-25.0] * 300, "silences": silences, "beat_strength": []}

    def test_short_silence_is_dead_air(self):
        a = audio.transition_audio(self.feat([[100, 104]]), 100, 30)
        self.assertEqual((a["silence_sec"], a["paused_sec"]), (4, 0))

    def test_long_silence_is_a_pause(self):
        # decks stopped for two minutes while setting up the next attempt
        a = audio.transition_audio(self.feat([[95, 228]]), 100, 30)
        self.assertEqual((a["silence_sec"], a["paused_sec"]), (0, 133))
        self.assertEqual(a["beat_drift"], "unclear")


if __name__ == "__main__":
    unittest.main()
