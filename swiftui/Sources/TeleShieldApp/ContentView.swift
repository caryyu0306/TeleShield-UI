import AppKit
import SwiftUI

private enum AppSection: String, CaseIterable, Hashable, Identifiable {
    case overview = "總覽"
    case historyScan = "掃描歷史訊息"
    case lists = "白名單／黑名單"
    case rules = "學習規則"
    case reports = "報告"
    case records = "封鎖記錄"
    case privacy = "隱私健檢"
    case settings = "全域設定"
    case accounts = "帳號"

    var id: String { rawValue }

    static var workspaceCases: [AppSection] {
        [.overview, .historyScan, .lists, .rules, .reports, .records, .privacy]
    }

    static var managementCases: [AppSection] {
        [.accounts, .settings]
    }
    var icon: String {
        switch self {
        case .overview: return "rectangle.3.group"
        case .historyScan: return "magnifyingglass.circle"
        case .lists: return "person.2.badge.gearshape"
        case .rules: return "text.magnifyingglass"
        case .reports: return "chart.bar.xaxis"
        case .records: return "list.bullet.rectangle"
        case .privacy: return "lock.shield"
        case .settings: return "gearshape"
        case .accounts: return "person.crop.circle"
        }
    }
}

struct ContentView: View {
    @ObservedObject var client: CoreClient
    @ObservedObject var updater: UpdateManager
    @State private var section: AppSection? = .overview
    @State private var showLogin = false
    @State private var showUpdateSheet = false
    @State private var temporaryLoginAccountID: String?

    var body: some View {
        NavigationSplitView {
            VStack(spacing: 0) {
                sidebar
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                sidebarStatus
            }
        } detail: {
            VStack(spacing: 0) {
                if section != .settings && section != .accounts {
                    CurrentAccountBar(client: client)
                    Divider()
                }
                detailView
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .frame(minWidth: 1080, minHeight: 700)
        .onAppear {
            client.launch()
            updater.startAutomaticChecks()
            showUpdateSheet = updater.availableUpdate != nil
        }
        .task {
            while !Task.isCancelled {
                await client.refresh()
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
        .sheet(isPresented: $showLogin) {
            LoginSheet(client: client, temporaryAccountID: $temporaryLoginAccountID)
        }
        .sheet(isPresented: $showUpdateSheet) {
            if let update = updater.availableUpdate {
                UpdateSheet(client: client, updater: updater, update: update)
            }
        }
        .onChange(of: client.authenticatedAccountID) { accountID in
            let targetAccountID = temporaryLoginAccountID ?? client.selectedAccountID
            guard AuthenticationPresentation.shouldDismissLoginSheet(
                event: "auth_succeeded",
                accountID: accountID,
                targetAccountID: targetAccountID
            ) else { return }
            temporaryLoginAccountID = nil
            showLogin = false
        }
        .onChange(of: updater.availableUpdate) { update in
            if update != nil { showUpdateSheet = true }
        }
        .onReceive(NotificationCenter.default.publisher(for: .teleShieldShowUpdate)) { _ in
            showUpdateSheet = updater.availableUpdate != nil
        }
    }

    private var sidebar: some View {
        List(selection: $section) {
            Section("工作帳號") {
                ForEach(client.status?.accounts ?? []) { account in
                    SidebarAccountRow(
                        account: account,
                        selected: client.selectedAccount?.id == account.id,
                        isBusy: client.isBusy,
                        onSelect: { Task { await client.selectAccount(account.id) } },
                        onToggleProtection: { enabled in
                            Task { await client.setProtection(accountID: account.id, enabled: enabled) }
                        }
                    )
                }
                Button {
                    Task {
                        if let accountID = await client.createAccount() {
                            temporaryLoginAccountID = accountID
                            section = .accounts
                            showLogin = true
                        }
                    }
                } label: {
                    Label("新增 Telegram 帳號", systemImage: "plus.circle")
                }
                .disabled(client.isBusy)
            }

            Section("工作區") {
                ForEach(AppSection.workspaceCases) { item in
                    Label(item.rawValue, systemImage: item.icon)
                        .tag(item)
                }
            }

            Section("管理") {
                ForEach(AppSection.managementCases) { item in
                    Label(item.rawValue, systemImage: item.icon)
                        .tag(item)
                }
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("TeleShield")
    }

    private var sidebarStatus: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label(client.connectionMessage, systemImage: client.helperIsRunning ? "circle.fill" : "circle")
                .font(.caption)
                .foregroundStyle(client.helperIsRunning ? .green : .secondary)
            if let account = client.selectedAccount {
                HStack(spacing: 6) {
                    Text(account.label)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    AccountStatusBadge(account: account)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.bar)
    }

    @ViewBuilder
    private var detailView: some View {
        switch section ?? .overview {
        case .overview: OverviewView(client: client, showLogin: $showLogin)
        case .historyScan: HistoryScanViewPage(client: client, showLogin: $showLogin)
        case .lists: ListManagementView(client: client)
        case .rules: RulesView(client: client)
        case .reports: ReportView(client: client)
        case .records: BlockRecordsView(client: client)
        case .privacy: PrivacyAuditView(client: client)
        case .settings: GlobalSettingsView(client: client, updater: updater)
        case .accounts: AccountsView(client: client, showLogin: $showLogin)
        }
    }
}

private struct PageHeader<Content: View>: View {
    let title: String
    let subtitle: String
    let content: () -> Content

    init(title: String, subtitle: String, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.subtitle = subtitle
        self.content = content
    }

    var body: some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.system(size: 30, weight: .bold))
                    .tracking(-0.3)
                Text(subtitle)
                    .font(.callout)
                    .foregroundStyle(TeleShieldDesign.muted)
                    .lineLimit(2)
            }
            Spacer()
            content()
        }
    }
}

private struct OverviewView: View {
    @ObservedObject var client: CoreClient
    @Binding var showLogin: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                PageHeader(title: "總覽", subtitle: "先看目前風險，再決定要啟動防護或處理歷史資料") {
                    Button { Task { await client.refresh() } } label: {
                        Label("重新整理", systemImage: "arrow.clockwise")
                    }
                    .disabled(client.isBusy)
                }
                if let account = client.selectedAccount, account.configured {
                    ProtectionSummary(client: client, account: account)
                    EventLogCard(client: client)
                } else {
                    OnboardingCard(client: client, showLogin: $showLogin)
                }
            }
            .teleShieldPageContent()
            .padding(TeleShieldDesign.pagePadding)
        }
        .task(id: client.selectedAccountID) {
            await client.fetchBlockRecords(query: "", source: "all")
        }
    }
}

private struct HistoryScanViewPage: View {
    @ObservedObject var client: CoreClient
    @Binding var showLogin: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                PageHeader(
                    title: "掃描歷史訊息",
                    subtitle: "掃描目前帳號的歷史私訊，可先預覽結果再決定是否處理"
                ) {
                    EmptyView()
                }
                Label(
                    "請先關閉防護才能掃描；防護與掃描使用同一個 Telegram Session，無法同時執行。",
                    systemImage: "info.circle"
                )
                .font(.callout)
                .foregroundStyle(.orange)
                if let account = client.selectedAccount, account.configured {
                    HistoryScanView(client: client)
                } else {
                    OnboardingCard(client: client, showLogin: $showLogin)
                }
            }
            .teleShieldPageContent()
            .padding(TeleShieldDesign.pagePadding)
        }
    }
}

private struct ProtectionSummary: View {
    @ObservedObject var client: CoreClient
    let account: AccountSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 4) {
                    Label(account.ready ? "防護執行中" : (account.running ? "正在啟動" : "防護已停止"), systemImage: account.ready ? "checkmark.shield.fill" : "shield")
                        .font(.title2.bold())
                        .foregroundStyle(account.ready ? .green : (account.running ? .orange : .secondary))
                    Text(account.displayName.isEmpty ? "@\(account.username)" : account.displayName)
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(.primary)
                    if let userID = account.userID { Text("Telegram ID：\(userID)").font(.caption).foregroundStyle(.tertiary) }
                    if client.selectedAccountID == account.id {
                        Text("陌生人防護：\(client.moderationPolicy.protectionMode.title)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button {
                    Task { await client.setProtection(accountID: account.id, enabled: !account.running) }
                } label: {
                    Label(
                        account.running ? "停止防護" : "啟動防護",
                        systemImage: account.running ? "pause.fill" : "play.fill"
                    )
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(client.isBusy)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 12)], spacing: 12) {
                MetricCard(title: "24 小時封鎖", value: "\(account.recentBlockCount)", icon: "calendar")
                MetricCard(title: "累計私訊封鎖", value: "\(account.blockedCount)", icon: "hand.raised")
                MetricCard(title: "白名單", value: "\(account.whitelistCount)", icon: "checkmark.shield")
                MetricCard(title: "黑名單", value: "\(account.blacklistCount)", icon: "nosign")
                MetricCard(title: "學習關鍵詞", value: "\(account.learnedKeywordCount)", icon: "text.magnifyingglass")
            }
        }
        .padding(22)
        .teleShieldSurface()
    }
}

private struct OnboardingCard: View {
    @ObservedObject var client: CoreClient
    @Binding var showLogin: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("先建立一個 Telegram 帳號", systemImage: "person.crop.circle.badge.plus")
                .font(.title2.bold())
            Text("每個帳號都有獨立 Session、設定、名單與封鎖記錄。建立後再進入登入流程。")
                .foregroundStyle(.secondary)
            Button {
                Task {
                    if await client.createAccount() != nil { showLogin = true }
                }
            } label: {
                Label("建立並登入", systemImage: "plus.circle.fill")
            }
            .buttonStyle(.borderedProminent)
            .disabled(client.isBusy)
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface()
    }
}

private struct EventLogCard: View {
    @ObservedObject var client: CoreClient

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("最近活動", systemImage: "list.bullet.rectangle")
                    .font(.headline)
                Spacer()
                Text("目前帳號的最新封鎖記錄")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            if !client.blockRecords.isEmpty {
                ForEach(Array(client.blockRecords.prefix(12))) { record in
                    HStack(alignment: .top, spacing: 8) {
                        Circle().fill(.red).frame(width: 7, height: 7).padding(.top, 5)
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                Text(record.name.isEmpty ? record.userID : record.name)
                                    .font(.callout.weight(.medium))
                                Text(record.source == "group" ? "群組" : "私訊")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Text(record.reason.isEmpty ? "未記錄原因" : record.reason)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        Spacer()
                        Text(record.displayTime)
                            .font(.caption2.monospaced())
                            .foregroundStyle(.tertiary)
                    }
                }
            } else if client.eventLog.isEmpty {
                Text("尚無封鎖活動。啟動防護、掃描或管理名單後，操作紀錄會顯示在這裡。")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(client.eventLog.suffix(12).reversed())) { event in
                    HStack(alignment: .top, spacing: 8) {
                        Circle().fill(event.level == "error" ? .red : (event.level == "stderr" ? .orange : .blue)).frame(width: 7, height: 7).padding(.top, 5)
                        Text(event.message).font(.callout.monospaced())
                        Spacer()
                        Text(event.time, style: .time).font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: 14)
    }
}

private struct HistoryScanView: View {
    @ObservedObject var client: CoreClient
    @State private var dryRun = true
    @State private var showApplyConfirmation = false
    @State private var showListConfirmation: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack {
                Label("掃描歷史訊息", systemImage: "magnifyingglass.circle")
                    .font(.title3.bold())
                Spacer()
                if client.hasActiveScan {
                    Button("停止掃描") { Task { await client.cancelScan() } }
                        .buttonStyle(.bordered)
                }
            }
            if client.hasActiveScan {
                HStack(spacing: 10) {
                    ProgressView()
                        .controlSize(.small)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("正在掃描 Telegram")
                            .font(.callout.weight(.semibold))
                        Text("掃描進度會持續顯示在下方，完成後可檢視匹配結果。")
                            .font(.caption)
                            .foregroundStyle(TeleShieldDesign.muted)
                    }
                    Spacer()
                }
                .padding(12)
                .teleShieldSurface(radius: TeleShieldDesign.innerRadius, fill: Color.accentColor.opacity(0.08))
            }
            HStack {
                Text("私訊（\(client.scanSettings.privateDialogLimit) 對話／\(client.scanSettings.privateMessageLimit) 訊息）")
                    .foregroundStyle(.secondary)
                Spacer()
                Toggle("預覽模式（不封鎖）", isOn: $dryRun)
            }
            HStack {
                Button(dryRun ? "開始預覽" : "開始處理") {
                    if dryRun { Task { await client.startScan(dryRun: true) } }
                    else { showApplyConfirmation = true }
                }
                .buttonStyle(.borderedProminent)
                .disabled(client.selectedAccount?.running == true || client.hasActiveScan || client.isBusy)
                if let result = client.scanResult {
                    Text("匹配 \(result.matched)／已處理 \(result.acted)")
                        .font(.callout.bold())
                        .foregroundStyle(result.dryRun ? .orange : .green)
                }
            }
            if !client.scanProgress.isEmpty {
                ScrollView {
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(Array(client.scanProgress.suffix(14).enumerated()), id: \.offset) { _, message in
                            Text(message).font(.caption.monospaced())
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 150)
                .padding(10)
                .teleShieldSurface(radius: TeleShieldDesign.innerRadius, fill: Color(nsColor: .textBackgroundColor))
            }
            if let result = client.scanResult, !result.findings.isEmpty {
                HStack {
                    Text("發現項目：\(result.findings.count)").font(.headline)
                    Spacer()
                    Button("加入黑名單") { showListConfirmation = "blacklist" }
                    Button("加入白名單") { showListConfirmation = "whitelist" }
                }
                ForEach(result.findings) { finding in
                    HStack {
                        Text(finding.name).fontWeight(.medium)
                        Text(finding.userID).font(.caption.monospaced()).foregroundStyle(.secondary)
                        Spacer()
                        Text(finding.reason).font(.caption).lineLimit(1).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(18)
        .teleShieldSurface(radius: 14)
        .confirmationDialog("即將對 Telegram 執行私訊封鎖", isPresented: $showApplyConfirmation, titleVisibility: .visible) {
            Button("確認開始處理", role: .destructive) { Task { await client.startScan(dryRun: false) } }
            Button("取消", role: .cancel) {}
        } message: {
            Text("這會對掃描匹配的私訊帳號執行封鎖，並寫入封鎖記錄。建議先用預覽模式確認結果。")
        }
        .confirmationDialog("將掃描發現加入名單", isPresented: Binding(get: { showListConfirmation != nil }, set: { if !$0 { showListConfirmation = nil } }), titleVisibility: .visible) {
            Button("確認加入") {
                if let list = showListConfirmation, let findings = client.scanResult?.findings { Task { await client.addFindings(findings, to: list) } }
                showListConfirmation = nil
            }
            Button("取消", role: .cancel) { showListConfirmation = nil }
        }
    }
}

private struct ListManagementView: View {
    @ObservedObject var client: CoreClient
    @State private var listType = "whitelist"
    @State private var query = ""
    @State private var userID = ""
    @State private var username = ""
    @State private var reason = "desktop"
    @State private var selectedIDs: Set<String> = []
    @State private var importReplace = false
    @State private var showDeleteConfirmation = false

    private var rows: [ListEntry] { listType == "whitelist" ? client.whitelist : client.blacklist }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
            PageHeader(title: "白名單／黑名單", subtitle: "先保護例外，再處理自動偵測到的帳號") {
                Picker("名單", selection: $listType) {
                    Text("白名單").tag("whitelist")
                    Text("黑名單").tag("blacklist")
                }
                .frame(width: 150)
            }
            HStack {
                TextField("搜尋 ID、Username 或原因", text: $query)
                Button("重新整理") { Task { await client.fetchList(listType, query: query) } }
            }
            .padding(12)
            .teleShieldSurface(radius: TeleShieldDesign.innerRadius, fill: Color(nsColor: .textBackgroundColor))
            HStack {
                TextField("Telegram User ID", text: $userID).frame(width: 170)
                TextField("Username", text: $username).frame(width: 170)
                TextField("原因", text: $reason)
                Button("新增／更新") {
                    Task { await client.upsertList(listType, userID: userID, username: username, reason: reason); userID = ""; username = "" }
                }
                .buttonStyle(.borderedProminent)
                .disabled(userID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            HStack {
                Button("移除選取項目", role: .destructive) { showDeleteConfirmation = true }
                    .disabled(selectedIDs.isEmpty)
                Toggle("匯入時取代整份名單", isOn: $importReplace)
                Spacer()
                Button("匯入 JSON／CSV") { importList() }
                Menu("匯出") {
                    Button("JSON") { exportList("json") }
                    Button("CSV") { exportList("csv") }
                }
            }
            .padding(12)
            .teleShieldSurface(radius: TeleShieldDesign.innerRadius)
            if rows.isEmpty {
                TeleShieldEmptyState(
                    icon: listType == "whitelist" ? "checkmark.shield" : "nosign",
                    title: listType == "whitelist" ? "尚無白名單項目" : "尚無黑名單項目",
                    message: "新增 Telegram User ID 或 Username 後，這裡會顯示可管理的例外項目。"
                )
            } else {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(rows) { row in
                        HStack {
                            Text(row.userID).font(.body.monospaced())
                            Text(row.username.isEmpty ? "—" : "@\(row.username)").foregroundStyle(.secondary)
                            Spacer()
                            Text(row.reason).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                            Text(row.added).font(.caption).foregroundStyle(TeleShieldDesign.muted)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(selectedIDs.contains(row.id) ? Color.accentColor.opacity(0.16) : .clear)
                        .contentShape(Rectangle())
                        .onTapGesture {
                            if selectedIDs.contains(row.id) {
                                selectedIDs.remove(row.id)
                            } else {
                                selectedIDs.insert(row.id)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .teleShieldSurface(radius: TeleShieldDesign.innerRadius, fill: Color(nsColor: .textBackgroundColor))
            }
        }
            .teleShieldPageContent()
            .padding(TeleShieldDesign.pagePadding)
        }
        .scrollIndicators(.automatic)
        .task(id: "\(client.selectedAccountID ?? "")|\(listType)") {
            await client.fetchList(listType, query: query)
        }
        .confirmationDialog("移除選取的名單項目？", isPresented: $showDeleteConfirmation, titleVisibility: .visible) {
            Button("確認移除", role: .destructive) {
                Task {
                    for id in selectedIDs { await client.removeListEntry(listType, userID: id) }
                    selectedIDs.removeAll()
                }
            }
            Button("取消", role: .cancel) {}
        }
    }

    private func importList() {
        guard let url = openPanel(extensions: ["json", "csv"]) else { return }
        Task { await client.importList(listType, path: url.path, replace: importReplace) }
    }

    private func exportList(_ format: String) {
        guard let url = savePanel(fileExtension: format, name: "\(listType).\(format)") else { return }
        Task { await client.exportList(listType, path: url.path, format: format) }
    }
}

private struct RulesView: View {
    @ObservedObject var client: CoreClient
    @State private var sample = ""
    @State private var selected: (kind: String, value: String)?

    private var rules: [(kind: String, value: String)] {
        client.learnedPatterns.keywords.map { ("keywords", $0) } + client.learnedPatterns.patterns.map { ("patterns", $0) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
            PageHeader(title: "學習規則", subtitle: "從實際廣告文字建立可持久化的關鍵詞與模式") {
                Button("重新整理") { Task { await client.refreshAccountData() } }
            }
            HStack(alignment: .top) {
                TextEditor(text: $sample)
                    .font(.body)
                    .frame(minHeight: 130)
                    .padding(6)
                    .teleShieldSurface(radius: TeleShieldDesign.innerRadius, fill: Color(nsColor: .textBackgroundColor))
                VStack(alignment: .leading, spacing: 10) {
                    Text("貼上一則廣告或可疑訊息").font(.headline)
                    Text("系統會拆出關鍵詞與可重用模式；不會把完整訊息送到網路。")
                        .font(.callout).foregroundStyle(.secondary)
                    Button("學習這則廣告") { Task { await client.learn(sample); sample = "" } }
                        .buttonStyle(.borderedProminent)
                        .disabled(sample.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || client.isBusy)
                }
                .frame(width: 280, alignment: .leading)
            }
            HStack {
                Text("目前規則：\(rules.count)").font(.headline)
                Spacer()
                Button("刪除選取規則", role: .destructive) {
                    if let selected { Task { await client.removeLearnedPattern(kind: selected.kind, value: selected.value); self.selected = nil } }
                }
                .disabled(selected == nil)
            }
            if rules.isEmpty {
                TeleShieldEmptyState(
                    icon: "text.magnifyingglass",
                    title: "尚未建立學習規則",
                    message: "貼上一則廣告或可疑訊息，讓 TeleShield 建立可重複使用的關鍵詞與模式。"
                )
            } else {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(rules.enumerated()), id: \.offset) { _, rule in
                        HStack {
                            Text(rule.kind == "keywords" ? "關鍵詞" : "模式")
                                .font(.caption.bold())
                                .foregroundStyle(rule.kind == "keywords" ? .blue : .orange)
                                .frame(width: 65, alignment: .leading)
                            Text(rule.value).font(.body.monospaced())
                            Spacer()
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(selected?.kind == rule.kind && selected?.value == rule.value ? Color.accentColor.opacity(0.16) : .clear)
                        .contentShape(Rectangle())
                        .onTapGesture { selected = rule }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .teleShieldSurface(radius: TeleShieldDesign.innerRadius, fill: Color(nsColor: .textBackgroundColor))
            }
        }
            .teleShieldPageContent()
            .padding(TeleShieldDesign.pagePadding)
        }
        .scrollIndicators(.automatic)
        .task { await client.refreshAccountData() }
    }
}

private struct ReportView: View {
    @ObservedObject var client: CoreClient
    @State private var period = "day"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "封鎖報告", subtitle: "用時間範圍整理來源、原因與每日趨勢") {
                    Picker("期間", selection: $period) {
                        Text("過去 24 小時").tag("day")
                        Text("過去 7 天").tag("week")
                        Text("全部").tag("all")
                    }
                    .frame(width: 150)
                    Button("重新產生") { Task { await client.buildReport(period: period) } }
                    Menu("匯出") {
                        Button("JSON") { exportReport() }
                    }
                }
                if let report = client.report {
                    HStack(spacing: 14) {
                        MetricCard(title: report.label, value: "\(report.total)", icon: "chart.bar")
                        MetricCard(title: "來源種類", value: "\(report.bySource.count)", icon: "arrow.triangle.branch")
                        MetricCard(title: "主要原因", value: "\(report.byReason.count)", icon: "tag")
                    }
                    HStack(alignment: .top, spacing: 20) {
                        ReportDictionaryCard(title: "來源", values: report.bySource)
                        ReportDictionaryCard(title: "原因 Top 5", values: report.byReason)
                    }
                    if !report.trend.isEmpty {
                        TrendCard(values: report.trend)
                    }
                } else {
                    EmptyPanel(title: "尚未產生報告", message: "選擇期間後按「重新產生」。")
                }
            }
            .teleShieldPageContent()
            .padding(TeleShieldDesign.pagePadding)
        }
        .task(id: client.selectedAccountID) { await client.buildReport(period: period) }
    }

    private func exportReport() {
        guard let url = savePanel(fileExtension: "json", name: "teleShield-report.json") else { return }
        Task { await client.exportReport(path: url.path) }
    }
}

private struct ReportDictionaryCard: View {
    let title: String
    let values: [String: Int]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            if values.isEmpty { Text("無資料").foregroundStyle(.secondary) }
            let maxValue = max(values.values.max() ?? 1, 1)
            ForEach(values.keys.sorted(by: { (values[$0] ?? 0) > (values[$1] ?? 0) }), id: \.self) { key in
                let count = values[key] ?? 0
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 8) {
                        Text(key)
                            .lineLimit(2)
                        Spacer(minLength: 8)
                        Text("\(count)")
                            .fontWeight(.semibold)
                            .monospacedDigit()
                    }
                    GeometryReader { proxy in
                        RoundedRectangle(cornerRadius: 4, style: .continuous)
                            .fill(Color.accentColor.opacity(0.8))
                            .frame(width: max(6, proxy.size.width * CGFloat(count) / CGFloat(maxValue)), height: 6)
                    }
                    .frame(height: 6)
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: 14)
    }
}

private struct TrendCard: View {
    let values: [String: Int]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("每日趨勢").font(.headline)
            let maxValue = max(values.values.max() ?? 1, 1)
            ForEach(values.keys.sorted(), id: \.self) { key in
                HStack(spacing: 10) {
                    Text(key).font(.caption.monospaced()).frame(width: 90, alignment: .leading)
                    GeometryReader { proxy in
                        RoundedRectangle(cornerRadius: 4, style: .continuous)
                            .fill(.blue.gradient)
                            .frame(width: max(4, proxy.size.width * CGFloat(values[key] ?? 0) / CGFloat(maxValue)), height: 14)
                    }
                    .frame(height: 14)
                    Text("\(values[key] ?? 0)").font(.caption.monospacedDigit())
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: 14)
    }
}

private struct BlockRecordsView: View {
    @ObservedObject var client: CoreClient
    @State private var query = ""
    @State private var source = "all"
    @State private var selectedRecord: BlockRecord?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            PageHeader(title: "封鎖記錄", subtitle: "查找封鎖處理歷史，必要時匯出交接或稽核") {
                Menu("匯出") {
                    Button("JSON") { export("json") }
                    Button("CSV") { export("csv") }
                }
            }
            HStack {
                TextField("搜尋姓名、ID、原因", text: $query)
                Picker("來源", selection: $source) {
                    Text("全部").tag("all")
                    Text("私訊").tag("private")
                }
                .frame(width: 120)
                Button("重新整理") { Task { await client.fetchBlockRecords(query: query, source: source) } }
            }
            if client.blockRecords.isEmpty {
                TeleShieldEmptyState(
                    icon: "checkmark.circle",
                    title: "尚無封鎖記錄",
                    message: "目前的帳號尚未產生封鎖事件，或沒有符合目前搜尋與來源篩選的資料。"
                )
            } else {
                HStack(alignment: .center, spacing: 12) {
                    Text("時間").frame(width: 175, alignment: .leading)
                    Text("來源").frame(width: 45, alignment: .leading)
                    Text("User ID").frame(width: 100, alignment: .leading)
                    Text("名稱").frame(width: 150, alignment: .leading)
                    Text("原因")
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(TeleShieldDesign.muted)
                .padding(.horizontal, 12)
                List(client.blockRecords) { record in
                    Button { selectedRecord = record } label: {
                        HStack(alignment: .top, spacing: 12) {
                            Text(record.displayTime).font(.caption.monospaced()).frame(width: 175, alignment: .leading)
                            Text(record.source == "group" ? "群組" : "私訊")
                                .font(.caption.bold())
                                .foregroundStyle(record.source == "group" ? .orange : .blue)
                                .frame(width: 45, alignment: .leading)
                            Text(record.userID).font(.caption.monospaced()).frame(width: 100, alignment: .leading)
                            Text(record.name).font(.callout.weight(.medium)).frame(width: 150, alignment: .leading)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(record.reason)
                                    .font(.callout)
                                    .foregroundStyle(.primary)
                                    .lineLimit(2)
                                if let analysis = record.details?.analysis {
                                    Text(analysis.score.map { score in
                                        if let threshold = analysis.threshold {
                                            let scoreLabel = analysis.scoreTypeLabel.isEmpty
                                                ? "分數"
                                                : analysis.scoreTypeLabel
                                            return "\(analysis.analysisSourceLabel) · \(scoreLabel) \(score) / \(threshold)"
                                        }
                                        return analysis.analysisSourceLabel
                                    } ?? analysis.analysisSourceLabel)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                }
                            }
                            Spacer(minLength: 4)
                            Image(systemName: "chevron.right")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.tertiary)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                .listStyle(.inset)
            }
        }
        .teleShieldPageContent()
        .padding(TeleShieldDesign.pagePadding)
        .task(id: client.selectedAccountID) { await client.fetchBlockRecords(query: query, source: source) }
        .sheet(item: $selectedRecord) { record in
            BlockRecordDetailView(record: record)
        }
    }

    private func export(_ format: String) {
        guard let url = savePanel(fileExtension: format, name: "teleShield-blocks.\(format)") else { return }
        Task { await client.exportBlocks(path: url.path, query: query, source: source, format: format) }
    }
}

private struct PrivacyAuditView: View {
    @ObservedObject var client: CoreClient
    @State private var showApplyConfirmation = false
    @State private var showPremiumConfirmation = false
    @State private var showRestoreConfirmation = false
    @State private var showPremiumAlert = false
    @State private var showTwoFactorSheet = false
    @State private var pendingRevokeSession: PrivacySession?
    @State private var actionError = ""
    @State private var draftPrivacySettings: [PrivacyRuleSetting] = []
    @State private var draftGlobalSettings = PrivacyGlobalSettings.defaults
    @State private var draftUsername = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(
                    title: "隱私健檢",
                    subtitle: "直接讀取目前 Telegram 帳號的隱私設定，找出陌生人、登入安全與 Premium 控制的風險"
                ) {
                    Button {
                        Task { await client.fetchPrivacyAudit() }
                    } label: {
                        Label("重新檢查", systemImage: "arrow.clockwise")
                    }
                    .disabled(client.isBusy)
                }

                if let audit = client.privacyAudit {
                    HStack(spacing: 14) {
                        MetricCard(title: "良好項目", value: "\(audit.healthyCheckCount)", icon: "checkmark.shield")
                        MetricCard(title: "建議處理", value: "\(audit.warningCheckCount)", icon: "exclamationmark.shield")
                        MetricCard(title: "其他 Session", value: "\(audit.unknownSessionCount)", icon: "laptopcomputer.and.iphone")
                    }

                    PrivacyAuditActionCard(
                        audit: audit,
                        isBusy: client.isBusy,
                        onApplyFree: { showApplyConfirmation = true },
                        onApplyPremium: { showPremiumConfirmation = true },
                        onRestore: { showRestoreConfirmation = true }
                    )

                    PrivacySettingsEditor(
                        settings: $draftPrivacySettings,
                        globalSettings: $draftGlobalSettings,
                        username: $draftUsername,
                        checks: audit.checks,
                        globalSettingsAvailable: audit.globalSettings != nil,
                        isBusy: client.isBusy,
                        onSave: { runCustomSettingsSave() },
                        onReset: { syncDraft(from: audit) }
                    )

                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("隱私設定檢查")
                                .font(.headline)
                            Spacer()
                            Text("讀取時間：\(TimestampFormatter.localString(audit.generatedAt))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        ForEach(audit.checks) { check in
                            PrivacyCheckRow(check: check)
                        }
                    }
                    .padding(18)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .teleShieldSurface(radius: 14)

                    PrivacyTwoFactorCard(audit: audit, isBusy: client.isBusy) {
                        showTwoFactorSheet = true
                    }

                    PrivacySessionsCard(
                        sessions: audit.sessions,
                        isBusy: client.isBusy,
                        onRevoke: { pendingRevokeSession = $0 }
                    )

                    Label(
                        "設定會透過 MTProto 同步到此 Telegram 帳號的其他已登入 Session；套用前會先保存目前設定，可從上方復原。",
                        systemImage: "info.circle"
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)

                    if !actionError.isEmpty {
                        Text(actionError)
                            .font(.callout)
                            .foregroundStyle(TeleShieldDesign.danger)
                            .textSelection(.enabled)
                    }
                } else if client.selectedAccount?.configured == true {
                    VStack(alignment: .leading, spacing: 10) {
                        if let errorMessage = client.errorMessage, !errorMessage.isEmpty {
                            Label("隱私健檢讀取失敗", systemImage: "exclamationmark.triangle")
                                .font(.headline)
                                .foregroundStyle(TeleShieldDesign.warning)
                            Text(errorMessage)
                                .font(.callout)
                                .foregroundStyle(TeleShieldDesign.danger)
                                .textSelection(.enabled)
                            Button("重試") {
                                Task { await client.fetchPrivacyAudit() }
                            }
                            .disabled(client.isBusy)
                        } else {
                            ProgressView("正在讀取 Telegram 隱私設定…")
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    TeleShieldEmptyState(
                        icon: "lock.shield",
                        title: "請先登入 Telegram 帳號",
                        message: "隱私健檢需要使用者 Session，登入後才能透過 MTProto 讀取與調整帳號級設定。"
                    )
                }
            }
            .teleShieldPageContent()
            .padding(TeleShieldDesign.pagePadding)
        }
        .task(id: client.selectedAccountID) {
            actionError = ""
            await client.fetchPrivacyAudit()
            syncDraft(from: client.privacyAudit)
        }
        .onChange(of: client.privacyAudit?.generatedAt) { _ in
            syncDraft(from: client.privacyAudit)
        }
        .confirmationDialog(
            "套用免費版隱私建議？",
            isPresented: $showApplyConfirmation,
            titleVisibility: .visible
        ) {
            Button("確認套用") {
                runPrivacyProfile(includePremium: false)
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("會依 Telegram 隱私與安全設定的建議調整各項規則，並保留目前的例外對象；套用前會保存目前設定。")
        }
        .confirmationDialog(
            "套用 Premium 隱私建議？",
            isPresented: $showPremiumConfirmation,
            titleVisibility: .visible
        ) {
            Button("確認套用") {
                runPrivacyProfile(includePremium: true)
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("除了免費版建議，也會啟用 Telegram 的「要求陌生人使用 Premium」設定。若此帳號未購買 Premium，將顯示帳號資格提示。")
        }
        .confirmationDialog(
            "復原隱私設定？",
            isPresented: $showRestoreConfirmation,
            titleVisibility: .visible
        ) {
            Button("確認復原", role: .destructive) {
                runRestore()
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("將嘗試還原套用建議前保存的 Telegram 隱私規則與全域隱私設定。")
        }
        .confirmationDialog(
            "撤銷此 Telegram Session？",
            isPresented: Binding(
                get: { pendingRevokeSession != nil },
                set: { if !$0 { pendingRevokeSession = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("撤銷 Session", role: .destructive) {
                guard let session = pendingRevokeSession else { return }
                pendingRevokeSession = nil
                runRevoke(session)
            }
            Button("取消", role: .cancel) { pendingRevokeSession = nil }
        } message: {
            Text("撤銷後該裝置會被 Telegram 登出；目前正在使用的 Session 不會列出此操作。")
        }
        .alert("此帳號沒有購買 Telegram Premium", isPresented: $showPremiumAlert) {
            Button("知道了", role: .cancel) {}
        } message: {
            Text("Premium 隱私功能只能由已購買 Telegram Premium 的帳號啟用；免費版隱私建議仍可單獨套用。")
        }
        .sheet(isPresented: $showTwoFactorSheet) {
            if let audit = client.privacyAudit {
                TwoFactorSettingsSheet(client: client, audit: audit)
            }
        }
    }

    private func runPrivacyProfile(includePremium: Bool) {
        actionError = ""
        Task {
            let result = await client.applyPrivacyProfile(includePremium: includePremium)
            handleActionResult(result)
        }
    }

    private func runRestore() {
        actionError = ""
        Task {
            let result = await client.restorePrivacySettings()
            handleActionResult(result)
        }
    }

    private func runCustomSettingsSave() {
        actionError = ""
        Task {
            let result = await client.updatePrivacySettings(
                settings: draftPrivacySettings,
                globalSettings: draftGlobalSettings,
                username: draftUsername
            )
            handleActionResult(result)
        }
    }

    private func runRevoke(_ session: PrivacySession) {
        actionError = ""
        Task {
            let result = await client.revokeAuthorization(sessionHash: session.hash)
            handleActionResult(result)
        }
    }

    private func handleActionResult(_ result: String?) {
        guard let result, !result.isEmpty else { return }
        if result.localizedCaseInsensitiveContains("premium") {
            showPremiumAlert = true
        } else {
            actionError = result
        }
    }

    private func syncDraft(from audit: PrivacyAudit?) {
        guard let audit else { return }
        draftPrivacySettings = audit.checks.compactMap(\.setting)
        draftGlobalSettings = audit.globalSettings ?? .defaults
        draftUsername = audit.username
    }
}

private struct PrivacyAuditActionCard: View {
    let audit: PrivacyAudit
    let isBusy: Bool
    let onApplyFree: () -> Void
    let onApplyPremium: () -> Void
    let onRestore: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Label(
                        audit.premium ? "Telegram Premium 已啟用" : "Telegram Premium 未購買",
                        systemImage: audit.premium ? "crown.fill" : "crown"
                    )
                    .font(.headline)
                    .foregroundStyle(audit.premium ? .purple : TeleShieldDesign.warning)
                    Text(audit.username.isEmpty ? "未設定公開 username" : "公開 username：@\(audit.username)")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if audit.backupAvailable {
                    Label("有可復原備份", systemImage: "arrow.uturn.backward.circle")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.blue)
                }
            }

            Text("可直接套用截圖中的建議規則，也可以在下方逐項調整主要規則、例外對象、全域設定與公開 username。")
                .font(.callout)
                .foregroundStyle(TeleShieldDesign.muted)

            HStack(spacing: 10) {
                Button("套用免費版建議", action: onApplyFree)
                    .buttonStyle(.borderedProminent)
                    .disabled(isBusy)
                Button("套用 Premium 建議", action: onApplyPremium)
                    .buttonStyle(.bordered)
                    .disabled(isBusy)
                if audit.backupAvailable {
                    Button("復原套用前設定", role: .destructive, action: onRestore)
                        .disabled(isBusy)
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: 14, fill: audit.premium ? Color.purple.opacity(0.08) : Color.orange.opacity(0.08))
    }
}

private struct PrivacySettingsEditor: View {
    @Binding var settings: [PrivacyRuleSetting]
    @Binding var globalSettings: PrivacyGlobalSettings
    @Binding var username: String
    let checks: [PrivacyCheck]
    let globalSettingsAvailable: Bool
    let isBusy: Bool
    let onSave: () -> Void
    let onReset: () -> Void

    private var editableIDs: Set<String> {
        Set(checks.filter { $0.editable }.map(\.id))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                Label("自訂隱私設定", systemImage: "slider.horizontal.3")
                    .font(.headline)
                Spacer()
                Text("每項設定都可單獨調整")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text("這裡的變更會透過 MTProto 寫入 Telegram 帳號雲端，所有已登入裝置都會同步。使用者例外可輸入 User ID 或 @username；群組例外請輸入群組 ID。")
                .font(.callout)
                .foregroundStyle(TeleShieldDesign.muted)

            HStack(spacing: 12) {
                Image(systemName: "at")
                    .foregroundStyle(.secondary)
                    .frame(width: 22)
                TextField("公開 username（留空移除）", text: $username)
                    .textFieldStyle(.roundedBorder)
                Text("可輸入或不輸入 @")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Divider()

            Text("Telegram 隱私規則")
                .font(.subheadline.weight(.semibold))
            if settings.isEmpty {
                Text("目前的 Telethon 版本沒有回傳可編輯的隱私規則。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach($settings) { $setting in
                    PrivacyRuleSettingEditor(
                        setting: $setting,
                        isEditable: editableIDs.contains(setting.id)
                    )
                }
            }

            Divider()

            PrivacyGlobalSettingsEditor(
                settings: $globalSettings,
                isAvailable: globalSettingsAvailable
            )

            HStack(spacing: 10) {
                Button("重設為目前 Telegram 設定", action: onReset)
                    .disabled(isBusy || !globalSettingsAvailable)
                Spacer()
                Button("儲存並套用自訂設定", action: onSave)
                    .buttonStyle(.borderedProminent)
                    .disabled(isBusy || settings.isEmpty || !globalSettingsAvailable)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: 14)
    }
}

private struct PrivacyRuleSettingEditor: View {
    @Binding var setting: PrivacyRuleSetting
    let isEditable: Bool

    private static let standardModeOptions: [(id: String, title: String)] = [
        ("allow_all", "所有人"),
        ("allow_contacts", "我的聯絡人"),
        ("disallow_all", "沒有人"),
        ("disallow_contacts", "除了聯絡人"),
        ("allow_bots_only", "僅限機器人"),
        ("disallow_bots", "排除機器人"),
    ]

    private static let botOptions: [(id: String, title: String)] = [
        ("default", "依主要規則"),
        ("allow", "額外允許機器人"),
        ("disallow", "額外排除機器人"),
    ]

    private var modeOptions: [(id: String, title: String)] {
        var options = Self.standardModeOptions
        if setting.id == "chat_invite" || setting.mode == "allow_premium" {
            options.insert(("allow_premium", "Premium 使用者"), at: 2)
        }
        if setting.mode == "allow_close_friends" {
            options.insert(("allow_close_friends", "摯友"), at: 2)
        }
        return options
    }

    private var allowUsersText: Binding<String> {
        Binding(
            get: { setting.allowUsers.map(\.value).joined(separator: ", ") },
            set: { setting.allowUsers = parsePrincipals($0, existing: setting.allowUsers) }
        )
    }

    private var disallowUsersText: Binding<String> {
        Binding(
            get: { setting.disallowUsers.map(\.value).joined(separator: ", ") },
            set: { setting.disallowUsers = parsePrincipals($0, existing: setting.disallowUsers) }
        )
    }

    private var allowChatsText: Binding<String> {
        Binding(
            get: { setting.allowChats.joined(separator: ", ") },
            set: { setting.allowChats = parseTokens($0) }
        )
    }

    private var disallowChatsText: Binding<String> {
        Binding(
            get: { setting.disallowChats.joined(separator: ", ") },
            set: { setting.disallowChats = parseTokens($0) }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(setting.title)
                        .font(.callout.weight(.semibold))
                    Text(setting.description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Picker("主要規則", selection: $setting.mode) {
                    ForEach(modeOptions, id: \.id) { option in
                        Text(option.title).tag(option.id)
                    }
                }
                .pickerStyle(.menu)
                .frame(width: 180)
            }

            HStack(spacing: 12) {
                Picker("機器人例外", selection: $setting.botMode) {
                    ForEach(Self.botOptions, id: \.id) { option in
                        Text(option.title).tag(option.id)
                    }
                }
                .pickerStyle(.menu)
                .frame(width: 220)
                Text("保留 Telegram 目前的例外順序並套用新的主要規則。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 12) {
                TextField("允許的使用者：ID 或 @username", text: allowUsersText)
                    .textFieldStyle(.roundedBorder)
                TextField("排除的使用者：ID 或 @username", text: disallowUsersText)
                    .textFieldStyle(.roundedBorder)
            }
            HStack(spacing: 12) {
                TextField("允許的群組 ID", text: allowChatsText)
                    .textFieldStyle(.roundedBorder)
                TextField("排除的群組 ID", text: disallowChatsText)
                    .textFieldStyle(.roundedBorder)
            }

            if !isEditable {
                Text("此項目目前由 Telegram／Telethon 標示為不可編輯。")
                    .font(.caption)
                    .foregroundStyle(TeleShieldDesign.warning)
            }
        }
        .padding(12)
        .background(Color(nsColor: .textBackgroundColor).opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: TeleShieldDesign.innerRadius))
        .disabled(!isEditable)
    }

    private func parsePrincipals(_ text: String, existing: [PrivacyPrincipal]) -> [PrivacyPrincipal] {
        let existingByValue = Dictionary(
            uniqueKeysWithValues: existing.map { ($0.value.lowercased(), $0) }
        )
        return parseTokens(text).map { token in
            existingByValue[token.lowercased()] ?? PrivacyPrincipal(value: token)
        }
    }

    private func parseTokens(_ text: String) -> [String] {
        var seen = Set<String>()
        return text.split { character in
            character == "," || character == " " || character == "\n" || character == "\t"
        }.compactMap { substring in
            let token = String(substring).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !token.isEmpty, seen.insert(token.lowercased()).inserted else { return nil }
            return token
        }
    }
}

private struct PrivacyGlobalSettingsEditor: View {
    @Binding var settings: PrivacyGlobalSettings
    let isAvailable: Bool

    private var paidStarsText: Binding<String> {
        Binding(
            get: { String(settings.noncontactPeersPaidStars) },
            set: {
                if let value = Int($0.trimmingCharacters(in: .whitespacesAndNewlines)) {
                    settings.noncontactPeersPaidStars = min(max(value, 0), 1_000_000)
                }
            }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Label("全域與禮物設定", systemImage: "globe")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                if !isAvailable {
                    Text("無法讀取 Telegram 全域設定")
                        .font(.caption)
                        .foregroundStyle(TeleShieldDesign.warning)
                }
            }

            Toggle("封存並靜音陌生人新對話", isOn: $settings.archiveAndMuteNewNoncontactPeers)
            Toggle("保留未靜音的封存對話", isOn: $settings.keepArchivedUnmuted)
            Toggle("保留封存資料夾", isOn: $settings.keepArchivedFolders)
            Toggle("隱藏已讀標記", isOn: $settings.hideReadMarks)
            Toggle("要求陌生人使用 Premium", isOn: $settings.newNoncontactPeersRequirePremium)
            Text("選取 Premium 限制時，未購買 Premium 的帳號在儲存時會顯示資格提示。")
                .font(.caption)
                .foregroundStyle(.secondary)

            Toggle("顯示禮物按鈕", isOn: $settings.displayGiftsButton)

            HStack(spacing: 10) {
                Text("陌生人付費訊息")
                TextField("Stars（0 = 不收費）", text: paidStarsText)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 170)
                Stepper("", value: $settings.noncontactPeersPaidStars, in: 0...1_000_000)
                    .labelsHidden()
                Text("Stars")
                    .foregroundStyle(.secondary)
            }
            Text("此功能需要 Telegram Premium，且仍受 Telegram 帳號資格與可用性限制。")
                .font(.caption)
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 7) {
                Text("拒絕接收的禮物類型")
                    .font(.callout.weight(.medium))
                Toggle("無限量星星禮物", isOn: $settings.disallowedGifts.disallowUnlimitedStargifts)
                Toggle("限量星星禮物", isOn: $settings.disallowedGifts.disallowLimitedStargifts)
                Toggle("獨特星星禮物", isOn: $settings.disallowedGifts.disallowUniqueStargifts)
                Toggle("Premium 禮物", isOn: $settings.disallowedGifts.disallowPremiumGifts)
                Toggle("頻道送出的星星禮物", isOn: $settings.disallowedGifts.disallowStargiftsFromChannels)
            }
        }
        .disabled(!isAvailable)
    }
}

private struct PrivacyCheckRow: View {
    let check: PrivacyCheck

    private var statusColor: Color {
        switch check.status {
        case "ok": return TeleShieldDesign.success
        case "error", "premium_required": return TeleShieldDesign.danger
        case "unsupported": return .secondary
        default: return TeleShieldDesign.warning
        }
    }

    private var statusIcon: String {
        switch check.status {
        case "ok": return "checkmark.circle.fill"
        case "error": return "xmark.octagon.fill"
        case "premium_required": return "crown.fill"
        case "unsupported": return "questionmark.circle.fill"
        default: return "exclamationmark.triangle.fill"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: statusIcon)
                .foregroundStyle(statusColor)
                .font(.title3)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(check.title).font(.callout.weight(.semibold))
                    Text(check.statusTitle)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(statusColor)
                }
                Text(check.description)
                    .font(.caption)
                    .foregroundStyle(TeleShieldDesign.muted)
                if let error = check.error, !error.isEmpty {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(TeleShieldDesign.danger)
                        .textSelection(.enabled)
                }
                if check.exceptionCount > 0 {
                    Text("目前包含 \(check.exceptionCount) 個例外對象")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 12)
            VStack(alignment: .trailing, spacing: 4) {
                Text(check.current)
                    .font(.callout.weight(.medium))
                    .multilineTextAlignment(.trailing)
                if !check.isHealthy && !check.recommended.isEmpty {
                    Text("建議：\(check.recommended)")
                        .font(.caption)
                        .foregroundStyle(statusColor)
                        .multilineTextAlignment(.trailing)
                }
            }
            .frame(maxWidth: 230, alignment: .trailing)
        }
        .padding(.vertical, 8)
        .overlay(alignment: .bottom) {
            Divider()
        }
    }
}

private struct PrivacyTwoFactorCard: View {
    let audit: PrivacyAudit
    let isBusy: Bool
    let onEdit: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("兩步驟驗證", systemImage: audit.twoFactorEnabled ? "checkmark.shield.fill" : "shield")
                    .font(.headline)
                    .foregroundStyle(audit.twoFactorEnabled ? TeleShieldDesign.success : TeleShieldDesign.warning)
                Spacer()
                Button(audit.twoFactorEnabled ? "更新設定" : "立即設定", action: onEdit)
                    .disabled(isBusy)
            }
            Text(
                audit.twoFactorEnabled
                    ? (audit.twoFactorRecoveryConfigured ? "已開啟，且已設定復原 Email。" : "已開啟，但尚未確認復原 Email。")
                    : "尚未開啟；建議為 Telegram 帳號設定額外密碼。"
            )
            .font(.callout)
            .foregroundStyle(.secondary)
            if !audit.twoFactorHint.isEmpty {
                Text("密碼提示：\(audit.twoFactorHint)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text("密碼只會暫時送往 Telegram，TeleShield 不會保存。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: 14)
    }
}

private struct PrivacySessionsCard: View {
    let sessions: [PrivacySession]
    let isBusy: Bool
    let onRevoke: (PrivacySession) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("登入中的裝置", systemImage: "laptopcomputer.and.iphone")
                    .font(.headline)
                Spacer()
                Text("\(sessions.count) 台")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if sessions.isEmpty {
                Text("Telegram 沒有回傳可檢視的 Session。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(sessions) { session in
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: session.current ? "macbook.and.iphone" : "desktopcomputer")
                            .foregroundStyle(session.current ? .green : .secondary)
                            .frame(width: 24)
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 8) {
                                Text(session.deviceTitle)
                                    .font(.callout.weight(.semibold))
                                if session.current {
                                    Text("目前裝置")
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(.green)
                                }
                            }
                            Text([session.appTitle, session.ip, session.country].filter { !$0.isEmpty }.joined(separator: " · "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if let dateActive = session.dateActive {
                                Text("最近活動：\(TimestampFormatter.localString(dateActive))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Spacer(minLength: 8)
                        if !session.current {
                            Button("撤銷", role: .destructive) {
                                onRevoke(session)
                            }
                            .disabled(isBusy)
                        }
                    }
                    .padding(.vertical, 7)
                    .overlay(alignment: .bottom) { Divider() }
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: 14)
    }
}

private struct TwoFactorSettingsSheet: View {
    @ObservedObject var client: CoreClient
    let audit: PrivacyAudit
    @Environment(\.dismiss) private var dismiss
    @State private var currentPassword = ""
    @State private var newPassword = ""
    @State private var confirmation = ""
    @State private var hint = ""
    @State private var removeTwoFactor = false
    @State private var validationMessage = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("兩步驟驗證")
                        .font(.title2.bold())
                    Text(audit.twoFactorEnabled ? "更新或關閉目前的額外密碼" : "為 Telegram 帳號設定額外密碼")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("取消") { dismiss() }
            }

            if audit.twoFactorEnabled {
                SecureField("目前密碼", text: $currentPassword)
                Toggle("關閉兩步驟驗證", isOn: $removeTwoFactor)
            }

            if !audit.twoFactorEnabled || !removeTwoFactor {
                SecureField("新的密碼", text: $newPassword)
                SecureField("確認新的密碼", text: $confirmation)
                TextField("密碼提示（不要輸入密碼本身）", text: $hint)
            }

            Text("Telegram 會直接驗證密碼；TeleShield 不會把密碼寫入設定檔或備份。")
                .font(.caption)
                .foregroundStyle(.secondary)

            if !validationMessage.isEmpty {
                Text(validationMessage)
                    .font(.callout)
                    .foregroundStyle(TeleShieldDesign.danger)
            }

            HStack {
                Spacer()
                Button("儲存") { save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(client.isBusy)
            }
        }
        .padding(24)
        .frame(width: 500)
    }

    private func save() {
        validationMessage = ""
        if audit.twoFactorEnabled && currentPassword.isEmpty {
            validationMessage = "請輸入目前密碼。"
            return
        }
        if !audit.twoFactorEnabled || !removeTwoFactor {
            guard !newPassword.isEmpty else {
                validationMessage = "請輸入新的密碼。"
                return
            }
            guard newPassword == confirmation else {
                validationMessage = "兩次輸入的新密碼不一致。"
                return
            }
        }

        let password = removeTwoFactor ? "" : newPassword
        Task {
            let result = await client.updateTwoFactor(
                currentPassword: currentPassword,
                newPassword: password,
                hint: hint
            )
            if let result, !result.isEmpty {
                validationMessage = result
            } else {
                dismiss()
            }
        }
    }
}

private struct BlockRecordDetailView: View {
    let record: BlockRecord
    @Environment(\.dismiss) private var dismiss

    private func joined(_ values: [String], fallback: String) -> String {
        values.isEmpty ? fallback : values.joined(separator: "、")
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("封鎖詳情")
                            .font(.title2.bold())
                        Text(record.name.isEmpty ? record.userID : record.name)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("關閉") {
                        dismiss()
                    }
                    .keyboardShortcut(.cancelAction)
                    Text(record.displayTime)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }

                VStack(alignment: .leading, spacing: 10) {
                    LabeledContent("User ID", value: record.userID)
                    LabeledContent("來源", value: record.source == "group" ? "群組" : "私訊")
                    LabeledContent("封鎖原因", value: record.reason)
                }

                if let analysis = record.details?.analysis {
                    Divider()
                    VStack(alignment: .leading, spacing: 10) {
                        Text("分析結果")
                            .font(.headline)
                        LabeledContent(
                            "分類",
                            value: joined(analysis.categoryLabels, fallback: "未分類")
                        )
                        LabeledContent(
                            "意圖",
                            value: joined(
                                analysis.intentLabels.isEmpty ? analysis.phishingLabels : analysis.intentLabels,
                                fallback: "未分類"
                            )
                        )
                        LabeledContent(
                            "命中規則",
                            value: joined(analysis.matchedRuleLabels, fallback: "未提供")
                        )
                        LabeledContent(
                            analysis.scoreTypeLabel.isEmpty
                                ? "分數"
                                : "分數（\(analysis.scoreTypeLabel)）",
                            value: analysis.score.flatMap { score in
                                analysis.threshold.map { "\(score) / \($0)" }
                            } ?? "不適用"
                        )
                        LabeledContent(
                            "來源",
                            value: analysis.analysisSourceLabel.isEmpty ? analysis.analysisSource : analysis.analysisSourceLabel
                        )
                        LabeledContent(
                            "內容摘要",
                            value: analysis.contentExcerpt.isEmpty ? "無" : analysis.contentExcerpt
                        )
                        if !analysis.senderContextLabels.isEmpty {
                            LabeledContent("帳號狀態", value: analysis.senderContextLabels.joined(separator: "、"))
                        }
                    }
                } else {
                    Text("此筆為舊格式記錄，只有原始封鎖原因，沒有結構化分析資料。")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(24)
            .frame(minWidth: 520, alignment: .leading)
        }
    }
}

private struct GlobalSettingsView: View {
    @ObservedObject var client: CoreClient
    @ObservedObject var updater: UpdateManager

    var body: some View {
        Form {
            Section("開機啟動") {
                VStack(alignment: .leading, spacing: 10) {
                    Toggle("登入系統時自動啟動 TeleShield", isOn: Binding<Bool>(get: { client.startupEnabled }, set: { value in Task { await client.setStartup(value) } }))
                    Toggle("啟用自動防護之帳號", isOn: Binding<Bool>(
                        get: { !clientAutoStartAccountIDs.isEmpty },
                        set: { enabled in
                            let ids: [String] = enabled
                                ? (clientAutoStartAccountIDs.isEmpty ? configuredAccountIDs : Array(clientAutoStartAccountIDs))
                                : []
                            Task { await client.setAutoStartAccounts(accountIDs: ids) }
                        }
                    ))
                    .disabled(configuredAccountIDs.isEmpty)
                    Text("勾選的 Telegram 帳號會在背景啟動時個別開始防護。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    ForEach(client.status?.accounts ?? []) { account in
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(account.label)
                                Text(account.configured ? (account.phoneMasked.isEmpty ? "電話未提供" : account.phoneMasked) : "尚未登入")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Toggle("", isOn: Binding<Bool>(
                                get: { clientAutoStartAccountIDs.contains(account.id) },
                                set: { enabled in updateAutoStart(accountID: account.id, enabled: enabled) }
                            ))
                            .labelsHidden()
                            .toggleStyle(.switch)
                            .accessibilityLabel("\(account.label) 自動防護")
                        }
                        .help(account.configured ? account.accountIdentifier : "尚未登入")
                        .disabled(!account.configured)
                    }
                }
            }
            Section("App 行為") {
                Text("關閉視窗只會隱藏到 Menu Bar，不會停止防護；要停止請使用左側帳號列表或 Menu Bar。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("OCR runtime 狀態") {
                HStack {
                    Label(client.status?.ocr.available == true ? "OCR 可用" : "OCR 尚未可用", systemImage: "text.viewfinder")
                    Spacer()
                    if let ocr = client.status?.ocr, ocr.available { Text(ocr.languages.joined(separator: "+")).foregroundStyle(.secondary) }
                    Button("重新檢查") { Task { await client.refreshOCR() } }
                }
            }
            Section("軟體更新") {
                VStack(alignment: .leading, spacing: 12) {
                    LabeledContent("目前版本", value: updater.currentVersion.description)
                    HStack {
                        Label(
                            updater.statusMessage,
                            systemImage: updater.availableUpdate == nil ? "checkmark.circle" : "arrow.down.circle"
                        )
                        .foregroundStyle(updater.availableUpdate == nil ? Color.secondary : Color.blue)
                        Spacer()
                        Button("檢查更新") {
                            Task { await updater.checkForUpdates() }
                        }
                        .disabled(updater.isChecking || updater.isDownloading)
                    }
                    Toggle(
                        "自動檢查更新",
                        isOn: Binding(
                            get: { updater.automaticChecksEnabled },
                            set: { updater.setAutomaticChecksEnabled($0) }
                        )
                    )
                    if let update = updater.availableUpdate {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("發現新版本 \(update.version.description)")
                                .font(.headline)
                            Text("目前架構：\(update.architecture)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Button("查看更新") {
                                NotificationCenter.default.post(name: .teleShieldShowUpdate, object: nil)
                            }
                        }
                    }
                    if let checkedAt = updater.lastCheckedAt {
                        Text("上次檢查：\(checkedAt.formatted(date: .abbreviated, time: .shortened))")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if let error = updater.lastError {
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .padding(20)
    }

    private var clientAutoStartAccountIDs: Set<String> {
        Set(client.details?.autoStartAccountIDs ?? [])
    }

    private var configuredAccountIDs: [String] {
        (client.status?.accounts ?? []).filter(\.configured).map(\.id)
    }

    private func updateAutoStart(accountID: String, enabled: Bool) {
        var ids = clientAutoStartAccountIDs
        if enabled {
            ids.insert(accountID)
        } else {
            ids.remove(accountID)
        }
        let orderedIDs = (client.status?.accounts ?? []).map(\.id).filter { ids.contains($0) }
        Task { await client.setAutoStartAccounts(accountIDs: orderedIDs) }
    }
}

private struct UpdateSheet: View {
    @ObservedObject var client: CoreClient
    @ObservedObject var updater: UpdateManager
    let update: AppUpdate
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Label("有可用更新", systemImage: "arrow.down.circle.fill")
                        .font(.title2.bold())
                        .foregroundStyle(.blue)
                    Text("TeleShield \(update.version.description)")
                        .font(.headline)
                }
                Spacer()
                Button("稍後") { dismiss() }
            }

            Divider()

            Text(update.releaseNotes.isEmpty ? "此版本沒有提供發布說明。" : update.releaseNotes)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)

            Text("更新檔：\(update.fileName)（\(update.architecture)）")
                .font(.caption)
                .foregroundStyle(.secondary)

            if client.hasActiveScan {
                Label(
                    "目前有歷史掃描正在執行，請先完成或取消掃描再更新。",
                    systemImage: "exclamationmark.triangle"
                )
                .font(.callout)
                .foregroundStyle(.orange)
            }

            if let error = updater.lastError {
                Text(error)
                    .font(.callout)
                    .foregroundStyle(.red)
            }

            HStack {
                if let releaseURL = update.releaseURL {
                    Link("查看 GitHub Release", destination: releaseURL)
                        .font(.callout)
                }
                Spacer()
                if updater.isDownloading {
                    ProgressView()
                        .controlSize(.small)
                    Text("正在準備更新…")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                } else {
                    Button("下載並更新") {
                        Task {
                            do {
                                try await updater.downloadAndInstall(update) {
                                    await client.shutdownGracefully()
                                }
                            } catch {
                                // UpdateManager publishes the user-facing error.
                            }
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(client.hasActiveScan || updater.isChecking)
                }
            }
        }
        .padding(24)
        .frame(width: 560)
    }
}

private struct ScanStepper: View {
    let title: String
    @Binding var value: Int
    let range: ClosedRange<Int>

    var body: some View {
        Stepper("\(title)：\(value)", value: $value, in: range)
    }
}

private struct AccountDetailsSettingsView: View {
    @ObservedObject var client: CoreClient
    let accountID: String
    @Environment(\.dismiss) private var dismiss
    @State private var policy = ModerationPolicy.defaults
    @State private var telegramNotification = TelegramNotificationPolicy.defaults
    @State private var scanSettings = ScanSettings.defaults
    @State private var pendingEnablePolicy: ModerationPolicy?
    @State private var pendingProtectionMode: ProtectionMode?
    @State private var pendingScope: PrivateHistoryDeletionScope?
    @State private var showEnableConfirmation = false
    @State private var showProtectionModeConfirmation = false
    @State private var showBothScopeConfirmation = false
    @State private var showLogoutConfirmation = false
    @State private var showClearConfirmation = false
    @State private var showClearCredentialsConfirmation = false
    @State private var notificationTestStatus = ""
    @State private var notificationSaveStatus = ""
    @State private var testingNotification = false

    private var account: AccountSummary? {
        client.status?.accounts.first(where: { $0.id == accountID })
    }

    private var sessionActionsDisabled: Bool {
        client.selectedAccountID != accountID || !client.canModifySelectedAccount
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("帳號") {
                    if let account {
                        LabeledContent("名稱", value: account.label)
                        LabeledContent("手機", value: account.phoneMasked.isEmpty ? "未提供" : account.phoneMasked)
                        LabeledContent("Telegram ID", value: account.userID.map(String.init) ?? "未登入")
                        HStack {
                            Text("防護狀態")
                            Spacer()
                            AccountStatusBadge(account: account)
                        }
                    } else {
                        Text("帳號資料載入中…").foregroundStyle(.secondary)
                    }
                }

                Section("防護政策") {
                    Picker(
                        "陌生人防護模式",
                        selection: Binding(
                            get: { policy.protectionMode },
                            set: { mode in
                                guard mode != policy.protectionMode else { return }
                                if mode == .strict {
                                    pendingProtectionMode = mode
                                    showProtectionModeConfirmation = true
                                } else {
                                    persistPolicy(policyWith(protectionMode: mode))
                                }
                            }
                        )
                    ) {
                        ForEach(ProtectionMode.allCases) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }

                    Text(policy.protectionMode.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    Toggle(
                        "封鎖後自動刪除一對一私訊",
                        isOn: Binding(
                            get: { policy.deletePrivateHistoryAfterBlock },
                            set: { enabled in
                                if enabled {
                                    pendingEnablePolicy = policyWith(
                                        protectionMode: policy.protectionMode,
                                        deletePrivateHistoryAfterBlock: true,
                                        deletePrivateHistoryScope: policy.deletePrivateHistoryScope,
                                        telegramNotification: policy.telegramNotification
                                    )
                                    showEnableConfirmation = true
                                } else {
                                    persistPolicy(policyWith(
                                        protectionMode: policy.protectionMode,
                                        deletePrivateHistoryAfterBlock: false,
                                        deletePrivateHistoryScope: policy.deletePrivateHistoryScope,
                                        telegramNotification: policy.telegramNotification
                                    ))
                                }
                            }
                        )
                    )

                    Picker(
                        "刪除範圍",
                        selection: Binding(
                            get: { policy.deletePrivateHistoryScope },
                            set: { scope in
                                guard scope != policy.deletePrivateHistoryScope else { return }
                                if scope == .both {
                                    pendingScope = scope
                                    showBothScopeConfirmation = true
                                } else {
                                    persistPolicy(policyWith(
                                        protectionMode: policy.protectionMode,
                                        deletePrivateHistoryAfterBlock: policy.deletePrivateHistoryAfterBlock,
                                        deletePrivateHistoryScope: scope,
                                        telegramNotification: policy.telegramNotification
                                    ))
                                }
                            }
                        )
                    ) {
                        ForEach(PrivateHistoryDeletionScope.allCases) { scope in
                            Text(scope.title).tag(scope)
                        }
                    }
                    .disabled(!policy.deletePrivateHistoryAfterBlock)

                    Text("只套用於一對一私訊。試運行永遠不會執行刪除。封鎖成功後才會嘗試刪除，刪除失敗不會取消封鎖。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("封鎖後 Telegram 通知") {
                    Toggle("啟用封鎖後 Telegram 通知", isOn: $telegramNotification.enabled)

                    SecureField("Bot Token", text: $telegramNotification.botToken)
                    TextField("Channel ID", text: $telegramNotification.channelID)

                    HStack {
                        Button("儲存通知設定") {
                            persistTelegramNotification()
                        }
                        .buttonStyle(.borderedProminent)

                        Button {
                            let botToken = telegramNotification.botToken
                            let channelID = telegramNotification.channelID
                            testingNotification = true
                            notificationTestStatus = "正在發送測試通知…"
                            Task {
                                let testError = await client.testTelegramNotification(
                                    botToken: botToken,
                                    channelID: channelID,
                                    accountID: accountID
                                )
                                testingNotification = false
                                notificationTestStatus = testError.map {
                                    "測試失敗：\($0)"
                                } ?? "測試通知已發送，請確認頻道是否收到訊息。"
                            }
                        } label: {
                            Label("測試通知", systemImage: "paperplane")
                        }
                        .disabled(
                            testingNotification
                                || telegramNotification.botToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                || telegramNotification.channelID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        )
                    }

                    if !notificationTestStatus.isEmpty {
                        Text(notificationTestStatus)
                            .font(.caption)
                            .foregroundStyle(
                                notificationTestStatus.hasPrefix("測試失敗")
                                    ? Color.red
                                    : Color.secondary
                            )
                    }
                    if !notificationSaveStatus.isEmpty {
                        Text(notificationSaveStatus)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Text("只通知私訊封鎖；此開關不會影響帳戶防護啟動。Bot 必須具備在指定頻道發送訊息的權限。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("掃描設定") {
                    ScanStepper(title: "私訊對話數", value: $scanSettings.privateDialogLimit, range: 1...100)
                    ScanStepper(title: "每個私訊訊息數", value: $scanSettings.privateMessageLimit, range: 1...100)
                    ScanStepper(title: "私訊日期範圍", value: $scanSettings.privateDays, range: 1...365)
                    Button("儲存掃描設定") {
                        Task { await client.updateScanSettings(scanSettings, accountID: accountID) }
                    }
                    .buttonStyle(.borderedProminent)
                }

                Section {
                    HStack {
                        Text("Session 狀態")
                        Spacer()
                        Text(client.details?.loggedIn == true ? "已登入" : "未登入")
                            .foregroundStyle(client.details?.loggedIn == true ? .green : .secondary)
                    }
                    HStack {
                        Text("API 設定")
                        Spacer()
                        Text(client.details?.hasAPICredentials == true ? "已設定" : "未設定")
                            .foregroundStyle(.secondary)
                    }
                    Button("登出 Telegram（保留 API 設定）", role: .destructive) { showLogoutConfirmation = true }
                        .disabled(sessionActionsDisabled)
                    Button("只清除本機 Session", role: .destructive) { showClearConfirmation = true }
                        .disabled(sessionActionsDisabled)
                    Button("刪除 Session 與 API 設定", role: .destructive) { showClearCredentialsConfirmation = true }
                        .disabled(sessionActionsDisabled)
                } header: {
                    Label("Telegram Session", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(TeleShieldDesign.danger)
                } footer: {
                    Text("這些操作只作用於此帳號；即時防護或歷史掃描執行中時會被鎖定。")
                }
            }
            .formStyle(.grouped)
            .navigationTitle("帳號詳細設定")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
        }
        .frame(minWidth: 620, minHeight: 650)
        .task(id: accountID) {
            await client.refreshAccountData(accountID: accountID)
            guard client.selectedAccountID == accountID else { return }
            policy = client.moderationPolicy
            telegramNotification = client.moderationPolicy.telegramNotification
            scanSettings = client.scanSettings
        }
        .confirmationDialog("開啟封鎖後刪除私訊？", isPresented: $showEnableConfirmation, titleVisibility: .visible) {
            Button("確認開啟", role: .destructive) {
                if let pendingEnablePolicy { persistPolicy(pendingEnablePolicy) }
                pendingEnablePolicy = nil
            }
            Button("取消", role: .cancel) { pendingEnablePolicy = nil }
        } message: {
            Text("只會對之後成功封鎖的一對一私訊生效；試運行不會刪除。")
        }
        .confirmationDialog("啟用嚴格模式？", isPresented: $showProtectionModeConfirmation, titleVisibility: .visible) {
            Button("確認啟用", role: .destructive) {
                if let pendingProtectionMode {
                    persistPolicy(policyWith(protectionMode: pendingProtectionMode))
                }
                pendingProtectionMode = nil
            }
            Button("取消", role: .cancel) { pendingProtectionMode = nil }
        } message: {
            Text("啟用後，所有非聯絡人的私訊都會直接封鎖，只有聯絡人與白名單可以傳訊息。這可能封鎖正常的新對話。")
        }
        .confirmationDialog("確認嘗試從雙方刪除？", isPresented: $showBothScopeConfirmation, titleVisibility: .visible) {
            Button("確認使用雙方刪除", role: .destructive) {
                if let pendingScope {
                    persistPolicy(policyWith(
                        protectionMode: policy.protectionMode,
                        deletePrivateHistoryAfterBlock: policy.deletePrivateHistoryAfterBlock,
                        deletePrivateHistoryScope: pendingScope,
                        telegramNotification: policy.telegramNotification
                    ))
                }
                pendingScope = nil
            }
            Button("取消", role: .cancel) { pendingScope = nil }
        } message: {
            Text("Telegram 可能因權限、對話類型或時間限制無法刪除雙方紀錄；目前帳號仍會保留封鎖結果。")
        }
        .confirmationDialog("登出此 Telegram 帳號？", isPresented: $showLogoutConfirmation, titleVisibility: .visible) {
            Button("確認登出", role: .destructive) { Task { await client.logout(removeCredentials: false) } }
            Button("取消", role: .cancel) {}
        } message: { Text("API 設定會保留，但 Session 會失效，需要再次登入。") }
        .confirmationDialog("刪除本機 Session？", isPresented: $showClearConfirmation, titleVisibility: .visible) {
            Button("確認刪除", role: .destructive) { Task { await client.clearSession(removeCredentials: false) } }
            Button("取消", role: .cancel) {}
        }
        .confirmationDialog("刪除 Session 與 API 設定？", isPresented: $showClearCredentialsConfirmation, titleVisibility: .visible) {
            Button("確認全部刪除", role: .destructive) { Task { await client.clearSession(removeCredentials: true) } }
            Button("取消", role: .cancel) {}
        }
    }

    private func persistPolicy(_ nextPolicy: ModerationPolicy) {
        policy = nextPolicy
        Task {
            _ = await client.updateModerationPolicy(nextPolicy, accountID: accountID)
            await client.refreshAccountData(accountID: accountID)
            if client.selectedAccountID == accountID { policy = client.moderationPolicy }
        }
    }

    private func policyWith(
        protectionMode: ProtectionMode? = nil,
        deletePrivateHistoryAfterBlock: Bool? = nil,
        deletePrivateHistoryScope: PrivateHistoryDeletionScope? = nil,
        telegramNotification: TelegramNotificationPolicy? = nil
    ) -> ModerationPolicy {
        ModerationPolicy(
            protectionMode: protectionMode ?? policy.protectionMode,
            deletePrivateHistoryAfterBlock: deletePrivateHistoryAfterBlock ?? policy.deletePrivateHistoryAfterBlock,
            deletePrivateHistoryScope: deletePrivateHistoryScope ?? policy.deletePrivateHistoryScope,
            telegramNotification: telegramNotification ?? policy.telegramNotification
        )
    }

    private func persistTelegramNotification() {
        let nextPolicy = policyWith(
            protectionMode: policy.protectionMode,
            deletePrivateHistoryAfterBlock: policy.deletePrivateHistoryAfterBlock,
            deletePrivateHistoryScope: policy.deletePrivateHistoryScope,
            telegramNotification: telegramNotification
        )
        notificationSaveStatus = "正在儲存通知設定…"
        Task {
            let saved = await client.updateModerationPolicy(nextPolicy, accountID: accountID)
            await client.refreshAccountData(accountID: accountID)
            if saved, client.selectedAccountID == accountID {
                policy = client.moderationPolicy
                telegramNotification = client.moderationPolicy.telegramNotification
                notificationSaveStatus = "通知設定已儲存。"
            } else if !saved {
                notificationSaveStatus = "通知設定儲存失敗。"
            }
        }
    }
}

private struct AccountsView: View {
    @ObservedObject var client: CoreClient
    @Binding var showLogin: Bool
    @State private var removalAccountID: String?
    @State private var detailsAccount: AccountSummary?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            PageHeader(title: "帳號", subtitle: "每個 Telegram 帳號都有獨立 Session 與政策資料") {
                Button("全部啟動") { Task { await client.startAll() } }
                Button("全部停止") { Task { await client.stopAll() } }
            }
            List(client.status?.accounts ?? []) { account in
                HStack(spacing: 12) {
                    Button {
                        Task { await client.selectAccount(account.id) }
                    } label: {
                        AccountRow(account: account, selected: client.selectedAccount?.id == account.id)
                    }
                    .buttonStyle(.plain)
                    Spacer()
                    Button("帳號詳細設定") {
                        Task {
                            await client.selectAccount(account.id)
                            if client.selectedAccountID == account.id {
                                detailsAccount = account
                            }
                        }
                    }
                    Button(account.configured ? "重新登入" : "登入") {
                        Task { await client.selectAccount(account.id); showLogin = true }
                    }
                    .disabled(account.running || client.isBusy)
                    Button("移除", role: .destructive) {
                        removalAccountID = account.id
                    }
                    .disabled(client.isBusy || (client.selectedAccount?.id == account.id && client.hasActiveScan))
                }
            }
            .listStyle(.inset)
        }
        .padding(TeleShieldDesign.pagePadding)
        .confirmationDialog("移除帳號？", isPresented: Binding(get: { removalAccountID != nil }, set: { if !$0 { removalAccountID = nil } }), titleVisibility: .visible) {
            Button("確認移除全部本機資料", role: .destructive) {
                if let accountID = removalAccountID {
                    Task {
                        if client.selectedAccount?.id == accountID,
                           client.selectedAccount?.running == true {
                            await client.stopProtection()
                        } else if client.status?.accounts.first(where: { $0.id == accountID })?.running == true {
                            await client.selectAccount(accountID)
                            await client.stopProtection()
                        }
                        _ = await client.removeAccount(accountID)
                    }
                }
                removalAccountID = nil
            }
            Button("取消", role: .cancel) { removalAccountID = nil }
        } message: {
            Text("若此帳號正在防護，會先停止防護，再刪除它的 Session、設定、名單與封鎖記錄；其他帳號不受影響。")
        }
        .sheet(item: $detailsAccount) { account in
            AccountDetailsSettingsView(client: client, accountID: account.id)
        }
    }
}

private struct LoginSheet: View {
    @ObservedObject var client: CoreClient
    @Binding var temporaryAccountID: String?
    @Environment(\.dismiss) private var dismiss
    @State private var apiID = ""
    @State private var apiHash = ""
    @State private var phone = ""
    @State private var code = ""
    @State private var password = ""
    @State private var cleanupStarted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(client.selectedAccount?.configured == true ? "重新登入 Telegram" : "登入 Telegram").font(.title2.bold())
                    Text("目前帳號：\(client.selectedAccount?.label ?? "尚未選取")").foregroundStyle(.secondary)
                }
                Spacer()
                Link("MTProto API 註冊", destination: URL(string: "https://my.telegram.org/auth?to=apps")!)
                    .font(.callout.weight(.semibold))
                Button("關閉") { dismiss() }
            }
            Divider()
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "lock.shield.fill")
                    .font(.title3)
                    .foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 4) {
                    Text("憑證只在本機處理")
                        .font(.callout.weight(.semibold))
                    Text("API 憑證只會交給本機 sidecar；請勿把 Hash 或 2FA 密碼貼到聊天或記錄中。")
                        .font(.caption)
                        .foregroundStyle(TeleShieldDesign.muted)
                }
            }
            .padding(12)
            .teleShieldSurface(radius: TeleShieldDesign.innerRadius, fill: Color.blue.opacity(0.08))
            Text(authStepTitle)
                .font(.headline)
            if client.authChallengeKind == nil {
                TextField("API ID（正整數）", text: $apiID)
                SecureField("API Hash", text: $apiHash)
                TextField("手機號碼（含國碼）", text: $phone)
            }
            if !client.authDeliveryMessage.isEmpty {
                Text(client.authDeliveryMessage).font(.callout).foregroundStyle(.secondary)
            }
            if client.authChallengeKind == "code" {
                TextField("Telegram 驗證碼", text: $code)
            } else if client.authChallengeKind == "password" {
                SecureField("Telegram 兩步驟驗證密碼", text: $password)
            }
            HStack {
                if client.authChallengeKind == "code" {
                    Button("送出驗證碼") {
                        Task {
                            await client.submitAuthCode(code)
                            code = ""
                        }
                    }
                        .disabled(code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || client.isBusy)
                } else if client.authChallengeKind == "password" {
                    Button("送出 2FA 密碼") {
                        Task {
                            await client.submitAuthPassword(password)
                            password = ""
                        }
                    }
                        .disabled(password.isEmpty || client.isBusy)
                } else {
                    Button("開始登入") {
                        Task {
                            let accountID = temporaryAccountID ?? client.selectedAccountID
                            await client.startAuthentication(
                                apiID: apiID,
                                apiHash: apiHash,
                                phone: phone,
                                accountID: accountID
                            )
                            apiID = ""
                            apiHash = ""
                            phone = ""
                        }
                    }
                        .buttonStyle(.borderedProminent)
                        .disabled(apiID.isEmpty || apiHash.isEmpty || phone.isEmpty || client.isBusy || client.authInProgress)
                }
                if client.authFlowID != nil {
                    Button("取消登入") { dismiss() }
                }
                Spacer()
                if !client.authInProgress && client.selectedAccount?.configured == true {
                    Button("完成") { temporaryAccountID = nil; dismiss() }
                }
            }
            if let error = client.errorMessage { Text(error).foregroundStyle(.red).font(.callout) }
        }
        .padding(TeleShieldDesign.pagePadding)
        .frame(width: 520)
        .onChange(of: client.authInProgress) { inProgress in
            if !inProgress {
                apiHash = ""
                code = ""
                password = ""
            }
        }
        .onDisappear { cleanupAfterDismissal() }
    }

    private func cleanupAfterDismissal() {
        guard !cleanupStarted else { return }
        cleanupStarted = true
        Task {
            if client.authFlowID != nil {
                await client.cancelAuthentication()
            }
            guard let accountID = temporaryAccountID else { return }
            if client.authenticatedAccountID == accountID {
                temporaryAccountID = nil
                return
            }
            if await client.removeAccount(accountID, deleteFiles: true) {
                temporaryAccountID = nil
            }
        }
    }

    private var authStepTitle: String {
        switch client.authChallengeKind {
        case "code": return "步驟 2：輸入 Telegram 驗證碼"
        case "password": return "步驟 3：輸入 Telegram 兩步驟驗證密碼"
        default: return "步驟 1：輸入 Telegram API 與手機號碼"
        }
    }
}

private struct AccountRow: View {
    let account: AccountSummary
    let selected: Bool

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: account.configured ? "person.crop.circle.fill" : "person.crop.circle")
                .font(.title3)
                .foregroundStyle(selected ? .blue : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(account.label).font(.body.weight(.medium))
                Text(account.configured ? (account.phoneMasked.isEmpty ? "電話未提供" : account.phoneMasked) : "尚未登入")
                    .font(.caption).foregroundStyle(.secondary)
                AccountStatusBadge(account: account)
            }
            Spacer()
            if selected {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.blue)
                    .accessibilityLabel("目前使用中")
            }
        }
        .padding(.vertical, 7)
        .padding(.horizontal, 8)
        .background(selected ? Color.accentColor.opacity(0.13) : .clear, in: RoundedRectangle(cornerRadius: 8))
        .contentShape(Rectangle())
    }
}

private struct SidebarAccountRow: View {
    let account: AccountSummary
    let selected: Bool
    let isBusy: Bool
    let onSelect: () -> Void
    let onToggleProtection: (Bool) -> Void

    var body: some View {
        HStack(spacing: 8) {
            Button(action: onSelect) {
                AccountRow(account: account, selected: selected)
            }
            .buttonStyle(.plain)
            .frame(maxWidth: .infinity, alignment: .leading)

            Toggle(
                "",
                isOn: Binding(
                    get: { account.running },
                    set: { onToggleProtection($0) }
                )
            )
            .labelsHidden()
            .toggleStyle(.switch)
            .controlSize(.small)
            .disabled(!account.configured || isBusy)
            .help(account.configured ? "開啟或關閉\(account.label)的防護" : "請先登入此 Telegram 帳號")
            .accessibilityLabel("\(account.label) 防護")
            .accessibilityValue(account.running ? "開啟" : "關閉")
        }
    }
}

private struct CurrentAccountBar: View {
    @ObservedObject var client: CoreClient

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "person.crop.circle.fill")
                .font(.title3)
                .foregroundStyle(.blue)

            VStack(alignment: .leading, spacing: 2) {
                Text("目前正在設定的帳號")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let account = client.selectedAccount {
                    Text(account.label)
                        .font(.body.weight(.semibold))
                    Text(account.phoneMasked.isEmpty ? "電話未提供" : account.phoneMasked)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    AccountStatusBadge(account: account)
                } else {
                    Text("尚未選取帳號")
                        .font(.body.weight(.semibold))
                }
            }

            Spacer()
        }
        .padding(.horizontal, TeleShieldDesign.pagePadding)
        .padding(.vertical, 10)
        .teleShieldPageContent()
        .fixedSize(horizontal: false, vertical: true)
        .frame(minHeight: 82, alignment: .center)
        .frame(maxWidth: .infinity, alignment: .center)
        .background(.bar)
        .accessibilityElement(children: .contain)
    }
}

private struct AccountStatusBadge: View {
    let account: AccountSummary

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: account.statusIcon)
            Text(account.statusLabel)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(account.statusColor)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(account.statusColor.opacity(0.12), in: Capsule())
        .overlay {
            Capsule()
                .strokeBorder(account.statusColor.opacity(0.2), lineWidth: 1)
        }
    }
}

private extension AccountSummary {
    var accountIdentifier: String {
        if !phoneMasked.isEmpty { return phoneMasked }
        if !username.isEmpty { return "@\(username)" }
        return "Telegram 帳號"
    }

    var statusLabel: String {
        if !configured { return "未登入" }
        if let error, !error.isEmpty { return "需要注意" }
        if ready { return "防護中" }
        if running { return "啟動中" }
        return "已停止"
    }

    var statusColor: Color {
        if !configured { return .secondary }
        if let error, !error.isEmpty { return .red }
        if ready { return .green }
        if running { return .orange }
        return .secondary
    }

    var statusIcon: String {
        if !configured { return "person.crop.circle" }
        if let error, !error.isEmpty { return "exclamationmark.triangle.fill" }
        if ready { return "checkmark.shield.fill" }
        if running { return "arrow.triangle.2.circlepath" }
        return "pause.circle.fill"
    }
}

private struct MetricCard: View {
    let title: String
    let value: String
    let icon: String

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(title, systemImage: icon)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(TeleShieldDesign.muted)
            Text(value)
                .font(.system(size: 26, weight: .bold).monospacedDigit())
                .foregroundStyle(.primary)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: TeleShieldDesign.innerRadius)
    }
}

private struct EmptyPanel: View {
    let title: String
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: "doc.text.magnifyingglass")
                .font(.title3.bold())
            Text(message).foregroundStyle(.secondary)
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: 14)
    }
}

struct MenuBarView: View {
    @ObservedObject var client: CoreClient
    @ObservedObject var updater: UpdateManager
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("TeleShield").font(.headline)
            Text(client.connectionMessage).font(.caption).foregroundStyle(.secondary)
            Divider()
            Button("開啟 TeleShield") { openMainWindow() }
            if client.status?.accounts.contains(where: { $0.configured }) == true {
                Button("啟用全部帳號防護") {
                    Task { await client.startAll() }
                }
                .disabled(client.isBusy)
                Button("關閉全部帳號防護") {
                    Task { await client.stopAll() }
                }
                .disabled(client.isBusy)
                Button("重新整理") { Task { await client.refresh() } }
            }
            Divider()
            if let update = updater.availableUpdate {
                Button("有可用更新：v\(update.version.description)") {
                    NotificationCenter.default.post(name: .teleShieldShowUpdate, object: nil)
                    openMainWindow()
                }
                .foregroundStyle(.blue)
            }
            Button("檢查更新…") {
                Task { await updater.checkForUpdates() }
            }
            .disabled(updater.isChecking || updater.isDownloading)
            Button("結束 TeleShield") {
                Task {
                    await client.shutdownGracefully()
                    NSApplication.shared.terminate(nil)
                }
            }
        }
        .padding(12)
        .frame(width: 230)
        .onAppear { updater.startAutomaticChecks() }
        .onReceive(NotificationCenter.default.publisher(for: .teleShieldOpenMainWindow)) { _ in
            openMainWindow()
        }
    }

    private func openMainWindow() {
        let windows = NSApp.windows
            .filter { $0.title == "TeleShield" && $0.styleMask.contains(.titled) }
            .sorted { lhs, rhs in
                if lhs.isVisible != rhs.isVisible { return lhs.isVisible }
                return lhs.isKeyWindow && !rhs.isKeyWindow
            }

        if let mainWindow = windows.first {
            // Reuse the existing SwiftUI window. Calling openWindow every time
            // would create another WindowGroup instance instead of restoring it.
            for duplicate in windows.dropFirst() {
                duplicate.close()
            }
            reveal(mainWindow)
            return
        }

        // The red close button may have removed the WindowGroup instance.
        // Only create one when there is no existing main window at all.
        openWindow(id: "main")
        DispatchQueue.main.async {
            if let mainWindow = NSApp.windows.first(where: { $0.title == "TeleShield" && $0.styleMask.contains(.titled) }) {
                reveal(mainWindow)
            }
        }
    }

    private func reveal(_ window: NSWindow) {
        if window.isMiniaturized { window.deminiaturize(nil) }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }
}

private func openPanel(extensions: [String]) -> URL? {
    let panel = NSOpenPanel()
    panel.canChooseFiles = true
    panel.canChooseDirectories = false
    panel.allowsMultipleSelection = false
    panel.allowedFileTypes = extensions
    return panel.runModal() == .OK ? panel.url : nil
}

private func savePanel(fileExtension: String, name: String) -> URL? {
    let panel = NSSavePanel()
    panel.nameFieldStringValue = name
    panel.allowedFileTypes = [fileExtension]
    return panel.runModal() == .OK ? panel.url : nil
}
