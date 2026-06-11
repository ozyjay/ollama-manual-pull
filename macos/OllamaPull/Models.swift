import Foundation

struct AppSnapshot: Decodable {
    let running: Bool
    let pauseRequested: Bool
    let stopAfterBlobRequested: Bool
    let modelsDir: String
    let registry: String
    let retries: Int
    let sources: [ModelSource]
    let installedModels: [InstalledModel]
    let items: [QueueItem]

    enum CodingKeys: String, CodingKey {
        case running
        case pauseRequested = "pause_requested"
        case stopAfterBlobRequested = "stop_after_blob_requested"
        case modelsDir = "models_dir"
        case registry
        case retries
        case sources
        case installedModels = "installed_models"
        case items
    }
}

struct ModelSource: Decodable, Identifiable, Hashable {
    let id: String
    let label: String
    let namespace: String
}

struct InstalledModel: Decodable, Identifiable {
    let name: String
    let namespace: String?
    var id: String { "\(namespace ?? "library")/\(name)" }
}

struct QueueItem: Decodable, Identifiable {
    let id: String
    let model: String
    let canonicalModel: String?
    let deduplicated: Bool?
    let status: String
    let error: String?
    let currentBlob: String?
    let messages: [QueueMessage]
    let progress: DownloadProgress
    let createdAt: Double
    let updatedAt: Double

    enum CodingKeys: String, CodingKey {
        case id
        case model
        case canonicalModel = "canonical_model"
        case deduplicated
        case status
        case error
        case currentBlob = "current_blob"
        case messages
        case progress
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct QueueMessage: Decodable {
    let timestamp: Double?
    let text: String

    init(from decoder: Decoder) throws {
        let single = try decoder.singleValueContainer()
        if let value = try? single.decode(String.self) {
            timestamp = nil
            text = value
            return
        }
        let keyed = try decoder.container(keyedBy: CodingKeys.self)
        timestamp = try keyed.decodeIfPresent(Double.self, forKey: .timestamp)
        text = try keyed.decode(String.self, forKey: .text)
    }

    enum CodingKeys: String, CodingKey {
        case timestamp
        case text
    }
}

struct DownloadProgress: Decodable {
    let phase: String
    let overall: ProgressAmount
    let currentFile: ProgressAmount?

    enum CodingKeys: String, CodingKey {
        case phase
        case overall
        case currentFile = "current_file"
    }
}

struct ProgressAmount: Decodable {
    let digest: String?
    let index: Int?
    let totalFiles: Int?
    let downloaded: Double?
    let total: Double?
    let percent: Double?
    let bytesPerSecond: Double?
    let etaSeconds: Double?
    let line: String?

    enum CodingKeys: String, CodingKey {
        case digest
        case index
        case totalFiles = "total_files"
        case downloaded
        case total
        case percent
        case bytesPerSecond = "bytes_per_second"
        case etaSeconds = "eta_seconds"
        case line
    }
}

struct SearchResponse: Decodable {
    let available: Bool
    let results: [SearchResult]
    let error: String?
}

struct SearchResult: Decodable, Identifiable {
    let name: String?
    let heading: String?
    let description: String?
    let tags: [String]?
    let variants: [SearchVariant]?

    var id: String {
        "\(name ?? "")|\(heading ?? "")|\(description ?? "")"
    }

    var queueableName: String {
        name ?? heading ?? ""
    }
}

struct SearchVariant: Decodable, Identifiable {
    let name: String
    let label: String?

    var id: String { name }

    init(from decoder: Decoder) throws {
        let single = try decoder.singleValueContainer()
        if let value = try? single.decode(String.self) {
            name = value
            label = value.split(separator: ":", maxSplits: 1).last.map(String.init)
            return
        }
        let keyed = try decoder.container(keyedBy: CodingKeys.self)
        name = try keyed.decode(String.self, forKey: .name)
        label = try keyed.decodeIfPresent(String.self, forKey: .label)
    }

    enum CodingKeys: String, CodingKey {
        case name
        case label
    }
}

struct CleanupReport: Decodable {
    let dryRun: Bool
    let includePartials: Bool
    let olderThanDays: Int
    let referencedCount: Int
    let orphanBlobCount: Int
    let orphanBlobBytes: Double
    let stalePartialCount: Int
    let stalePartialBytes: Double
    let skippedPartialCount: Int
    let skippedPartialBytes: Double
    let orphanBlobs: [CleanupFile]
    let stalePartials: [CleanupFile]
    let skippedPartials: [CleanupFile]
    let deleted: [String]
    let warnings: [String]

    var candidateCount: Int {
        orphanBlobCount + stalePartialCount
    }

    enum CodingKeys: String, CodingKey {
        case dryRun = "dry_run"
        case includePartials = "include_partials"
        case olderThanDays = "older_than_days"
        case referencedCount = "referenced_count"
        case orphanBlobCount = "orphan_blob_count"
        case orphanBlobBytes = "orphan_blob_bytes"
        case stalePartialCount = "stale_partial_count"
        case stalePartialBytes = "stale_partial_bytes"
        case skippedPartialCount = "skipped_partial_count"
        case skippedPartialBytes = "skipped_partial_bytes"
        case orphanBlobs = "orphan_blobs"
        case stalePartials = "stale_partials"
        case skippedPartials = "skipped_partials"
        case deleted
        case warnings
    }
}

struct CleanupFile: Decodable, Identifiable {
    let path: String
    let name: String
    let digest: String
    let size: Double
    let modifiedAt: Double
    let type: String

    var id: String { path }

    enum CodingKeys: String, CodingKey {
        case path
        case name
        case digest
        case size
        case modifiedAt = "modified_at"
        case type
    }
}

struct APIErrorBody: Decodable {
    let error: String?
}

struct OKResponse: Decodable {
    let ok: Bool
}

struct APIError: LocalizedError {
    let message: String

    var errorDescription: String? {
        message
    }
}
