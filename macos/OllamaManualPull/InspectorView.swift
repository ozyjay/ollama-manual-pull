import SwiftUI

struct InspectorView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Inspector")
                .font(.headline)
                .padding(18)

            Divider()

            if let item = store.selectedItem {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        DetailSection(title: "Model") {
                            DetailRow(label: "Requested", value: item.model)
                            if let canonical = item.canonicalModel {
                                DetailRow(label: "Canonical", value: canonical)
                            }
                            DetailRow(label: "Registry", value: store.snapshot?.registry ?? "Unknown")
                        }

                        DetailSection(title: "Status") {
                            HStack {
                                StatusBadge(status: item.status)
                                Spacer()
                            }
                            DetailRow(label: "Retries", value: "\(store.snapshot?.retries ?? 0)")
                            DetailRow(label: "Updated", value: AppFormatters.date(item.updatedAt))
                        }

                        DetailSection(title: "Progress") {
                            ProgressSummary(progress: item.progress)
                            if let currentBlob = item.currentBlob, !currentBlob.isEmpty {
                                DetailRow(label: "Blob", value: currentBlob)
                            }
                        }

                        if let error = item.error, !error.isEmpty {
                            DetailSection(title: "Error") {
                                Text(error)
                                    .font(.callout)
                                    .foregroundStyle(.red)
                            }
                        }

                        DetailSection(title: "Recent Messages") {
                            if item.messages.isEmpty {
                                Text("No messages yet.")
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                            } else {
                                VStack(alignment: .leading, spacing: 6) {
                                    ForEach(Array(item.messages.suffix(8).enumerated()), id: \.offset) { _, message in
                                        Text(message)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                    }
                                }
                            }
                        }

                        HStack(spacing: 8) {
                            Button {
                                Task { await store.retry(item) }
                            } label: {
                                Label("Retry", systemImage: "arrow.clockwise")
                            }
                            .disabled(item.status != "failed")

                            Button {
                                Task { await store.remove(item) }
                            } label: {
                                Label("Remove", systemImage: "trash")
                            }
                            .disabled(item.status == "running")
                        }
                        .controlSize(.small)
                    }
                    .padding(18)
                }
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("No queue item selected.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Text("Choose an item from the queue to inspect details and recent messages.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(18)
                Spacer()
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

private struct DetailSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
            content
        }
    }
}

private struct DetailRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.callout)
                .lineLimit(3)
                .truncationMode(.middle)
        }
    }
}
