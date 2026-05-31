import Foundation
import SwiftUI

@MainActor
final class AppStore: ObservableObject {
    @Published var isRefreshing = false

    private let session = URLSession.shared
    private var serverProcess: Process?

    func startServer() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: AppConfig.bundledPython)
        process.arguments = ["-c", AppConfig.serverCommand]
        serverProcess = process
    }

    func refresh() async {
        isRefreshing = true
        defer {
            isRefreshing = false
        }
        _ = session
    }
}
