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
