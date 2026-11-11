"""Moving a pre-1.0 install's data must happen once and never overwrite anything."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = self.root / "repo"
        (self.old / ".cache").mkdir(parents=True)
        (self.old / ".cache" / "blendbook.db").write_text("mine")
        (self.old / "Recordings").mkdir()
        (self.old / "Recordings" / "set.webm").write_text("audio")
        self.data, self.rec = self.root / "new" / "data", self.root / "new" / "rec"
        env = {k: v for k, v in os.environ.items() if k != "BLENDBOOK_DATA"}
        for p in (mock.patch.object(paths, "DATA_DIR", self.data), mock.patch.object(paths, "RECORDINGS_DIR", self.rec),
                  mock.patch.dict(os.environ, env, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_moves_data_once(self):
        self.assertEqual(len(paths.migrate_legacy(self.old)), 2)
        self.assertEqual((self.data / "blendbook.db").read_text(), "mine")
        self.assertEqual((self.rec / "set.webm").read_text(), "audio")
        self.assertFalse((self.old / ".cache").exists())
        self.assertEqual(paths.migrate_legacy(self.old), [])

    def test_never_overwrites_existing_data(self):
        self.data.mkdir(parents=True)
        (self.data / "blendbook.db").write_text("newer")
        paths.migrate_legacy(self.old)
        self.assertEqual((self.data / "blendbook.db").read_text(), "newer")
        self.assertEqual((self.old / ".cache" / "blendbook.db").read_text(), "mine")

    def test_custom_data_folder_is_left_alone(self):
        with mock.patch.dict(os.environ, {"BLENDBOOK_DATA": str(self.data)}):
            self.assertEqual(paths.migrate_legacy(self.old), [])
        self.assertTrue((self.old / ".cache").exists())


class LocationTests(unittest.TestCase):
    def test_windows_defaults(self):
        with mock.patch.object(paths, "IS_WINDOWS", True), mock.patch.dict(os.environ, {"APPDATA": "C:/Users/dj/AppData/Roaming"}):
            self.assertEqual(paths._default_rb_dir(), Path("C:/Users/dj/AppData/Roaming/Pioneer/rekordbox"))
            self.assertEqual(paths._default_data_dir(), Path("C:/Users/dj/AppData/Roaming/Blendbook"))

    def test_finds_tools_outside_path(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ffmpeg").write_text("")
            with mock.patch.object(paths, "_TOOL_DIRS", [Path(d)]), mock.patch.object(paths, "IS_WINDOWS", False):
                self.assertEqual(paths.tool("ffmpeg"), str(Path(d) / "ffmpeg"))


if __name__ == "__main__":
    unittest.main()
