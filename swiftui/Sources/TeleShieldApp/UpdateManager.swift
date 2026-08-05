import AppKit
import Combine
import CryptoKit
import Foundation

struct UpdateVersion: Codable, Comparable, Equatable, Hashable, CustomStringConvertible {
    let major: Int
    let minor: Int
    let patch: Int

    init?(_ rawValue: String) {
        let normalized = rawValue
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
        let components = normalized.split(separator: ".", omittingEmptySubsequences: false)
        guard (2...3).contains(components.count),
              let major = Int(components[0]),
              let minor = Int(components[1]),
              major >= 0,
              minor >= 0 else {
            return nil
        }
        let patch = components.count == 3 ? Int(components[2]) : 0
        guard let patch, patch >= 0 else { return nil }
        self.major = major
        self.minor = minor
        self.patch = patch
    }

    init(major: Int, minor: Int, patch: Int = 0) {
        self.major = major
        self.minor = minor
        self.patch = patch
    }

    var description: String { "\(major).\(minor).\(patch)" }

    static func < (lhs: UpdateVersion, rhs: UpdateVersion) -> Bool {
        (lhs.major, lhs.minor, lhs.patch) < (rhs.major, rhs.minor, rhs.patch)
    }
}

struct AppUpdate: Identifiable, Equatable {
    let id: String
    let version: UpdateVersion
    let tag: String
    let releaseName: String
    let releaseNotes: String
    let releaseURL: URL?
    let downloadURL: URL
    let fileName: String
    let sha256: String
    let architecture: String
}

enum UpdateError: LocalizedError, Equatable {
    case invalidResponse
    case httpStatus(Int)
    case invalidManifest
    case noCompatibleAsset
    case checksumMismatch(expected: String, actual: String)
    case invalidApplication(String)
    case updaterUnavailable
    case processFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "GitHub 回傳的更新資料無法解析。"
        case .httpStatus(let status):
            return "GitHub 更新服務回傳 HTTP \(status)。"
        case .invalidManifest:
            return "更新檔缺少有效的 manifest.json。"
        case .noCompatibleAsset:
            return "找不到適合這台 Mac 架構的更新檔。"
        case .checksumMismatch:
            return "更新檔驗證失敗，已停止更新。"
        case .invalidApplication(let message):
            return "新版 App 驗證失敗：\(message)"
        case .updaterUnavailable:
            return "找不到更新 helper。請使用完整的 TeleShield.app。"
        case .processFailed(let message):
            return "更新工具執行失敗：\(message)"
        }
    }
}

@MainActor
final class UpdateManager: ObservableObject {
    @Published private(set) var availableUpdate: AppUpdate?
    @Published private(set) var isChecking = false
    @Published private(set) var isDownloading = false
    @Published private(set) var lastCheckedAt: Date?
    @Published private(set) var lastError: String?
    @Published private(set) var statusMessage = "尚未檢查"
    @Published private(set) var automaticChecksEnabled: Bool

    private let session: URLSession
    private let latestReleaseURL: URL
    private var automaticTask: Task<Void, Never>?

    static let repository = "caryyu0306/TeleShield-UI"
    static let currentArchitecture: String = {
        #if arch(arm64)
        return "arm64"
        #elseif arch(x86_64)
        return "x86_64"
        #else
        return "unknown"
        #endif
    }()

    var currentVersion: UpdateVersion {
        if let value = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
           let version = UpdateVersion(value) {
            return version
        }
        return UpdateVersion(major: 0, minor: 0, patch: 0)
    }

    init(
        session: URLSession = .shared,
        latestReleaseURL: URL = URL(string: "https://api.github.com/repos/caryyu0306/TeleShield-UI/releases/latest")!
    ) {
        self.session = session
        self.latestReleaseURL = latestReleaseURL
        self.automaticChecksEnabled = UserDefaults.standard.object(forKey: "TeleShield.automaticUpdateChecks") as? Bool ?? true
    }

    deinit {
        automaticTask?.cancel()
    }

    func startAutomaticChecks() {
        guard automaticTask == nil else { return }
        automaticTask = Task { [weak self] in
            guard let self else { return }
            if self.automaticChecksEnabled {
                await self.checkForUpdates()
            }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 6 * 60 * 60 * 1_000_000_000)
                guard !Task.isCancelled else { return }
                if self.automaticChecksEnabled {
                    await self.checkForUpdates()
                }
            }
        }
    }

    func setAutomaticChecksEnabled(_ enabled: Bool) {
        automaticChecksEnabled = enabled
        UserDefaults.standard.set(enabled, forKey: "TeleShield.automaticUpdateChecks")
        if enabled {
            startAutomaticChecks()
        } else {
            automaticTask?.cancel()
            automaticTask = nil
        }
    }

    func checkForUpdates() async {
        guard !isChecking, !isDownloading else { return }
        isChecking = true
        lastError = nil
        statusMessage = "正在檢查更新…"
        defer {
            isChecking = false
            lastCheckedAt = Date()
        }

        do {
            let release: GitHubRelease = try await fetchJSON(from: latestReleaseURL)
            guard !release.draft, !release.prerelease else {
                availableUpdate = nil
                statusMessage = "目前沒有穩定版更新"
                return
            }

            guard let manifestAsset = release.assets.first(where: { $0.name == "manifest.json" }),
                  let manifestURL = manifestAsset.browserDownloadURL else {
                availableUpdate = nil
                statusMessage = "目前版本沒有可用的更新資訊"
                return
            }

            let manifestData: Data = try await fetchData(from: manifestURL, accept: "application/octet-stream")
            let manifest = try JSONDecoder().decode(UpdateManifest.self, from: manifestData)
            guard let manifestVersion = UpdateVersion(manifest.version),
                  manifest.tag == nil || manifest.tag == release.tagName,
                  manifestVersion > currentVersion,
                  let manifestAsset = manifest.assets.first(where: { $0.architecture == Self.currentArchitecture }),
                  let releaseAsset = release.assets.first(where: { $0.name == manifestAsset.fileName }),
                  let downloadURL = releaseAsset.browserDownloadURL,
                  let expectedHash = normalizedHash(manifestAsset.sha256) else {
                availableUpdate = nil
                statusMessage = "目前已是最新版本"
                return
            }

            let update = AppUpdate(
                id: "\(release.tagName)-\(Self.currentArchitecture)",
                version: manifestVersion,
                tag: release.tagName,
                releaseName: release.name.isEmpty ? release.tagName : release.name,
                releaseNotes: release.body?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "",
                releaseURL: release.htmlURL,
                downloadURL: downloadURL,
                fileName: manifestAsset.fileName,
                sha256: expectedHash,
                architecture: Self.currentArchitecture
            )
            availableUpdate = update
            statusMessage = "發現新版本 \(update.version)"
        } catch {
            availableUpdate = nil
            lastError = error.localizedDescription
            statusMessage = "檢查更新失敗"
        }
    }

    func downloadAndInstall(
        _ update: AppUpdate,
        beforeReplacing: @escaping @MainActor () async -> Void
    ) async throws {
        guard !isDownloading else { return }
        isDownloading = true
        lastError = nil
        statusMessage = "正在下載 \(update.version)…"
        defer { isDownloading = false }

        do {
            let stagingRoot = FileManager.default.temporaryDirectory
                .appendingPathComponent("TeleShield-update-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: stagingRoot, withIntermediateDirectories: true)
            let archiveURL = stagingRoot.appendingPathComponent(update.fileName)
            let downloadedURL = try await download(from: update.downloadURL)
            try FileManager.default.moveItem(at: downloadedURL, to: archiveURL)

            let actualHash = try Self.sha256(of: archiveURL)
            guard actualHash.caseInsensitiveCompare(update.sha256) == .orderedSame else {
                throw UpdateError.checksumMismatch(expected: update.sha256, actual: actualHash)
            }

            let extractionRoot = stagingRoot.appendingPathComponent("extracted", isDirectory: true)
            try FileManager.default.createDirectory(at: extractionRoot, withIntermediateDirectories: true)
            try runProcess("/usr/bin/ditto", arguments: ["-x", "-k", archiveURL.path, extractionRoot.path])

            let stagedApp = extractionRoot.appendingPathComponent("TeleShield.app", isDirectory: true)
            try validateApplication(at: stagedApp, expectedVersion: update.version)
            try removeQuarantine(from: stagedApp)
            await beforeReplacing()
            try launchUpdater(stagedApp: stagedApp, stagingRoot: stagingRoot)
            statusMessage = "更新程式已啟動，App 即將重新啟動"
        } catch {
            lastError = error.localizedDescription
            statusMessage = "更新失敗"
            throw error
        }
    }

    private func fetchJSON<Result: Decodable>(from url: URL) async throws -> Result {
        let data: Data = try await fetchData(from: url, accept: "application/vnd.github+json")
        do {
            return try JSONDecoder().decode(Result.self, from: data)
        } catch {
            throw UpdateError.invalidResponse
        }
    }

    private func fetchData(from url: URL, accept: String) async throws -> Data {
        var request = URLRequest(url: url)
        request.setValue("TeleShield/\(currentVersion)", forHTTPHeaderField: "User-Agent")
        request.setValue(accept, forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else { throw UpdateError.invalidResponse }
        guard (200..<300).contains(httpResponse.statusCode) else {
            throw UpdateError.httpStatus(httpResponse.statusCode)
        }
        return data
    }

    private func download(from url: URL) async throws -> URL {
        var request = URLRequest(url: url)
        request.setValue("TeleShield/\(currentVersion)", forHTTPHeaderField: "User-Agent")
        request.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        let (temporaryURL, response) = try await session.download(for: request)
        guard let httpResponse = response as? HTTPURLResponse,
              (200..<300).contains(httpResponse.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            throw UpdateError.httpStatus(status)
        }
        return temporaryURL
    }

    private func validateApplication(at appURL: URL, expectedVersion: UpdateVersion) throws {
        guard FileManager.default.fileExists(atPath: appURL.path) else {
            throw UpdateError.invalidApplication("找不到 TeleShield.app")
        }
        guard let bundle = Bundle(url: appURL) else {
            throw UpdateError.invalidApplication("App bundle 無法讀取")
        }
        guard bundle.bundleIdentifier == "com.caryyu0306.TeleShield" else {
            throw UpdateError.invalidApplication("Bundle ID 不符合 TeleShield")
        }
        guard let rawVersion = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
              let version = UpdateVersion(rawVersion),
              version == expectedVersion else {
            throw UpdateError.invalidApplication("版本號與更新資訊不一致")
        }
        guard let executable = bundle.executableURL,
              FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw UpdateError.invalidApplication("找不到可執行檔")
        }
    }

    private func removeQuarantine(from appURL: URL) throws {
        try runProcess("/usr/bin/xattr", arguments: ["-dr", "com.apple.quarantine", appURL.path], allowFailure: true)
    }

    private func launchUpdater(stagedApp: URL, stagingRoot: URL) throws {
        let helperPath = ProcessInfo.processInfo.environment["TELESHIELD_UPDATER_PATH"]
            ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/TeleShieldUpdater").path
        guard FileManager.default.isExecutableFile(atPath: helperPath) else {
            throw UpdateError.updaterUnavailable
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: helperPath)
        process.arguments = [
            "--pid", String(ProcessInfo.processInfo.processIdentifier),
            "--source", stagedApp.path,
            "--destination", Bundle.main.bundleURL.path,
            "--cleanup", stagingRoot.path,
        ]
        try process.run()
        NSApplication.shared.terminate(nil)
    }

    private func runProcess(_ executable: String, arguments: [String], allowFailure: Bool = false) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let errorPipe = Pipe()
        process.standardError = errorPipe
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            throw UpdateError.processFailed(error.localizedDescription)
        }
        guard process.terminationStatus == 0 || allowFailure else {
            let message = String(data: errorPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? executable
            throw UpdateError.processFailed(message.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    private static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            let data = try handle.read(upToCount: 1024 * 1024) ?? Data()
            if data.isEmpty { break }
            hasher.update(data: data)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func normalizedHash(_ value: String) -> String? {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard normalized.count == 64,
              normalized.allSatisfy({ $0.isHexDigit }) else { return nil }
        return normalized
    }
}

private struct GitHubRelease: Decodable {
    let tagName: String
    let name: String
    let body: String?
    let htmlURL: URL?
    let draft: Bool
    let prerelease: Bool
    let assets: [GitHubAsset]

    enum CodingKeys: String, CodingKey {
        case tagName = "tag_name"
        case name
        case body
        case htmlURL = "html_url"
        case draft
        case prerelease
        case assets
    }
}

private struct GitHubAsset: Decodable {
    let name: String
    let browserDownloadURL: URL?

    enum CodingKeys: String, CodingKey {
        case name
        case browserDownloadURL = "browser_download_url"
    }
}

private struct UpdateManifest: Decodable {
    let version: String
    let tag: String?
    let assets: [UpdateManifestAsset]
}

private struct UpdateManifestAsset: Decodable {
    let architecture: String
    let fileName: String
    let sha256: String

    enum CodingKeys: String, CodingKey {
        case architecture
        case fileName = "file_name"
        case sha256
    }
}
