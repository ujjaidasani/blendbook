"""
Where Blendbook keeps things, on macOS and Windows, run from source or as the packaged app.

- APP_DIR: the code and web/ (inside the .app / .exe bundle when packaged, read-only there).
- DATA_DIR: each person's own data: Blendbook's database, library copy, backups, settings (API key). Owner-only.
- RECORDINGS_DIR: the folder to drop any mix recording into.
- RB_DIR / RB_RECORDING_DIR: where rekordbox keeps its library and its own REC files.

Every location can be overridden with an environment variable (BLENDBOOK_DATA, BLENDBOOK_RECORDINGS,
REKORDBOX_DIR, REKORDBOX_RECORDING_DIR).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
FROZEN = getattr(sys, "frozen", False)  # running from the PyInstaller bundle

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_DIR = APP_DIR / "web"


def _home_dir(*parts: str) -> Path:
    return Path.home().joinpath(*parts)


def _default_data_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA") or _home_dir("AppData", "Roaming")) / "Blendbook"
    if IS_MAC:
        return _home_dir("Library", "Application Support", "Blendbook")
    return Path(os.environ.get("XDG_DATA_HOME") or _home_dir(".local", "share")) / "blendbook"


def _default_rb_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA") or _home_dir("AppData", "Roaming")) / "Pioneer" / "rekordbox"
    return _home_dir("Library", "Pioneer", "rekordbox")


DATA_DIR = Path(os.environ.get("BLENDBOOK_DATA") or _default_data_dir())
RECORDINGS_DIR = Path(os.environ.get("BLENDBOOK_RECORDINGS") or _home_dir("Music", "Blendbook Recordings"))
RB_DIR = Path(os.environ.get("REKORDBOX_DIR") or _default_rb_dir())
RB_RECORDING_DIR = Path(os.environ.get("REKORDBOX_RECORDING_DIR") or _home_dir("Music", "rekordbox", "Recording"))

RB_SETTINGS = [Path(os.environ.get("APPDATA") or _home_dir("AppData", "Roaming")) / "Pioneer" / d / "rekordbox3.settings"
               for d in ("rekordbox6", "rekordbox")] if IS_WINDOWS else \
    [_home_dir("Library", "Application Support", "Pioneer", d, "rekordbox3.settings") for d in ("rekordbox6", "rekordbox")]

CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "server.log"


def ensure_data_dir() -> None:
    """DATA_DIR holds a copy of the rekordbox library, backups and the API key: owner-only."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:  # Windows keeps %APPDATA% per user already; chmod there only toggles read-only
        os.chmod(DATA_DIR, 0o700)


def migrate_legacy(old_root: Path | None = None) -> list[str]:
    """Versions before 1.0 kept data in .cache/ and Recordings/ next to the code. Move them to the new places once,
    only when the new place doesn't exist yet, so nothing is ever overwritten. Returns what was moved."""
    if FROZEN or "BLENDBOOK_DATA" in os.environ:
        return []
    moved = []
    old_root = old_root or Path(__file__).resolve().parent
    for old, new in ((old_root / ".cache", DATA_DIR), (old_root / "Recordings", RECORDINGS_DIR)):
        if old.is_dir() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
            moved.append(f"{old} → {new}")
    return moved


# The packaged app can't see a Terminal's PATH (a Mac app opened from Finder gets only /usr/bin:/bin), so look in
# the usual install places too. Bundled copies in APP_DIR/bin win, if a build includes them.
_TOOL_DIRS = [APP_DIR / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
# Keep a console window from flashing up for each ffmpeg run in the Windows app
NO_WINDOW = {"creationflags": 0x08000000} if IS_WINDOWS else {}  # CREATE_NO_WINDOW


def tool(name: str) -> str | None:
    """Full path to a command-line tool (ffmpeg, ffprobe), or None if it isn't installed."""
    exe = name + ".exe" if IS_WINDOWS else name
    for d in _TOOL_DIRS:
        if (d / exe).is_file():
            return str(d / exe)
    return shutil.which(name)


def ffmpeg_help() -> str:
    if IS_WINDOWS:
        return "Recordings need ffmpeg: open PowerShell, run  winget install ffmpeg  and restart Blendbook."
    return "Recordings need ffmpeg: install Homebrew (brew.sh), run  brew install ffmpeg  and restart Blendbook."
