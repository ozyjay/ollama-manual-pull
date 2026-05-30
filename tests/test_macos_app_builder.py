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

            executable = app_path / "Contents" / "MacOS" / "Ollama Manual Pull"
            info_plist = app_path / "Contents" / "Info.plist"
            resources = app_path / "Contents" / "Resources"

            self.assertEqual(app_path, output_dir / "Ollama Manual Pull.app")
            self.assertTrue(executable.is_file())
            self.assertTrue(executable.stat().st_mode & 0o111)
            self.assertEqual(executable.read_bytes()[:4], b"\xcf\xfa\xed\xfe")
            self.assertTrue((resources / "NativeApp.m").is_file())
            self.assertTrue((resources / "AppIcon.icns").is_file())
            self.assertEqual((resources / "AppIcon.icns").read_bytes()[:4], b"icns")
            self.assertTrue((resources / "AppIcon.svg").is_file())
            self.assertTrue((resources / "src" / "ollama_manual_pull" / "server.py").is_file())
            self.assertTrue((resources / "src" / "ollama_manual_pull" / "web" / "app.js").is_file())
            self.assertTrue((resources / "README.md").is_file())
            self.assertTrue((resources / "LICENSE").is_file())

            plist = plistlib.loads(info_plist.read_bytes())
            self.assertEqual(plist["CFBundleName"], "Ollama Manual Pull")
            self.assertEqual(plist["CFBundleExecutable"], "Ollama Manual Pull")
            self.assertEqual(plist["CFBundleIconFile"], "AppIcon")

            source_text = (resources / "NativeApp.m").read_text()
            self.assertIn("/Users/example/.pyenv/versions/3.12.13/bin/python3", source_text)
            self.assertIn("WKWebView", source_text)
            self.assertIn("NSTask", source_text)
            self.assertIn("ollama_manual_pull.server", source_text)
            self.assertIn("create_server(('127.0.0.1', 0)", source_text)
            self.assertLess(source_text.index("[self createWindow];"), source_text.index("[self startServer];"))
            self.assertNotIn("webbrowser", source_text)
            self.assertNotIn("run_web", source_text)

    def test_install_app_copies_bundle_to_applications_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_path = build_macos_app.build_app(
                output_dir=root / "dist",
                python_executable=Path("/Users/example/.pyenv/versions/3.12.13/bin/python3"),
            )
            applications_dir = root / "Applications"
            stale_app = applications_dir / "Ollama Manual Pull.app"
            stale_app.mkdir(parents=True)
            (stale_app / "stale.txt").write_text("old")

            installed = build_macos_app.install_app(app_path, applications_dir=applications_dir)

            self.assertEqual(installed, applications_dir / "Ollama Manual Pull.app")
            self.assertTrue((installed / "Contents" / "Info.plist").is_file())
            self.assertTrue((installed / "Contents" / "Resources" / "AppIcon.icns").is_file())
            self.assertTrue((installed / "Contents" / "MacOS" / "Ollama Manual Pull").is_file())
            self.assertFalse((installed / "stale.txt").exists())


if __name__ == "__main__":
    unittest.main()
