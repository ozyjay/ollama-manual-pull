import SwiftUI

struct ContentView: View {
    @ObservedObject var store: AppStore

    var body: some View {
        VStack(spacing: 12) {
            Text("Ollama Manual Pull")
                .font(.headline)
            BottomCommandBar(isRefreshing: false) {
                Task {
                    await store.refreshState()
                }
            }
        }
        .frame(minWidth: 480, minHeight: 320)
        .padding()
    }
}

struct BottomCommandBar: View {
    let isRefreshing: Bool
    let refresh: () -> Void

    var body: some View {
        HStack {
            Button(isRefreshing ? "Refreshing" : "Refresh", action: refresh)
                .disabled(isRefreshing)
        }
    }
}
