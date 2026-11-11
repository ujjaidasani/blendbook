"""
Habit goals: one metric per set, tracked across sets against a target the DJ picks.
Every metric is computed from data Blendbook already has (rekordbox history, plus the FLX4 mix log).
"""

from __future__ import annotations

from datetime import datetime, timedelta

# direction: "at_most" means lower is better.
METRICS = {
    "gaps": {"label": "Gaps between tracks", "unit": "per set", "direction": "at_most", "default": 0,
             "help": "The next track started after the previous one had ended."},
    "bpm_jumps": {"label": "Tempo jumps over 6%", "unit": "per set", "direction": "at_most", "default": 1,
                  "help": "Tempo changes too big to blend smoothly."},
    "played_out_pct": {"label": "Tracks played to the end", "unit": "%", "direction": "at_most", "default": 30,
                       "help": "Share of tracks that ran to 95% or more before the next came in."},
    "quick_cuts": {"label": "Tracks under 75 s", "unit": "per set", "direction": "at_most", "default": 1,
                   "help": "Very short plays, usually rushed or abandoned tracks."},
    "genre_switches": {"label": "Genre switches", "unit": "per set", "direction": "at_most", "default": 5,
                       "help": "How often the genre changed between consecutive tracks."},
    "first_time_pct": {"label": "New tracks", "unit": "%", "direction": "at_least", "default": 25,
                       "help": "Share of tracks never played in an earlier set."},
    "bass_clash_sec": {"label": "Bass clash time (FLX4)", "unit": "s", "direction": "at_most", "default": 10,
                       "controller": True, "help": "Seconds with both LOW EQs up during blends."},
    "no_eq_blends": {"label": "Blends with no EQ moves (FLX4)", "unit": "per set", "direction": "at_most",
                     "default": 1, "controller": True, "help": "Blends longer than 4 s where no EQ was touched."},
}

MIN_TRACKS = 3  # sets shorter than this are warm-ups/tests and don't count


def metric_value(metric: str, detail: dict) -> float | None:
    st = detail["stats"]
    ctrl = detail.get("controller") or {}
    n = st["track_count"]
    if n < MIN_TRACKS:
        return None
    if METRICS[metric].get("controller") and not ctrl.get("seen"):
        return None
    return {
        "gaps": lambda: st["gaps"],
        "bpm_jumps": lambda: st["bpm_jumps"],
        "played_out_pct": lambda: round(100 * st["played_out"] / max(1, n - 1)),
        "quick_cuts": lambda: st["quick_cuts"],
        "genre_switches": lambda: st["genre_switches"],
        "first_time_pct": lambda: round(100 * st["first_time_tracks"] / n),
        "bass_clash_sec": lambda: ctrl["bass_clash_sec"],
        "no_eq_blends": lambda: ctrl["no_eq_blends"],
    }[metric]()


def meets(value, target, direction) -> bool | None:
    if value is None:
        return None
    return value <= target if direction == "at_most" else value >= target


def trend(values: list, direction: str) -> str | None:
    """Last 5 sets vs the 5 before, direction-aware."""
    vals = [v for v in values if v is not None]
    if len(vals) < 6:
        return None
    recent, before = vals[-5:], vals[-10:-5]
    a, b = sum(recent) / len(recent), sum(before) / len(before)
    if abs(a - b) < max(0.5, 0.1 * abs(b)):
        return "steady"
    better = a < b if direction == "at_most" else a > b
    return "improving" if better else "slipping"


def series(details: list[dict], metric: str) -> list[dict]:
    return [{"session_id": d["id"], "date": d["started_at"][:10], "value": metric_value(metric, d)} for d in details]


def goal_view(goal: dict, details: list[dict]) -> dict:
    m = METRICS[goal["metric"]]
    pts = series(details, goal["metric"])
    vals = [p["value"] for p in pts]
    recent = [v for v in vals if v is not None][-5:]
    met = [meets(v, goal["target"], goal["direction"]) for v in recent]
    return {
        **goal, "label": m["label"], "unit": m["unit"], "help": m["help"], "points": pts,
        "trend": trend(vals, goal["direction"]),
        "latest": recent[-1] if recent else None,
        "met_recent": sum(1 for x in met if x), "recent_count": len(recent),
    }


def fmt_value(metric: str, v) -> str:
    unit = METRICS[metric]["unit"]
    if v is None:
        return "—"
    return f"{v:g}%" if unit == "%" else f"{v:g}s" if unit == "s" else f"{v:g}"


def digest(details: list[dict], goals: list[dict], now: datetime | None = None) -> dict:
    """'Since last week': sets played, focus-goal progress, one standout, one thing to drill."""
    now = now or datetime.now()
    week_ago, two_weeks = now - timedelta(days=7), now - timedelta(days=14)
    when = lambda d: datetime.fromisoformat(d["started_at"])  # noqa: E731
    this_week = [d for d in details if when(d) >= week_ago and d["stats"]["track_count"] >= MIN_TRACKS]
    last_week = [d for d in details if two_weeks <= when(d) < week_ago and d["stats"]["track_count"] >= MIN_TRACKS]
    out = {
        "sets": len(this_week), "sets_prev": len(last_week),
        "tracks": sum(d["stats"]["track_count"] for d in this_week),
        "minutes": round(sum(d["stats"]["duration_sec"] for d in this_week) / 60),
        "last_set": details[-1]["started_at"] if details else None,
        "focus": None, "standout": None, "drill": None, "lines": [],
    }
    if not this_week:
        last = details[-1]["started_at"][:10] if details else None
        out["lines"].append(f"No sets in the last 7 days{f' (last one {last})' if last else ''}. "
                            "Even 20 minutes of practice keeps your transitions sharp.")
        return out

    out["lines"].append(f"{len(this_week)} set{'s' if len(this_week) > 1 else ''} this week "
                        f"({out['tracks']} tracks, {out['minutes']} min), vs {len(last_week)} the week before.")

    focus = next((g for g in goals if g["focus"]), goals[0] if goals else None)
    if focus:
        vals = [metric_value(focus["metric"], d) for d in this_week]
        vals = [v for v in vals if v is not None]
        if vals:
            hit = sum(1 for v in vals if meets(v, focus["target"], focus["direction"]))
            sign = "≤" if focus["direction"] == "at_most" else "≥"
            out["focus"] = {"metric": focus["metric"], "hit": hit, "of": len(vals)}
            out["lines"].append(f"Focus goal, {METRICS[focus['metric']]['label'].lower()} {sign} "
                                f"{fmt_value(focus['metric'], focus['target'])}: met in {hit} of {len(vals)} sets this week.")

    # standout: cleanest measured blend, else the smoothest set
    blends = [(t, d) for d in this_week for t in d["transitions"] if t.get("controller")]
    clean = [(t, d) for t, d in blends
             if (t["controller"]["duration_sec"] or 0) >= 16 and (t["controller"]["bass_overlap_sec"] or 0) < 1
             and (t["controller"]["eq_moves"] or 0) > 0]
    if clean:
        t, d = max(clean, key=lambda x: x[0]["controller"]["duration_sec"])
        out["standout"] = {"session_id": d["id"], "n": t["n"] + 1}
        out["lines"].append(f"Standout: {t['from']['title']} → {t['to']['title']}, a clean "
                            f"{round(t['controller']['duration_sec'])} s blend with the basses swapped.")
    else:
        best = max(this_week, key=lambda d: d["stats"]["smooth_tempo_pct"] or 0)
        if best["stats"]["smooth_tempo_pct"]:
            out["standout"] = {"session_id": best["id"], "n": None}
            out["lines"].append(f"Standout: your {best['started_at'][:10]} set kept "
                                f"{best['stats']['smooth_tempo_pct']}% of tempo changes within 3%.")

    # drill: the goal furthest from target, else the most common warning this week
    worst = None
    for g in goals:
        vals = [metric_value(g["metric"], d) for d in this_week]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        miss = (avg - g["target"]) if g["direction"] == "at_most" else (g["target"] - avg)
        scale = max(1.0, abs(g["target"]))
        if miss > 0 and (worst is None or miss / scale > worst[0]):
            worst = (miss / scale, g, avg)
    if worst:
        _, g, avg = worst
        out["drill"] = {"metric": g["metric"]}
        out["lines"].append(f"Drill: {METRICS[g['metric']]['label'].lower()} averaged "
                            f"{fmt_value(g['metric'], round(avg, 1))} against a goal of {fmt_value(g['metric'], g['target'])}. "
                            f"{DRILLS[g['metric']]}")
    else:
        warns = {}
        for d in this_week:
            for o in d["observations"]:
                if o["kind"] == "warn":
                    warns[o["title"].split(" ", 1)[-1]] = o
        if warns:
            o = next(iter(warns.values()))
            out["drill"] = {"observation": o["title"]}
            out["lines"].append(f"Drill: {o['title'].lower()} came up this week. {o['detail'].split('. ')[-1]}")
    return out


DRILLS = {
    "gaps": "Load the next track while the current one is still in its breakdown, and practise starting it on the outro's first phrase.",
    "bpm_jumps": "Practise bridging: pick two tracks 10 BPM apart and find one in your library that sits between them.",
    "played_out_pct": "Set a memory cue 32 bars before each outro and start your blend there.",
    "quick_cuts": "Preview the next track's drop on headphones before committing to it.",
    "genre_switches": "Plan blocks of 3–4 tracks per sound before switching.",
    "first_time_pct": "Pull 5 tracks from your 'never played' list into your next practice set.",
    "bass_clash_sec": "Drill the bass swap: incoming LOW at 9 o'clock, swap both LOW knobs on the phrase, 10 times in a row.",
    "no_eq_blends": "On every blend this week, make at least one LOW swap, even on short ones.",
}
