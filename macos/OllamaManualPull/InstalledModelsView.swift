import SwiftUI

struct InstalledModelsView: View {
    @EnvironmentObject private var store: AppStore

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
                        InstalledModelRow(model: model)
                        Divider()
                    }
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }
}

private struct InstalledModelRow: View {
    let model: InstalledModel

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
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }
}
