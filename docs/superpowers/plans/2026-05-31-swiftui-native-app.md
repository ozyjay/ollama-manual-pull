# SwiftUI Native App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Maintain the completed SwiftUI native app migration while keeping the Python downloader engine and fixing duplicate queue entries, unreachable pause controls, refresh churn, and standard macOS commands.

**Architecture:** Python remains the queue and downloader engine behind the local HTTP API. SwiftUI becomes a real multi-file macOS app under `macos/OllamaManualPull/`, with typed models, an API client, a process supervisor, an app store, fixed command bar layout, and menu commands.

**Tech Stack:** Python 3.10+, `unittest`, Swift 5, SwiftUI, AppKit, Foundation, `swiftc`, local HTTP API.

---

## File Structure

- Modify `src/ollama_manual_pull/queue.py`: add canonical model references and duplicate queue detection.
- Modify `tests/test_queue.py`: add duplicate-prevention tests.
- Modify `tests/test_server.py`: verify `/api/queue` exposes `deduplicated: true` when reusing an existing item.
- Create `macos/OllamaManualPull/OllamaManualPullApp.swift`: SwiftUI `App` entry point and command menu definitions.
- Create `macos/OllamaManualPull/PythonServerSupervisor.swift`: starts, observes, and terminates the bundled Python server process.
- Create `macos/OllamaManualPull/Models.swift`: decodable API payloads and lightweight computed properties.
- Create `macos/OllamaManualPull/APIClient.swift`: typed local API client.
- Create `macos/OllamaManualPull/AppStore.swift`: observable state, refresh loop, selection rules, and queue actions.
- Create `macos/OllamaManualPull/Formatters.swift`: date, byte, ETA, and status helpers.
- Create `macos/OllamaManualPull/ContentView.swift`: top-level three-region app layout with fixed bottom command bar.
- Create `macos/OllamaManualPull/SidebarView.swift`: section navigation.
- Create `macos/OllamaManualPull/SearchView.swift`: search and direct add controls.
- Create `macos/OllamaManualPull/InstalledModelsView.swift`: installed model list.
- Create `macos/OllamaManualPull/QueueView.swift`: active download and stable queue list.
- Create `macos/OllamaManualPull/QueueRowView.swift`: queue row presentation.
- Create `macos/OllamaManualPull/InspectorView.swift`: selected item details.
- Modify `scripts/build_macos_app.py`: copy and compile all Swift app sources, inject the Python executable into a generated Swift config file, and avoid the old generated resource-file app source.
- Modify `tests/test_macos_app_builder.py`: assert the multi-file app source layout, fixed command bar, commands, and resource packaging.
- Remove the legacy generated Swift template after the new app sources compile and tests no longer reference it.

## Task 1: Add Queue Canonicalization And Duplicate Prevention

**Files:**
- Modify: `src/ollama_manual_pull/queue.py`
- Modify: `tests/test_queue.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing queue tests**

Add these tests to `tests/test_queue.py` before the pause test:

```python
    def test_add_returns_existing_waiting_item_for_duplicate_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=lambda *args, **kwargs: None)

            first = queue.add("qwen3-coder:30b")
            second = queue.add("qwen3-coder:30b")
            snapshot = queue.snapshot()

        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(snapshot["items"]), 1)

    def test_add_deduplicates_implicit_latest_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=lambda *args, **kwargs: None)

            first = queue.add("qwen3-coder")
            second = queue.add("qwen3-coder:latest")
            snapshot = queue.snapshot()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["canonical_model"], "qwen3-coder:latest")
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(snapshot["items"]), 1)

    def test_add_returns_failed_duplicate_without_adding_row(self):
        def fake_pull(model, **kwargs):
            raise RuntimeError("download broke")

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            first = queue.add("broken:latest")
            queue.start()
            self.assertTrue(queue.wait_until_idle(2))

            second = queue.add("broken")
            snapshot = queue.snapshot()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["status"], "failed")
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(snapshot["items"]), 1)
```

- [ ] **Step 2: Write failing server API test**

Add this test to `tests/test_server.py`:

```python
    def test_queue_route_reports_deduplicated_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            first_status, first = self.request_json(
                f"{base_url}/api/queue",
                method="POST",
                body={"model": "qwen3-coder"},
            )
            second_status, second = self.request_json(
                f"{base_url}/api/queue",
                method="POST",
                body={"model": "qwen3-coder:latest"},
            )
            state_status, state = self.request_json(f"{base_url}/api/state")

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(state_status, 200)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(second["canonical_model"], "qwen3-coder:latest")
        self.assertEqual(len(state["items"]), 1)
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_queue tests.test_server -v
```

Expected: failures because queue items do not yet include `canonical_model` or `deduplicated`, and duplicates still append new rows.

- [ ] **Step 4: Implement canonical references**

In `src/ollama_manual_pull/queue.py`, add:

```python
def canonical_model_ref(model: str) -> str:
    ref = parse_model_ref(model)
    name = f"{ref.name}:{ref.tag}"
    return name if ref.namespace == "library" else f"{ref.namespace}/{name}"
```

In `DownloadQueue.add`, compute `canonical = canonical_model_ref(model)` before creating the item.

- [ ] **Step 5: Return existing duplicate items**

Inside the `with self._condition:` block in `DownloadQueue.add`, before creating a new item, add:

```python
            for existing in self._items:
                if existing.get("canonical_model") != canonical:
                    continue
                if existing["status"] in {"waiting", "running", "completed", "failed"}:
                    copied = self._copy_item(existing)
                    copied["deduplicated"] = True
                    return copied
```

When creating a new item, include:

```python
            "canonical_model": canonical,
            "deduplicated": False,
```

Ensure `_copy_item` preserves both public fields because they do not start with `_`.

- [ ] **Step 6: Run tests to verify pass**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_queue tests.test_server -v
```

Expected: all queue and server tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/ollama_manual_pull/queue.py tests/test_queue.py tests/test_server.py
git commit -m "Prevent duplicate queued downloads"
```

## Task 2: Prepare Builder For Multi-File Swift Sources

**Files:**
- Modify: `scripts/build_macos_app.py`
- Modify: `tests/test_macos_app_builder.py`
- Create: `macos/OllamaManualPull/AppConfig.swift.in`

- [ ] **Step 1: Write failing builder tests**

In `tests/test_macos_app_builder.py`, replace assertions that expect the old generated resource-file app source with assertions for the source directory:

```python
            source_dir = resources / "macos" / "OllamaManualPull"
            self.assertTrue((source_dir / "OllamaManualPullApp.swift").is_file())
            self.assertTrue((source_dir / "AppConfig.swift").is_file())
            self.assertTrue((source_dir / "AppStore.swift").is_file())
            self.assertTrue((source_dir / "ContentView.swift").is_file())
            self.assertFalse(any(path.name == "Native" + "App.swift" for path in resources.glob("*.swift")))
```

Replace the old `source_text` checks with:

```python
            combined_source = "\n".join(path.read_text() for path in sorted(source_dir.glob("*.swift")))
            self.assertIn("/Users/example/.pyenv/versions/3.12.13/bin/python3", combined_source)
            self.assertIn("@main", combined_source)
            self.assertIn("CommandGroup", combined_source)
            self.assertIn(".keyboardShortcut(\"q\"", combined_source)
            self.assertIn("BottomCommandBar", combined_source)
            self.assertIn("isRefreshing", combined_source)
            self.assertIn("NSHostingView", combined_source)
            self.assertIn("URLSession", combined_source)
            self.assertIn("Process", combined_source)
            self.assertIn("ollama_manual_pull.server", combined_source)
            self.assertIn("create_server(('127.0.0.1', 0)", combined_source)
            self.assertNotIn("WKWebView", combined_source)
            self.assertNotIn("WebKit", combined_source)
            self.assertNotIn("webbrowser", combined_source)
            self.assertNotIn("run_web", combined_source)
```

- [ ] **Step 2: Run builder test to verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_macos_app_builder -v
```

Expected: failure because `macos/OllamaManualPull` does not exist and the builder still writes the old generated resource-file app source.

- [ ] **Step 3: Add Swift config template**

Create `macos/OllamaManualPull/AppConfig.swift.in`:

```swift
import Foundation

enum AppConfig {
    static let bundledPython = %%PYTHON_EXECUTABLE%%
    static let serverCommand = "from ollama_manual_pull.server import create_server; from ollama_manual_pull.core import default_models_dir; httpd = create_server(('127.0.0.1', 0), models_dir=default_models_dir()); host, port = httpd.server_address; print(f'URL=http://{host}:{port}/', flush=True); httpd.serve_forever()"
}
```

- [ ] **Step 4: Update builder constants**

In `scripts/build_macos_app.py`, replace the legacy generated-template constant with:

```python
NATIVE_APP_SOURCE_DIR = PROJECT_ROOT / "macos" / "OllamaManualPull"
```

- [ ] **Step 5: Copy Swift sources into resources**

Replace the `native_source = ...` block in `build_app` with:

```python
    native_source_dir = resources / "macos" / "OllamaManualPull"
    _write_native_sources(native_source_dir, python_executable)
    _compile_native_app(sorted(native_source_dir.glob("*.swift")), macos / APP_NAME)
```

Add:

```python
def _write_native_sources(destination: Path, python_executable: Path) -> None:
    shutil.copytree(NATIVE_APP_SOURCE_DIR, destination, ignore=shutil.ignore_patterns("*.in"))
    template = (NATIVE_APP_SOURCE_DIR / "AppConfig.swift.in").read_text()
    rendered = template.replace("%%PYTHON_EXECUTABLE%%", _swift_string_literal(python_executable))
    (destination / "AppConfig.swift").write_text(rendered)
```

- [ ] **Step 6: Compile multiple Swift files**

Change `_compile_native_app` to:

```python
def _compile_native_app(sources: list[Path], executable: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="ollama-manual-pull-swift-cache-") as module_cache:
        subprocess.run(
            [
                "swiftc",
                *[str(source) for source in sources],
                "-o",
                str(executable),
                "-parse-as-library",
                "-module-cache-path",
                module_cache,
                "-framework",
                "AppKit",
                "-framework",
                "SwiftUI",
            ],
            check=True,
        )
```

- [ ] **Step 7: Run builder test to verify current expected failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_macos_app_builder -v
```

Expected: failure because required Swift source files are not created yet.

- [ ] **Step 8: Commit**

```bash
git add scripts/build_macos_app.py tests/test_macos_app_builder.py macos/OllamaManualPull/AppConfig.swift.in
git commit -m "Prepare macOS builder for Swift source tree"
```

## Task 3: Add Typed Swift Models, API Client, And Server Supervisor

**Files:**
- Create: `macos/OllamaManualPull/Models.swift`
- Create: `macos/OllamaManualPull/APIClient.swift`
- Create: `macos/OllamaManualPull/PythonServerSupervisor.swift`
- Create: `macos/OllamaManualPull/Formatters.swift`

- [ ] **Step 1: Create `Models.swift`**

Create:

```swift
import Foundation

struct AppSnapshot: Decodable {
    let running: Bool
    let pauseRequested: Bool
    let modelsDir: String
    let registry: String
    let retries: Int
    let installedModels: [InstalledModel]
    let items: [QueueItem]

    enum CodingKeys: String, CodingKey {
        case running
        case pauseRequested = "pause_requested"
        case modelsDir = "models_dir"
        case registry
        case retries
        case installedModels = "installed_models"
        case items
    }
}

struct InstalledModel: Decodable, Identifiable {
    let name: String
    let namespace: String?
    var id: String { "\(namespace ?? "library")/\(name)" }
}

struct QueueItem: Decodable, Identifiable {
    let id: String
    let model: String
    let canonicalModel: String?
    let deduplicated: Bool?
    let status: String
    let error: String?
    let currentBlob: String?
    let messages: [String]
    let progress: DownloadProgress
    let createdAt: Double
    let updatedAt: Double

    enum CodingKeys: String, CodingKey {
        case id
        case model
        case canonicalModel = "canonical_model"
        case deduplicated
        case status
        case error
        case currentBlob = "current_blob"
        case messages
        case progress
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct DownloadProgress: Decodable {
    let phase: String
    let overall: ProgressAmount
    let currentFile: ProgressAmount?

    enum CodingKeys: String, CodingKey {
        case phase
        case overall
        case currentFile = "current_file"
    }
}

struct ProgressAmount: Decodable {
    let digest: String?
    let downloaded: Double?
    let total: Double?
    let percent: Double?
    let bytesPerSecond: Double?
    let etaSeconds: Double?
    let line: String?

    enum CodingKeys: String, CodingKey {
        case digest
        case downloaded
        case total
        case percent
        case bytesPerSecond = "bytes_per_second"
        case etaSeconds = "eta_seconds"
        case line
    }
}

struct SearchResponse: Decodable {
    let available: Bool
    let results: [SearchResult]
    let error: String?
}

struct SearchResult: Decodable, Identifiable {
    let name: String?
    let heading: String?
    let description: String?
    let tags: [String]?
    let variants: [SearchVariant]?

    var id: String {
        "\(name ?? "")|\(heading ?? "")|\(description ?? "")"
    }

    var queueableName: String {
        name ?? heading ?? ""
    }
}

struct SearchVariant: Decodable, Identifiable {
    let name: String
    let label: String?

    var id: String { name }

    init(from decoder: Decoder) throws {
        let single = try decoder.singleValueContainer()
        if let value = try? single.decode(String.self) {
            name = value
            label = value.split(separator: ":", maxSplits: 1).last.map(String.init)
            return
        }
        let keyed = try decoder.container(keyedBy: CodingKeys.self)
        name = try keyed.decode(String.self, forKey: .name)
        label = try keyed.decodeIfPresent(String.self, forKey: .label)
    }

    enum CodingKeys: String, CodingKey {
        case name
        case label
    }
}

struct APIErrorBody: Decodable {
    let error: String?
}

struct OKResponse: Decodable {
    let ok: Bool
}

struct APIError: LocalizedError {
    let message: String

    var errorDescription: String? {
        message
    }
}
```

- [ ] **Step 2: Create `APIClient.swift`**

Create a typed API client:

```swift
import Foundation

struct APIClient {
    let baseURL: URL

    func state() async throws -> AppSnapshot {
        try await request("/api/state")
    }

    func search(_ query: String) async throws -> SearchResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/search"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "q", value: query)]
        guard let url = components?.url else {
            throw APIError(message: "Could not build search URL.")
        }
        return try await request(url)
    }

    func queue(_ model: String) async throws -> QueueItem {
        try await request("/api/queue", method: "POST", json: ["model": model])
    }

    func startQueue() async throws -> AppSnapshot {
        try await request("/api/start", method: "POST")
    }

    func pauseAfterCurrent() async throws -> AppSnapshot {
        try await request("/api/pause", method: "POST")
    }

    func retry(_ item: QueueItem) async throws -> QueueItem {
        try await request("/api/retry/\(encodedPath(item.id))", method: "POST")
    }

    func remove(_ item: QueueItem) async throws -> OKResponse {
        try await request("/api/remove/\(encodedPath(item.id))", method: "POST")
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET", json: [String: String]? = nil) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIError(message: "Local app server is not ready.")
        }
        return try await request(url, method: method, json: json)
    }

    private func request<T: Decodable>(_ url: URL, method: String = "GET", json: [String: String]? = nil) async throws -> T {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            if let payload = try? JSONDecoder().decode(APIErrorBody.self, from: data), let message = payload.error {
                throw APIError(message: message)
            }
            throw APIError(message: "Request failed with HTTP \(http.statusCode).")
        }
        if let payload = try? JSONDecoder().decode(APIErrorBody.self, from: data), let message = payload.error {
            throw APIError(message: message)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func encodedPath(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? value
    }
}
```

- [ ] **Step 3: Create `PythonServerSupervisor.swift`**

Create:

```swift
import Foundation

@MainActor
final class PythonServerSupervisor: ObservableObject {
    private var serverTask: Process?
    private var outputBuffer = ""
    var onURL: ((URL) -> Void)?
    var onStartupError: ((String) -> Void)?

    func start() {
        guard let resourcesURL = Bundle.main.resourceURL else {
            onStartupError?("Could not locate bundled app resources.")
            return
        }
        let task = Process()
        let output = Pipe()
        let errorOutput = Pipe()
        task.executableURL = URL(fileURLWithPath: resolvedPython())
        task.arguments = ["-c", AppConfig.serverCommand]
        task.environment = [
            "PYTHONPATH": resourcesURL.appendingPathComponent("src").path,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        ]
        task.standardOutput = output
        task.standardError = errorOutput
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.appendServerOutput(chunk) }
        }
        errorOutput.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.onStartupError?(chunk.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        do {
            try task.run()
            serverTask = task
        } catch {
            onStartupError?("Could not start the local app server: \(error.localizedDescription)")
        }
    }

    func stop() {
        if let output = serverTask?.standardOutput as? Pipe {
            output.fileHandleForReading.readabilityHandler = nil
        }
        if let error = serverTask?.standardError as? Pipe {
            error.fileHandleForReading.readabilityHandler = nil
        }
        serverTask?.terminate()
        serverTask = nil
    }

    private func appendServerOutput(_ chunk: String) {
        outputBuffer += chunk
        guard let prefix = outputBuffer.range(of: "URL=") else { return }
        let tail = outputBuffer[prefix.upperBound...]
        guard let end = tail.firstIndex(where: { $0 == "\n" || $0 == "\r" }) else { return }
        let urlString = String(tail[..<end])
        guard let url = URL(string: urlString) else {
            onStartupError?("The local server returned an invalid URL: \(urlString)")
            return
        }
        onURL?(url)
    }

    private func resolvedPython() -> String {
        let manager = FileManager.default
        if manager.isExecutableFile(atPath: AppConfig.bundledPython) {
            return AppConfig.bundledPython
        }
        let home = NSHomeDirectory()
        let candidates = [
            "\(home)/.pyenv/shims/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]
        return candidates.first { manager.isExecutableFile(atPath: $0) } ?? "/usr/bin/python3"
    }
}
```

- [ ] **Step 4: Create `Formatters.swift`**

Move `formatDate`, `formatBytes`, `formatEta`, and status color helpers into a simple namespace:

```swift
import SwiftUI

enum AppFormatters {
    static func date(_ seconds: Double) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .medium
        return formatter.string(from: Date(timeIntervalSince1970: seconds))
    }

    static func bytes(_ value: Double?) -> String {
        guard var size = value, size.isFinite else { return "Unknown" }
        let units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units {
            if abs(size) < 1000 || unit == units.last {
                return unit == "B" ? "\(Int(size.rounded()))B" : String(format: "%.1f%@", size, unit)
            }
            size /= 1000
        }
        return String(format: "%.1fTB", size)
    }

    static func eta(_ seconds: Double) -> String {
        let safeSeconds = max(0, Int(seconds.rounded()))
        let minutes = safeSeconds / 60
        let remaining = safeSeconds % 60
        if minutes >= 60 {
            return "\(minutes / 60)h \(minutes % 60)m"
        }
        return "\(minutes)m \(String(format: "%02d", remaining))s"
    }

    static func statusColor(_ status: String) -> Color {
        switch status {
        case "running": return .blue
        case "completed": return .green
        case "failed": return .red
        case "waiting": return .orange
        default: return .secondary
        }
    }
}
```

- [ ] **Step 5: Run compile to verify expected failure**

Run:

```bash
python3 scripts/build_macos_app.py --output-dir /private/tmp/ollama-manual-pull-plan-build
```

Expected: failure because app entry point and views are not created yet.

- [ ] **Step 6: Commit**

```bash
git add macos/OllamaManualPull/Models.swift macos/OllamaManualPull/APIClient.swift macos/OllamaManualPull/PythonServerSupervisor.swift macos/OllamaManualPull/Formatters.swift
git commit -m "Add native app model and API layers"
```

## Task 4: Add AppStore With Stable Refresh And Actions

**Files:**
- Create: `macos/OllamaManualPull/AppStore.swift`

- [ ] **Step 1: Create `AppStore.swift`**

Create the observable store:

```swift
import Foundation

@MainActor
final class AppStore: ObservableObject {
    @Published var snapshot: AppSnapshot?
    @Published var selectedId: String?
    @Published var searchText = ""
    @Published var searchResults: [SearchResult] = []
    @Published var searchStatus = ""
    @Published var appError: String?
    @Published var isSearching = false
    @Published var serverReady = false
    @Published var selectedSection: AppSection = .queue

    private var apiClient: APIClient?
    private var refreshTask: Task<Void, Never>?
    private var isRefreshing = false

    var selectedItem: QueueItem? {
        snapshot?.items.first { $0.id == selectedId }
    }

    var canStartQueue: Bool {
        snapshot?.items.contains { $0.status == "waiting" } ?? false
    }

    var canPauseQueue: Bool {
        snapshot?.running ?? false
    }

    var canRetrySelected: Bool {
        selectedItem?.status == "failed"
    }

    var canRemoveSelected: Bool {
        guard let item = selectedItem else { return false }
        return item.status != "running"
    }

    func connect(to url: URL) {
        apiClient = APIClient(baseURL: url)
        serverReady = true
        appError = nil
        startRefreshLoop()
        Task { await refreshState() }
    }

    func showStartupError(_ message: String) {
        guard !message.isEmpty else { return }
        appError = message
    }

    func stopRefreshLoop() {
        refreshTask?.cancel()
        refreshTask = nil
    }

    func refreshState() async {
        guard let apiClient, !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let next = try await apiClient.state()
            snapshot = next
            reconcileSelection(with: next)
        } catch {
            appError = "State refresh failed: \(error.localizedDescription)"
        }
    }

    func search() async {
        let trimmed = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            searchStatus = "Enter a search term or direct model reference."
            searchResults = []
            return
        }
        guard let apiClient else {
            appError = "Local app server is still starting."
            return
        }
        isSearching = true
        searchResults = []
        searchStatus = "Searching..."
        defer { isSearching = false }
        do {
            let payload = try await apiClient.search(trimmed)
            searchResults = payload.results
            if payload.available == false, let error = payload.error {
                searchStatus = error
            } else if payload.results.isEmpty {
                searchStatus = "No matching models found."
            } else {
                searchStatus = "\(payload.results.count) official result\(payload.results.count == 1 ? "" : "s"). Choose a version to queue."
            }
            appError = nil
        } catch {
            searchResults = []
            searchStatus = ""
            appError = "Search failed: \(error.localizedDescription)"
        }
    }

    func queue(_ model: String) async {
        let trimmed = model.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            searchStatus = "Enter a model name or reference first."
            return
        }
        guard let apiClient else {
            appError = "Local app server is still starting."
            return
        }
        do {
            let item = try await apiClient.queue(trimmed)
            selectedId = item.id
            searchText = ""
            searchStatus = (item.deduplicated ?? false) ? "Already in queue: \(item.canonicalModel ?? item.model)." : "Queued \(trimmed)."
            appError = nil
            await refreshState()
        } catch {
            appError = "Queue failed: \(error.localizedDescription)"
        }
    }

    func startQueue() async {
        guard let apiClient else { return }
        do {
            _ = try await apiClient.startQueue()
            appError = nil
            await refreshState()
        } catch {
            appError = "Start failed: \(error.localizedDescription)"
        }
    }

    func pauseAfterCurrent() async {
        guard let apiClient else { return }
        do {
            _ = try await apiClient.pauseAfterCurrent()
            appError = nil
            await refreshState()
        } catch {
            appError = "Pause failed: \(error.localizedDescription)"
        }
    }

    func retrySelected() async {
        guard let item = selectedItem else { return }
        await retry(item)
    }

    func removeSelected() async {
        guard let item = selectedItem else { return }
        await remove(item)
    }

    func retry(_ item: QueueItem) async {
        guard let apiClient else { return }
        do {
            let next = try await apiClient.retry(item)
            selectedId = next.id
            appError = nil
            await refreshState()
        } catch {
            appError = "Retry failed: \(error.localizedDescription)"
        }
    }

    func remove(_ item: QueueItem) async {
        guard let apiClient else { return }
        do {
            _ = try await apiClient.remove(item)
            if selectedId == item.id {
                selectedId = nil
            }
            appError = nil
            await refreshState()
        } catch {
            appError = "Remove failed: \(error.localizedDescription)"
        }
    }

    private func startRefreshLoop() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                await self?.refreshState()
            }
        }
    }

    private func reconcileSelection(with snapshot: AppSnapshot) {
        if let selectedId, snapshot.items.contains(where: { $0.id == selectedId }) {
            return
        }
        selectedId = snapshot.items.first(where: { $0.status == "running" })?.id ?? snapshot.items.first?.id
    }
}

enum AppSection: String, CaseIterable, Identifiable {
    case queue = "Queue"
    case search = "Search"
    case installed = "Installed"
    var id: String { rawValue }
}
```

- [ ] **Step 2: Run compile to verify expected failure**

Run:

```bash
python3 scripts/build_macos_app.py --output-dir /private/tmp/ollama-manual-pull-plan-build
```

Expected: failure because app entry point and views are still missing.

- [ ] **Step 3: Commit**

```bash
git add macos/OllamaManualPull/AppStore.swift
git commit -m "Add native app state store"
```

## Task 5: Add SwiftUI App Entry Point, Commands, And Views

**Files:**
- Create: `macos/OllamaManualPull/OllamaManualPullApp.swift`
- Create: `macos/OllamaManualPull/ContentView.swift`
- Create: `macos/OllamaManualPull/SidebarView.swift`
- Create: `macos/OllamaManualPull/SearchView.swift`
- Create: `macos/OllamaManualPull/InstalledModelsView.swift`
- Create: `macos/OllamaManualPull/QueueView.swift`
- Create: `macos/OllamaManualPull/QueueRowView.swift`
- Create: `macos/OllamaManualPull/InspectorView.swift`

- [ ] **Step 1: Create `OllamaManualPullApp.swift`**

Create:

```swift
import AppKit
import SwiftUI

@main
struct OllamaManualPullApp: App {
    @StateObject private var store = AppStore()
    @StateObject private var supervisor = PythonServerSupervisor()

    var body: some Scene {
        WindowGroup("Ollama Manual Pull") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 1040, minHeight: 700)
                .onAppear {
                    supervisor.onURL = { store.connect(to: $0) }
                    supervisor.onStartupError = { store.showStartupError($0) }
                    supervisor.start()
                    NSApplication.shared.activate(ignoringOtherApps: true)
                }
                .onDisappear {
                    store.stopRefreshLoop()
                    supervisor.stop()
                }
        }
        .commands {
            CommandGroup(replacing: .appTermination) {
                Button("Quit Ollama Manual Pull") {
                    NSApplication.shared.terminate(nil)
                }
                .keyboardShortcut("q", modifiers: .command)
            }
            CommandMenu("Queue") {
                Button("Start Queue") {
                    Task { await store.startQueue() }
                }
                .keyboardShortcut("s", modifiers: [.command, .shift])
                .disabled(!store.canStartQueue)

                Button("Pause After Current") {
                    Task { await store.pauseAfterCurrent() }
                }
                .keyboardShortcut("p", modifiers: [.command, .shift])
                .disabled(!store.canPauseQueue)

                Divider()

                Button("Retry Selected Item") {
                    Task { await store.retrySelected() }
                }
                .disabled(!store.canRetrySelected)

                Button("Remove Selected Item") {
                    Task { await store.removeSelected() }
                }
                .keyboardShortcut(.delete, modifiers: [])
                .disabled(!store.canRemoveSelected)

                Divider()

                Button("Refresh") {
                    Task { await store.refreshState() }
                }
                .keyboardShortcut("r", modifiers: .command)
            }
        }
    }
}
```

- [ ] **Step 2: Create `ContentView.swift` with fixed bottom command bar**

Create:

```swift
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(spacing: 0) {
            HeaderView()
            if let message = store.appError {
                Text(message)
                    .font(.callout)
                    .foregroundColor(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(Color.red.opacity(0.09))
            }
            HStack(spacing: 0) {
                SidebarView()
                    .frame(width: 180)
                Divider()
                mainContent
                Divider()
                InspectorView()
                    .frame(width: 360)
            }
            BottomCommandBar()
        }
    }

    @ViewBuilder
    private var mainContent: some View {
        switch store.selectedSection {
        case .queue:
            QueueView()
        case .search:
            SearchView()
        case .installed:
            InstalledModelsView()
        }
    }
}

struct HeaderView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Ollama Manual Pull")
                    .font(.title2)
                    .fontWeight(.semibold)
                Text(store.serverReady ? "Native macOS queue window" : "Starting local downloader server...")
                    .foregroundColor(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                Text(store.snapshot?.modelsDir ?? "Waiting for server state")
                    .font(.caption)
                    .lineLimit(2)
                    .multilineTextAlignment(.trailing)
                Text(store.snapshot?.registry ?? "registry unavailable")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .frame(maxWidth: 430, alignment: .trailing)
        }
        .padding(16)
        .background(Color(NSColor.windowBackgroundColor))
    }
}

struct BottomCommandBar: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HStack {
            Button("Start Queue") {
                Task { await store.startQueue() }
            }
            .disabled(!store.canStartQueue)

            Button("Pause After Current") {
                Task { await store.pauseAfterCurrent() }
            }
            .disabled(!store.canPauseQueue)

            Spacer()

            Button("Retry") {
                Task { await store.retrySelected() }
            }
            .disabled(!store.canRetrySelected)

            Button("Remove") {
                Task { await store.removeSelected() }
            }
            .disabled(!store.canRemoveSelected)
        }
        .padding(12)
        .background(Color(NSColor.controlBackgroundColor))
    }
}
```

- [ ] **Step 3: Create `SidebarView.swift`**

Create:

```swift
import SwiftUI

struct SidebarView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        List(selection: $store.selectedSection) {
            ForEach(AppSection.allCases) { section in
                Text(section.rawValue).tag(section)
            }
        }
        .listStyle(.sidebar)
    }
}
```

- [ ] **Step 4: Create `SearchView.swift`**

Create:

```swift
import SwiftUI

struct SearchView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Add Model")
                    .font(.headline)
                HStack {
                    TextField("Search models or paste a ref, e.g. qwen3-coder:30b", text: $store.searchText)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit {
                            Task { await store.search() }
                        }
                    Button("Search") {
                        Task { await store.search() }
                    }
                    .disabled(store.isSearching)
                    Button("Add") {
                        Task { await store.queue(store.searchText) }
                    }
                }
                if !store.searchStatus.isEmpty {
                    Text(store.searchStatus)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                ForEach(store.searchResults) { result in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(result.queueableName.isEmpty ? "Unnamed model" : result.queueableName)
                            .fontWeight(.semibold)
                        if let heading = result.heading, heading != result.queueableName {
                            Text(heading).foregroundColor(.secondary)
                        }
                        Text(result.description ?? "No description provided.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        FlowButtons(variants: Array((result.variants ?? []).prefix(8)))
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(NSColor.controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                Spacer(minLength: 0)
            }
            .padding(16)
        }
    }
}

private struct FlowButtons: View {
    @EnvironmentObject private var store: AppStore
    let variants: [SearchVariant]

    var body: some View {
        HStack {
            ForEach(variants) { variant in
                Button(variant.label ?? variant.name) {
                    Task { await store.queue(variant.name) }
                }
            }
        }
    }
}
```

- [ ] **Step 5: Create `InstalledModelsView.swift`**

Create:

```swift
import SwiftUI

struct InstalledModelsView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Installed Models")
                    .font(.headline)
                let installed = store.snapshot?.installedModels ?? []
                if installed.isEmpty {
                    Text("No installed model manifests found.")
                        .foregroundColor(.secondary)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(NSColor.controlBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                } else {
                    ForEach(installed) { item in
                        HStack {
                            Text(item.name).fontWeight(.medium)
                            Spacer()
                            Text(item.namespace == nil || item.namespace == "library" ? "official library" : item.namespace!)
                                .foregroundColor(.secondary)
                        }
                        .padding(10)
                        .background(Color(NSColor.controlBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }
            .padding(16)
        }
    }
}
```

- [ ] **Step 6: Create `QueueView.swift`**

Create:

```swift
import SwiftUI

struct QueueView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ActiveDownloadView()
            Divider()
            Text("Download Queue")
                .font(.headline)
            let items = store.snapshot?.items ?? []
            if items.isEmpty {
                Text("Queue is empty.")
                    .foregroundColor(.secondary)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(NSColor.controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                ScrollViewReader { _ in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 8) {
                            ForEach(items) { item in
                                QueueRowView(item: item)
                                    .id(item.id)
                                    .background(item.id == store.selectedId ? Color.accentColor.opacity(0.12) : Color(NSColor.controlBackgroundColor))
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                                    .onTapGesture {
                                        store.selectedId = item.id
                                    }
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
            }
        }
        .padding(16)
    }
}

private struct ActiveDownloadView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Current Download")
                .font(.headline)
            if let running = store.snapshot?.items.first(where: { $0.status == "running" }) {
                QueueRowView(item: running)
                Text(running.messages.last ?? "Waiting for progress")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                Text("No active download. Start the queue when models are waiting.")
                    .foregroundColor(.secondary)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(NSColor.controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}
```

- [ ] **Step 7: Create `QueueRowView.swift`**

Create:

```swift
import SwiftUI

struct QueueRowView: View {
    let item: QueueItem

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                StatusBadge(status: item.status)
                Text(item.model)
                    .fontWeight(.semibold)
                    .lineLimit(1)
                Spacer()
            }
            Text("Updated \(AppFormatters.date(item.updatedAt))")
                .font(.caption)
                .foregroundColor(.secondary)
            if let error = item.error {
                Text(error)
                    .font(.caption)
                    .foregroundColor(.red)
            }
            if item.status == "running" || item.status == "completed" {
                ProgressSummary(progress: item.progress)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct StatusBadge: View {
    let status: String

    var body: some View {
        Text(status.capitalized)
            .font(.caption)
            .fontWeight(.bold)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(AppFormatters.statusColor(status).opacity(0.14))
            .foregroundColor(AppFormatters.statusColor(status))
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

struct ProgressSummary: View {
    let progress: DownloadProgress

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let percent = progress.overall.percent {
                ProgressView(value: max(0, min(100, percent)), total: 100)
                HStack {
                    Text("\(percent, specifier: "%.1f")%")
                    Spacer()
                    Text("\(AppFormatters.bytes(progress.overall.downloaded)) of \(AppFormatters.bytes(progress.overall.total))")
                }
                .font(.caption)
                .foregroundColor(.secondary)
            } else {
                Text("Waiting for progress")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }
}
```

- [ ] **Step 8: Create `InspectorView.swift`**

Create:

```swift
import SwiftUI

struct InspectorView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Selected Item")
                .font(.headline)
            if let item = store.selectedItem {
                detailField("Model", item.model)
                detailField("Status", item.status.capitalized)
                detailField("Source Registry", store.snapshot?.registry ?? "Unknown")
                detailField("Retries", "\(store.snapshot?.retries ?? 0)")
                ProgressSummary(progress: item.progress)
                detailField("Error", item.error ?? "None")
                Text("Activity")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(item.messages.suffix(8).enumerated()), id: \.offset) { _, message in
                        Text(message).font(.caption)
                    }
                }
                HStack {
                    Button("Retry") {
                        Task { await store.retry(item) }
                    }
                    .disabled(item.status != "failed")
                    Button("Remove") {
                        Task { await store.remove(item) }
                    }
                    .disabled(item.status == "running")
                }
            } else {
                Text("Select a queue item to inspect download details.")
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
        .padding(16)
    }

    private func detailField(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.secondary)
            Text(value)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
```

- [ ] **Step 9: Run build and tests**

Run:

```bash
python3 scripts/build_macos_app.py --output-dir /private/tmp/ollama-manual-pull-plan-build
PYTHONPATH=src python3 -m unittest tests.test_macos_app_builder -v
```

Expected: both commands exit with status 0; app builds and builder test passes.

- [ ] **Step 10: Commit**

```bash
git add macos/OllamaManualPull
git commit -m "Add multi-file SwiftUI macOS app"
```

## Task 6: Remove The Generated Swift Monolith

**Files:**
- Delete: the legacy generated Swift template
- Modify: `scripts/build_macos_app.py`
- Modify: `tests/test_macos_app_builder.py`

- [ ] **Step 1: Delete the old generated template**

Remove the legacy generated Swift template.

- [ ] **Step 2: Remove stale builder references**

Run:

```bash
rg -n "legacy-generated-swift-template-marker"
```

Expected: no references except in git history.

- [ ] **Step 3: Run full Python and builder verification**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m py_compile src/ollama_manual_pull/*.py tests/*.py scripts/build_macos_app.py
python3 scripts/build_macos_app.py --output-dir /private/tmp/ollama-manual-pull-plan-build
```

Expected: all tests pass, Python files compile, and the macOS app builds.

- [ ] **Step 4: Commit**

```bash
git add scripts/build_macos_app.py tests/test_macos_app_builder.py
git rm path/to/legacy/generated/swift/template
git commit -m "Remove generated Swift app template"
```

## Task 7: Manual Smoke Verification

**Files:**
- Verify: installed app bundle and running app behavior.

- [ ] **Step 1: Install the app**

Run:

```bash
python3 scripts/build_macos_app.py --install
```

Expected: `/Applications/Ollama Manual Pull.app` is replaced with the new build.

- [ ] **Step 2: Launch the app**

Run:

```bash
open "/Applications/Ollama Manual Pull.app"
```

Expected: native app opens, starts the local Python server, and shows the fixed bottom command bar.

- [ ] **Step 3: Verify commands**

In the launched app:

- Press `Command-Q`.
- Reopen the app.
- Open the Queue menu and confirm Start Queue, Pause After Current, Retry Selected Item, Remove Selected Item, and Refresh are present.

Expected: `Command-Q` quits the app and menu items exist with correct disabled states.

- [ ] **Step 4: Verify duplicate add behavior**

Queue `qwen3-coder`, then queue `qwen3-coder:latest`.

Expected: the queue contains one row and the Search section reports `Already in queue: qwen3-coder:latest.`

- [ ] **Step 5: Verify pause placement**

Add enough queue rows to make the central queue scroll. Start the queue.

Expected: `Pause After Current` remains visible in the bottom command bar while the queue scrolls.

- [ ] **Step 6: Verify refresh stability**

Select a queue item, scroll the queue, and wait through several refresh ticks.

Expected: selection remains on the same item, visible rows do not flicker, and scroll position does not jump during ordinary progress updates.

- [ ] **Step 7: Commit smoke notes if source changes were needed**

Run:

```bash
git status --short
```

Expected: no uncommitted source changes from smoke verification. If smoke testing required source fixes, return to the relevant implementation task, add focused tests for the issue, implement the fix, rerun Task 6 Step 3, and commit the fix with a specific message that names the behavior.

## Self-Review

- Spec coverage: duplicate prevention is covered in Task 1; multi-file Swift app structure is covered in Tasks 2-6; fixed bottom command bar and menu commands are covered in Task 5; refresh stability is covered in Task 4; packaging and smoke verification are covered in Tasks 6-7.
- Placeholder scan: no `TBD`, `TODO`, or deferred implementation placeholders remain.
- Type consistency: Swift payload names match the approved spec and existing API fields: `AppSnapshot`, `QueueItem`, `SearchResult`, `InstalledModel`, `canonical_model`, and `deduplicated`.
