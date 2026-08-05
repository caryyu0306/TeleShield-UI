import AppKit
import Foundation

@main
struct TeleShieldUpdaterMain {
    static func main() {
        do {
            let arguments = try UpdaterArguments(arguments: Array(CommandLine.arguments.dropFirst()))
            try waitForProcessToExit(arguments.pid)
            try replaceApplication(source: arguments.source, destination: arguments.destination)
            try removeQuarantine(from: arguments.destination)
            if let cleanup = arguments.cleanup {
                try? FileManager.default.removeItem(at: cleanup)
            }
            try launchApplication(at: arguments.destination)
        } catch {
            fputs("TeleShieldUpdater: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }

    private static func waitForProcessToExit(_ pid: Int32) throws {
        for _ in 0..<600 {
            if !processIsRunning(pid) { return }
            usleep(100_000)
        }
        throw UpdaterError.timeout
    }

    private static func processIsRunning(_ pid: Int32) -> Bool {
        guard pid > 0 else { return false }
        if kill(pid, 0) == 0 { return true }
        return errno == EPERM
    }

    private static func replaceApplication(source: URL, destination: URL) throws {
        let fileManager = FileManager.default
        guard fileManager.fileExists(atPath: source.path) else { throw UpdaterError.missingSource }
        if fileManager.fileExists(atPath: destination.path) {
            _ = try fileManager.replaceItemAt(
                destination,
                withItemAt: source,
                backupItemName: nil,
                options: [.usingNewMetadataOnly]
            )
        } else {
            try fileManager.moveItem(at: source, to: destination)
        }
    }

    private static func removeQuarantine(from url: URL) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
        process.arguments = ["-dr", "com.apple.quarantine", url.path]
        try process.run()
        process.waitUntilExit()
        // xattr exits non-zero when the attribute is already absent.  The
        // updater intentionally treats that as success.
    }

    private static func launchApplication(at url: URL) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = [url.path]
        try process.run()
    }
}

private struct UpdaterArguments {
    let pid: Int32
    let source: URL
    let destination: URL
    let cleanup: URL?

    init(arguments: [String]) throws {
        func value(for flag: String) throws -> String {
            guard let index = arguments.firstIndex(of: flag), arguments.indices.contains(index + 1) else {
                throw UpdaterError.invalidArguments
            }
            return arguments[index + 1]
        }

        guard let pid = Int32(try value(for: "--pid")) else { throw UpdaterError.invalidArguments }
        self.pid = pid
        self.source = URL(fileURLWithPath: try value(for: "--source"))
        self.destination = URL(fileURLWithPath: try value(for: "--destination"))
        if let index = arguments.firstIndex(of: "--cleanup"), arguments.indices.contains(index + 1) {
            cleanup = URL(fileURLWithPath: arguments[index + 1])
        } else {
            cleanup = nil
        }
    }
}

private enum UpdaterError: LocalizedError {
    case invalidArguments
    case missingSource
    case timeout

    var errorDescription: String? {
        switch self {
        case .invalidArguments: return "缺少更新參數。"
        case .missingSource: return "找不到暫存的新版 App。"
        case .timeout: return "等待 TeleShield 關閉逾時。"
        }
    }
}
