import Combine
import Foundation

@MainActor
final class PythonServerSupervisor: ObservableObject {
    private var serverTask: Process?
    private var outputBuffer = ""
    private var didEmitServerURL = false
    private var processGeneration = 0
    var onURL: ((URL) -> Void)?
    var onStartupError: ((String) -> Void)?

    func start() {
        if let task = serverTask, task.isRunning {
            return
        }
        if serverTask != nil {
            cleanupServerTask(terminate: false)
        }
        guard let resourcesURL = Bundle.main.resourceURL else {
            onStartupError?("Could not locate bundled app resources.")
            return
        }
        resetProcessState()
        let generation = processGeneration
        let task = Process()
        let output = Pipe()
        let errorOutput = Pipe()
        task.executableURL = URL(fileURLWithPath: resolvedPython())
        task.arguments = ["-c", AppConfig.serverCommand]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = resourcesURL.appendingPathComponent("src").path
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        task.environment = environment
        task.standardOutput = output
        task.standardError = errorOutput
        task.terminationHandler = { [weak self, weak task] _ in
            DispatchQueue.main.async {
                guard let self, let task else { return }
                self.cleanupFinishedTask(task, generation: generation)
            }
        }
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.appendServerOutput(chunk, generation: generation) }
        }
        errorOutput.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.appendServerError(chunk, generation: generation) }
        }
        do {
            try task.run()
            serverTask = task
        } catch {
            resetProcessState()
            onStartupError?("Could not start the local app server: \(error.localizedDescription)")
        }
    }

    func stop() {
        cleanupServerTask(terminate: true)
    }

    private func cleanupServerTask(terminate: Bool) {
        guard let task = serverTask else {
            resetProcessState()
            return
        }
        clearReadabilityHandlers(for: task)
        if terminate, task.isRunning {
            task.terminate()
        }
        serverTask = nil
        resetProcessState()
    }

    private func cleanupFinishedTask(_ task: Process, generation: Int) {
        guard generation == processGeneration else { return }
        guard serverTask === task else { return }
        clearReadabilityHandlers(for: task)
        serverTask = nil
        resetProcessState()
    }

    private func clearReadabilityHandlers(for task: Process) {
        if let output = task.standardOutput as? Pipe {
            output.fileHandleForReading.readabilityHandler = nil
        }
        if let error = task.standardError as? Pipe {
            error.fileHandleForReading.readabilityHandler = nil
        }
    }

    private func resetProcessState() {
        processGeneration += 1
        outputBuffer = ""
        didEmitServerURL = false
    }

    private func appendServerOutput(_ chunk: String, generation: Int) {
        guard generation == processGeneration else { return }
        guard !didEmitServerURL else { return }
        outputBuffer += chunk
        guard let prefix = outputBuffer.range(of: "URL=") else { return }
        let tail = outputBuffer[prefix.upperBound...]
        guard let end = tail.firstIndex(where: { $0 == "\n" || $0 == "\r" }) else { return }
        let urlString = String(tail[..<end])
        guard let url = URL(string: urlString) else {
            onStartupError?("The local server returned an invalid URL: \(urlString)")
            return
        }
        didEmitServerURL = true
        onURL?(url)
    }

    private func appendServerError(_ chunk: String, generation: Int) {
        guard generation == processGeneration else { return }
        guard !didEmitServerURL else { return }
        onStartupError?(chunk.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private func resolvedPython() -> String {
        let manager = FileManager.default
        if manager.isExecutableFile(atPath: AppConfig.bundledPython) {
            return AppConfig.bundledPython
        }
        let home = NSHomeDirectory()
        let candidates = [
            "\(home)/.pyenv/shims/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]
        return candidates.first { manager.isExecutableFile(atPath: $0) } ?? "/usr/bin/python3"
    }
}
