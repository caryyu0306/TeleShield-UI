import Foundation

struct AccountSummary: Codable, Identifiable {
    let accountID: String?
    let userID: Int64?
    let username: String
    let displayName: String
    let phoneMasked: String
    let configured: Bool
    let blockedCount: Int
    let kickedCount: Int
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

    enum CodingKeys: String, CodingKey {
        case accountID = "id"
        case userID = "user_id"
        case username
        case displayName = "display_name"
        case phoneMasked = "phone_masked"
        case configured
        case blockedCount = "blocked_count"
        case kickedCount = "kicked_count"
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

struct EmptyResult: Codable {}

struct CoreClientError: LocalizedError {
    let message: String

    var errorDescription: String? { message }
}

struct RPCRequest: Encodable {
    let id: Int
    let method: String
    let params: [String: String]?
}
