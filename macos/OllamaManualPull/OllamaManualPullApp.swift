import AppKit
import SwiftUI

@main
struct OllamaManualPullApp: App {
    @StateObject private var store = AppStore()
    @StateObject private var supervisor = PythonServerSupervisor()

    var body: some Scene {
        WindowGroup("Ollama Manual Pull") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 1040, minHeight: 700)
                .background(HostingViewMarker())
                .onAppear {
                    supervisor.onURL = { store.connect(to: $0) }
                    supervisor.onStartupError = { store.showStartupError($0) }
                    supervisor.start()
                    NSApplication.shared.activate(ignoringOtherApps: true)
                }
                .onDisappear {
                    store.stopRefreshLoop()
                    supervisor.stop()
                }
        }
        .commands {
            CommandGroup(replacing: .appTermination) {
                Button("Quit Ollama Manual Pull") {
                    NSApplication.shared.terminate(nil)
                }
                .keyboardShortcut("q", modifiers: .command)
            }

            CommandMenu("Queue") {
                Button("Start Queue") {
                    Task { await store.startQueue() }
                }
                .keyboardShortcut("s", modifiers: [.command, .shift])
                .disabled(!store.canStartQueue)

                Button("Pause After Current") {
                    Task { await store.pauseAfterCurrent() }
                }
                .keyboardShortcut("p", modifiers: [.command, .shift])
                .disabled(!store.canPauseQueue)

                Divider()

                Button("Retry Selected Item") {
                    Task { await store.retrySelected() }
                }
                .disabled(!store.canRetrySelected)

                Button("Remove Selected Item") {
                    Task { await store.removeSelected() }
                }
                .keyboardShortcut(.delete, modifiers: [])
                .disabled(!store.canRemoveSelected)

                Divider()

                Button("Refresh") {
                    Task { await store.refreshState() }
                }
                .keyboardShortcut("r", modifiers: .command)
                .disabled(store.isRefreshing)
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
