#!/usr/bin/env python3
"""
Start Blendbook and open it in the browser. The entry point of the Mac app, the Windows app and the start scripts.

- If Blendbook is already running, just opens it again.
- Uses port 8080, or the next free one if another app has it.
- In the packaged app there's no terminal or window: output goes to server.log in the data folder, and a menu bar /
  system tray icon opens the page and quits (so does Settings in the page).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import paths

PORTS = [int(os.environ["PORT"])] if os.environ.get("PORT") else [8080, *range(8082, 8090)]


def is_blendbook(port: int) -> bool:
    """Only Blendbook's own status answer counts: another app on the port must not be opened by mistake."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=2) as r:
            return "rekordbox_dir" in json.loads(r.read())
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def open_browser(url: str) -> None:
    """Chrome or Edge give the full experience (controller, pop-out mini view, recording); otherwise the default."""
    if paths.IS_MAC:
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser"):
            if os.path.isdir(f"/Applications/{app}.app"):
                subprocess.run(["open", "-a", app, url], check=False)
                return
    elif paths.IS_WINDOWS:
        for env, rel in (("PROGRAMFILES", r"Google\Chrome\Application\chrome.exe"),
                         ("PROGRAMFILES(X86)", r"Google\Chrome\Application\chrome.exe"),
                         ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
                         ("PROGRAMFILES(X86)", r"Microsoft\Edge\Application\msedge.exe"),
                         ("PROGRAMFILES", r"Microsoft\Edge\Application\msedge.exe")):
            exe = os.path.join(os.environ.get(env, ""), rel)
            if os.path.isfile(exe):
                subprocess.Popen([exe, url])
                return
    webbrowser.open(url)


def run_with_tray(server, port: int) -> None:
    """Keep a menu bar / tray icon on the main thread (macOS needs that) and the server on a background one."""
    import pystray
    from PIL import Image

    url = f"http://localhost:{port}"
    icon = pystray.Icon("Blendbook", Image.open(paths.WEB_DIR / "img" / "tray.png"), "Blendbook", pystray.Menu(
        pystray.MenuItem("Open Blendbook", lambda: open_browser(url), default=True),
        pystray.MenuItem("Quit Blendbook", lambda: server.shutdown())))

    def serve():
        try:
            server.serve(port)
        finally:
            icon.stop()  # also when you quit from the page

    threading.Thread(target=serve, daemon=True).start()
    icon.run()


def self_test() -> None:
    """Checks a build can load everything it needs (imports that only happen later, with a library or a key)."""
    import anthropic  # noqa: F401
    import numpy  # noqa: F401
    import pyrekordbox.masterdb  # noqa: F401
    import sqlcipher3  # noqa: F401
    import truststore  # noqa: F401

    assert (paths.WEB_DIR / "index.html").is_file() and (paths.WEB_DIR / "img" / "tray.png").is_file()
    print("self-test ok", flush=True)


def main() -> None:
    if "--self-test" in sys.argv:
        return self_test()
    if paths.FROZEN or sys.stdout is None:  # no terminal: keep a log people can send with a bug report
        paths.ensure_data_dir()
        log = open(paths.LOG_PATH, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log
    for port in PORTS:
        if is_blendbook(port):
            open_browser(f"http://localhost:{port}")
            return
        if port_free(port):
            break
    else:
        print(f"Ports {PORTS[0]}–{PORTS[-1]} are all taken by other apps. Set PORT to a free one.")
        sys.exit(1)

    paths.migrate_legacy()  # before anything creates the data folder, and before server opens the database there
    paths.ensure_data_dir()
    import server  # opens the database: only once we know we're the one running

    def open_when_ready():
        for _ in range(60):
            if is_blendbook(port):
                open_browser(f"http://localhost:{port}")
                return
            time.sleep(0.5)

    if "--no-browser" not in sys.argv:
        threading.Thread(target=open_when_ready, daemon=True).start()
    if paths.FROZEN or "--tray" in sys.argv:
        try:
            return run_with_tray(server, port)
        except ImportError as e:  # the app still works without the icon: Settings has Quit
            print(f"No tray icon: {e}", flush=True)
    server.serve(port)


if __name__ == "__main__":
    main()
