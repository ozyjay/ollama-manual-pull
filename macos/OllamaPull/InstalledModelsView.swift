import SwiftUI

struct InstalledModelsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var modelPendingDeletion: InstalledModel?
    @State private var cleanupPendingDeletion = false

    private var models: [InstalledModel] {
        store.snapshot?.installedModels ?? []
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                CleanupPanel(cleanupPendingDeletion: $cleanupPendingDeletion)
                Divider()

                if models.isEmpty {
                    Text("No installed model manifests found.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(18)
                } else {
                    ForEach(models) { model in
                        InstalledModelRow(model: model) {
                            modelPendingDeletion = model
                        }
                        Divider()
                    }
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
        .confirmationDialog(
            "Delete Model",
            isPresented: Binding(
                get: { modelPendingDeletion != nil },
                set: { if !$0 { modelPendingDeletion = nil } }
            ),
            presenting: modelPendingDeletion
        ) { model in
            Button("Delete Model", role: .destructive) {
                Task { await store.deleteInstalledModel(model) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { model in
            Text("Remove \(model.name) from installed model manifests. Shared blobs are left in place.")
        }
        .confirmationDialog(
            "Delete Orphan Shards",
            isPresented: $cleanupPendingDeletion
        ) {
            Button("Delete Orphan Shards", role: .destructive) {
                Task { await store.deleteCleanupCandidates() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            if store.includeStalePartials {
                Text("Delete complete orphan blobs and stale partial downloads. Shared blobs are kept.")
            } else {
                Text("Delete complete orphan blobs. Shared blobs are kept, and partial downloads are left in place.")
            }
        }
    }
}

private struct CleanupPanel: View {
    @EnvironmentObject private var store: AppStore
    @Binding var cleanupPendingDeletion: Bool

    private var report: CleanupReport? {
        store.cleanupReport
    }

    private var canDelete: Bool {
        guard let report else { return false }
        return report.dryRun && report.candidateCount > 0 && !store.isCleanupBusy
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Label("Shard Cleanup", systemImage: "externaldrive.badge.minus")
                    .font(.headline)

                Spacer()

                Button {
                    Task { await store.scanCleanup() }
                } label: {
                    Label("Scan Orphan Shards", systemImage: "magnifyingglass")
                }
                .disabled(store.isCleanupBusy)

                Button(role: .destructive) {
                    cleanupPendingDeletion = true
                } label: {
                    Label("Delete Orphan Shards", systemImage: "trash")
                }
                .disabled(!canDelete)
            }

            Toggle("Include stale partials", isOn: $store.includeStalePartials)
                .toggleStyle(.checkbox)
                .disabled(store.isCleanupBusy)
                .onChange(of: store.includeStalePartials) { _, _ in
                    store.cleanupReport = nil
                }

            Text("Shared blobs are kept. Partial downloads are included only when stale partials are enabled.")
                .font(.caption)
                .foregroundStyle(.secondary)

            CleanupSummary(report: report)
        }
        .padding(18)
    }
}

private struct CleanupSummary: View {
    let report: CleanupReport?

    var body: some View {
        if let report {
            VStack(alignment: .leading, spacing: 4) {
                Text(report.dryRun ? "Dry run complete." : "Cleanup complete.")
                    .font(.callout.weight(.medium))
                Text("\(report.referencedCount) referenced blobs")
                Text("\(report.orphanBlobCount) complete orphan blobs, \(AppFormatters.bytes(report.orphanBlobBytes))")
                Text("\(report.stalePartialCount) stale partial downloads, \(AppFormatters.bytes(report.stalePartialBytes))")
                if !report.deleted.isEmpty {
                    Text("\(report.deleted.count) deleted files")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        } else {
            Text("Scan to find complete blobs that are not referenced by installed manifests.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

private struct InstalledModelRow: View {
    let model: InstalledModel
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "shippingbox")
                .foregroundStyle(.secondary)
                .frame(width: 22)

            VStack(alignment: .leading, spacing: 3) {
                Text(model.name)
                    .font(.headline)
                Text(model.namespace ?? "library")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Button(role: .destructive, action: onDelete) {
                Label("Delete Model", systemImage: "trash")
            }
            .labelStyle(.iconOnly)
            .buttonStyle(.borderless)
            .help("Delete installed model")
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }
}
