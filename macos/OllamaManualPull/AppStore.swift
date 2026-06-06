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
    @Published private(set) var isRefreshing = false

    private var apiClient: APIClient?
    private var refreshTask: Task<Void, Never>?
    private var refreshInFlight = false
    private var refreshPending = false

    var selectedItem: QueueItem? {
        snapshot?.items.first { $0.id == selectedId }
    }

    var canStartQueue: Bool {
        snapshot?.items.contains { $0.status == "waiting" } ?? false
    }

    var canPauseQueue: Bool {
        snapshot?.running ?? false
    }

    var canStopAfterBlob: Bool {
        guard let snapshot else { return false }
        return snapshot.running && !snapshot.stopAfterBlobRequested
    }

    var canRetrySelected: Bool {
        selectedItem?.status == "failed"
    }

    var canRemoveSelected: Bool {
        guard let item = selectedItem else { return false }
        return item.status != "running"
    }

    var installedModelRefs: Set<String> {
        Set((snapshot?.installedModels ?? []).map { normalizedModelRef($0.name) })
    }

    func isDownloaded(modelRef: String) -> Bool {
        installedModelRefs.contains(normalizedModelRef(modelRef))
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

    func refreshState(showIndicator: Bool = true) async {
        guard apiClient != nil else { return }
        if refreshInFlight {
            refreshPending = true
            return
        }
        refreshInFlight = true
        if showIndicator {
            isRefreshing = true
        }
        defer {
            refreshInFlight = false
            if showIndicator {
                isRefreshing = false
            }
        }
        repeat {
            refreshPending = false
            guard !Task.isCancelled else { break }
            guard let apiClient else { break }
            do {
                let next = try await apiClient.state()
                snapshot = next
                reconcileSelection(with: next)
                clearStateRefreshError()
            } catch {
                if isCancellation(error) || Task.isCancelled { break }
                appError = "State refresh failed: \(error.localizedDescription)"
            }
        } while refreshPending && !Task.isCancelled
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
            clearActionError(prefix: "Search failed:")
        } catch {
            if isCancellation(error) || Task.isCancelled { return }
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
            clearActionError(prefix: "Queue failed:")
            await refreshState()
        } catch {
            if isCancellation(error) || Task.isCancelled { return }
            appError = "Queue failed: \(error.localizedDescription)"
        }
    }

    func startQueue() async {
        guard let apiClient else { return }
        do {
            _ = try await apiClient.startQueue()
            clearActionError(prefix: "Start failed:")
            await refreshState()
        } catch {
            if isCancellation(error) || Task.isCancelled { return }
            appError = "Start failed: \(error.localizedDescription)"
        }
    }

    func pauseAfterCurrent() async {
        guard let apiClient else { return }
        do {
            _ = try await apiClient.pauseAfterCurrent()
            clearActionError(prefix: "Pause failed:")
            await refreshState()
        } catch {
            if isCancellation(error) || Task.isCancelled { return }
            appError = "Pause failed: \(error.localizedDescription)"
        }
    }

    func stopAfterCurrentBlob() async {
        guard let apiClient else { return }
        do {
            _ = try await apiClient.stopAfterCurrentBlob()
            clearActionError(prefix: "Stop after blob failed:")
            await refreshState()
        } catch {
            if isCancellation(error) || Task.isCancelled { return }
            appError = "Stop after blob failed: \(error.localizedDescription)"
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
            clearActionError(prefix: "Retry failed:")
            await refreshState()
        } catch {
            if isCancellation(error) || Task.isCancelled { return }
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
            clearActionError(prefix: "Remove failed:")
            await refreshState()
        } catch {
            if isCancellation(error) || Task.isCancelled { return }
            appError = "Remove failed: \(error.localizedDescription)"
        }
    }

    func deleteInstalledModel(_ model: InstalledModel) async {
        guard let apiClient else { return }
        do {
            _ = try await apiClient.deleteInstalledModel(model)
            clearActionError(prefix: "Delete model failed:")
            await refreshState()
        } catch {
            if isCancellation(error) || Task.isCancelled { return }
            appError = "Delete model failed: \(error.localizedDescription)"
        }
    }

    private func startRefreshLoop() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    try await Task.sleep(nanoseconds: 1_000_000_000)
                } catch {
                    break
                }
                await self?.refreshState(showIndicator: false)
            }
        }
    }

    private func clearStateRefreshError() {
        if appError?.hasPrefix("State refresh failed:") == true {
            appError = nil
        }
    }

    private func clearActionError(prefix: String) {
        if appError?.hasPrefix(prefix) == true {
            appError = nil
        }
    }

    private func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        if let urlError = error as? URLError, urlError.code == .cancelled { return true }
        return false
    }

    private func reconcileSelection(with snapshot: AppSnapshot) {
        if let selectedId, snapshot.items.contains(where: { $0.id == selectedId }) {
            return
        }
        selectedId = snapshot.items.first(where: { $0.status == "running" })?.id ?? snapshot.items.first?.id
    }

    private func normalizedModelRef(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !trimmed.isEmpty else { return "" }
        let namespaced = trimmed.contains("/") ? trimmed : "library/\(trimmed)"
        guard let slash = namespaced.lastIndex(of: "/") else { return namespaced }
        let nameStart = namespaced.index(after: slash)
        let nameAndTag = namespaced[nameStart...]
        if nameAndTag.contains(":") {
            return namespaced
        }
        return "\(namespaced):latest"
    }
}

enum AppSection: String, CaseIterable, Identifiable {
    case queue = "Queue"
    case search = "Search"
    case installed = "Installed"
    var id: String { rawValue }
}
