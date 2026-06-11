import SwiftUI

struct QueueRowView: View {
    let item: QueueItem
    let isSelected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                StatusBadge(status: item.status)

                VStack(alignment: .leading, spacing: 3) {
                    Text(item.canonicalModel ?? item.model)
                        .font(.headline)
                        .lineLimit(1)
                    if item.canonicalModel != nil, item.canonicalModel != item.model {
                        Text(item.model)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }

                Spacer()

                Text(AppFormatters.date(item.updatedAt))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            ProgressSummary(progress: item.progress)

            if let error = item.error, !error.isEmpty {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
        .background(isSelected ? Color.accentColor.opacity(0.12) : Color.clear)
    }
}

struct StatusBadge: View {
    let status: String

    var body: some View {
        Text(status.capitalized)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(AppFormatters.statusColor(status))
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(AppFormatters.statusColor(status).opacity(0.12))
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}

struct ProgressSummary: View {
    let progress: DownloadProgress

    private var amount: ProgressAmount {
        progress.overall
    }

    private var currentFile: ProgressAmount? {
        progress.currentFile
    }

    private var percent: Double {
        min(100, max(0, amount.percent ?? 0))
    }

    private var currentBlobLabel: String? {
        guard let currentFile else { return nil }
        if let index = currentFile.index, let totalFiles = currentFile.totalFiles {
            return "Current blob \(index) of \(totalFiles)"
        }
        return currentFile.digest == nil ? nil : "Current blob"
    }

    private var currentPercent: Double? {
        guard let value = currentFile?.percent else { return nil }
        return min(100, max(0, value))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            ProgressView(value: percent, total: 100)
                .progressViewStyle(.linear)

            HStack(spacing: 8) {
                Text(progress.phase.isEmpty ? "Waiting" : "Model \(progress.phase)")
                    .lineLimit(1)

                if let downloaded = amount.downloaded, let total = amount.total {
                    Text("\(AppFormatters.bytes(downloaded)) / \(AppFormatters.bytes(total))")
                }

                if let bytesPerSecond = amount.bytesPerSecond {
                    Text("\(AppFormatters.bytes(bytesPerSecond))/s")
                }

                if let etaSeconds = amount.etaSeconds {
                    Text("ETA \(AppFormatters.eta(etaSeconds))")
                }

                Spacer()

                Text(String(format: "%.0f%%", percent))
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            if let currentFile, let currentBlobLabel {
                HStack(spacing: 6) {
                    Text(currentBlobLabel)
                    if let currentPercent {
                        Text(String(format: "%.0f%%", currentPercent))
                    }
                    if let digest = currentFile.digest {
                        Text(digest)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }
                .font(.caption2)
                .foregroundStyle(.tertiary)
            }
        }
    }
}
