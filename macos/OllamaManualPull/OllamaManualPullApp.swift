import AppKit
import SwiftUI

@main
struct OllamaManualPullApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = AppStore()
    @StateObject private var supervisor = PythonServerSupervisor()

    var body: some Scene {
        WindowGroup("Ollama Manual Pull") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 1040, minHeight: 700)
                .background(HostingViewMarker())
                .onAppear {
                    appDelegate.onTerminate = {
                        store.stopRefreshLoop()
                        supervisor.stop()
                    }
                    supervisor.onURL = { store.connect(to: $0) }
                    supervisor.onStartupError = { store.showStartupError($0) }
                    supervisor.start()
                    NSApplication.shared.activate(ignoringOtherApps: true)
                }
        }
        .commands {
            CommandGroup(replacing: .newItem) {}

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

                Button("Stop After Blob") {
                    Task { await store.stopAfterCurrentBlob() }
                }
                .disabled(!store.canStopAfterBlob)

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

final class AppDelegate: NSObject, NSApplicationDelegate {
    var onTerminate: (() -> Void)?

    func applicationWillTerminate(_ notification: Notification) {
        onTerminate?()
    }
}

private struct HostingViewMarker: NSViewRepresentable {
    func makeNSView(context: Context) -> NSHostingView<EmptyView> {
        NSHostingView(rootView: EmptyView())
    }

    func updateNSView(_ nsView: NSHostingView<EmptyView>, context: Context) {}
}
