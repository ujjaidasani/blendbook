"""
The optional check for a newer Blendbook: off until you turn it on in Settings, and then at most once a day.
It asks GitHub for the latest release and sends nothing about you or your library.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request

import online

LATEST_URL = "https://api.github.com/repos/ujjaidasani/blendbook/releases/latest"
RELEASES_PREFIX = "https://github.com/ujjaidasani/blendbook/releases/"
CHECK_EVERY_SEC = 86400

_last: dict = {"at": 0.0, "result": None}


def version_tuple(v: str | None) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,5})", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer_release(release: dict, current: str) -> dict | None:
    """The release, if it's newer than this copy. The answer is untrusted: only a plain version and a link to this
    project's own releases page get through."""
    if not isinstance(release, dict):
        return None
    tag, url = release.get("tag_name"), release.get("html_url")
    found, have = version_tuple(tag if isinstance(tag, str) else None), version_tuple(current)
    if not found or not have or found <= have:
        return None
    if not isinstance(url, str) or not url.startswith(RELEASES_PREFIX):
        return None
    return {"version": ".".join(map(str, found)), "url": url}


def check(current: str) -> dict | None:
    """The newer release, if any. Failing quietly is right: being offline or rate limited isn't worth a message."""
    if time.time() - _last["at"] < CHECK_EVERY_SEC:
        return _last["result"]
    _last["at"] = time.time()
    req = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Blendbook/{current}"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=online._ssl_context()) as r:
            _last["result"] = newer_release(json.loads(r.read(200_000)), current)
    except Exception:
        _last["result"] = None
    return _last["result"]
