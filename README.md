> 本專案是從 [WAHSUN 的 TeleShield](https://github.com/c92d58/TeleShield) fork 出來的延伸工作。原始 TeleShield 核心、設計與領域邏輯屬於上游作者；本 fork 主要加入 macOS 原生 SwiftUI 桌面介面與 sidecar 整合，並保留原有 attribution 與 MIT License。

<div align="center">
  <h1>🛡️ TeleShield</h1>
  <p><strong>macOS 原生 SwiftUI Telegram 個人帳號防護工具</strong><br>
  <em>Native SwiftUI shell with a Python/Telethon protection sidecar.</em></p>
  <a href="LICENSE">MIT License</a>
</div>

## 專案定位

本 repository 只提供 macOS SwiftUI 原生桌面路徑：

```text
TeleShield.app
    │ SwiftUI / AppKit
    │ line-delimited JSON-RPC over stdin/stdout
    ▼
Contents/Helpers/TeleShieldCore
    │ PyInstaller Python sidecar
    ▼
core_service.py → teleshield.py → Telethon → Telegram
```

SwiftUI 不直接 import Python，也不直接持有 Telethon client。SwiftUI 負責畫面、狀態與 macOS 整合；`core_service.py` 負責 sidecar 的 IPC、背景工作生命週期；`teleshield.py` 提供 Telegram、帳號、Session、掃描、OCR 與報告核心。

`teleshield.py` 現在是 sidecar 使用的 library，不再提供對外 CLI 入口。

目前是測試版 macOS artifact。DMG 未完成 Apple Developer code signing、notarization 或 stapling，不代表已通過正式發布驗證。

## 功能

- 多個 Telegram 個人帳號與獨立 Session、設定、名單、群組及記錄
- MTProto API ID／Hash、驗證碼與 Telegram 2FA 登入
- 私訊廣告即時偵測與封鎖
- 管理員群組廣告偵測與成員移除
- 歷史私訊／群組掃描、dry-run 預覽、進度與取消
- 文字規則、白名單、黑名單與學習模式
- 本機 Tesseract OCR，支援英文、簡體中文與繁體中文
- 封鎖記錄、每日／每週報告與 JSON／CSV 匯出
- 封鎖後自動刪除一對一私訊
- 封鎖後透過 Telegram Bot API 發送通知
- Menu Bar 常駐、背景防護與 macOS 開機啟動

所有封鎖、踢除與刪除動作都應先使用預覽或確認流程；群組處理需要 Telegram 管理員權限。

## Repository map

| 路徑 | 用途 |
|---|---|
| `swiftui/Sources/TeleShieldApp/` | SwiftUI App、模型、RPC client、Menu Bar |
| `swiftui/Tests/` | SwiftUI 狀態與格式化測試 |
| `core_service.py` | stdio JSON-RPC sidecar 與背景工作管理 |
| `teleshield.py` | Telegram／Telethon 核心 library，無 CLI entry point |
| `desktop_platform.py` | macOS LaunchAgent startup adapter |
| `tests/test_core_service.py` | sidecar protocol、登入、worker、事件與 redaction 測試 |
| `tests/test_teleshield_core.py` | Telegram 核心、帳號隔離、掃描、OCR、名單、報告與 Session 測試 |
| `scripts/build_swiftui_macos.sh` | App + PyInstaller helper bundle 建置 |
| `scripts/bundle_tesseract_macos.sh` | 建立可攜式 OCR runtime |
| `scripts/install_sidecar_dependencies.sh` | 安裝 sidecar 建置依賴 |
| `.github/workflows/swiftui-build.yml` | Python checks 與 Intel／Apple Silicon SwiftUI DMG build |

## 安全與資料處理

- 只使用自己控制的 Telegram 個人帳號與裝置。
- API ID、API Hash、手機號碼、驗證碼、2FA 密碼與 Session 不可提交至 Git、issue、CI log 或聊天訊息。
- sidecar 會對 RPC event、錯誤與 log 做 credential-like value redaction。
- Session、設定與記錄放在使用者資料目錄，不放在 App bundle；Session 等同 Telegram 身分憑證。
- 每個帳號都有獨立的 Session、設定、名單、群組與封鎖記錄。
- 登出、清除 Session、刪除帳號資料、封鎖、踢除與刪除對話都是可能不可逆的操作。

## 建置需求

- macOS 13 或更新版本
- Xcode／Swift toolchain
- Python 3.9 以上
- Homebrew：`tesseract`、`tesseract-lang`、`dylibbundler`、`zlib`、`jpeg-turbo`
- Telegram MTProto API credentials

安裝 sidecar 依賴：

```bash
python3 -m pip install -r requirements-sidecar.txt
```

## 本機建置

先建立 OCR runtime：

```bash
scripts/bundle_tesseract_macos.sh build/tesseract-runtime
```

再執行：

```bash
scripts/build_swiftui_macos.sh
```

產物為：

```text
dist/TeleShieldSwiftUI.app
```

正式交付的 Intel／Apple Silicon DMG 由 GitHub Actions 產生。Action 會驗證 SwiftUI 架構、sidecar 架構、OCR runtime、Info.plist 與 JSON-RPC smoke test。

## 測試

Python sidecar 與核心測試：

```bash
python3 -m py_compile teleshield.py desktop_platform.py core_service.py
python3 -m pytest -q
```

SwiftUI 測試與 release build：

```bash
swift test --package-path swiftui
swift build --package-path swiftui -c release
```

SwiftUI action 會在 macOS Intel 與 Apple Silicon runner 各自建置，只產生 SwiftUI App 與對應 DMG artifact。

## License

本 fork 使用 [MIT License](LICENSE)。請保留上游 TeleShield 作者與原始專案 attribution。
