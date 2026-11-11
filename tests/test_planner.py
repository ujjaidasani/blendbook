"""Set planner: the energy curve next to the tempo curve. Libraries are built in memory; importing test_security
first points the database at its throwaway copy, never .cache/blendbook.db."""

import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_security import track  # noqa: E402  (points db.DB_PATH at a temp dir first)

import planner  # noqa: E402
import rb  # noqa: E402

ENERGY_REASONS = {"lifts the energy", "eases the energy down", "keeps it calm", "keeps the energy up", "holds the energy"}


def library(tracks, sessions=()):
    lib = rb.RekordboxLibrary(Path("/nonexistent"))
    lib.tracks = {t["id"]: {"energy": None, **t, "rb_key": None, "stream_id": None} for t in tracks}
    lib.sessions = [{"id": str(n), "name": f"Set {n}", "started_at": None,
                     "plays": [{"track_id": i, "played_at": None} for i in ids]} for n, ids in enumerate(sessions)]
    return lib


def ladder(energies=(0.9, 0.75, 0.6, 0.45)):
    """120-130 BPM in half steps, one track per energy at every tempo, every artist different: nothing but
    energy separates tracks at the same tempo. High energy comes first, so an energy-blind tie-break picks it."""
    tracks = [{**track("open", 120, "Opener", "Opener Artist", "House"), "energy": 0.5}]
    for n in range(21):
        for e in energies:
            tid = f"t{n}-{e}"
            tracks.append({**track(tid, 120 + n / 2, artist=f"Artist {tid}", genre="House"), "energy": e})
    return tracks


def thirds(values):
    n = len(values) // 3
    return statistics.mean(values[:n]), statistics.mean(values[-n:])


class EnergyCurveTests(unittest.TestCase):
    def test_each_shape_has_its_energy_curve(self):
        c = {s: planner.energy_curve(11, s) for s in planner.SHAPES}
        self.assertEqual(set(planner.ENERGY_SHAPES), set(planner.SHAPES))
        self.assertTrue(c["warmup"][0] < c["warmup"][-1] <= 0.6, c["warmup"])          # low, rising gently
        self.assertEqual(c["build"], sorted(c["build"]))
        self.assertGreaterEqual(c["build"][-1] - c["build"][0], 0.3)                   # rising
        self.assertEqual(len(set(c["peak"])), 1)
        self.assertGreaterEqual(c["peak"][0], 0.8)                                     # high and flat
        self.assertGreater(c["journey"][5], max(c["journey"][0], c["journey"][-1]))   # up, then down
        self.assertEqual(c["cooldown"], sorted(c["cooldown"], reverse=True))           # falling
        self.assertGreater(c["cooldown"][0] - c["cooldown"][-1], 0.2)

    def test_missing_energy_scores_nothing(self):
        self.assertEqual(planner.energy_fit(None, 0.8, 0.5), (0.0, None))
        self.assertEqual(planner.energy_fit(0.5, None, 0.5), (0.0, None))
        on_curve, why = planner.energy_fit(0.5, 0.52, 0.52)
        off_curve, _ = planner.energy_fit(0.5, 0.95, 0.55)
        self.assertGreater(on_curve, 0)
        self.assertLess(off_curve, 0)
        self.assertEqual(why, "keeps it calm")
        self.assertEqual(planner.energy_fit(0.5, 0.62, 0.7)[1], "lifts the energy")
        self.assertEqual(planner.energy_fit(0.8, 0.7, 0.6)[1], "eases the energy down")

    def test_energy_stays_modest(self):
        best = planner.energy_fit(0.5, 0.55, 0.55)[0]
        worst = planner.energy_fit(0.5, 0.95, 0.55)[0]
        self.assertLess(best - worst, 30)  # a "played after it" bonus alone is 30-40: energy must never outweigh one

    def test_extra_reason_keeps_a_key_clash_warning(self):
        self.assertEqual(planner._with_reason(["a"], "lifts the energy"), ["a", "lifts the energy"])
        self.assertEqual(planner._with_reason(["a", "b", "c"], "x"), ["a", "b", "x"])
        self.assertEqual(planner._with_reason(["a", "b", "key clash (1A→6B)"], "x"), ["a", "key clash (1A→6B)", "x"])


class PlanEnergyTests(unittest.TestCase):
    def test_build_plan_energy_rises_with_energy_data(self):
        lib = library(ladder())
        plan = planner.plan_set(lib, "open", 12, "build")
        energies = [t["energy"] for t in plan["tracks"]]
        first, last = thirds(energies)
        self.assertGreaterEqual(last - first, 0.2, energies)
        self.assertGreaterEqual(energies[-1], 0.75, energies)
        self.assertEqual(plan["energy_curve"], planner.energy_curve(12, "build"))
        self.assertEqual([t["target_energy"] for t in plan["tracks"]], plan["energy_curve"])
        named = [r for t in plan["tracks"] for r in t["reasons"] if r in ENERGY_REASONS]
        self.assertIn("lifts the energy", named)
        self.assertTrue(all(len(t["reasons"]) <= 3 for t in plan["tracks"]))

        # the same library, energy-blind: the tie-break takes the loudest track every time, so nothing rises
        blind = planner.plan_set(library([{**t, "energy": None} for t in ladder()]), "open", 12, "build")
        blind_energies = [lib.track(t["id"])["energy"] for t in blind["tracks"]]
        first, last = thirds(blind_energies)
        self.assertLess(last - first, 0.1, blind_energies)

    def test_cooldown_plan_energy_falls(self):
        lib = library(ladder())
        lib.tracks["open"].update(bpm=130, energy=0.75)  # the ladder covers a cool-down's tempo from 130
        energies = [t["energy"] for t in planner.plan_set(lib, "open", 12, "cooldown")["tracks"]]
        first, last = thirds(energies)
        self.assertLessEqual(last - first, -0.15, energies)

    def test_tracks_without_energy_are_neither_helped_nor_hurt(self):
        # same tempo, genre and history: only energy differs. Unknown energy sits between a good and a bad fit.
        tracks = [{**track("open", 124, "Opener", "O", "House"), "energy": 0.45},
                  {**track("good", 124, "Good", "G", "House"), "energy": 0.46},
                  {**track("unknown", 124, "Unknown", "U", "House")},
                  {**track("bad", 124, "Bad", "B", "House"), "energy": 0.95}]
        plan = planner.plan_set(library(tracks), "open", 4, "warmup")
        self.assertEqual([t["id"] for t in plan["tracks"]], ["open", "good", "unknown", "bad"])

    def test_plans_without_energy_data_are_unchanged(self):
        tracks = [track(str(n), 118 + (n * 7) % 14, artist=f"A{n % 9}", genre=("House", "Techno")[n % 2])
                  for n in range(1, 60)]
        for t, k in zip(tracks, ["8A", "9A", None, "8B", "3A", None, "10A"] * 9):
            t["key"] = k
        sessions = [["1", "8", "15", "22", "29"], ["8", "15", "36", "43"], ["2", "9", "16", "23"], ["1", "8", "16"]]
        orig = planner.energy_fit
        for shape in planner.SHAPES:
            for fresh in planner.FRESH_BONUS:
                plan = planner.plan_set(library(tracks, sessions), "1", 10, shape, ["30"], fresh)
                planner.energy_fit = lambda *a: (0.0, None)  # the planner before energy counted
                try:
                    before = planner.plan_set(library(tracks, sessions), "1", 10, shape, ["30"], fresh)
                finally:
                    planner.energy_fit = orig
                self.assertEqual([(t["id"], t["reasons"]) for t in plan["tracks"]],
                                 [(t["id"], t["reasons"]) for t in before["tracks"]], (shape, fresh))
                self.assertFalse(any(r in ENERGY_REASONS for t in plan["tracks"] for r in t["reasons"]))

    def test_your_past_transitions_still_win_over_energy(self):
        tracks = [{**track("open", 124, "Opener", "O", "House"), "energy": 0.45},
                  {**track("habit", 124, "Habit", "H", "House"), "energy": 0.95},   # far off the warm-up curve
                  {**track("fits", 124, "Fits", "F", "House"), "energy": 0.46}]    # right on it, never played
        lib = library(tracks, [["open", "habit"]] * 3)
        plan = planner.plan_set(lib, "open", 3, "warmup")
        self.assertEqual(plan["tracks"][1]["id"], "habit")
        self.assertFalse(ENERGY_REASONS & set(plan["tracks"][1]["reasons"]), plan["tracks"][1]["reasons"])


if __name__ == "__main__":
    unittest.main()
