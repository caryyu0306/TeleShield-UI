import Combine
import Foundation

@MainActor
final class CoreClient: ObservableObject {
    @Published private(set) var status: CoreStatus?
    @Published private(set) var connectionMessage = "尚未啟動"
    @Published private(set) var lastLog = ""
    @Published private(set) var errorMessage: String?
    @Published private(set) var isBusy = false
    @Published private(set) var authFlowID: String?
    @Published private(set) var authChallengeKind: String?
    @Published private(set) var authDeliveryMessage = ""
    @Published private(set) var authInProgress = false

    private var process: Process?
    private var inputHandle: FileHandle?
    private var outputHandle: FileHandle?
    private var stdoutBuffer = Data()
    private var nextRequestID = 1
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]

    var selectedAccount: AccountSummary? {
        status?.selectedAccount
    }

    var helperIsRunning: Bool {
        process?.isRunning == true
    }

    func launch() {
        guard process == nil else { return }
        guard let helperPath = resolveHelperPath() else {
            errorMessage = "找不到 TeleShieldCore。請從 DMG 啟動完整的 TeleShield.app。"
            connectionMessage = "sidecar 不存在"
            return
        }

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        let child = Process()
        child.executableURL = URL(fileURLWithPath: helperPath)
        child.arguments = ["--stdio"]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        child.environment = environment
        child.standardInput = stdinPipe
        child.standardOutput = stdoutPipe
        child.standardError = stderrPipe

        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            Task { @MainActor [weak self] in
                self?.consume(data)
            }
        }
        stderrPipe.fileHandleForReading.readabilityHandler = { handle in
            _ = handle.availableData
        }
        child.terminationHandler = { [weak self] terminatedProcess in
            let status = terminatedProcess.terminationStatus
            Task { @MainActor [weak self] in
                self?.sidecarTerminated(exitCode: status)
            }
        }

        do {
            try child.run()
        } catch {
            errorMessage = "無法啟動 Python sidecar：\(error.localizedDescription)"
            connectionMessage = "sidecar 啟動失敗"
            return
        }

        process = child
        inputHandle = stdinPipe.fileHandleForWriting
        outputHandle = stdoutPipe.fileHandleForReading
        connectionMessage = "sidecar 已啟動"
        errorMessage = nil

        Task { [weak self] in
            await self?.refresh()
        }
    }

    func refresh() async {
        guard helperIsRunning else { return }
        do {
            let data = try await request(method: "get_status")
            status = try decodeResult(CoreStatus.self, from: data)
            connectionMessage = "已連線"
            errorMessage = nil
        } catch {
            present(error: error)
        }
    }

    func startProtection() async {
        await performAction("start_protection")
    }

    func stopProtection() async {
        await performAction("stop_protection")
    }

    func selectAccount(_ accountID: String) async {
        isBusy = true
        defer { isBusy = false }
        do {
            _ = try await request(
                method: "select_account",
                params: ["account_id": accountID]
            )
            await refresh()
        } catch {
            present(error: error)
        }
    }

    func startAuthentication(
        apiID: String,
        apiHash: String,
        phone: String
    ) async {
        isBusy = true
        authInProgress = true
        errorMessage = nil
        defer { isBusy = false }
        do {
            let data = try await request(
                method: "start_auth",
                params: [
                    "api_id": apiID,
                    "api_hash": apiHash,
                    "phone": phone,
                ]
            )
            let result = try decodeResult(AuthStartResult.self, from: data)
            authFlowID = result.flowID
            connectionMessage = "等待 Telegram 驗證"
        } catch {
            authInProgress = false
            present(error: error)
        }
    }

    func submitAuthCode(_ value: String) async {
        await submitAuthValue(value, method: "submit_auth_code")
    }

    func submitAuthPassword(_ value: String) async {
        await submitAuthValue(value, method: "submit_auth_password")
    }

    func cancelAuthentication() async {
        guard let authFlowID else { return }
        do {
            _ = try await request(
                method: "cancel_auth",
                params: ["flow_id": authFlowID]
            )
        } catch {
            present(error: error)
        }
        authInProgress = false
        self.authFlowID = nil
        authChallengeKind = nil
    }

    private func submitAuthValue(_ value: String, method: String) async {
        guard let authFlowID else { return }
        do {
            _ = try await request(
                method: method,
                params: ["flow_id": authFlowID, "value": value]
            )
            authChallengeKind = nil
        } catch {
            present(error: error)
        }
    }

    func shutdown() {
        guard inputHandle != nil else { return }
        let request = RPCRequest(id: nextRequestID, method: "shutdown", params: nil)
        nextRequestID += 1
        guard var data = try? JSONEncoder().encode(request) else { return }
        data.append(0x0A)
        try? inputHandle?.write(contentsOf: data)
    }

    private func performAction(_ method: String) async {
        isBusy = true
        defer { isBusy = false }
        do {
            let params = selectedAccount?.accountID.map { ["account_id": $0] }
            _ = try await request(method: method, params: params)
            await refresh()
        } catch {
            present(error: error)
        }
    }

    private func request(
        method: String,
        params: [String: String]? = nil
    ) async throws -> Data {
        guard process?.isRunning == true, let inputHandle else {
            throw CoreClientError(message: "Python sidecar 尚未啟動")
        }

        let requestID = nextRequestID
        nextRequestID += 1
        let request = RPCRequest(id: requestID, method: method, params: params)
        var data = try JSONEncoder().encode(request)
        data.append(0x0A)

        return try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<Data, Error>) in
            pending[requestID] = continuation
            do {
                try inputHandle.write(contentsOf: data)
            } catch {
                pending.removeValue(forKey: requestID)
                continuation.resume(throwing: error)
            }
        }
    }

    private func decodeResult<Result: Decodable>(
        _ type: Result.Type,
        from data: Data
    ) throws -> Result {
        let response = try JSONDecoder().decode(RPCResponse<Result>.self, from: data)
        guard response.ok else {
            throw CoreClientError(
                message: response.error?.message ?? "sidecar 回傳未知錯誤"
            )
        }
        guard let result = response.result else {
            throw CoreClientError(message: "sidecar 回傳缺少 result")
        }
        return result
    }

    private func consume(_ data: Data) {
        stdoutBuffer.append(data)
        while let newline = stdoutBuffer.firstIndex(of: 0x0A) {
            let line = stdoutBuffer.subdata(in: 0..<newline)
            stdoutBuffer.removeSubrange(0...newline)
            guard !line.isEmpty else { continue }
            handleLine(line)
        }
    }

    private func handleLine(_ line: Data) {
        guard
            let object = try? JSONSerialization.jsonObject(with: line),
            let dictionary = object as? [String: Any]
        else {
            lastLog = String(data: line, encoding: .utf8) ?? "sidecar 傳回無法解析的資料"
            return
        }

        if let event = dictionary["event"] as? String {
            handleEvent(event, dictionary: dictionary)
            return
        }

        guard let requestID = dictionary["id"] as? Int,
              let continuation = pending.removeValue(forKey: requestID)
        else {
            return
        }
        continuation.resume(returning: line)
    }

    private func handleEvent(_ event: String, dictionary: [String: Any]) {
        switch event {
        case "log":
            if let message = dictionary["message"] as? String {
                lastLog = message
            }
        case "status":
            let state = dictionary["state"] as? String ?? "unknown"
            connectionMessage = "listener：\(state)"
            if state == "error", let message = dictionary["error"] as? String {
                errorMessage = message
            }
            Task { [weak self] in
                await self?.refresh()
            }
        case "auth_delivery":
            authDeliveryMessage = dictionary["message"] as? String ?? ""
            connectionMessage = "驗證碼已發送"
        case "auth_challenge":
            authFlowID = dictionary["flow_id"] as? String ?? authFlowID
            authChallengeKind = dictionary["kind"] as? String
            authInProgress = true
            connectionMessage = authChallengeKind == "password"
                ? "需要 Telegram 兩步驟驗證"
                : "需要 Telegram 驗證碼"
        case "auth_succeeded":
            authInProgress = false
            authFlowID = nil
            authChallengeKind = nil
            connectionMessage = "Telegram 登入成功"
            Task { [weak self] in
                await self?.refresh()
            }
        case "auth_failed":
            authInProgress = false
            authFlowID = nil
            authChallengeKind = nil
            if let error = dictionary["error"] as? [String: Any] {
                errorMessage = error["message"] as? String ?? "Telegram 登入失敗"
            } else {
                errorMessage = "Telegram 登入失敗"
            }
            connectionMessage = "登入失敗"
        case "scan_progress":
            if let message = dictionary["message"] as? String {
                lastLog = message
            }
        default:
            lastLog = event
        }
    }

    private func sidecarTerminated(exitCode: Int32) {
        let message = exitCode == 0
            ? "sidecar 已停止"
            : "sidecar 已停止（exit \(exitCode)）"
        connectionMessage = message
        process = nil
        inputHandle = nil
        outputHandle = nil
        let error = CoreClientError(message: message)
        pending.values.forEach { $0.resume(throwing: error) }
        pending.removeAll()
    }

    private func present(error: Error) {
        errorMessage = error.localizedDescription
        connectionMessage = "sidecar 通訊失敗"
    }

    private func resolveHelperPath() -> String? {
        if let override = ProcessInfo.processInfo.environment["TELESHIELD_CORE_PATH"],
           FileManager.default.isExecutableFile(atPath: override) {
            return override
        }

        let bundle = Bundle.main.bundleURL
        let candidates = [
            bundle.appendingPathComponent("Contents/Helpers/TeleShieldCore/TeleShieldCore"),
            bundle.appendingPathComponent("Helpers/TeleShieldCore/TeleShieldCore"),
            bundle.deletingLastPathComponent().appendingPathComponent("TeleShieldCore"),
        ]
        return candidates.first {
            FileManager.default.isExecutableFile(atPath: $0.path)
        }?.path
    }
}
