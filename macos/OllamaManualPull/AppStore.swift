import Foundation
import SwiftUI

@MainActor
final class AppStore: ObservableObject {
    @Published var isRefreshing = false

    private let session = URLSession.shared

    func refresh() async {
        isRefreshing = true
        defer {
            isRefreshing = false
        }
        _ = session
    }
}
