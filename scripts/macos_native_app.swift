import AppKit
import Foundation
import SwiftUI

private let bundledPython = %%PYTHON_EXECUTABLE%%
private let serverCommand = "from ollama_manual_pull.server import create_server; from ollama_manual_pull.core import default_models_dir; httpd = create_server(('127.0.0.1', 0), models_dir=default_models_dir()); host, port = httpd.server_address; print(f'URL=http://{host}:{port}/', flush=True); httpd.serve_forever()"

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

    var id: String {
        "\(namespace ?? "library")/\(name)"
    }
}

struct QueueItem: Decodable, Identifiable {
    let id: String
    let model: String
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

    var id: String {
        name
    }

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

@MainActor
final class AppModel: ObservableObject {
    @Published var snapshot: AppSnapshot?
    @Published var selectedId: String?
    @Published var searchText = ""
    @Published var searchResults: [SearchResult] = []
    @Published var searchStatus = ""
    @Published var appError: String?
    @Published var isSearching = false
    @Published var serverReady = false

    private var serverURL: URL?
    private var refreshTimer: Timer?

    var selectedItem: QueueItem? {
        snapshot?.items.first { $0.id == selectedId }
    }

    func connect(to url: URL) {
        serverURL = url
        serverReady = true
        appError = nil
        refreshTimer?.invalidate()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.refreshState()
            }
        }
        Task {
            await refreshState()
        }
    }

    func showStartupError(_ message: String) {
        appError = message
    }

    func refreshState() async {
        guard serverURL != nil else {
            return
        }
        do {
            let next: AppSnapshot = try await api("/api/state")
            snapshot = next
            if selectedId == nil || !next.items.contains(where: { $0.id == selectedId }) {
                selectedId = next.items.first(where: { $0.status == "running" })?.id ?? next.items.first?.id
            }
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
        guard let base = serverURL else {
            appError = "Local app server is still starting."
            return
        }

        isSearching = true
        searchResults = []
        searchStatus = "Searching..."
        defer {
            isSearching = false
        }

        do {
            var components = URLComponents(url: base.appendingPathComponent("api/search"), resolvingAgainstBaseURL: false)
            components?.queryItems = [URLQueryItem(name: "q", value: trimmed)]
            guard let url = components?.url else {
                throw APIError(message: "Could not build search URL.")
            }
            let payload: SearchResponse = try await api(url)
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

        do {
            let item: QueueItem = try await api("/api/queue", method: "POST", json: ["model": trimmed])
            selectedId = item.id
            searchText = ""
            searchStatus = "Queued \(trimmed)."
            appError = nil
            await refreshState()
        } catch {
            appError = "Queue failed: \(error.localizedDescription)"
        }
    }

    func startQueue() async {
        do {
            let _: AppSnapshot = try await api("/api/start", method: "POST")
            appError = nil
            await refreshState()
        } catch {
            appError = "Start failed: \(error.localizedDescription)"
        }
    }

    func pauseAfterCurrent() async {
        do {
            let _: AppSnapshot = try await api("/api/pause", method: "POST")
            appError = nil
            await refreshState()
        } catch {
            appError = "Pause failed: \(error.localizedDescription)"
        }
    }

    func retry(_ item: QueueItem) async {
        do {
            let next: QueueItem = try await api("/api/retry/\(encodedPath(item.id))", method: "POST")
            selectedId = next.id
            appError = nil
            await refreshState()
        } catch {
            appError = "Retry failed: \(error.localizedDescription)"
        }
    }

    func remove(_ item: QueueItem) async {
        do {
            let _: OKResponse = try await api("/api/remove/\(encodedPath(item.id))", method: "POST")
            if selectedId == item.id {
                selectedId = nil
            }
            appError = nil
            await refreshState()
        } catch {
            appError = "Remove failed: \(error.localizedDescription)"
        }
    }

    private func api<T: Decodable>(_ path: String, method: String = "GET", json: [String: String]? = nil) async throws -> T {
        guard let base = serverURL, let url = URL(string: path, relativeTo: base)?.absoluteURL else {
            throw APIError(message: "Local app server is not ready.")
        }
        return try await api(url, method: method, json: json)
    }

    private func api<T: Decodable>(_ url: URL, method: String = "GET", json: [String: String]? = nil) async throws -> T {
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

struct ContentView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            header
            if let message = model.appError {
                Text(message)
                    .font(.callout)
                    .foregroundColor(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(Color.red.opacity(0.09))
            }
            HStack(alignment: .top, spacing: 16) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        searchPanel
                        installedPanel
                        activePanel
                        queuePanel
                    }
                    .padding(16)
                }
                Divider()
                detailsPanel
                    .frame(width: 360)
                    .padding(16)
            }
        }
        .frame(minWidth: 1040, minHeight: 700)
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Ollama Manual Pull")
                    .font(.title2)
                    .fontWeight(.semibold)
                Text(model.serverReady ? "Native macOS queue window" : "Starting local downloader server...")
                    .foregroundColor(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                Text(model.snapshot?.modelsDir ?? "Waiting for server state")
                    .font(.caption)
                    .lineLimit(2)
                    .multilineTextAlignment(.trailing)
                Text(model.snapshot?.registry ?? "registry unavailable")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .frame(maxWidth: 430, alignment: .trailing)
        }
        .padding(16)
        .background(Color(NSColor.windowBackgroundColor))
    }

    private var searchPanel: some View {
        panel("Add model") {
            HStack {
                TextField("Search models or paste a ref, e.g. qwen3-coder:30b", text: $model.searchText)
                    .textFieldStyle(RoundedBorderTextFieldStyle())
                    .onSubmit {
                        Task {
                            await model.search()
                        }
                    }
                Button("Search") {
                    Task {
                        await model.search()
                    }
                }
                Button("Add") {
                    Task {
                        await model.queue(model.searchText)
                    }
                }
            }

            if !model.searchStatus.isEmpty {
                Text(model.searchStatus)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            ForEach(model.searchResults) { result in
                VStack(alignment: .leading, spacing: 6) {
                    Text(result.queueableName.isEmpty ? "Unnamed model" : result.queueableName)
                        .fontWeight(.semibold)
                    if let heading = result.heading, heading != result.queueableName {
                        Text(heading)
                            .foregroundColor(.secondary)
                    }
                    Text(result.description ?? "No description provided.")
                        .font(.caption)
                        .foregroundColor(.secondary)

                    HStack {
                        ForEach((result.variants ?? []).prefix(8)) { variant in
                            Button(variant.label ?? variant.name) {
                                Task {
                                    await model.queue(variant.name)
                                }
                            }
                        }
                    }
                }
                .padding(10)
                .background(panelBackground)
            }
        }
    }

    private var installedPanel: some View {
        panel("Installed models") {
            let installed = model.snapshot?.installedModels ?? []
            if installed.isEmpty {
                emptyText("No installed model manifests found.")
            } else {
                ForEach(installed) { item in
                    HStack {
                        Text(item.name)
                            .fontWeight(.medium)
                        Spacer()
                        Text(item.namespace == nil || item.namespace == "library" ? "official library" : item.namespace!)
                            .foregroundColor(.secondary)
                    }
                    .padding(8)
                    .background(panelBackground)
                }
            }
        }
    }

    private var activePanel: some View {
        panel("Current download") {
            if let running = model.snapshot?.items.first(where: { $0.status == "running" }) {
                queueSummary(running, showActions: false)
                Text(running.messages.last ?? "Waiting for progress")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                emptyText("No active download. Start the queue when models are waiting.")
            }
        }
    }

    private var queuePanel: some View {
        panel("Download queue") {
            let items = model.snapshot?.items ?? []
            if items.isEmpty {
                emptyText("Queue is empty.")
            } else {
                ForEach(items) { item in
                    queueSummary(item, showActions: true)
                        .padding(10)
                        .background(item.id == model.selectedId ? Color.accentColor.opacity(0.12) : Color(NSColor.controlBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .onTapGesture {
                            model.selectedId = item.id
                        }
                }
            }

            HStack {
                Button("Start") {
                    Task {
                        await model.startQueue()
                    }
                }
                .disabled(!(model.snapshot?.items.contains { $0.status == "waiting" } ?? false))

                Button("Pause after current download") {
                    Task {
                        await model.pauseAfterCurrent()
                    }
                }
                .disabled(!(model.snapshot?.running ?? false))
            }
        }
    }

    private var detailsPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Selected item")
                .font(.headline)
            if let item = model.selectedItem {
                detailField("Model", item.model)
                detailField("Status", item.status.capitalized)
                detailField("Source registry", model.snapshot?.registry ?? "Unknown")
                detailField("Retries", "\(model.snapshot?.retries ?? 0)")
                progressView(item.progress)
                detailField("Error", item.error ?? "None")

                Text("Activity")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(item.messages.suffix(8).enumerated()), id: \.offset) { _, message in
                        Text(message)
                            .font(.caption)
                    }
                }

                HStack {
                    if item.status == "failed" {
                        Button("Retry") {
                            Task {
                                await model.retry(item)
                            }
                        }
                    }
                    if item.status != "running" {
                        Button("Remove") {
                            Task {
                                await model.remove(item)
                            }
                        }
                    }
                }
            } else {
                emptyText("Select a queue item to inspect download details.")
            }
            Spacer()
        }
    }

    private func queueSummary(_ item: QueueItem, showActions: Bool) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                statusBadge(item.status)
                Text(item.model)
                    .fontWeight(.semibold)
                Spacer()
                if showActions {
                    if item.status == "failed" {
                        Button("Retry") {
                            Task {
                                await model.retry(item)
                            }
                        }
                    }
                    if item.status != "running" {
                        Button("Remove") {
                            Task {
                                await model.remove(item)
                            }
                        }
                    }
                }
            }
            Text("Updated \(formatDate(item.updatedAt))")
                .font(.caption)
                .foregroundColor(.secondary)
            if let error = item.error {
                Text(error)
                    .font(.caption)
                    .foregroundColor(.red)
            }
            if item.status == "running" {
                progressView(item.progress)
            }
        }
    }

    private func progressView(_ progress: DownloadProgress) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            if let percent = progress.overall.percent {
                ProgressView(value: max(0, min(100, percent)), total: 100)
                HStack {
                    Text("\(percent, specifier: "%.1f")%")
                    Spacer()
                    Text("\(formatBytes(progress.overall.downloaded)) of \(formatBytes(progress.overall.total))")
                }
                .font(.caption)
                .foregroundColor(.secondary)
            } else {
                Text("Waiting for progress")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            if let current = progress.currentFile {
                Text(current.digest ?? "Waiting for file")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                if let percent = current.percent {
                    ProgressView(value: max(0, min(100, percent)), total: 100)
                    Text("\(percent, specifier: "%.1f")% - \(formatBytes(current.downloaded)) of \(formatBytes(current.total))\(rateAndEta(current))")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
    }

    private func detailField(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.secondary)
            Text(value)
                .font(.body)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func panel<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased())
                .font(.caption)
                .fontWeight(.bold)
                .foregroundColor(.secondary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func statusBadge(_ status: String) -> some View {
        Text(status.capitalized)
            .font(.caption)
            .fontWeight(.bold)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(statusColor(status).opacity(0.14))
            .foregroundColor(statusColor(status))
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func emptyText(_ message: String) -> some View {
        Text(message)
            .foregroundColor(.secondary)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(panelBackground)
    }

    private var panelBackground: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Color(NSColor.controlBackgroundColor))
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "running":
            return .blue
        case "completed":
            return .green
        case "failed":
            return .red
        case "waiting":
            return .orange
        default:
            return .secondary
        }
    }

    private func formatDate(_ seconds: Double) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .medium
        return formatter.string(from: Date(timeIntervalSince1970: seconds))
    }

    private func formatBytes(_ value: Double?) -> String {
        guard var size = value, size.isFinite else {
            return "Unknown"
        }
        let units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units {
            if abs(size) < 1000 || unit == units.last {
                return unit == "B" ? "\(Int(size.rounded()))B" : String(format: "%.1f%@", size, unit)
            }
            size /= 1000
        }
        return String(format: "%.1fTB", size)
    }

    private func rateAndEta(_ progress: ProgressAmount) -> String {
        var parts: [String] = []
        if let rate = progress.bytesPerSecond, rate.isFinite {
            parts.append("\(formatBytes(rate))/s")
        }
        if let eta = progress.etaSeconds, eta.isFinite {
            parts.append("ETA \(formatEta(eta))")
        }
        return parts.isEmpty ? "" : " - " + parts.joined(separator: " - ")
    }

    private func formatEta(_ seconds: Double) -> String {
        let safeSeconds = max(0, Int(seconds.rounded()))
        let minutes = safeSeconds / 60
        let remaining = safeSeconds % 60
        if minutes >= 60 {
            return "\(minutes / 60)h \(minutes % 60)m"
        }
        return "\(minutes)m \(String(format: "%02d", remaining))s"
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let model = AppModel()
    private var window: NSWindow?
    private var serverTask: Process?
    private var outputBuffer = ""

    func applicationDidFinishLaunching(_ notification: Notification) {
        createWindow()
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let output = serverTask?.standardOutput as? Pipe {
            output.fileHandleForReading.readabilityHandler = nil
        }
        if let error = serverTask?.standardError as? Pipe {
            error.fileHandleForReading.readabilityHandler = nil
        }
        serverTask?.terminate()
    }

    private func createWindow() {
        let rootView = ContentView().environmentObject(model)
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Ollama Manual Pull"
        window.minSize = NSSize(width: 980, height: 640)
        window.contentView = NSHostingView(rootView: rootView)
        window.center()
        window.makeKeyAndOrderFront(nil)
        self.window = window
    }

    private func startServer() {
        guard let resourcesURL = Bundle.main.resourceURL else {
            model.showStartupError("Could not locate bundled app resources.")
            return
        }

        let task = Process()
        let output = Pipe()
        let errorOutput = Pipe()
        task.executableURL = URL(fileURLWithPath: resolvedPython())
        task.arguments = ["-c", serverCommand]
        task.environment = [
            "PYTHONPATH": resourcesURL.appendingPathComponent("src").path,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        ]
        task.standardOutput = output
        task.standardError = errorOutput

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else {
                return
            }
            DispatchQueue.main.async {
                self?.appendServerOutput(chunk)
            }
        }
        errorOutput.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else {
                return
            }
            DispatchQueue.main.async {
                self?.appendServerError(chunk)
            }
        }

        do {
            try task.run()
            serverTask = task
        } catch {
            model.showStartupError("Could not start the local app server: \(error.localizedDescription)")
        }
    }

    private func appendServerOutput(_ chunk: String) {
        outputBuffer += chunk
        guard let prefix = outputBuffer.range(of: "URL=") else {
            return
        }
        let tail = outputBuffer[prefix.upperBound...]
        guard let end = tail.firstIndex(where: { $0 == "\n" || $0 == "\r" }) else {
            return
        }
        let urlString = String(tail[..<end])
        guard let url = URL(string: urlString) else {
            model.showStartupError("The local server returned an invalid URL: \(urlString)")
            return
        }
        model.connect(to: url)
    }

    private func appendServerError(_ chunk: String) {
        if !model.serverReady {
            model.showStartupError(chunk.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    private func resolvedPython() -> String {
        let manager = FileManager.default
        if manager.isExecutableFile(atPath: bundledPython) {
            return bundledPython
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

@main
struct NativeAppMain {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.activate(ignoringOtherApps: true)
        app.run()
    }
}
