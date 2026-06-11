import SwiftUI

struct SearchView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            searchBar

            if !store.searchStatus.isEmpty {
                Text(store.searchStatus)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 18)
                    .padding(.bottom, 10)
            }

            Divider()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(store.searchResults) { result in
                        SearchResultRow(result: result)
                        Divider()
                    }
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }

    private var searchBar: some View {
        HStack(spacing: 10) {
            Picker("Source", selection: $store.selectedSourceId) {
                ForEach(store.availableSources) { source in
                    Text(source.label).tag(source.id)
                }
            }
            .labelsHidden()
            .frame(width: 150)

            TextField("Search models or paste a model reference", text: $store.searchText)
                .textFieldStyle(.roundedBorder)
                .onSubmit {
                    Task { await store.search() }
                }

            Button {
                Task { await store.search() }
            } label: {
                Label(store.isSearching ? "Searching" : "Search", systemImage: "magnifyingglass")
            }
            .disabled(store.isSearching)

            Button {
                Task { await store.queue(store.searchText) }
            } label: {
                Label("Queue Text", systemImage: "plus")
            }
            .disabled(store.searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .controlSize(.small)
        .padding(18)
    }
}

private struct SearchResultRow: View {
    @EnvironmentObject private var store: AppStore
    let result: SearchResult

    private var isDownloadedResult: Bool {
        !result.queueableName.isEmpty && store.isDownloaded(modelRef: result.queueableName)
    }

    private var downloadedVariantCount: Int {
        (result.variants ?? []).filter { store.isDownloaded(modelRef: $0.name) }.count
    }

    private var title: String {
        result.heading ?? result.name ?? "Untitled model"
    }

    private var subtitle: String? {
        guard result.heading != result.name else { return result.description }
        if let name = result.name, !name.isEmpty {
            return name
        }
        return result.description
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(title)
                            .font(.headline)
                        if isDownloadedResult {
                            DownloadedBadge(text: "Downloaded")
                        } else if downloadedVariantCount > 0 {
                            DownloadedBadge(text: "\(downloadedVariantCount) downloaded")
                        }
                    }
                    if let subtitle, !subtitle.isEmpty {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    if let description = result.description, description != subtitle {
                        Text(description)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .lineLimit(3)
                    }
                }

                Spacer()

                Button {
                    Task { await store.queue(result.queueableName) }
                } label: {
                    Label("Queue", systemImage: "plus")
                }
                .disabled(result.queueableName.isEmpty)
            }

            if let tags = result.tags, !tags.isEmpty {
                FlowLine(items: Array(tags.prefix(8)))
            }

            if let variants = result.variants, !variants.isEmpty {
                HStack(spacing: 6) {
                    ForEach(Array(variants.prefix(8))) { variant in
                        let isDownloadedVariant = store.isDownloaded(modelRef: variant.name)
                        Button(variant.label ?? variant.name) {
                            Task { await store.queue(variant.name) }
                        }
                        .labelStyle(.titleAndIcon)
                        .overlay(alignment: .topTrailing) {
                            if isDownloadedVariant {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(.green)
                                    .font(.caption2)
                                    .offset(x: 5, y: -5)
                            }
                        }
                        .help(isDownloadedVariant ? "Downloaded" : "Queue this variant")
                    }
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 14)
    }
}

private struct DownloadedBadge: View {
    let text: String

    var body: some View {
        Label(text, systemImage: "checkmark.circle.fill")
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.green)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Color.green.opacity(0.12))
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}

private struct FlowLine: View {
    let items: [String]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(items, id: \.self) { item in
                Text(item)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 4))
            }
        }
    }
}
