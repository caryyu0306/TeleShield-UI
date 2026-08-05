import Foundation

/// JSON values used by the line-delimited RPC bridge. This keeps booleans,
/// numbers, arrays, and nested objects typed instead of stringifying them.
enum JSONValue: Codable, Equatable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        case .double(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}

enum AuthenticationPresentation {
    static func shouldDismissLoginSheet(
        event: String,
        accountID: String?,
        targetAccountID: String?
    ) -> Bool {
        event == "auth_succeeded" && accountID != nil && accountID == targetAccountID
    }
}

struct AccountSummary: Codable, Identifiable {
    let accountID: String?
    let userID: Int64?
    let username: String
    let displayName: String
    let phoneMasked: String
    let configured: Bool
    let blockedCount: Int
    let recentBlockCount: Int
    let whitelistCount: Int
    let blacklistCount: Int
    let learnedKeywordCount: Int
    let lastScan: String?
    let running: Bool
    let ready: Bool
    let state: String
    let error: String?

    var id: String { accountID ?? "__default__" }
    var label: String { displayName.isEmpty ? (username.isEmpty ? id : "@\(username)") : displayName }

    enum CodingKeys: String, CodingKey {
        case accountID = "id"
        case userID = "user_id"
        case username
        case displayName = "display_name"
        case phoneMasked = "phone_masked"
        case configured
        case blockedCount = "blocked_count"
        case recentBlockCount = "recent_block_count"
        case whitelistCount = "whitelist_count"
        case blacklistCount = "blacklist_count"
        case learnedKeywordCount = "learned_keyword_count"
        case lastScan = "last_scan"
        case running
        case ready
        case state
        case error
    }
}

struct OCRStatus: Codable {
    let available: Bool
    let bundled: Bool
    let languages: [String]
}

struct CoreStatus: Codable {
    let activeAccountID: String?
    let selectedAccount: AccountSummary
    let accounts: [AccountSummary]
    let ocr: OCRStatus

    enum CodingKeys: String, CodingKey {
        case activeAccountID = "active_account_id"
        case selectedAccount = "selected_account"
        case accounts
        case ocr
    }
}

struct ScanSettings: Codable {
    var privateDialogLimit: Int
    var privateMessageLimit: Int
    var privateDays: Int

    enum CodingKeys: String, CodingKey {
        case privateDialogLimit = "private_dialog_limit"
        case privateMessageLimit = "private_message_limit"
        case privateDays = "private_days"
    }

    static let defaults = ScanSettings(
        privateDialogLimit: 30,
        privateMessageLimit: 5,
        privateDays: 14
    )
}

struct LearnedPatterns: Codable {
    var keywords: [String]
    var patterns: [String]

    static let empty = LearnedPatterns(keywords: [], patterns: [])
}

enum PrivateHistoryDeletionScope: String, Codable, CaseIterable, Identifiable {
    case selfOnly = "self"
    case both = "both"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .selfOnly: return "只刪除自己"
        case .both: return "嘗試從雙方刪除"
        }
    }
}

enum ProtectionMode: String, Codable, CaseIterable, Identifiable {
    case normal
    case strict

    var id: String { rawValue }

    var title: String {
        switch self {
        case .normal: return "一般模式"
        case .strict: return "嚴格模式"
        }
    }

    var detail: String {
        switch self {
        case .normal:
            return "只封鎖高信心判定為垃圾訊息的非聯絡人。"
        case .strict:
            return "非聯絡人一律封鎖；只有聯絡人與白名單可以傳訊息。"
        }
    }
}

struct TelegramNotificationPolicy: Codable, Equatable {
    var enabled: Bool
    var botToken: String
    var channelID: String

    static let defaults = TelegramNotificationPolicy(
        enabled: false,
        botToken: "",
        channelID: ""
    )

    enum CodingKeys: String, CodingKey {
        case enabled
        case botToken = "bot_token"
        case channelID = "channel_id"
    }

    init(enabled: Bool, botToken: String, channelID: String) {
        self.enabled = enabled
        self.botToken = botToken
        self.channelID = channelID
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        enabled = (try? container.decode(Bool.self, forKey: .enabled)) ?? Self.defaults.enabled
        botToken = (try? container.decode(String.self, forKey: .botToken)) ?? Self.defaults.botToken
        channelID = (try? container.decode(String.self, forKey: .channelID)) ?? Self.defaults.channelID
    }
}

struct ModerationPolicy: Codable, Equatable {
    var protectionMode: ProtectionMode
    var deletePrivateHistoryAfterBlock: Bool
    var deletePrivateHistoryScope: PrivateHistoryDeletionScope
    var telegramNotification: TelegramNotificationPolicy

    static let defaults = ModerationPolicy(
        protectionMode: .normal,
        deletePrivateHistoryAfterBlock: false,
        deletePrivateHistoryScope: .selfOnly,
        telegramNotification: .defaults
    )

    enum CodingKeys: String, CodingKey {
        case protectionMode = "protection_mode"
        case deletePrivateHistoryAfterBlock = "delete_private_history_after_block"
        case deletePrivateHistoryScope = "delete_private_history_scope"
        case telegramNotification = "telegram_notification"
    }

    init(
        protectionMode: ProtectionMode = .normal,
        deletePrivateHistoryAfterBlock: Bool,
        deletePrivateHistoryScope: PrivateHistoryDeletionScope,
        telegramNotification: TelegramNotificationPolicy
    ) {
        self.protectionMode = protectionMode
        self.deletePrivateHistoryAfterBlock = deletePrivateHistoryAfterBlock
        self.deletePrivateHistoryScope = deletePrivateHistoryScope
        self.telegramNotification = telegramNotification
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let rawMode = try container.decodeIfPresent(
            String.self,
            forKey: .protectionMode
        ) ?? Self.defaults.protectionMode.rawValue
        protectionMode = ProtectionMode(rawValue: rawMode) ?? .normal
        deletePrivateHistoryAfterBlock = try container.decodeIfPresent(
            Bool.self,
            forKey: .deletePrivateHistoryAfterBlock
        ) ?? Self.defaults.deletePrivateHistoryAfterBlock
        let rawScope = try container.decodeIfPresent(
            String.self,
            forKey: .deletePrivateHistoryScope
        ) ?? Self.defaults.deletePrivateHistoryScope.rawValue
        deletePrivateHistoryScope = PrivateHistoryDeletionScope(rawValue: rawScope) ?? .selfOnly
        telegramNotification = try container.decodeIfPresent(
            TelegramNotificationPolicy.self,
            forKey: .telegramNotification
        ) ?? Self.defaults.telegramNotification
    }
}

struct AccountDetails: Codable {
    let accountID: String?
    let loggedIn: Bool
    let hasAPICredentials: Bool
    let scanSettings: ScanSettings
    let learnedPatterns: LearnedPatterns
    let autoStart: Bool
    let autoStartAccountID: String?
    let autoStartAccountIDs: [String]
    let moderationPolicy: ModerationPolicy?

    enum CodingKeys: String, CodingKey {
        case accountID = "account_id"
        case loggedIn = "logged_in"
        case hasAPICredentials = "has_api_credentials"
        case scanSettings = "scan_settings"
        case learnedPatterns = "learned_patterns"
        case autoStart = "auto_start"
        case autoStartAccountID = "auto_start_account_id"
        case autoStartAccountIDs = "auto_start_account_ids"
        case moderationPolicy = "moderation_policy"
    }
}

struct PrivacyCheck: Codable, Identifiable {
    let id: String
    let title: String
    let description: String
    let current: String
    let recommended: String
    let status: String
    let supported: Bool
    let editable: Bool
    let premiumRequired: Bool
    let exceptionCount: Int
    let error: String?

    var isHealthy: Bool { status == "ok" }

    var statusTitle: String {
        switch status {
        case "ok": return "良好"
        case "unsupported": return "不支援"
        case "error": return "讀取失敗"
        case "premium_required": return "需要 Premium"
        default: return "建議調整"
        }
    }

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case description
        case current
        case recommended
        case status
        case supported
        case editable
        case premiumRequired = "premium_required"
        case exceptionCount = "exception_count"
        case error
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? container.decode(String.self, forKey: .id)) ?? UUID().uuidString
        title = (try? container.decode(String.self, forKey: .title)) ?? "隱私設定"
        description = (try? container.decode(String.self, forKey: .description)) ?? ""
        current = (try? container.decode(String.self, forKey: .current)) ?? "未知"
        recommended = (try? container.decode(String.self, forKey: .recommended)) ?? ""
        status = (try? container.decode(String.self, forKey: .status)) ?? "warning"
        supported = (try? container.decode(Bool.self, forKey: .supported)) ?? true
        editable = (try? container.decode(Bool.self, forKey: .editable)) ?? false
        premiumRequired = (try? container.decode(Bool.self, forKey: .premiumRequired)) ?? false
        exceptionCount = (try? container.decode(Int.self, forKey: .exceptionCount)) ?? 0
        error = try? container.decode(String.self, forKey: .error)
    }
}

struct PrivacySession: Codable, Identifiable {
    let hash: String
    let current: Bool
    let deviceModel: String
    let platform: String
    let systemVersion: String
    let appName: String
    let appVersion: String
    let dateActive: String?
    let ip: String
    let country: String
    let officialApp: Bool

    var id: String { hash }

    var deviceTitle: String {
        let title = [deviceModel, platform].filter { !$0.isEmpty }.joined(separator: " · ")
        return title.isEmpty ? "未知裝置" : title
    }

    var appTitle: String {
        guard !appName.isEmpty else { return appVersion }
        return appVersion.isEmpty ? appName : "\(appName) \(appVersion)"
    }

    enum CodingKeys: String, CodingKey {
        case hash
        case current
        case deviceModel = "device_model"
        case platform
        case systemVersion = "system_version"
        case appName = "app_name"
        case appVersion = "app_version"
        case dateActive = "date_active"
        case ip
        case country
        case officialApp = "official_app"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        hash = (try? container.decode(String.self, forKey: .hash)) ?? ""
        current = (try? container.decode(Bool.self, forKey: .current)) ?? false
        deviceModel = (try? container.decode(String.self, forKey: .deviceModel)) ?? ""
        platform = (try? container.decode(String.self, forKey: .platform)) ?? ""
        systemVersion = (try? container.decode(String.self, forKey: .systemVersion)) ?? ""
        appName = (try? container.decode(String.self, forKey: .appName)) ?? ""
        appVersion = (try? container.decode(String.self, forKey: .appVersion)) ?? ""
        dateActive = try? container.decode(String.self, forKey: .dateActive)
        ip = (try? container.decode(String.self, forKey: .ip)) ?? ""
        country = (try? container.decode(String.self, forKey: .country)) ?? ""
        officialApp = (try? container.decode(Bool.self, forKey: .officialApp)) ?? false
    }
}

struct PrivacyAudit: Codable {
    let accountID: String?
    let premium: Bool
    let premiumStatus: String
    let generatedAt: String
    let checks: [PrivacyCheck]
    let username: String
    let twoFactorEnabled: Bool
    let twoFactorRecoveryConfigured: Bool
    let twoFactorHint: String
    let sessions: [PrivacySession]
    let unknownSessionCount: Int
    let backupAvailable: Bool

    var healthyCheckCount: Int { checks.filter(\.isHealthy).count }
    var warningCheckCount: Int { checks.filter { !$0.isHealthy }.count }

    enum CodingKeys: String, CodingKey {
        case accountID = "account_id"
        case premium
        case premiumStatus = "premium_status"
        case generatedAt = "generated_at"
        case checks
        case username
        case twoFactorEnabled = "two_factor_enabled"
        case twoFactorRecoveryConfigured = "two_factor_recovery_configured"
        case twoFactorHint = "two_factor_hint"
        case sessions
        case unknownSessionCount = "unknown_session_count"
        case backupAvailable = "backup_available"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        accountID = try? container.decode(String.self, forKey: .accountID)
        premium = (try? container.decode(Bool.self, forKey: .premium)) ?? false
        premiumStatus = (try? container.decode(String.self, forKey: .premiumStatus)) ?? "unknown"
        generatedAt = (try? container.decode(String.self, forKey: .generatedAt)) ?? ""
        checks = (try? container.decode([PrivacyCheck].self, forKey: .checks)) ?? []
        username = (try? container.decode(String.self, forKey: .username)) ?? ""
        twoFactorEnabled = (try? container.decode(Bool.self, forKey: .twoFactorEnabled)) ?? false
        twoFactorRecoveryConfigured = (try? container.decode(Bool.self, forKey: .twoFactorRecoveryConfigured)) ?? false
        twoFactorHint = (try? container.decode(String.self, forKey: .twoFactorHint)) ?? ""
        sessions = (try? container.decode([PrivacySession].self, forKey: .sessions)) ?? []
        unknownSessionCount = (try? container.decode(Int.self, forKey: .unknownSessionCount)) ?? 0
        backupAvailable = (try? container.decode(Bool.self, forKey: .backupAvailable)) ?? false
    }
}

struct ProtectionActionResult: Codable {
    let accountID: String?
    let running: Bool
    let ready: Bool?
    let state: String?

    enum CodingKeys: String, CodingKey {
        case accountID = "account_id"
        case running
        case ready
        case state
    }
}

struct TelegramNotificationTestResult: Codable {
    let sent: Bool
}

struct AuthStartResult: Codable {
    let flowID: String
    let accountID: String?
    let running: Bool

    enum CodingKeys: String, CodingKey {
        case flowID = "flow_id"
        case accountID = "account_id"
        case running
    }
}

struct AuthSubmissionResult: Codable {
    let flowID: String
    let accepted: Bool

    enum CodingKeys: String, CodingKey {
        case flowID = "flow_id"
        case accepted
    }
}

struct JobStartResult: Codable {
    let jobID: String
    let accountID: String?
    let running: Bool

    enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case accountID = "account_id"
        case running
    }
}

struct ListEntry: Codable, Identifiable {
    let userID: String
    let username: String
    let added: String
    let reason: String

    var id: String { userID }

    enum CodingKeys: String, CodingKey {
        case userID = "user_id"
        case username
        case added
        case reason
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let value = try? container.decode(String.self, forKey: .userID) {
            userID = value
        } else if let value = try? container.decode(Int64.self, forKey: .userID) {
            userID = String(value)
        } else {
            userID = ""
        }
        username = (try? container.decode(String.self, forKey: .username)) ?? ""
        added = (try? container.decode(String.self, forKey: .added)) ?? ""
        reason = (try? container.decode(String.self, forKey: .reason)) ?? ""
    }
}

enum TimestampFormatter {
    private static let fractionalISO8601: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let ISO8601: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func localString(_ rawValue: String, timeZone: TimeZone = .autoupdatingCurrent) -> String {
        let normalized = rawValue.replacingOccurrences(of: " ", with: "T")
        guard let date = fractionalISO8601.date(from: normalized) ?? ISO8601.date(from: normalized) else {
            return rawValue.replacingOccurrences(of: "T", with: " ")
        }

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.string(from: date)
    }
}

struct BlockAnalysis: Codable {
    let reasonCode: String
    let categoryLabels: [String]
    let intentLabels: [String]
    let phishingLabels: [String]
    let matchedRuleLabels: [String]
    let score: Int?
    let threshold: Int?
    let scoreType: String
    let scoreTypeLabel: String
    let analysisSource: String
    let analysisSourceLabel: String
    let contentExcerpt: String
    let senderContextLabels: [String]

    enum CodingKeys: String, CodingKey {
        case reasonCode = "reason_code"
        case categoryLabels = "category_labels"
        case intentLabels = "intent_labels"
        case phishingLabels = "phishing_labels"
        case matchedRuleLabels = "matched_rule_labels"
        case score
        case threshold
        case scoreType = "score_type"
        case scoreTypeLabel = "score_type_label"
        case analysisSource = "analysis_source"
        case analysisSourceLabel = "analysis_source_label"
        case contentExcerpt = "content_excerpt"
        case senderContextLabels = "sender_context_labels"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        reasonCode = (try? container.decode(String.self, forKey: .reasonCode)) ?? ""
        categoryLabels = (try? container.decode([String].self, forKey: .categoryLabels)) ?? []
        intentLabels = (try? container.decode([String].self, forKey: .intentLabels)) ?? []
        phishingLabels = (try? container.decode([String].self, forKey: .phishingLabels)) ?? []
        matchedRuleLabels = (try? container.decode([String].self, forKey: .matchedRuleLabels)) ?? []
        score = try? container.decode(Int.self, forKey: .score)
        threshold = try? container.decode(Int.self, forKey: .threshold)
        scoreType = (try? container.decode(String.self, forKey: .scoreType)) ?? ""
        scoreTypeLabel = (try? container.decode(String.self, forKey: .scoreTypeLabel)) ?? ""
        analysisSource = (try? container.decode(String.self, forKey: .analysisSource)) ?? ""
        analysisSourceLabel = (try? container.decode(String.self, forKey: .analysisSourceLabel)) ?? ""
        contentExcerpt = (try? container.decode(String.self, forKey: .contentExcerpt)) ?? ""
        senderContextLabels = (try? container.decode([String].self, forKey: .senderContextLabels)) ?? []
    }
}

struct BlockRecordDetails: Codable {
    let analysis: BlockAnalysis?

    enum CodingKeys: String, CodingKey {
        case analysis
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        analysis = try? container.decode(BlockAnalysis.self, forKey: .analysis)
    }
}

struct BlockRecord: Codable, Identifiable {
    let time: String
    let source: String
    let userID: String
    let name: String
    let reason: String
    let details: BlockRecordDetails?

    var id: String { "\(time)-\(userID)-\(name)" }
    var displayTime: String { TimestampFormatter.localString(time) }

    enum CodingKeys: String, CodingKey {
        case time
        case source
        case userID = "user_id"
        case name
        case reason
        case details
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        time = (try? container.decode(String.self, forKey: .time)) ?? ""
        source = (try? container.decode(String.self, forKey: .source)) ?? ""
        if let value = try? container.decode(String.self, forKey: .userID) {
            userID = value
        } else if let value = try? container.decode(Int64.self, forKey: .userID) {
            userID = String(value)
        } else {
            userID = ""
        }
        name = (try? container.decode(String.self, forKey: .name)) ?? ""
        reason = (try? container.decode(String.self, forKey: .reason)) ?? ""
        details = try? container.decode(BlockRecordDetails.self, forKey: .details)
    }
}

struct ScanFinding: Codable, Identifiable {
    let userID: String
    let name: String
    let reason: String

    var id: String { "\(userID)-\(reason)" }

    enum CodingKeys: String, CodingKey {
        case userID = "user_id"
        case name
        case reason
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let value = try? container.decode(String.self, forKey: .userID) {
            userID = value
        } else if let value = try? container.decode(Int64.self, forKey: .userID) {
            userID = String(value)
        } else {
            userID = ""
        }
        name = (try? container.decode(String.self, forKey: .name)) ?? userID
        reason = (try? container.decode(String.self, forKey: .reason)) ?? ""
    }
}

struct ScanResult: Codable {
    let accountID: String?
    let scope: String
    let dryRun: Bool
    let dialogsSeen: Int
    let dialogsScanned: Int
    let messagesScanned: Int
    let matched: Int
    let acted: Int
    let errors: [String]
    let findings: [ScanFinding]
    let cancelled: Bool

    enum CodingKeys: String, CodingKey {
        case accountID = "account_id"
        case scope
        case dryRun = "dry_run"
        case dialogsSeen = "dialogs_seen"
        case dialogsScanned = "dialogs_scanned"
        case messagesScanned = "messages_scanned"
        case matched
        case acted
        case errors
        case findings
        case cancelled
    }
}

struct Report: Codable {
    let period: String
    let label: String
    let total: Int
    let bySource: [String: Int]
    let byReason: [String: Int]
    let trend: [String: Int]
    let records: [BlockRecord]

    enum CodingKeys: String, CodingKey {
        case period
        case label
        case total
        case bySource = "by_source"
        case byReason = "by_reason"
        case trend
        case records
    }
}

struct RPCErrorPayload: Codable {
    let type: String
    let message: String
}

struct RPCResponse<Result: Decodable>: Decodable {
    let id: Int?
    let ok: Bool
    let result: Result?
    let error: RPCErrorPayload?
}

struct RPCRequest: Encodable {
    let id: Int
    let method: String
    let params: [String: JSONValue]?
}

struct CoreClientError: LocalizedError {
    let message: String

    var errorDescription: String? { message }
}

struct EventLogEntry: Identifiable {
    let id = UUID()
    let time = Date()
    let level: String
    let message: String
}
