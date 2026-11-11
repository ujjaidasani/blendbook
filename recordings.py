"""
Connects recordings (files, or features captured live from the mic) to rekordbox sets.

Files in the recording folders are analysed in a background thread once they've stopped growing. A recording is
matched to a set at read time by overlapping time spans, so it works whichever was logged first.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import audio

STABLE_SEC = 10
LOUDNESS_STEP_WARN_DB = 3.0
MAX_CHUNK_SEC = 600          # one browser upload carries at most 10 min of per-second values
MAX_MIC_SEC = 12 * 3600      # longest live-listening session kept

_state = {"busy": None, "last_scan": None, "sizes": {}}
_lock = threading.Lock()
_align_cache: dict = {}


def status() -> dict:
    return {"analysing": _state["busy"], "last_scan": _state["last_scan"],
            "folders": [str(d) for d in audio.RECORDING_DIRS]}


def scan(store) -> int:
    """Analyse new, finished recording files. Returns how many were analysed."""
    if not _lock.acquire(blocking=False):
        return 0
    done = 0
    try:
        _state["last_scan"] = datetime.now().isoformat(timespec="seconds")
        for path in audio.candidate_files():
            key = str(path)
            size, mtime = path.stat().st_size, path.stat().st_mtime
            prev = _state["sizes"].get(key)
            _state["sizes"][key] = size
            # still being written (rekordbox records straight to disk)
            if prev != size or time.time() - mtime < STABLE_SEC:
                continue
            row = store.recording_by("path", key)
            if row and (row["features_path"] or row["error"]):
                continue
            _state["busy"] = path.name
            try:
                res = audio.analyse_file(path)
                existing = store.recording_by("sha1", res["sha1"])
                fields = {"features_path": res["features_path"], "kind": "file",
                          "started_at": res["start"].isoformat(sep=" ", timespec="seconds"),
                          "duration_sec": res["duration_sec"], "analysed_at": datetime.now().isoformat(timespec="seconds"),
                          "error": None}
                if existing:  # same audio, e.g. renamed or moved
                    store.update_recording(existing["id"], path=key, **fields)
                else:
                    store.add_recording(key, res["sha1"], **fields)
                done += 1
            except Exception as e:
                msg = f"Couldn't analyse: {e}"[:300]
                if row:
                    store.update_recording(row["id"], error=msg)
                else:
                    store.add_recording(key, f"error:{key}", error=msg, kind="file")
            finally:
                _state["busy"] = None
    finally:
        _lock.release()
    return done


def _values(chunk: dict, key: str, lo: float, hi: float) -> list[float]:
    """Numbers from one browser chunk, clamped; anything that isn't a finite number reads as the floor."""
    raw = chunk.get(key)
    if not isinstance(raw, list):
        return []
    out = []
    for v in raw[:MAX_CHUNK_SEC]:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        out.append(max(lo, min(hi, float(v))) if ok else lo)
    return out


def add_mic_features(store, client_session: str, start_ms: int, chunk: dict) -> dict:
    """Append per-second features captured in the browser (mic / line-in) to a 'mic' recording.

    client_session must already be checked as a plain id ([A-Za-z0-9-]); it becomes part of a file name."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", client_session):
        raise ValueError("bad client_session")
    key = f"mic:{client_session}"
    audio.FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    fpath = audio.FEATURES_DIR / f"mic-{client_session}.json"
    row = store.recording_by("path", key)
    if row and row["features_path"] and Path(row["features_path"]).is_file():
        feat = json.loads(Path(row["features_path"]).read_text())
    else:
        feat = {"duration_sec": 0, "hop_sec": 1, "loudness_db": [], "bands": {"low": [], "mid": [], "high": []},
                "silences": [], "tempo": [], "beat_strength": [], "source": "mic"}
    if len(feat["loudness_db"]) >= MAX_MIC_SEC:  # a mic left on for days: stop growing the file
        return {"seconds": len(feat["loudness_db"]), "full": True}
    feat["loudness_db"] += _values(chunk, "loudness_db", -60.0, 0.0)
    for b in ("low", "mid", "high"):
        feat["bands"][b] += _values(chunk, b, 0.0, 1.0)
    n = len(feat["loudness_db"])
    for b in ("low", "mid", "high"):  # keep every lane the same length
        feat["bands"][b] = (feat["bands"][b] + [0.0] * n)[:n]
    feat["duration_sec"] = n
    feat["silences"] = _silences(feat["loudness_db"])
    fpath.write_text(json.dumps(feat))
    start = datetime.fromtimestamp(start_ms / 1000).isoformat(sep=" ", timespec="seconds")
    if row:
        store.update_recording(row["id"], duration_sec=n)
    else:
        store.add_recording(key, key, kind="mic", features_path=str(fpath), started_at=start, duration_sec=n,
                            analysed_at=datetime.now().isoformat(timespec="seconds"))
    return {"seconds": n}


def _silences(loud: list[float]) -> list[list[int]]:
    out, i = [], 0
    while i < len(loud):
        if loud[i] < audio.SILENCE_DB:
            j = i
            while j < len(loud) and loud[j] < audio.SILENCE_DB:
                j += 1
            if j - i >= audio.SILENCE_MIN_SEC:
                out.append([i, j])
            i = j
        else:
            i += 1
    return out


def enrich(detail: dict, store) -> dict:
    """Attach the best recording for this set: per-transition audio checks and audio observations."""
    start = datetime.fromisoformat(detail["started_at"])
    end = datetime.fromisoformat(detail["tracks"][-1]["played_at"]) + timedelta(minutes=5)
    # rekordbox logs a track about a minute after it starts, so the music began before the set's first entry
    recs = store.recordings_between((start - timedelta(seconds=audio.LOG_DELAY_SEC)).isoformat(), end.isoformat())
    if not recs:
        detail["audio"] = None
        for t in detail["transitions"]:
            t["audio"] = None
        return detail
    rec = max(recs, key=lambda r: (r["kind"] == "file", r["duration_sec"] or 0))  # prefer real recordings
    feat = audio.load_features(rec["features_path"])
    rec_start = datetime.fromisoformat(rec["started_at"])

    cache_key = (rec["id"], detail["id"], len(detail["transitions"]))
    if cache_key not in _align_cache:
        # browser clock: exact, for live listening and for files the page recorded (their name carries the start)
        if rec["kind"] == "mic" or Path(rec["path"]).name.startswith("Blendbook "):
            _align_cache[cache_key] = {"offset_sec": (start - rec_start).total_seconds(), "refined": False,
                                       "confidence": "high"}
        else:
            # rekordbox logs a track ~1 min in, so the real change sits a little before at_sec
            _align_cache[cache_key] = audio.align(feat, rec_start, start, [t["at_sec"] - 45 for t in detail["transitions"]])
    al = _align_cache[cache_key]

    drifts, jumps, gaps = [], [], []
    starts = [t["at_sec"] - 45 + al["offset_sec"] for t in detail["transitions"]]
    for i, t in enumerate(detail["transitions"]):
        blend = (t.get("controller") or {}).get("duration_sec")
        at_rec = starts[i]
        a = audio.transition_audio(feat, at_rec, blend, next_at_sec=starts[i + 1] if i + 1 < len(starts) else None,
                                   prev_at_sec=starts[i - 1] if i else None)
        if rec["kind"] == "mic":
            a["beat_drift"] = "unclear" if a.get("in_recording") else a.get("beat_drift")
        a["at_rec_sec"] = round(at_rec) if a.get("in_recording") else None
        t["audio"] = a
        if not a.get("in_recording"):
            continue
        if a["beat_drift"] == "drift":
            drifts.append(t)
        if a["loudness_step_db"] is not None and abs(a["loudness_step_db"]) >= LOUDNESS_STEP_WARN_DB:
            jumps.append(t)
        if a["silence_sec"]:
            gaps.append(t)

    detail["audio"] = {
        "recording_id": rec["id"], "kind": rec["kind"], "name": Path(rec["path"]).name if rec["kind"] == "file" else "Live listening",
        "offset_sec": round(al["offset_sec"], 1), "alignment": al["confidence"], "duration_sec": rec["duration_sec"],
        "features_url": f"/api/recordings/{rec['id']}/features",
        "audio_url": f"/api/recordings/{rec['id']}/audio" if rec["kind"] == "file" else None,
    }
    obs = []
    if gaps:
        obs.append({"kind": "warn", "title": f"Silence in {len(gaps)} transition{'s' if len(gaps) > 1 else ''} (recording)",
                    "detail": "The recording goes quiet around: " + ", ".join(f"into {t['to']['title']}" for t in gaps[:3])
                              + ". On a dance floor even 2 seconds of silence is noticed.",
                    "at_n": gaps[0]["n"] + 1, "source": "audio"})
    if drifts:
        obs.append({"kind": "warn", "title": f"Beats drifted in {len(drifts)} blend{'s' if len(drifts) > 1 else ''}",
                    "detail": "The kicks stopped landing together around: " + ", ".join(f"into {t['to']['title']}" for t in drifts[:3])
                              + ". Nudge the jog wheel or tempo fader as soon as you hear flamming, or blend for less time.",
                    "at_n": drifts[0]["n"] + 1, "source": "audio"})
    if jumps:
        worst = max(jumps, key=lambda t: abs(t["audio"]["loudness_step_db"]))
        obs.append({"kind": "info", "title": f"{len(jumps)} loudness jump{'s' if len(jumps) > 1 else ''} of 3 dB or more",
                    "detail": f"Biggest: into {worst['to']['title']} ({worst['audio']['loudness_step_db']:+.1f} dB). "
                              "Match levels with the TRIM knobs while cueing in your headphones.",
                    "at_n": worst["n"] + 1, "source": "audio"})
    detail["observations"] = obs + detail.get("observations", [])
    return detail
