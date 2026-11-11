"""
Request guards and response headers shared by Blendbook's two servers.

The laptop server listens on 127.0.0.1 only, but any web page open in the same browser can still send it requests.
Two attacks matter:
- Cross-site requests: a page on evil.example POSTs to http://localhost:8080/api/... (no CORS needed for a "simple"
  request). Blocked by requiring JSON plus a same-origin Origin / Sec-Fetch-Site on every POST.
- DNS rebinding: evil.example re-points its own name at 127.0.0.1, so its scripts become "same origin" with
  Blendbook and can read every API. Blocked by only answering requests whose Host is localhost / 127.0.0.1 / [::1].
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

LOCAL_NAMES = ("localhost", "127.0.0.1", "[::1]")
_INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
_csp_cache: dict = {}


def local_hosts(port: int) -> set[str]:
    return {f"{name}:{port}" for name in LOCAL_NAMES}


def allowed_host(host: str | None, port: int) -> bool:
    return (host or "").strip().lower() in local_hosts(port)


def same_origin_post(headers, port: int) -> str | None:
    """Why a POST must be refused, or None if it came from Blendbook's own pages (or a non-browser client)."""
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return "Send JSON (Content-Type: application/json)."
    site = headers.get("Sec-Fetch-Site")
    if site and site not in ("same-origin", "none"):
        return "Cross-site request blocked."
    origin = headers.get("Origin")
    # browsers always send Origin on POST; curl and the tests don't, and can't be lured by a web page
    if origin is not None and origin.strip().lower() not in {f"http://{h}" for h in local_hosts(port)}:
        return "Cross-site request blocked."
    return None


def content_security_policy(web_dir: Path) -> str:
    """Only Blendbook's own scripts run: files from this server, plus the exact inline scripts in its pages."""
    pages = sorted(web_dir.glob("*.html"))
    key = tuple((p.name, p.stat().st_mtime) for p in pages)
    if _csp_cache.get("key") != key:
        hashes = set()
        for p in pages:
            for body in _INLINE_SCRIPT.findall(p.read_text(encoding="utf-8")):
                digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
                hashes.add(f"'sha256-{digest}'")
        _csp_cache.update(key=key, value="; ".join([
            "default-src 'self'",
            " ".join(["script-src 'self'", *sorted(hashes)]),
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "media-src 'self' blob: https://audio-ssl.itunes.apple.com",  # 30 s previews, streamed (online.py)
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]))
    return _csp_cache["value"]


def security_headers(web_dir: Path) -> list[tuple[str, str]]:
    return [
        ("Content-Security-Policy", content_security_policy(web_dir)),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),  # no clickjacking "Send to rekordbox" from a framing page
        ("Referrer-Policy", "no-referrer"),
        ("Cross-Origin-Resource-Policy", "same-origin"),  # other sites can't embed the API or recordings
        ("Permissions-Policy", "camera=(), geolocation=(), payment=(), usb=()"),
    ]
