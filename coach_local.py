"""
The built-in coach: answers questions from the data alone when no Anthropic API key is set.

It can't hold a conversation, but it does read the question: each question is matched to a topic (what's next,
lift or drop the energy, key, tempo, time left, how the set's going, the last blend, getting out of this track,
the saved plan...) and answered from that part of the data. Anything it can't place gets the closest useful answer
plus a list of what it can answer.
"""

from __future__ import annotations

import re

from mixlog import LOG_LAG_SEC

KEY_HINT = "Add an Anthropic API key in Settings for free-form answers."


def _has(q: str, *patterns: str) -> bool:
    return any(re.search(p, q) for p in patterns)


def live_direction(question: str) -> str:
    """Which way the question wants the energy to go: 'up', 'down' or 'hold'. Used to rank the suggestions."""
    q = question.lower()
    if _has(q, r"\b(bring|take|calm|cool|wind|slow|tone|chill)\w*\b.*\b(down|it|off|things)\b", r"\blower\b",
            r"\bdrop (the )?(energy|tempo|bpm)\b", r"\bless energy\b", r"\bslower\b", r"\bchill\w*\b", r"\bcloser\b",
            r"\bcool[- ]?down\b", r"\bwind[- ]?down\b"):
        return "down"
    if _has(q, r"\b(lift|build|peak|raise|push|hype|bang\w*)\b", r"\bharder\b", r"\bfaster\b", r"\bmore energy\b",
            r"\b(energy|tempo|bpm|it|things) up\b", r"\bturn it up\b", r"\bheat\b"):
        return "up"
    return "hold"


def _mmss(sec: float) -> str:
    sec = max(0, int(sec))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _track(t: dict) -> str:
    bits = [f"{t['bpm']:g} BPM" if t.get("bpm") else None, f"≈{t['key']}" if t.get("key_source") == "online" and t.get("key")
            else t.get("key")]
    extra = ", ".join(b for b in bits if b)
    return f"**{t['title']}** by {t['artist']}" + (f" ({extra})" if extra else "")


def _pick(s: dict) -> str:
    return f"{_track(s)}: {', '.join(s['reasons'])}" if s.get("reasons") else _track(s)


def _picks(sugg: list[dict], n: int = 3) -> str:
    return "\n".join(f"{i}. {_pick(s)}" for i, s in enumerate(sugg[:n], 1))


def _exit_advice(cur: dict, s: dict) -> str:
    """How to get from the playing track into suggestion s, from the tempo and key gap alone."""
    pct = abs(s.get("bpm_delta_pct") or 0)
    rel = s.get("key_relation")
    if pct > 8:
        tip = "The tempos are too far apart to ride: cut on the beat, or echo out and bring the new track in clean."
    elif pct > 5:
        tip = (f"The tempos are {pct:.0f}% apart, too far to ride smoothly: loop the last 4 beats of {cur['title']}, "
               "bring the new track in on the filter, then drop the loop on a phrase. An echo out also hides the jump.")
    elif pct > 2:
        tip = f"Nudge the tempo {pct:.0f}% over a phrase or two before the swap, then a normal bass swap works."
    else:
        tip = "The tempos already match: a 16- or 32-beat blend with a clean bass swap on the phrase."
    if rel == "clash":
        tip += " The keys clash, so keep the overlap short and swap the mids early."
    elif rel in ("same", "relative", "adjacent"):
        tip += " The keys are compatible, so a longer overlap will sound good."
    return tip


def live_answer(question: str, ctx: dict) -> str:
    q = question.lower()
    rb = ctx.get("rekordbox") or {}
    if not rb.get("now_playing"):
        return rb.get("reason") or "rekordbox hasn't logged any tracks yet, so there's nothing to go on."
    cur, sess, sugg = rb["now_playing"], rb.get("session") or {}, rb.get("suggestions") or []
    plan = ctx.get("plan")
    ctl = ctx.get("controller") or {}
    stale = "" if rb.get("active") else (
        f"(rekordbox hasn't logged a track for a while, so this is from the last one: {cur['title']}.)\n\n")

    # the energy direction already re-ranked the suggestions (see live_direction)
    direction = live_direction(question)
    if direction != "hold" and sugg:
        verb = "lift it" if direction == "up" else "bring it down"
        return stale + f"To {verb} from {_track(cur)}:\n{_picks(sugg)}"

    if _has(q, r"\bplan\b"):
        if plan and plan.get("next"):
            return stale + f"Next in your plan “{plan['name']}”: {_track(plan['next'])}."
        return stale + "The playing track isn't in a saved plan. Build one on the Plan page and it shows up here."

    if _has(q, r"get out|out of (this|it)|mix (out|into)|transition (out|into)|how (do|should) i (blend|mix|go)"):
        if not sugg:
            return stale + "Nothing else in your library has a BPM to match against; an echo out or a hard cut on a phrase is the cleanest exit."
        return stale + f"Into {_track(sugg[0])}. {_exit_advice(cur, sugg[0])}"

    if _has(q, r"\bkeys?\b", r"harmonic", r"camelot", r"\bclash"):
        if not cur.get("key"):
            return stale + (f"{cur['title']} has no key yet. Run Analyze Track on it in rekordbox, and harmonic picks "
                            "show up here.")
        if cur.get("key_source") != "rekordbox":
            return stale + (f"This track is about **≈{cur['key']}**, but that's an online estimate, so Blendbook won't "
                            "call anything harmonic with it. Run Analyze Track on it in rekordbox for a real key.")
        good = [s for s in sugg if s.get("key_relation") in ("same", "relative", "adjacent", "boost")]
        if not good:
            return stale + f"You're in **{cur['key']}**. None of the top tempo matches has a compatible key; keep the next blend short."
        return stale + f"You're in **{cur['key']}**. Harmonic picks:\n{_picks(good)}"

    if _has(q, r"how long|time left|remaining|how much (time|longer|left)|minutes? left|when (does|will) (it|this) end"):
        elapsed = cur.get("since_logged_sec", 0) + LOG_LAG_SEC
        lines = []
        if cur.get("length"):
            left = cur["length"] - elapsed
            lines.append(f"About **{_mmss(left)}** left of {cur['title']}" if left > 0 else
                         f"{cur['title']} has probably ended (it's {_mmss(cur['length'])} long)")
        if sess.get("elapsed_sec") and rb.get("active"):
            lines.append(f"you're {_mmss(sess['elapsed_sec'])} into the set, {sess.get('track_count', 0)} tracks in")
        if sess.get("track_pace_sec"):
            lines.append(f"a track lasts about {_mmss(sess['track_pace_sec'])} at this pace")
        return stale + ("; ".join(lines) + ". Set “Set ends at” on this page for a countdown.") if lines else \
            stale + "rekordbox doesn't have this track's length."

    if _has(q, r"\bbpm\b", r"\btempo\b", r"\bspeed\b", r"how fast"):
        path = [b for b in rb.get("bpm_path") or [] if b]
        line = f"You're at **{cur['bpm']:g} BPM**" if cur.get("bpm") else "This track has no BPM in rekordbox"
        if len(path) > 1:
            line += f", from {path[0]:g} at the start (range {min(path):g}–{max(path):g})"
        tight = [s for s in sugg if abs(s.get("bpm_delta_pct") or 99) <= 2]
        return stale + line + "." + (f" Easiest tempo matches next:\n{_picks(tight)}" if tight else "")

    if _has(q, r"last (blend|transition|mix)", r"how was (that|the|my) (blend|transition|mix)", r"\bthat blend\b"):
        out = []
        ct = (ctl.get("recentTransitions") or [])[-1:] or []
        if ct:
            c = ct[0]
            out.append(f"The controller saw a {round(c.get('durationSec') or 0)} s blend with both basses up for "
                       f"{round(c.get('bassOverlapSec') or 0)} s" + (f" ({c['verdict']})" if c.get("verdict") else "") + ".")
        lt = rb.get("last_transition")
        if lt:
            flags = ", ".join(f.replace("_", " ") for f in lt.get("flags") or []) or "no problems flagged"
            out.append(f"{lt['from']['title']} → {lt['to']['title']}: {lt['bpm_delta_pct']:+.1f}% tempo, {flags}.")
        return stale + (" ".join(out) if out else "No blend logged in this set yet.")

    if _has(q, r"how('?s| is| am| are)\b.*\b(set|going|doing|i)\b", r"so far", r"\bstatus\b", r"\bsummar"):
        lines = [f"**{sess.get('track_count', 0)} tracks**" + (f" in {_mmss(sess['elapsed_sec'])}" if rb.get("active") else "")]
        path = [b for b in rb.get("bpm_path") or [] if b]
        if len(path) > 1:
            lines.append(f"tempo {path[0]:g} → {path[-1]:g} BPM")
        lt = rb.get("last_transition")
        if lt and lt.get("flags"):
            lines.append(f"last change flagged: {', '.join(f.replace('_', ' ') for f in lt['flags'])}")
        if ctl.get("bassClash"):
            lines.append("**both basses are up right now**")
        clashes = [t for t in ctl.get("recentTransitions") or [] if (t.get("bassOverlapSec") or 0) >= 5]
        if clashes:
            lines.append(f"{len(clashes)} of your last {len(ctl['recentTransitions'])} blends had the basses up 5 s or more")
        return stale + "; ".join(lines) + "."

    if not sugg:
        return stale + "No other track in your library has a BPM to match against."
    lead = ""
    if plan and plan.get("next"):
        lead = f"Your plan says {_track(plan['next'])}. Otherwise:\n"
    answer = stale + lead + f"Best fits after {cur['title']}:\n{_picks(sugg)}"
    if not _has(q, r"\bnext\b", r"\bplay\b", r"suggest", r"\bwhat\b.*\bshould\b", r"recommend"):
        answer += ("\n\nI can answer: what to play next, lift it / bring it down, keys, tempo, time left, "
                   "how the set's going, the last blend, how to get out of this track, and your plan. " + KEY_HINT)
    return answer


def session_answer(question: str, ctx: dict) -> str:
    q = question.lower()
    obs, stats, trans = ctx.get("observations") or [], ctx.get("stats") or {}, ctx.get("transitions") or []
    if not stats:
        return "That set couldn't be found."
    if _has(q, r"\b(worst|roughest|bad|messy|clash|problem|wrong|improve|better|fix)\w*"):
        warn = [o for o in obs if o.get("kind") == "warn"]
        if warn:
            return "To work on:\n" + "\n".join(f"- **{o['title']}.** {o['detail']}" for o in warn[:4])
        return "Nothing stood out as a problem in this set's history."
    if _has(q, r"\b(best|good|well|smooth|strong)\w*"):
        good = [o for o in obs if o.get("kind") == "good"]
        clean = [t for t in trans if not t.get("flags")]
        lines = [f"- **{o['title']}.** {o['detail']}" for o in good[:3]]
        if clean:
            lines.append(f"- {len(clean)} of {len(trans)} changes had no tempo or key problems.")
        return "What went well:\n" + "\n".join(lines) if lines else "No clear highlights in the history for this set."
    if _has(q, r"\bbpm\b", r"\btempo\b"):
        return (f"Tempo ran {stats.get('bpm_start', 0):g} → {stats.get('bpm_end', 0):g} BPM (range "
                f"{stats.get('bpm_min', 0):g}–{stats.get('bpm_max', 0):g}), {stats.get('smooth_tempo_pct', 0)}% of "
                f"changes within 3%, {stats.get('bpm_jumps', 0)} jump(s) over 6%.")
    if _has(q, r"genre"):
        g = ", ".join(f"{name} ({n})" for name, n in (stats.get("genres") or [])[:5])
        return f"Genres: {g}. {stats.get('genre_switches', 0)} genre switches across {stats.get('track_count', 0)} tracks."
    if obs:
        return "From this set's history:\n" + "\n".join(f"- **{o['title']}.** {o['detail']}" for o in obs[:4]) + \
            "\n\nAsk about the best or worst blends, tempo or genres. " + KEY_HINT
    return f"{stats.get('track_count', 0)} tracks, nothing unusual in the history. " + KEY_HINT


def insights_answer(question: str, ctx: dict) -> str:
    q = question.lower()
    totals = ctx.get("totals")
    if not totals:
        return "There's no rekordbox history to learn from yet."
    if _has(q, r"artist"):
        return "Most-played artists: " + ", ".join(f"{a['artist']} ({a['plays']})" for a in ctx.get("top_artists", [])[:6]) + "."
    if _has(q, r"genre"):
        return "Your genres: " + ", ".join(f"{g['genre']} ({g['plays']})" for g in ctx.get("top_genres", [])[:6]) + "."
    if _has(q, r"open(er|ing)|start (with|my)|first track"):
        o = ctx.get("go_to_openers") or []
        return ("Go-to openers:\n" + "\n".join(f"- {_track(t)}, {t['times']}×" for t in o)) if o else \
            "You haven't opened with the same track twice yet."
    if _has(q, r"relying|leaning|overplay|same tracks|too much|too often"):
        t = ctx.get("leaning_on") or []
        return ("You're leaning on:\n" + "\n".join(f"- {_track(x)}: {x['sets']} of your last {x['of']} sets" for x in t[:6])) \
            if t else "No track has been in most of your recent sets, so you're keeping them fresh."
    if _has(q, r"forgot|dropped|stopped|dormant|haven'?t played|used to"):
        d = ctx.get("dormant_favourites") or []
        return ("Favourites you've stopped playing:\n" + "\n".join(f"- {_track(t)} ({t['sessions']} sets)" for t in d[:6])) \
            if d else "No favourites have dropped out of your last five sets."
    if _has(q, r"never|unplayed|new|fresh|undiscovered"):
        n = ctx.get("never_played") or []
        return f"**{ctx.get('never_played_count', 0)} tracks** have never made it into a set. Oldest additions first:\n" + \
            "\n".join(f"- {_track(t)}" for t in n[:6])
    if _has(q, r"transition|blend|pair|signature|combo|after"):
        p = ctx.get("signature_transitions") or []
        return ("Your signature blends:\n" + "\n".join(f"- {x['from']['title']} → {x['to']['title']} ({x['count']}×)" for x in p[:6])) \
            if p else "No blend repeats across sets yet."
    if _has(q, r"most|top|favou?rite|played"):
        t = ctx.get("top_tracks") or []
        return "Most-played tracks:\n" + "\n".join(f"- {_track(x)}: {x['plays']} plays in {x['sessions']} sets" for x in t[:6])
    lines = [f"- **{totals['sessions']} sets**, typically {totals['median_set_minutes']} min; "
             f"{totals['median_smooth_tempo_pct']}% of your tempo changes stay within 3%."]
    if ctx.get("signature_transitions"):
        p = ctx["signature_transitions"][0]
        lines.append(f"- **Go-to blend:** {p['from']['title']} → {p['to']['title']} ({p['count']}×).")
    if ctx.get("dormant_favourites"):
        lines.append(f"- **Favourites you've dropped:** {', '.join(t['title'] for t in ctx['dormant_favourites'][:3])}.")
    lines.append(f"- **{ctx.get('never_played_count', 0)} tracks** in your library have never made it into a set.")
    return ("Your habits so far:\n" + "\n".join(lines) + "\n\nAsk about top tracks, artists, genres, openers, "
            "signature blends, dropped favourites or unplayed tracks. " + KEY_HINT)


def answer(question: str, context: dict, scope: str) -> str:
    if scope == "live":
        return live_answer(question, context)
    if scope == "session":
        return session_answer(question, context)
    return insights_answer(question, context)
