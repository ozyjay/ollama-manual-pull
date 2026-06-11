import SwiftUI

struct SidebarView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        List(AppSection.allCases, selection: $store.selectedSection) { section in
            Label(section.rawValue, systemImage: icon(for: section))
                .tag(section)
        }
        .listStyle(.sidebar)
    }

    private func icon(for section: AppSection) -> String {
        switch section {
        case .queue:
            return "list.bullet.rectangle"
        case .search:
            return "magnifyingglass"
        case .installed:
            return "externaldrive"
        }
    }
}
