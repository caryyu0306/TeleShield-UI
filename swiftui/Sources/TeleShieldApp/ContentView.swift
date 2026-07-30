import AppKit
import SwiftUI

struct ContentView: View {
    @ObservedObject var client: CoreClient

    var body: some View {
        NavigationSplitView {
            accountList
        } detail: {
            dashboard
        }
        .frame(minWidth: 820, minHeight: 560)
        .onAppear {
            client.launch()
        }
    }

    private var accountList: some View {
        List {
            Section("Telegram 帳號") {
                ForEach(client.status?.accounts ?? []) { account in
                    Button {
                        guard let accountID = account.accountID else { return }
                        Task { await client.selectAccount(accountID) }
                    } label: {
                        AccountRow(account: account)
                    }
                    .buttonStyle(.plain)
                }

                if (client.status?.accounts ?? []).isEmpty {
                    Label("尚未建立帳號", systemImage: "person.crop.circle.badge.plus")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("帳號")
    }

    private var dashboard: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                Divider()
                if let account = client.selectedAccount, account.configured {
                    statusCards(account)
                } else {
                    setupCard
                }
                runtimeCard
            }
            .padding(28)
        }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 6) {
                Text("TeleShield")
                    .font(.largeTitle.bold())
                Text("SwiftUI + Python sidecar")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task { await client.refresh() }
            } label: {
                Label("重新整理", systemImage: "arrow.clockwise")
            }
            .disabled(!client.helperIsRunning || client.isBusy)
        }
    }

    @ViewBuilder
    private func statusCards(_ account: AccountSummary) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                statusBadge(for: account)
                Spacer()
                Button {
                    Task {
                        if account.running {
                            await client.stopProtection()
                        } else {
                            await client.startProtection()
                        }
                    }
                } label: {
                    Label(
                        account.running ? "停止防護" : "啟動防護",
                        systemImage: account.running ? "stop.fill" : "play.fill"
                    )
                }
                .buttonStyle(.borderedProminent)
                .disabled(client.isBusy)
            }

            HStack(spacing: 14) {
                MetricCard(title: "今日封鎖", value: "\(account.recentBlockCount)", icon: "calendar")
                MetricCard(title: "累計私訊", value: "\(account.blockedCount)", icon: "hand.raised")
                MetricCard(title: "累計群組", value: "\(account.kickedCount)", icon: "person.2")
            }

            HStack(spacing: 14) {
                MetricCard(title: "白名單", value: "\(account.whitelistCount)", icon: "checkmark.shield")
                MetricCard(title: "黑名單", value: "\(account.blacklistCount)", icon: "nosign")
                MetricCard(title: "學習關鍵詞", value: "\(account.learnedKeywordCount)", icon: "text.magnifyingglass")
            }
        }
    }

    private var setupCard: some View {
        LoginCard(client: client)
    }

    private var runtimeCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Runtime", systemImage: "gearshape.2")
                .font(.headline)
            Text(client.connectionMessage)
                .foregroundStyle(.secondary)
            if !client.lastLog.isEmpty {
                Text(client.lastLog)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            if let error = client.errorMessage {
                Text(error)
                    .foregroundStyle(.red)
                    .font(.callout)
            }
            if let ocr = client.status?.ocr {
                Label(
                    ocr.available
                        ? "OCR 可用（\(ocr.languages.joined(separator: "+"))）"
                        : "OCR 尚未可用",
                    systemImage: ocr.available ? "text.viewfinder" : "text.viewfinder斜線"
                )
                .font(.callout)
                .foregroundStyle(ocr.available ? .green : .secondary)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .windowBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
    }

    private func statusBadge(for account: AccountSummary) -> some View {
        let color: Color = account.ready ? .green : (account.running ? .orange : .secondary)
        let text = account.ready ? "防護執行中" : (account.running ? "正在啟動" : "防護已停止")
        return Label(text, systemImage: account.ready ? "checkmark.circle.fill" : "circle")
            .foregroundStyle(color)
            .font(.headline)
    }
}

private struct LoginCard: View {
    @ObservedObject var client: CoreClient
    @State private var apiID = ""
    @State private var apiHash = ""
    @State private var phone = ""
    @State private var code = ""
    @State private var password = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("登入 Telegram 個人帳號", systemImage: "lock.shield")
                .font(.title3.bold())
            Text("API 憑證只會交給本機 Python sidecar，既有 Session 仍留在使用者資料目錄。")
                .foregroundStyle(.secondary)

            HStack {
                TextField("API ID", text: $apiID)
                SecureField("API Hash", text: $apiHash)
            }
            TextField("手機號碼（含國碼，例如 +886...）", text: $phone)

            if client.authChallengeKind == "code" {
                TextField("Telegram 驗證碼", text: $code)
                    .textFieldStyle(.roundedBorder)
            } else if client.authChallengeKind == "password" {
                SecureField("Telegram 兩步驟驗證密碼", text: $password)
                    .textFieldStyle(.roundedBorder)
            }

            if !client.authDeliveryMessage.isEmpty {
                Text(client.authDeliveryMessage)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            HStack {
                if client.authChallengeKind == "code" {
                    Button("送出驗證碼") {
                        Task { await client.submitAuthCode(code) }
                    }
                    .disabled(code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } else if client.authChallengeKind == "password" {
                    Button("送出密碼") {
                        Task { await client.submitAuthPassword(password) }
                    }
                    .disabled(password.isEmpty)
                } else {
                    Button("開始登入") {
                        Task {
                            await client.startAuthentication(
                                apiID: apiID,
                                apiHash: apiHash,
                                phone: phone
                            )
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(
                        apiID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || apiHash.isEmpty
                            || phone.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || !client.helperIsRunning
                            || client.isBusy
                    )
                }

                if client.authFlowID != nil {
                    Button("取消登入") {
                        Task { await client.cancelAuthentication() }
                    }
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct AccountRow: View {
    let account: AccountSummary

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: account.configured ? "person.crop.circle.fill" : "person.crop.circle")
                .foregroundStyle(account.configured ? .blue : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(account.username.isEmpty ? "未登入帳號" : "@\(account.username)")
                    .font(.body.weight(.medium))
                Text(account.displayName.isEmpty ? account.accountID ?? "尚未命名" : account.displayName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if account.ready {
                Circle().fill(.green).frame(width: 8, height: 8)
            }
        }
        .padding(.vertical, 4)
    }
}

private struct MetricCard: View {
    let title: String
    let value: String
    let icon: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon)
                .font(.callout)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title2.bold().monospacedDigit())
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }
}

struct MenuBarView: View {
    @ObservedObject var client: CoreClient

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("TeleShield")
                .font(.headline)
            Text(client.connectionMessage)
                .font(.caption)
                .foregroundStyle(.secondary)
            Divider()
            if let account = client.selectedAccount, account.configured {
                Button(account.running ? "停止防護" : "啟動防護") {
                    Task {
                        if account.running {
                            await client.stopProtection()
                        } else {
                            await client.startProtection()
                        }
                    }
                }
            }
            Button("重新整理") {
                Task { await client.refresh() }
            }
            Button("結束 TeleShield") {
                client.shutdown()
                NSApplication.shared.terminate(nil)
            }
        }
        .padding(12)
        .frame(width: 220)
    }
}
