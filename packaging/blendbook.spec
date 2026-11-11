# PyInstaller build for the Mac app and the Windows app. Run from the repo root:
#   pyinstaller --noconfirm packaging/blendbook.spec
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'VERSION = "(.+?)"', (ROOT / "server.py").read_text(encoding="utf-8")).group(1)

datas = [(str(ROOT / "web"), "web")]
binaries = []
hiddenimports = []
for pkg in ("pyrekordbox", "sqlcipher3", "anthropic", "truststore", "pystray"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis([str(ROOT / "app.py")], pathex=[str(ROOT)], datas=datas, binaries=binaries, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "pytest", "IPython"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Blendbook", console=False,
          icon=str(ROOT / "packaging" / ("icon.ico" if sys.platform == "win32" else "Blendbook.icns")))
coll = COLLECT(exe, a.binaries, a.datas, name="Blendbook")

if sys.platform == "darwin":
    app = BUNDLE(coll, name="Blendbook.app", icon=str(ROOT / "packaging" / "Blendbook.icns"),
                 bundle_identifier="app.blendbook.Blendbook", version=VERSION,
                 info_plist={
                     "CFBundleName": "Blendbook",
                     "CFBundleDisplayName": "Blendbook",
                     "CFBundleShortVersionString": VERSION,
                     "LSMinimumSystemVersion": "12.0",
                     "LSUIElement": True,  # a menu bar app: the interface is the browser page
                     "NSHumanReadableCopyright": "MIT License",
                 })
