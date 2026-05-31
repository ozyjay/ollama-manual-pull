import SwiftUI

enum AppFormatters {
    static func date(_ seconds: Double) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .medium
        return formatter.string(from: Date(timeIntervalSince1970: seconds))
    }

    static func bytes(_ value: Double?) -> String {
        guard var size = value, size.isFinite else { return "Unknown" }
        let units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units {
            if abs(size) < 1000 || unit == units.last {
                return unit == "B" ? "\(Int(size.rounded()))B" : String(format: "%.1f%@", size, unit)
            }
            size /= 1000
        }
        return String(format: "%.1fTB", size)
    }

    static func eta(_ seconds: Double) -> String {
        let safeSeconds = max(0, Int(seconds.rounded()))
        let minutes = safeSeconds / 60
        let remaining = safeSeconds % 60
        if minutes >= 60 {
            return "\(minutes / 60)h \(minutes % 60)m"
        }
        return "\(minutes)m \(String(format: "%02d", remaining))s"
    }

    static func statusColor(_ status: String) -> Color {
        switch status {
        case "running": return .blue
        case "completed": return .green
        case "failed": return .red
        case "waiting": return .orange
        default: return .secondary
        }
    }
}
