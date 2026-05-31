import SwiftUI

struct InstalledModelsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var modelPendingDeletion: InstalledModel?

    private var models: [InstalledModel] {
        store.snapshot?.installedModels ?? []
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
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
