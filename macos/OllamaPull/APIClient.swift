import Foundation

struct APIClient {
    let baseURL: URL

    func state() async throws -> AppSnapshot {
        try await request("/api/state")
    }

    func search(_ query: String, sourceId: String) async throws -> SearchResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/search"), resolvingAgainstBaseURL: false)
        components?.queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "source", value: sourceId),
        ]
        guard let url = components?.url else {
            throw APIError(message: "Could not build search URL.")
        }
        return try await request(url)
    }

    func queue(_ model: String) async throws -> QueueItem {
        try await request("/api/queue", method: "POST", json: ["model": model])
    }

    func startQueue() async throws -> AppSnapshot {
        try await request("/api/start", method: "POST")
    }

    func pauseAfterCurrent() async throws -> AppSnapshot {
        try await request("/api/pause", method: "POST")
    }

    func stopAfterCurrentBlob() async throws -> AppSnapshot {
        try await request("/api/stop-after-blob", method: "POST")
    }

    func retry(_ item: QueueItem) async throws -> QueueItem {
        try await request("/api/retry/\(encodedPath(item.id))", method: "POST")
    }

    func remove(_ item: QueueItem) async throws -> OKResponse {
        try await request("/api/remove/\(encodedPath(item.id))", method: "POST")
    }

    func deleteInstalledModel(_ model: InstalledModel) async throws -> OKResponse {
        try await request("/api/installed/remove", method: "POST", json: ["model": model.name])
    }

    func scanCleanup(includePartials: Bool, olderThanDays: Int = 7) async throws -> CleanupReport {
        try await request(
            "/api/cleanup/scan",
            method: "POST",
            json: ["include_partials": includePartials, "older_than_days": olderThanDays]
        )
    }

    func deleteCleanupCandidates(includePartials: Bool, olderThanDays: Int = 7) async throws -> CleanupReport {
        try await request(
            "/api/cleanup/delete",
            method: "POST",
            json: ["include_partials": includePartials, "older_than_days": olderThanDays]
        )
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET", json: [String: Any]? = nil) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIError(message: "Local app server is not ready.")
        }
        return try await request(url, method: method, json: json)
    }

    private func request<T: Decodable>(_ url: URL, method: String = "GET", json: [String: Any]? = nil) async throws -> T {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            if let payload = try? JSONDecoder().decode(APIErrorBody.self, from: data), let message = payload.error {
                throw APIError(message: message)
            }
            throw APIError(message: "Request failed with HTTP \(http.statusCode).")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func encodedPath(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? value
    }
}
