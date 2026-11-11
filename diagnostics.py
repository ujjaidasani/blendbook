"""
What a bug report needs, in text a DJ can paste. No track titles, keys or API keys, and the home folder is hidden
so a username doesn't travel with it. The log can still mention file names, so people see the text before sending it.
"""

from __future__ import annotations

import platform
from pathlib import Path

import paths

LOG_LINES = 40


def redact(text: str) -> str:
    return text.replace(str(Path.home()), "~")


def report(version: str, status: dict, online: dict, ai: bool) -> str:
    c = online["coverage"]
    lines = [
        f"Blendbook {version} ({'app' if paths.FROZEN else 'source'})",
        f"System: {platform.platform()}",
        f"rekordbox: {status.get('rekordbox_version') or 'version unknown'} (tested up to {status.get('tested_version')})",
        f"Library folder: {status['rekordbox_dir']} ({'found' if status['found'] else 'NOT FOUND'})",
        f"Library: {status['tracks']} tracks, {status['sessions']} sets, loaded {status.get('loaded_at')}",
        f"Keys: {c['rekordbox_keys']} from rekordbox, {c['from_online']} online, no rekordbox key {c['no_rekordbox_key']}",
        f"ffmpeg: {'yes' if paths.tool('ffmpeg') else 'no'}. AI coach: {'on' if ai else 'off'}.",
        f"Problem reading rekordbox: {status['error'] or 'none'}",
    ]
    try:
        log = paths.LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_LINES:]
    except OSError:
        log = []
    if log:
        lines += ["", f"Last {len(log)} log lines:", *log]
    return redact("\n".join(lines))
