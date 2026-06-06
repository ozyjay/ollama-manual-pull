import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
            source_dir = resources / "macos" / "OllamaManualPull"
            self.assertTrue((source_dir / "OllamaManualPullApp.swift").is_file())
            self.assertTrue((source_dir / "AppConfig.swift").is_file())
            self.assertTrue((source_dir / "AppStore.swift").is_file())
            self.assertTrue((source_dir / "ContentView.swift").is_file())
            self.assertFalse((resources / "NativeApp.swift").exists())
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

            combined_source = "\n".join(path.read_text() for path in sorted(source_dir.rglob("*.swift")))
            self.assertIn("/Users/example/.pyenv/versions/3.12.13/bin/python3", combined_source)
            self.assertIn("@main", combined_source)
            self.assertIn("CommandGroup", combined_source)
            self.assertIn(".keyboardShortcut(\"q\"", combined_source)
            self.assertIn("BottomCommandBar", combined_source)
            self.assertIn('items.filter { $0.status != "running" }', combined_source)
            self.assertIn("ForEach(listedItems)", combined_source)
            self.assertIn("store.selectedId == activeItem.id", combined_source)
            self.assertIn("store.selectedId = activeItem.id", combined_source)
            self.assertIn("isRefreshing", combined_source)
            self.assertIn("NSHostingView", combined_source)
            self.assertIn("URLSession", combined_source)
            self.assertIn("Process", combined_source)
            self.assertIn("ollama_manual_pull.server", combined_source)
            self.assertIn("create_server(('127.0.0.1', 0)", combined_source)
            self.assertIn("func refreshState(showIndicator: Bool = true) async", combined_source)
            self.assertIn("@Published private(set) var isRefreshing = false", combined_source)
            self.assertIn("private var refreshInFlight = false", combined_source)
            self.assertIn("private var refreshPending = false", combined_source)
            self.assertIn("if refreshInFlight", combined_source)
            self.assertIn("refreshPending = true", combined_source)
            self.assertIn("while refreshPending", combined_source)
            self.assertIn("await self?.refreshState(showIndicator: false)", combined_source)
            self.assertIn("clearStateRefreshError()", combined_source)
            self.assertIn('appError?.hasPrefix("State refresh failed:")', combined_source)
            self.assertIn("clearActionError(prefix: \"Search failed:\")", combined_source)
            self.assertIn("clearActionError(prefix: \"Queue failed:\")", combined_source)
            self.assertIn("clearActionError(prefix: \"Start failed:\")", combined_source)
            self.assertIn("clearActionError(prefix: \"Pause failed:\")", combined_source)
            self.assertIn("clearActionError(prefix: \"Retry failed:\")", combined_source)
            self.assertIn("clearActionError(prefix: \"Remove failed:\")", combined_source)
            self.assertNotIn("appError = nil\n            await refreshState()", combined_source)
            self.assertIn("try await Task.sleep(nanoseconds: 1_000_000_000)", combined_source)
            self.assertNotIn("try? await Task.sleep", combined_source)
            self.assertIn("func queue(_ model: String) async", combined_source)
            self.assertIn("deduplicated", combined_source)
            self.assertIn("installedModelRefs", combined_source)
            self.assertIn("func isDownloaded(modelRef: String) -> Bool", combined_source)
            self.assertIn("isDownloadedVariant", combined_source)
            self.assertIn("Downloaded", combined_source)
            self.assertIn("checkmark.circle.fill", combined_source)
            self.assertIn("/api/installed/remove", combined_source)
            self.assertIn("func deleteInstalledModel", combined_source)
            self.assertIn("confirmationDialog", combined_source)
            self.assertIn("Delete Model", combined_source)
            self.assertIn("enum AppSection", combined_source)
            self.assertIn("if let task = serverTask, task.isRunning", combined_source)
            self.assertIn("private var didEmitServerURL = false", combined_source)
            self.assertIn("didEmitServerURL = true", combined_source)
            self.assertIn("resetProcessState()", combined_source)
            self.assertIn("private var processGeneration = 0", combined_source)
            self.assertIn("processGeneration += 1", combined_source)
            self.assertIn("appendServerOutput(chunk, generation: generation)", combined_source)
            self.assertIn("guard generation == processGeneration else { return }", combined_source)
            self.assertIn("var environment = ProcessInfo.processInfo.environment", combined_source)
            self.assertIn("task.environment = environment", combined_source)
            self.assertIn("guard !didEmitServerURL else { return }", combined_source)
            self.assertEqual(combined_source.count("decode(APIErrorBody.self"), 1)
            self.assertIn("private static let dateFormatter", combined_source)
            self.assertNotIn("WKWebView", combined_source)
            self.assertNotIn("WebKit", combined_source)
            self.assertNotIn("webbrowser", combined_source)
            self.assertNotIn("run_web", combined_source)

    def test_build_app_compiles_nested_swift_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "OllamaManualPull"
            output_dir = root / "dist"
            shutil.copytree(build_macos_app.NATIVE_APP_SOURCE_DIR, source_root)
            nested_dir = source_root / "Support"
            nested_dir.mkdir()
            (nested_dir / "NestedCompileCheck.swift").write_text(
                'enum NestedCompileCheck { static let value = "nested" }\n'
            )
            with (source_root / "ContentView.swift").open("a") as source:
                source.write("\nprivate let nestedCompileCheck = NestedCompileCheck.value\n")

            original_source_dir = build_macos_app.NATIVE_APP_SOURCE_DIR
            build_macos_app.NATIVE_APP_SOURCE_DIR = source_root
            try:
                app_path = build_macos_app.build_app(
                    output_dir=output_dir,
                    python_executable=Path("/Users/example/.pyenv/versions/3.12.13/bin/python3"),
                )
            finally:
                build_macos_app.NATIVE_APP_SOURCE_DIR = original_source_dir

            bundled_nested_source = (
                app_path
                / "Contents"
                / "Resources"
                / "macos"
                / "OllamaManualPull"
                / "Support"
                / "NestedCompileCheck.swift"
            )
            self.assertTrue(bundled_nested_source.is_file())

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

    def test_main_prompts_for_admin_install_when_applications_copy_is_denied(self):
        app_path = Path("/tmp/Ollama Manual Pull.app")
        installed_path = Path("/Applications/Ollama Manual Pull.app")

        with mock.patch.object(build_macos_app, "build_app", return_value=app_path), \
            mock.patch.object(build_macos_app, "install_app", side_effect=PermissionError("denied")), \
            mock.patch.object(
                build_macos_app,
                "install_app_with_admin_prompt",
                return_value=installed_path,
            ) as install_with_admin:
            result = build_macos_app.main(["--install"])

        self.assertEqual(result, 0)
        install_with_admin.assert_called_once_with(
            app_path,
            applications_dir=Path("/Applications"),
        )


if __name__ == "__main__":
    unittest.main()
