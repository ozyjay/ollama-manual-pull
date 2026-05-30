import plistlib
import tempfile
import unittest
from pathlib import Path

from scripts import build_macos_app


class MacOSAppBuilderTests(unittest.TestCase):
    def test_build_app_creates_pyenv_aware_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            app_path = build_macos_app.build_app(
                output_dir=output_dir,
                python_executable=Path("/Users/example/.pyenv/versions/3.12.13/bin/python3"),
            )

            launcher = app_path / "Contents" / "MacOS" / "Ollama Manual Pull"
            info_plist = app_path / "Contents" / "Info.plist"
            resources = app_path / "Contents" / "Resources"

            self.assertEqual(app_path, output_dir / "Ollama Manual Pull.app")
            self.assertTrue(launcher.is_file())
            self.assertTrue(launcher.stat().st_mode & 0o111)
            self.assertTrue((resources / "src" / "ollama_manual_pull" / "server.py").is_file())
            self.assertTrue((resources / "src" / "ollama_manual_pull" / "web" / "app.js").is_file())
            self.assertTrue((resources / "README.md").is_file())
            self.assertTrue((resources / "LICENSE").is_file())

            plist = plistlib.loads(info_plist.read_bytes())
            self.assertEqual(plist["CFBundleName"], "Ollama Manual Pull")
            self.assertEqual(plist["CFBundleExecutable"], "Ollama Manual Pull")

            launcher_text = launcher.read_text()
            self.assertIn("/Users/example/.pyenv/versions/3.12.13/bin/python3", launcher_text)
            self.assertIn("PYTHONPATH", launcher_text)
            self.assertIn("ollama_manual_pull", launcher_text)
            self.assertIn("PYENV_ROOT", launcher_text)


if __name__ == "__main__":
    unittest.main()
