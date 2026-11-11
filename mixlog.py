"""
Ties the controller's mix log to rekordbox's history.

rekordbox logs a track to History some time after it starts (LOG_LAG_SEC, to be confirmed on real hardware).
A blend starts when the incoming deck becomes audible, which is roughly when that track starts. So the
blend that brought track n+1 in should start about LOG_LAG_SEC before rekordbox logged track n+1.
"""

from __future__ import annotations

import os
import statistics
from datetime import datetime, timedelta

LOG_LAG_SEC = float(os.environ.get("BLENDBOOK_LOG_LAG_SEC", "60"))
# How far a blend may sit from the expected moment and still be matched.
EARLY_SEC = 60      # blend started up to this long *after* the track was logged
LATE_SEC = 240      # or up to this long before it
HIGH_CONFIDENCE_SEC = 45
WINDOW_BEFORE = timedelta(minutes=10)
WINDOW_AFTER = timedelta(minutes=15)
BASS_OVERLAP_WARN_SEC = 5
HARD_CUT_SEC = 4


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def session_window(detail: dict) -> tuple[int, int]:
    start = datetime.fromisoformat(detail["started_at"])
    last = datetime.fromisoformat(detail["tracks"][-1]["played_at"])
    return _ms(start - WINDOW_BEFORE), _ms(last + WINDOW_AFTER)


def match(rb_transitions: list[dict], blends: list[dict]) -> dict[int, tuple[int, float]]:
    """Map controller blend index -> (rekordbox transition index, cost in seconds). One-to-one, cheapest first.

    rb_transitions need "logged_ms" (when rekordbox logged the incoming track); blends need "started_ts".
    """
    pairs = []
    for bi, b in enumerate(blends):
        for ti, t in enumerate(rb_transitions):
            delta = (t["logged_ms"] - b["started_ts"]) / 1000  # positive: logged after the blend began
            if -EARLY_SEC <= delta <= LATE_SEC:
                pairs.append((abs(delta - LOG_LAG_SEC), bi, ti))
    pairs.sort()
    used_b, used_t, out = set(), set(), {}
    for cost, bi, ti in pairs:
        if bi in used_b or ti in used_t:
            continue
        used_b.add(bi)
        used_t.add(ti)
        out[bi] = (ti, cost)
    return out


def enrich(detail: dict, store) -> dict:
    """Add what the controller measured to a rekordbox session detail (in place) and return it."""
    lo, hi = session_window(detail)
    blends = store.mix_transitions_between(lo, hi)
    clashes = store.mix_events_between(lo, hi, ("bass_clash",))
    if not blends and not clashes:
        detail["controller"] = {"seen": False}
        for t in detail["transitions"]:
            t["controller"] = None
        return detail

    start = datetime.fromisoformat(detail["started_at"])
    rb = [{"logged_ms": _ms(start + timedelta(seconds=t["at_sec"]))} for t in detail["transitions"]]
    matches = match(rb, blends)
    by_transition = {ti: (bi, cost) for bi, (ti, cost) in matches.items()}

    for ti, t in enumerate(detail["transitions"]):
        hit = by_transition.get(ti)
        if not hit:
            t["controller"] = None
            continue
        b = blends[hit[0]]
        t["controller"] = {
            "duration_sec": b["duration_sec"],
            "bass_overlap_sec": b["bass_overlap_sec"],
            "eq_moves": b["eq_moves"],
            "verdict": b["verdict"],
            "started_at": datetime.fromtimestamp(b["started_ts"] / 1000).isoformat(timespec="seconds"),
            "confidence": "high" if hit[1] <= HIGH_CONFIDENCE_SEC else "low",
        }

    unmatched = [
        {"started_at": datetime.fromtimestamp(b["started_ts"] / 1000).isoformat(timespec="seconds"),
         "duration_sec": b["duration_sec"], "bass_overlap_sec": b["bass_overlap_sec"],
         "eq_moves": b["eq_moves"], "verdict": b["verdict"]}
        for bi, b in enumerate(blends) if bi not in matches
    ]
    durations = [b["duration_sec"] for b in blends if b["duration_sec"] and b["duration_sec"] >= HARD_CUT_SEC]
    summary = {
        "seen": True,
        "blends": len(blends),
        "matched": len(matches),
        "unmatched": unmatched,
        "bass_clash_sec": round(sum(b["bass_overlap_sec"] or 0 for b in blends)),
        "bass_clash_events": len(clashes),
        "heavy_bass_blends": sum(1 for b in blends if (b["bass_overlap_sec"] or 0) >= BASS_OVERLAP_WARN_SEC),
        "no_eq_blends": sum(1 for b in blends if not b["eq_moves"] and (b["duration_sec"] or 0) >= HARD_CUT_SEC),
        "hard_cuts": sum(1 for b in blends if (b["duration_sec"] or 0) < HARD_CUT_SEC),
        "median_blend_sec": round(statistics.median(durations)) if durations else None,
    }
    detail["controller"] = summary
    detail["observations"] = _observations(detail, summary) + detail.get("observations", [])
    return detail


def _observations(detail: dict, s: dict) -> list[dict]:
    obs = []
    n = s["blends"]
    if s["heavy_bass_blends"]:
        worst = max((t for t in detail["transitions"] if t.get("controller")),
                    key=lambda t: t["controller"]["bass_overlap_sec"] or 0, default=None)
        detail_txt = (f"{s['heavy_bass_blends']} of {n} blends had both LOW EQs up for {BASS_OVERLAP_WARN_SEC}s or more "
                      f"({s['bass_clash_sec']}s in total). Two kick drums and basslines at once sounds muddy. "
                      "Bring the new track in with its LOW cut, then swap the LOWs on a phrase.")
        if worst and (worst["controller"]["bass_overlap_sec"] or 0) >= BASS_OVERLAP_WARN_SEC:
            detail_txt += f" Worst: into {worst['to']['title']} ({round(worst['controller']['bass_overlap_sec'])}s)."
        obs.append({"kind": "warn", "title": "Basses clashed in your blends", "detail": detail_txt,
                    "at_n": worst["n"] + 1 if worst else None, "source": "controller"})
    if s["no_eq_blends"] >= 2:
        obs.append({"kind": "info", "title": f"{s['no_eq_blends']} blends with no EQ moves",
                    "detail": "Longer blends without touching the EQs usually stack two full mixes on top of each other. "
                              "Even one LOW swap per blend makes them cleaner.",
                    "at_n": None, "source": "controller"})
    if s["median_blend_sec"] and n >= 3:
        kind = "good" if 16 <= s["median_blend_sec"] <= 90 else "info"
        obs.append({"kind": kind, "title": f"Typical blend: {s['median_blend_sec']}s",
                    "detail": f"Measured from your FLX4 across {n} blends"
                              + (f", plus {s['hard_cuts']} hard cut{'s' if s['hard_cuts'] != 1 else ''}." if s["hard_cuts"] else "."),
                    "at_n": None, "source": "controller"})
    if not s["heavy_bass_blends"] and n >= 3:
        obs.append({"kind": "good", "title": "Clean low end",
                    "detail": f"No blend had both basses up for {BASS_OVERLAP_WARN_SEC}s or more.",
                    "at_n": None, "source": "controller"})
    return obs


def has_controller_data(store, summary: dict) -> bool:
    """Cheap check for the session list."""
    start = datetime.fromisoformat(summary["started_at"])
    end = start + timedelta(seconds=summary["stats"]["duration_sec"] or 0)
    return bool(store.mix_transitions_between(_ms(start - WINDOW_BEFORE), _ms(end + WINDOW_AFTER)))
