import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(spacing: 0) {
            HeaderView()

            if let appError = store.appError {
                ErrorBanner(message: appError)
            }

            HStack(spacing: 0) {
                SidebarView()
                    .frame(width: 190)

                Divider()

                mainContent
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                Divider()

                InspectorView()
                    .frame(width: 300)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            Divider()
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

private struct HeaderView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Ollama Manual Pull")
                    .font(.title3.weight(.semibold))
                HStack(spacing: 8) {
                    Circle()
                        .fill(store.serverReady ? Color.green : Color.orange)
                        .frame(width: 8, height: 8)
                    Text(store.serverReady ? "Local server ready" : "Starting local server...")
                        .foregroundStyle(.secondary)
                }
                .font(.caption)
            }

            Spacer()

            HeaderMetadata(label: "Models", value: store.snapshot?.modelsDir ?? "Waiting for state")
            HeaderMetadata(label: "Registry", value: store.snapshot?.registry ?? "Unknown")
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 14)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

private struct HeaderMetadata: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .trailing, spacing: 3) {
            Text(label.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.caption)
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(maxWidth: 300, alignment: .trailing)
        }
    }
}

private struct ErrorBanner: View {
    let message: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            Text(message)
                .font(.callout)
                .lineLimit(2)
            Spacer()
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 8)
        .background(Color.red.opacity(0.10))
    }
}

struct BottomCommandBar: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HStack(spacing: 10) {
            Button {
                Task { await store.startQueue() }
            } label: {
                Label("Start Queue", systemImage: "play.fill")
            }
            .disabled(!store.canStartQueue)

            Button {
                Task { await store.pauseAfterCurrent() }
            } label: {
                Label("Pause After Current", systemImage: "pause.fill")
            }
            .disabled(!store.canPauseQueue)

            Button {
                Task { await store.stopAfterCurrentBlob() }
            } label: {
                Label("Stop After Blob", systemImage: "stop.circle")
            }
            .disabled(!store.canStopAfterBlob)

            Divider()
                .frame(height: 22)

            Button {
                Task { await store.retrySelected() }
            } label: {
                Label("Retry", systemImage: "arrow.clockwise")
            }
            .disabled(!store.canRetrySelected)

            Button {
                Task { await store.removeSelected() }
            } label: {
                Label("Remove", systemImage: "trash")
            }
            .disabled(!store.canRemoveSelected)

            Spacer()

            Button {
                Task { await store.refreshState() }
            } label: {
                Label(store.isRefreshing ? "Refreshing" : "Refresh", systemImage: "arrow.triangle.2.circlepath")
            }
            .disabled(store.isRefreshing)
        }
        .controlSize(.small)
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        .background(Color(nsColor: .controlBackgroundColor))
    }
}
