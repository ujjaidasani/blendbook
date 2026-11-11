"""Start a built Blendbook, check it answers and reads as Blendbook, then quit it from its own API. Used by CI."""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

exe, port = sys.argv[1], 8099
base = f"http://localhost:{port}"

subprocess.run([exe, "--self-test"], check=True, timeout=120)

with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, "PORT": str(port), "BLENDBOOK_DATA": os.path.join(tmp, "data"),
           "BLENDBOOK_RECORDINGS": os.path.join(tmp, "rec"), "REKORDBOX_DIR": os.path.join(tmp, "none")}
    app = subprocess.Popen([exe, "--no-browser"], env=env)
    try:
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"{base}/api/status", timeout=2) as r:
                    status = json.loads(r.read())
                break
            except OSError:
                time.sleep(0.5)
        else:
            sys.exit("The app didn't start within a minute.")
        assert status["found"] is False and status["version"], status
        with urllib.request.urlopen(f"{base}/", timeout=5) as r:
            assert b"Blendbook" in r.read()
        req = urllib.request.Request(f"{base}/api/quit", data=b"{}", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        app.wait(timeout=30)
        print("smoke test ok")
    finally:
        if app.poll() is None:
            app.kill()
