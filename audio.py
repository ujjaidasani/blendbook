"""
Recording analysis: turns a mix recording into per-second features and per-transition checks.

Only numbers are stored (in the data folder's features/), never a copy of the audio. Beat analysis on a mixed recording is
noisy, so every beat verdict comes with "unclear" as an honest option.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import paths

SR = 22050
HOP_SEC = 1
ONSET_HOP = 512                      # ~23 ms
TEMPO_WINDOW_SEC = 8
SILENCE_DB = -50.0
SILENCE_MIN_SEC = 1.5
LOG_DELAY_SEC = 120  # rekordbox adds a track to history up to this long after it starts playing
PAUSE_SEC = 20       # silence this long is the decks stopped (practice, setting up a mix), not dead air in a blend
AUDIO_EXT = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}

FEATURES_DIR = paths.DATA_DIR / "features"
RECORDING_DIRS = [
    paths.RB_RECORDING_DIR,
    paths.RECORDINGS_DIR,  # drop any mix recording here
]


# ---------- decoding & features ----------

def decode(path: Path) -> np.ndarray:
    ffmpeg = paths.tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(paths.ffmpeg_help())
    out = subprocess.run(
        # file-only input: a crafted "recording" (e.g. an HLS playlist renamed .mp3) can't make ffmpeg fetch URLs
        [ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-i", str(path),
         "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True, check=True, timeout=900, **paths.NO_WINDOW)
    return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0


_NAME_TIME = re.compile(r"(20\d\d)[-_.]?(\d\d)[-_.]?(\d\d)[ _T-]*(\d\d)[-_.:h]?(\d\d)[-_.:m]?(\d\d)?")


def probe_start(path: Path) -> datetime:
    """When the recording started: a creation_time tag, else a date+time in the file name, else file birth time."""
    try:
        out = subprocess.run([paths.tool("ffprobe") or "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries",
                              "format_tags=creation_time,date", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=30, **paths.NO_WINDOW)
        tags = json.loads(out.stdout or "{}").get("format", {}).get("tags", {})
        ct = tags.get("creation_time")
        if ct:
            return datetime.fromisoformat(ct.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except Exception:
        pass
    m = _NAME_TIME.search(path.stem)
    if m and m.group(4):
        try:
            y, mo, d, hh, mm, ss = (int(g) if g else 0 for g in m.groups())
            return datetime(y, mo, d, hh, mm, ss)
        except ValueError:
            pass
    st = path.stat()
    return datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))


def features(y: np.ndarray) -> dict:
    n_sec = len(y) // SR
    if n_sec < 10:
        raise ValueError("Recording is shorter than 10 seconds.")
    frames = y[: n_sec * SR].reshape(n_sec, SR)

    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    loud = np.maximum(20 * np.log10(rms), -60.0)

    spec = np.abs(np.fft.rfft(frames * np.hanning(SR), axis=1)) ** 2
    freqs = np.fft.rfftfreq(SR, 1 / SR)
    bands = {}
    for name, lo, hi in (("low", 20, 200), ("mid", 200, 2000), ("high", 2000, 10000)):
        e = spec[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
        e = np.log10(e + 1e-9)
        top = np.percentile(e, 95)
        floor = np.percentile(e, 5)
        bands[name] = np.clip((e - floor) / max(1e-6, top - floor), 0, 1)

    silences = []
    quiet = loud < SILENCE_DB
    i = 0
    while i < n_sec:
        if quiet[i]:
            j = i
            while j < n_sec and quiet[j]:
                j += 1
            if j - i >= SILENCE_MIN_SEC:
                silences.append([i, j])
            i = j
        else:
            i += 1

    onset = onset_envelope(y)
    kick = onset_envelope(y, max_bin=10)  # below ~215 Hz: kicks only
    tempo, strength = [], []
    fps = SR / ONSET_HOP
    win = int(TEMPO_WINDOW_SEC * fps)
    for start in range(0, len(onset) - win, win):
        bpm, conf, lag = window_tempo(onset[start:start + win], fps)
        t = int(start / fps)
        tempo.append({"t": t, "bpm": bpm, "confidence": conf})
        strength.append({"t": t, "s": beat_focus(kick[start:start + win], lag)})

    return {
        "duration_sec": int(n_sec), "hop_sec": HOP_SEC,
        "loudness_db": [round(float(v), 1) for v in loud],
        "bands": {k: [round(float(v), 2) for v in arr] for k, arr in bands.items()},
        "silences": silences, "tempo": tempo, "beat_strength": strength,
    }


def onset_envelope(y: np.ndarray, max_bin: int = 200) -> np.ndarray:
    """Spectral flux up to max_bin (200 = ~4.3 kHz, where kicks and claps live; 10 = kicks only)."""
    n = (len(y) - 1024) // ONSET_HOP
    if n <= 0:
        return np.zeros(1)
    idx = np.arange(1024)[None, :] + ONSET_HOP * np.arange(n)[:, None]
    win = np.hanning(1024).astype(np.float32)
    flux = np.empty(n, dtype=np.float32)
    chunk = 4096
    prev = None
    for c in range(0, n, chunk):
        mag = np.abs(np.fft.rfft(y[idx[c:c + chunk]] * win, axis=1))[:, 1:max_bin]
        mag = np.log1p(mag)
        if prev is not None:
            mag_full = np.vstack([prev[None, :], mag])
        else:
            mag_full = np.vstack([mag[:1], mag])
        flux[c:c + len(mag)] = np.maximum(np.diff(mag_full, axis=0), 0).sum(axis=1)
        prev = mag[-1]
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # remove slow loudness changes
    return np.maximum(flux, 0)


def window_tempo(env: np.ndarray, fps: float) -> tuple[float | None, float, float | None]:
    """Tempo, confidence and the beat period in frames for one window, via autocorrelation in 70–180 BPM."""
    env = env - env.mean()
    if not env.any():
        return None, 0.0, None
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    ac /= ac[0] + 1e-9
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    seg = ac[lo:hi]
    if not len(seg):
        return None, 0.0, None
    k = int(np.argmax(seg)) + lo
    peak = float(ac[k])
    # parabolic interpolation for a sub-frame period
    if 0 < k < len(ac) - 1:
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        denom = a - 2 * b + c
        lag = k + (0.5 * (a - c) / denom if denom else 0.0)
    else:
        lag = float(k)
    bpm = 60 * fps / lag
    while bpm < 100:  # fold half-time detections into the club range
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    conf = float(np.clip((peak - np.median(seg)) / (1 - np.median(seg) + 1e-9), 0, 1))
    return round(float(bpm), 1), round(conf, 2), float(lag)


def beat_focus(kick_env: np.ndarray, lag: float | None) -> float:
    """How tightly kicks land on one point of the beat cycle (0..1).

    Fold the kick onsets onto the beat period and take the mean resultant length. One clean kick pattern gives
    ~1; two patterns out of phase (a flam or drifting blend) spread the energy and pull it down.
    """
    if not lag or not kick_env.any():
        return 0.0
    w = kick_env - np.median(kick_env)
    w = np.maximum(w, 0)
    if not w.any():
        return 0.0
    theta = 2 * np.pi * (np.arange(len(w)) % lag) / lag
    return round(float(np.abs((w * np.exp(1j * theta)).sum()) / w.sum()), 3)


# ---------- per-transition checks ----------

def transition_audio(feat: dict, at_sec: float, blend_sec: float | None = None,
                     next_at_sec: float | None = None, prev_at_sec: float | None = None) -> dict:
    """Checks around one transition. at_sec is when the incoming track starts, in recording time."""
    loud = np.array(feat["loudness_db"])
    n = len(loud)
    t = int(round(at_sec))
    if t < 5 or t > n - 5:
        return {"in_recording": False}
    span = int(blend_sec or 30)
    # compare each track on its own: before the blend starts, and after it has certainly finished
    before = loud[max(0, t - 40): max(1, t - 10)]
    after = loud[min(n - 1, t + max(60, span + 20)): min(n, t + max(90, span + 50))]
    before, after = before[before > SILENCE_DB], after[after > SILENCE_DB]
    # windows that spill into a neighbouring change would compare the wrong tracks
    clean = (next_at_sec is None or next_at_sec >= t + max(90, span + 50)) and (prev_at_sec is None or prev_at_sec <= t - 70)
    step = round(float(after.mean() - before.mean()), 1) if clean and len(before) and len(after) else None

    silence = paused = 0
    for a, b in feat["silences"]:
        if b >= t - 10 and a <= t + span + 10:
            if b - a >= PAUSE_SEC:
                paused += b - a
            else:
                silence += b - a

    strengths = [w for w in feat.get("beat_strength", []) if w["s"] > 0]
    beat = "unclear"
    if len(strengths) >= 8:
        median = float(np.median([w["s"] for w in strengths]))
        around = [w["s"] for w in strengths if t - 4 <= w["t"] <= t + span + 4]
        if around and median > 0.05 and not paused:  # stopped decks have no beat to drift
            ratio = min(around) / median
            beat = "drift" if ratio < 0.8 else "steady" if ratio > 0.9 else "unclear"
    return {"in_recording": True, "loudness_step_db": step, "silence_sec": int(silence), "paused_sec": int(paused),
            "beat_drift": beat}


# ---------- alignment ----------

def align(feat: dict, rec_start: datetime, session_start: datetime, transition_secs: list[float]) -> dict:
    """Offset (seconds) to add to set time to get recording time, refined by where the low end dips.

    A bass swap usually dips the low band near each track change, so slide the transition grid +-90 s and keep
    the shift only if the dips line up clearly better than chance.
    """
    base = (session_start - rec_start).total_seconds()
    low = np.array(feat["bands"]["low"])
    n = len(low)

    def score(shift):
        idx = [int(round(t + base + shift)) for t in transition_secs]
        idx = [i for i in idx if 0 <= i < n]
        if len(idx) < 3:
            return None
        return -float(np.mean([low[max(0, i - 3): i + 4].min() for i in idx]))

    shifts = list(range(-90, 91))
    scores = [score(s) for s in shifts]
    valid = [(s, v) for s, v in zip(shifts, scores) if v is not None]
    if len(valid) < 30:
        return {"offset_sec": base, "refined": False, "confidence": "low"}
    vals = np.array([v for _, v in valid])
    best_shift, best = max(valid, key=lambda x: x[1])
    z = (best - vals.mean()) / (vals.std() + 1e-9)
    if z >= 2.5:
        return {"offset_sec": base + best_shift, "refined": True, "confidence": "high" if z >= 3.5 else "medium"}
    return {"offset_sec": base, "refined": False, "confidence": "low"}


# ---------- files ----------

def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def candidate_files() -> list[Path]:
    out = []
    for d in RECORDING_DIRS:
        if d.exists():
            out += [p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT and p.is_file() and not p.is_symlink()]
    return sorted(out, key=lambda p: p.stat().st_mtime)


def analyse_file(path: Path) -> dict:
    """Decode + features, saved to FEATURES_DIR. Returns {sha1, start, features_path, duration_sec}."""
    digest = sha1(path)
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FEATURES_DIR / f"{digest}.json"
    if not out.exists():
        feat = features(decode(path))
        out.write_text(json.dumps(feat))
    else:
        feat = json.loads(out.read_text())
    return {"sha1": digest, "start": probe_start(path), "features_path": str(out), "duration_sec": feat["duration_sec"]}


def load_features(features_path: str) -> dict:
    return json.loads(Path(features_path).read_text())


def match_session(sessions: list[dict], start: datetime, duration_sec: int) -> str | None:
    """The rekordbox session that overlaps the recording most."""
    end = start + timedelta(seconds=duration_sec)
    best, best_overlap = None, 0.0
    for s in sessions:
        s_start = s["started_at"] - timedelta(seconds=LOG_DELAY_SEC)
        s_end = s["plays"][-1]["played_at"] + timedelta(minutes=5)
        overlap = (min(end, s_end) - max(start, s_start)).total_seconds()
        if overlap > best_overlap:
            best, best_overlap = s["id"], overlap
    return best
