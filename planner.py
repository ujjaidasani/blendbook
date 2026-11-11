"""
Set builder: plans an ordered set from the DJ's own library and habits, then (optionally) writes it into
rekordbox as a playlist.

Writing touches rekordbox's own master.db, so export_playlist() refuses while rekordbox is running, backs up
master.db first, and only ever creates playlists inside a "Blendbook" folder.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import paths

SHAPES = {
    # fraction of the set (0..1) -> tempo multiplier on the opening BPM
    "warmup": lambda x: 1 + 0.04 * x,
    "build": lambda x: 1 + 0.08 * x,
    "peak": lambda x: 1 + 0.05 * min(1.0, x * 3),
    "journey": lambda x: 1 + 0.07 * math.sin(math.pi * x),
    "cooldown": lambda x: 1 - 0.06 * x,
}
# fraction of the set (0..1) -> target energy (0..1: rb_analysis places a library on 0.5-0.95, and ReccoBeats
# scores dance music around 0.5-0.9)
ENERGY_SHAPES = {
    "warmup": lambda x: 0.45 + 0.15 * x,
    "build": lambda x: 0.5 + 0.35 * x,
    "peak": lambda x: 0.85,
    "journey": lambda x: 0.55 + 0.3 * math.sin(math.pi * x),
    "cooldown": lambda x: 0.75 - 0.3 * x,
}
# energy steers but doesn't rule: +8 for a track on the curve and a comfortable step from the last one, down to -8 for
# one 0.2 or more away from both. History and tempo fit are worth more (up to 40 each). Raising this follows the shape
# more closely but gives up proven blends: on a real library, 8 cut the distance from the curve 30% for 30% fewer
# proven blends, and 20 cut it 54% for 68% fewer.
ENERGY_WEIGHT = 8.0
ENERGY_TOLERANCE = 0.2
ENERGY_STEP = 0.12  # most energy change asked of one transition: catch up with the curve gradually
FRESH_BONUS = {"none": -6.0, "some": 4.0, "lots": 12.0}
BEAM = 8
CANDIDATES = 10
FOLDER_NAME = "Blendbook"
BACKUP_DIR = paths.DATA_DIR / "backups"


def target_curve(start_bpm: float, length: int, shape: str) -> list[float]:
    f = SHAPES.get(shape, SHAPES["warmup"])
    return [round(start_bpm * f(i / max(1, length - 1)), 1) for i in range(length)]


def energy_curve(length: int, shape: str) -> list[float]:
    f = ENERGY_SHAPES.get(shape, ENERGY_SHAPES["warmup"])
    return [round(f(i / max(1, length - 1)), 2) for i in range(length)]


def energy_fit(prev: float | None, energy: float | None, target: float) -> tuple[float, str | None]:
    """Score for following the energy curve from the previous track, and how to say it. Without energy data
    on both tracks: (0, None), so tracks that were never matched online are neither helped nor hurt."""
    if prev is None or energy is None:
        return 0.0, None
    want = prev + max(-ENERGY_STEP, min(ENERGY_STEP, target - prev))
    # half for being on the curve, half for getting there in comfortable steps rather than one big jump
    off = min(1.0, abs(energy - target) / ENERGY_TOLERANCE) + min(1.0, abs(energy - want) / ENERGY_TOLERANCE)
    score = ENERGY_WEIGHT * (1 - off)
    if energy - prev >= 0.05:
        return score, "lifts the energy"
    if energy - prev <= -0.05:
        return score, "eases the energy down"
    return score, "keeps it calm" if target <= 0.55 else "keeps the energy up" if target >= 0.75 else "holds the energy"


def plan_set(lib, start_id: str, length: int = 12, shape: str = "warmup", include_ids: list[str] | None = None,
             fresh: str = "some", locked: dict[int, str] | None = None) -> dict:
    """Beam search over the library. Each step is scored like the live suggester (your past transitions,
    tempo fit, genre), against a target tempo curve, with penalties for repeating artists and a small nudge
    along the shape's energy curve where both tracks have energy data."""
    start = lib.track(start_id)
    if not start or not start.get("bpm"):
        raise ValueError("Pick an opening track that has a BPM.")
    length = max(3, min(40, int(length)))
    include = [i for i in (include_ids or []) if lib.track(i) and i != start_id]
    # locked tracks can be online ones you added (not in rekordbox yet): they keep their slot on a re-roll
    locked = {int(k): v for k, v in (locked or {}).items() if lib.track(v) and 0 < int(k) < length}
    curve = target_curve(start["bpm"], length, shape)
    ecurve = energy_curve(length, shape)
    fresh_bonus = FRESH_BONUS.get(fresh, 4.0)
    _, session_counts = lib._session_sets()

    # beam entries: (score, [track ids], [reasons per step])
    beam = [(0.0, [start_id], [["your opener"]])]
    for i in range(1, length):
        nxt = []
        for score, ids, reasons in beam:
            used = set(ids)
            artists = {lib.track(t)["artist"] for t in ids[-4:]}
            if i in locked:
                tid = locked[i]
                if tid in used:
                    continue
                nxt.append((score, ids + [tid], reasons + [["added from online" if tid.startswith("web:") else "locked by you"]]))
                continue
            recent_genres = [lib.track(t)["genre"] for t in ids[-4:]]
            cands = lib.suggest(ids[-1], exclude=used | set(locked.values()), recent_genres=recent_genres,
                                limit=CANDIDATES * 3, target_bpm=curve[i])
            cands += [_must_include_candidate(lib, ids[-1], t, curve[i]) for t in include if t not in used]
            prev = lib.track(ids[-1])
            seen, scored = set(), []
            for c in cands:
                if not c or c["id"] in seen:
                    continue
                seen.add(c["id"])
                s = c["score"]
                why = list(c["reasons"])
                if c["artist"] in artists:
                    s -= 15
                if session_counts[c["id"]] == 0:
                    s += fresh_bonus
                if c["id"] in include:
                    s += 40
                    why.insert(0, "you asked for it")
                if prev["bpm"] and c["bpm"] and abs(c["bpm"] - prev["bpm"]) / prev["bpm"] > 0.06:
                    s -= 20
                e, lift = energy_fit(prev.get("energy"), c.get("energy"), ecurve[i])
                scored.append((s, e, lift, c["id"], why[:3]))
            for k, (s, e, lift, cid, why) in enumerate(scored):
                # name the energy only when it moved this track past one that ranked above it on everything else
                # (ranks as the stable sort below sees them: score first, then candidate order)
                if e > 0 and any((s2, -j) > (s, -k) and (s + e, -k) > (s2 + e2, -j)
                                 for j, (s2, e2, *_) in enumerate(scored)):
                    why = _with_reason(why, lift)
                nxt.append((score + s + e, ids + [cid], reasons + [why]))
            nxt.sort(key=lambda x: -x[0])
            nxt = nxt[: CANDIDATES * BEAM]
        if not nxt:
            break
        nxt.sort(key=lambda x: -x[0])
        # keep the beam diverse: don't let one prefix take every slot
        beam, prefixes = [], {}
        for entry in nxt:
            key = tuple(entry[1][:-1])
            if prefixes.get(key, 0) >= 3:
                continue
            prefixes[key] = prefixes.get(key, 0) + 1
            beam.append(entry)
            if len(beam) >= BEAM:
                break

    best = max(beam, key=lambda x: x[0])
    ids, reasons = best[1], best[2]
    warnings = []
    missing = [lib.track(t)["title"] for t in include if t not in ids]
    if missing:
        warnings.append("Couldn't fit: " + ", ".join(missing) + " (tempo too far from the plan).")
    if len(ids) < length:
        warnings.append(f"Only found {len(ids)} tracks that fit this tempo plan.")
    tracks = []
    for i, tid in enumerate(ids):
        t = lib._brief(tid)
        tracks.append({**t, "target_bpm": curve[i] if i < len(curve) else None,
                       "target_energy": ecurve[i] if i < len(ecurve) else None, "reasons": reasons[i],
                       "times_played": session_counts[tid]})
    return {"tracks": tracks, "curve": curve, "energy_curve": ecurve, "shape": shape, "warnings": warnings}


def _with_reason(why: list[str], extra: str) -> list[str]:
    """At most three reasons with `extra` among them. A key-clash warning stays visible."""
    if len(why) >= 3:
        why = why[:3]
        del why[1 if why[2].startswith("key clash") else 2]
    return why + [extra]


def _must_include_candidate(lib, prev_id: str, tid: str, target: float) -> dict | None:
    t = lib.track(tid)
    if not t["bpm"]:
        return None
    pct = 100 * (t["bpm"] - target) / target
    if abs(pct) > 8:
        return None
    return {**lib._brief(tid), "score": 30 * max(0.0, 1 - abs(pct) / 8), "reasons": [f"{t['bpm']:g} BPM"]}


def alternatives(lib, prev_id: str, target_bpm: float, exclude: set[str], limit: int = 6) -> list[dict]:
    return lib.suggest(prev_id, exclude=exclude, target_bpm=target_bpm, limit=limit)


# ---------- rekordbox export ----------

def rekordbox_running() -> bool:
    from pyrekordbox.utils import get_rekordbox_pid

    try:
        if get_rekordbox_pid():
            return True
    except Exception:
        pass
    try:  # belt and braces: pyrekordbox's check depends on the process name
        if paths.IS_WINDOWS:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq rekordbox.exe", "/NH"], capture_output=True,
                                 text=True, timeout=10, **paths.NO_WINDOW).stdout
            return "rekordbox.exe" in out.lower()
        return subprocess.run(["pgrep", "-if", "rekordbox.app/Contents/MacOS"], capture_output=True,
                              timeout=5).returncode == 0
    except Exception:
        return False


def backup(rb_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("master.db", "master.db-wal", "master.db-shm", "masterPlaylists6.xml"):
        src = rb_dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
    return dest


def export_playlist(rb_dir: Path, name: str, track_ids: list[str]) -> dict:
    """Create `name` inside the Blendbook folder in rekordbox. Caller must have the user's confirmation.

    Only rekordbox content ids go in: tracks found online aren't in your library until you add them in rekordbox."""
    track_ids = [t for t in track_ids if t.isdigit()]
    if rekordbox_running():
        raise RuntimeError("Close rekordbox first. Blendbook only writes to your library while rekordbox is closed.")
    from pyrekordbox.masterdb import MasterDatabase

    backup_dir = backup(rb_dir)
    db = MasterDatabase(str(rb_dir / "master.db"))
    try:
        folder = next((p for p in db.get_playlist().all() if p.Name == FOLDER_NAME and p.is_folder), None)
        if folder is None:
            folder = db.create_playlist_folder(FOLDER_NAME)
        pl = db.create_playlist(name, parent=folder)
        for tid in track_ids:
            content = db.get_content(ID=tid)
            if content is not None:
                db.add_to_playlist(pl, content)
        db.commit()
        return {"playlist_id": str(pl.ID), "folder": FOLDER_NAME, "name": name, "backup": str(backup_dir)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def remove_playlist(rb_dir: Path, playlist_id: str) -> None:
    if rekordbox_running():
        raise RuntimeError("Close rekordbox first.")
    from pyrekordbox.masterdb import MasterDatabase

    backup(rb_dir)
    db = MasterDatabase(str(rb_dir / "master.db"))
    try:
        pl = db.get_playlist(ID=playlist_id)
        if pl is None:
            return
        if pl.Parent is None or pl.Parent.Name != FOLDER_NAME:
            raise RuntimeError("That playlist isn't in the Blendbook folder, so Blendbook won't delete it.")
        db.delete_playlist(pl)
        db.commit()
    finally:
        db.close()
