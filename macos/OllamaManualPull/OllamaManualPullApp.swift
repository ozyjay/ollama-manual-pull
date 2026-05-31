import AppKit
import SwiftUI

@main
struct OllamaManualPullApp: App {
    @StateObject private var store = AppStore()

    var body: some Scene {
        WindowGroup {
            ContentView(store: store)
                .background(HostingViewMarker())
        }
        .commands {
            CommandGroup(replacing: .appTermination) {
                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
                .keyboardShortcut("q", modifiers: .command)
            }
        }
    }
}

private struct HostingViewMarker: NSViewRepresentable {
    func makeNSView(context: Context) -> NSHostingView<EmptyView> {
        NSHostingView(rootView: EmptyView())
    }

    func updateNSView(_ nsView: NSHostingView<EmptyView>, context: Context) {}
}
