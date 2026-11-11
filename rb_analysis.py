"""
What rekordbox's own analysis files can tell us beyond its database.

rekordbox saves a waveform for every track it analyses, streamed ones included, in its share/PIONEER/USBANLZ folder.
Its three-band preview (the PWV6 tag in each .2EX file) gives Blendbook an energy reading with no internet and no
audio file. It's relative: how a track compares with the rest of this DJ's library, which is the question a set plan
asks. Checked against ReccoBeats' energy on 585 tracks, the two agree moderately (rank correlation 0.60).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

MIN_TRACKS = 20  # with fewer, "compared with your library" doesn't mean much
ENERGY_LOW, ENERGY_HIGH = 0.5, 0.95  # the range the planner's energy shapes work in
MIN_COLUMNS = 50  # of the waveform's 1200 columns: fewer than this isn't a real analysis


def waveform_features(entries: bytes) -> tuple[float, float] | None:
    """Typical overall level, and how hard the first band peaks. None if there's too little waveform to judge."""
    data = np.frombuffer(entries, dtype=np.uint8)
    bands = data[: len(data) - len(data) % 3].reshape(-1, 3).astype(float)
    bands = bands[bands.sum(1) > 0]  # silence at either end says nothing about the track
    if len(bands) < MIN_COLUMNS:
        return None
    return float(np.median(bands.sum(1))), float(np.percentile(bands[:, 0], 90))


def read_features(path: Path) -> tuple[float, float] | None:
    from pyrekordbox.anlz import AnlzFile

    try:
        return waveform_features(AnlzFile.parse_file(path).get_tag("PWV6").content.entries)
    except Exception:  # a file rekordbox is rewriting, or one from an older analysis without this tag
        return None


def relative_energy(features: dict[str, tuple[float, float]]) -> dict[str, float]:
    """Each track's place in the library, on the planner's energy scale."""
    if len(features) < MIN_TRACKS:
        return {}
    ids = list(features)
    values = np.array([features[i] for i in ids])
    score = ((values - values.mean(0)) / (values.std(0) + 1e-9)).sum(1)
    ranks = score.argsort().argsort() / (len(ids) - 1)
    return {i: round(float(ENERGY_LOW + (ENERGY_HIGH - ENERGY_LOW) * r), 2) for i, r in zip(ids, ranks)}


class EnergyReader:
    """Reads each analysis file once: the library reloads often, and these only change when a track is re-analysed."""

    def __init__(self):
        self._seen: dict[str, tuple[float, tuple[float, float] | None]] = {}

    def energies(self, rb_dir: Path, tracks: dict[str, dict]) -> dict[str, float]:
        features = {}
        for tid, t in tracks.items():
            if t.get("is_sample") or not t.get("analysis_path"):
                continue
            path = (rb_dir / "share" / str(t["analysis_path"]).lstrip("/")).with_suffix(".2EX")
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            seen = self._seen.get(str(path))
            if not seen or seen[0] != mtime:
                seen = self._seen[str(path)] = (mtime, read_features(path))
            if seen[1]:
                features[tid] = seen[1]
        return relative_energy(features)
