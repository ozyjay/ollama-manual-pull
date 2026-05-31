import SwiftUI

struct QueueView: View {
    @EnvironmentObject private var store: AppStore

    private var items: [QueueItem] {
        store.snapshot?.items ?? []
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ActiveDownloadSummary()

            Divider()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if items.isEmpty {
                        Text("No queued models.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(18)
                    } else {
                        ForEach(items) { item in
                            QueueRowView(item: item, isSelected: store.selectedId == item.id)
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    store.selectedId = item.id
                                }
                            Divider()
                        }
                    }
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }
}

private struct ActiveDownloadSummary: View {
    @EnvironmentObject private var store: AppStore

    private var activeItem: QueueItem? {
        store.snapshot?.items.first { $0.status == "running" }
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: activeItem == nil ? "tray" : "arrow.down.circle.fill")
                .foregroundStyle(activeItem == nil ? Color.secondary : Color.blue)
                .font(.title3)

            VStack(alignment: .leading, spacing: 4) {
                Text(activeItem?.model ?? "No active download")
                    .font(.headline)
                    .lineLimit(1)
                if let activeItem {
                    ProgressSummary(progress: activeItem.progress)
                } else {
                    Text(store.snapshot?.pauseRequested == true ? "Queue will pause after the current item." : "Queue is idle.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer()

            if let snapshot = store.snapshot {
                Text(snapshot.running ? "Running" : "Stopped")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(snapshot.running ? .blue : .secondary)
            }
        }
        .padding(18)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}
