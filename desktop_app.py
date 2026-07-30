"""TeleShield desktop application.

The desktop shell keeps the existing Telethon core in a background QThread,
so closing the window hides it while the Telegram listener remains connected.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
from datetime import datetime, timezone
from typing import Optional

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from desktop_platform import is_start_on_login_enabled, set_start_on_login
from teleshield import authenticate, load_config, listen, save_config


class SetupWorker(QThread):
    """Run Telegram login away from the Qt UI thread."""

    code_required = Signal()
    password_required = Signal()
    code_delivery = Signal(str)
    completed = Signal(bool, str)

    def __init__(self, api_id: str, api_hash: str, phone: str):
        super().__init__()
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self._code_event = threading.Event()
        self._password_event = threading.Event()
        self._code: Optional[str] = None
        self._password: Optional[str] = None

    def provide_code(self, value: str) -> None:
        self._code = value.strip()
        self._code_event.set()

    def provide_password(self, value: str) -> None:
        self._password = value
        self._password_event.set()

    def cancel_prompt(self) -> None:
        self._code = ""
        self._password = ""
        self._code_event.set()
        self._password_event.set()

    async def _get_code(self) -> str:
        self.code_required.emit()
        await asyncio.to_thread(self._code_event.wait)
        return self._code or ""

    async def _get_password(self) -> str:
        self.password_required.emit()
        await asyncio.to_thread(self._password_event.wait)
        return self._password or ""

    def run(self) -> None:
        try:
            me = asyncio.run(
                authenticate(
                    self.api_id,
                    self.api_hash,
                    self.phone,
                    self._get_code,
                    self._get_password,
                    self.code_delivery.emit,
                )
            )
            username = f"@{me.username}" if me.username else "無 username"
            self.completed.emit(True, f"登入成功：{me.first_name or ''} ({username})")
        except Exception as exc:  # surfaced in the setup dialog
            self.completed.emit(False, str(exc))


class ListenerWorker(QThread):
    """Own an asyncio loop for the long-running Telethon listener."""

    started_ok = Signal()
    stopped = Signal(str)

    def __init__(self):
        super().__init__()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._stop_requested = False
        self._ready = False

    def _on_ready(self) -> None:
        self._ready = True
        self.started_ok.emit()

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stop_event = asyncio.Event()
        self._ready = False
        if self._stop_requested:
            self._stop_event.set()

        try:
            success = loop.run_until_complete(listen(self._stop_event, self._on_ready))
            if not self._ready:
                self.stopped.emit("防護未啟動：請先完成 Telegram 登入")
            elif success:
                self.stopped.emit("防護已停止")
            else:
                self.stopped.emit("防護因連線錯誤停止")
        except Exception as exc:
            self.stopped.emit(f"防護停止：{exc}")
        finally:
            loop.close()
            self._loop = None
            self._stop_event = None

    def stop(self) -> None:
        self._stop_requested = True
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)


class SetupDialog(QDialog):
    """GUI version of the original --setup flow."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("TeleShield Telegram 登入")
        self.setModal(True)
        self.resize(520, 230)
        self.worker: Optional[SetupWorker] = None

        form = QFormLayout()
        self.api_id = QLineEdit()
        self.api_hash = QLineEdit()
        self.phone = QLineEdit()
        self.api_id.setPlaceholderText("例如 1234567")
        self.api_hash.setPlaceholderText("從 my.telegram.org/apps 取得")
        self.phone.setPlaceholderText("例如 +886...")
        form.addRow("API ID", self.api_id)
        form.addRow("API Hash", self.api_hash)
        form.addRow("手機號碼", self.phone)

        hint = QLabel(
            "首次登入會要求 Telegram 驗證碼；如果帳號啟用兩步驟驗證，"
            "也會在下一步要求密碼。"
        )
        hint.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.accepted.connect(self.start_login)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def start_login(self) -> None:
        if not self.api_id.text().strip().isdigit():
            QMessageBox.warning(self, "設定錯誤", "API ID 必須是數字。")
            return
        if not self.api_hash.text().strip() or not self.phone.text().strip():
            QMessageBox.warning(self, "設定錯誤", "請填寫 API Hash 和手機號碼。")
            return

        self.buttons.setEnabled(False)
        self.worker = SetupWorker(
            self.api_id.text().strip(),
            self.api_hash.text().strip(),
            self.phone.text().strip(),
        )
        self.worker.code_required.connect(self.ask_code)
        self.worker.code_delivery.connect(self.show_code_delivery)
        self.worker.password_required.connect(self.ask_password)
        self.worker.completed.connect(self.login_completed)
        self.worker.start()

    def show_code_delivery(self, delivery: str) -> None:
        labels = {
            "App": "Telegram App（已登入的其他裝置）",
            "Sms": "SMS 簡訊",
            "Call": "語音電話",
            "FlashCall": "閃電電話",
            "MissedCall": "未接來電",
        }
        method, separator, details = delivery.partition("；")
        destination = labels.get(method, method)
        detail_text = f"\n{details}" if separator and details else ""
        QMessageBox.information(
            self,
            "驗證碼已請求",
            f"Telegram 回報的投遞方式：{destination}。{detail_text}\n\n"
            "請先查看其他已登入裝置的 Telegram，尤其是官方「Telegram」服務訊息；"
            "第三方登入不一定會收到 SMS。",
        )

    def ask_code(self) -> None:
        value, ok = QInputDialog.getText(
            self,
            "Telegram 驗證碼",
            "請輸入 Telegram 收到的驗證碼（不一定是 SMS）：",
        )
        if ok and self.worker:
            self.worker.provide_code(value)
        elif self.worker:
            self.worker.cancel_prompt()

    def ask_password(self) -> None:
        value, ok = QInputDialog.getText(
            self,
            "Telegram 兩步驟驗證",
            "請輸入兩步驟驗證密碼：",
            QLineEdit.EchoMode.Password,
        )
        if ok and self.worker:
            self.worker.provide_password(value)
        elif self.worker:
            self.worker.cancel_prompt()

    def login_completed(self, success: bool, message: str) -> None:
        self.buttons.setEnabled(True)
        if success:
            QMessageBox.information(self, "登入完成", message)
            self.accept()
        else:
            QMessageBox.critical(self, "登入失敗", message)

    def reject(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel_prompt()
            self.worker.wait(2000)
        super().reject()


class MainWindow(QMainWindow):
    def __init__(self, background: bool = False):
        super().__init__()
        self.background_start = background
        self.allow_close = False
        self.listener: Optional[ListenerWorker] = None
        self.setWindowTitle("TeleShield")
        self.resize(720, 540)

        self._build_ui()
        self._build_tray()
        self.refresh_status()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_status)
        self.refresh_timer.start(3000)

        cfg = load_config()
        self.auto_start_checkbox.setChecked(is_start_on_login_enabled())
        self.auto_protection_checkbox.setChecked(bool(cfg.get("auto_start_protection")))

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)

        title = QLabel("🛡️ TeleShield")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        subtitle = QLabel("Telegram 個人帳號廣告防護")
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        status_box = QGroupBox("目前狀態")
        status_layout = QGridLayout(status_box)
        self.status_label = QLabel("尚未啟動")
        self.account_label = QLabel("尚未登入")
        self.count_label = QLabel("")
        status_layout.addWidget(QLabel("防護"), 0, 0)
        status_layout.addWidget(self.status_label, 0, 1)
        status_layout.addWidget(QLabel("帳號"), 1, 0)
        status_layout.addWidget(self.account_label, 1, 1)
        status_layout.addWidget(QLabel("名單／封鎖"), 2, 0)
        status_layout.addWidget(self.count_label, 2, 1)
        layout.addWidget(status_box)

        actions = QHBoxLayout()
        self.setup_button = QPushButton("登入 Telegram")
        self.start_button = QPushButton("開始防護")
        self.stop_button = QPushButton("停止防護")
        self.start_button.clicked.connect(self.start_protection)
        self.stop_button.clicked.connect(self.stop_protection)
        self.setup_button.clicked.connect(self.open_setup)
        actions.addWidget(self.setup_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)

        tabs = QTabWidget()
        tabs.addTab(self._list_tab(), "黑名單／白名單")
        tabs.addTab(self._settings_tab(), "設定")
        layout.addWidget(tabs)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("事件摘要會顯示在這裡。詳細封鎖記錄保存在本機資料目錄。")
        layout.addWidget(self.log_view)
        self.setCentralWidget(root)

    def _list_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel("輸入 Telegram numeric user ID；設定會立即套用到背景防護。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.user_id_edit = QLineEdit()
        self.user_id_edit.setPlaceholderText("例如 123456789")
        grid = QGridLayout()
        grid.addWidget(QLabel("使用者 ID"), 0, 0)
        grid.addWidget(self.user_id_edit, 0, 1, 1, 2)

        for row, list_type, label in (
            (1, "whitelist", "白名單"),
            (2, "blacklist", "黑名單"),
        ):
            add_button = QPushButton(f"加入{label}")
            remove_button = QPushButton(f"移除{label}")
            add_button.clicked.connect(lambda _=False, kind=list_type: self.update_list(kind, "add"))
            remove_button.clicked.connect(lambda _=False, kind=list_type: self.update_list(kind, "remove"))
            grid.addWidget(add_button, row, 0)
            grid.addWidget(remove_button, row, 1)
            grid.addWidget(QLabel(f"目前：{label}"), row, 2)
        layout.addLayout(grid)

        refresh = QPushButton("重新整理")
        refresh.clicked.connect(self.refresh_status)
        layout.addWidget(refresh)
        layout.addStretch()
        return tab

    def _settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.auto_start_checkbox = QCheckBox("登入系統時自動啟動 TeleShield")
        self.auto_protection_checkbox = QCheckBox("啟動後自動開始防護")
        self.auto_start_checkbox.toggled.connect(self.update_startup)
        self.auto_protection_checkbox.toggled.connect(self.update_auto_protection)
        layout.addWidget(self.auto_start_checkbox)
        layout.addWidget(self.auto_protection_checkbox)
        layout.addWidget(
            QLabel(
                "關閉主視窗只會縮到系統匣，背景防護不會停止。\n"
                "要真正停止，請按「停止防護」或從系統匣選擇「結束」。"
            )
        )
        layout.addStretch()
        return tab

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.make_tray_icon(), self)
        self.tray.setToolTip("TeleShield 防護中")
        menu = QMenu(self)
        show_action = QAction("開啟 TeleShield", self)
        start_action = QAction("開始防護", self)
        stop_action = QAction("停止防護", self)
        exit_action = QAction("結束程式", self)
        show_action.triggered.connect(self.show_window)
        start_action.triggered.connect(self.start_protection)
        stop_action.triggered.connect(self.stop_protection)
        exit_action.triggered.connect(self.quit_application)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(start_action)
        menu.addAction(stop_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    @staticmethod
    def make_tray_icon() -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#2563eb"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 28, 28)
        painter.setPen(QColor("white"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")
        painter.end()
        return QIcon(pixmap)

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh_status(self) -> None:
        cfg = load_config()
        if cfg.get("user_id"):
            username = f"@{cfg['username']}" if cfg.get("username") else "無 username"
            self.account_label.setText(f"{username} (ID: {cfg['user_id']})")
            self.setup_button.setText("重新登入 Telegram")
        else:
            self.account_label.setText("尚未登入")
            self.setup_button.setText("登入 Telegram")

        running = bool(self.listener and self.listener.isRunning())
        self.status_label.setText("防護中" if running else "已停止")
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.count_label.setText(
            f"白 {len(cfg.get('whitelist', {}))} / 黑 {len(cfg.get('blacklist', {}))} / "
            f"私訊封鎖 {cfg.get('blocked_count', 0)} / 群組踢除 {cfg.get('kicked_count', 0)}"
        )

    def open_setup(self) -> None:
        dialog = SetupDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_status()
            self.log_view.append("✅ Telegram 登入設定完成")

    def start_protection(self) -> None:
        if self.listener and self.listener.isRunning():
            return
        if not load_config().get("api_id"):
            QMessageBox.information(self, "尚未登入", "請先登入 Telegram。")
            self.open_setup()
            if not load_config().get("api_id"):
                return

        self.listener = ListenerWorker()
        self.listener.started_ok.connect(self.listener_started)
        self.listener.stopped.connect(self.listener_stopped)
        self.listener.start()
        self.status_label.setText("啟動中")

    def listener_started(self) -> None:
        self.status_label.setText("防護中")
        self.tray.setToolTip("TeleShield 防護中")
        self.log_view.append(f"[{self.now()}] ✅ 背景防護已啟動")

    def stop_protection(self) -> None:
        if not self.listener or not self.listener.isRunning():
            return
        self.status_label.setText("停止中")
        self.listener.stop()
        if not self.listener.wait(10000):
            QMessageBox.warning(self, "停止逾時", "Telegram 連線尚未回應，請稍後再試。")

    def listener_stopped(self, message: str) -> None:
        self.log_view.append(f"[{self.now()}] {message}")
        self.tray.setToolTip("TeleShield")
        self.refresh_status()

    def update_list(self, list_type: str, action: str) -> None:
        user_id = self.user_id_edit.text().strip()
        if not user_id or not user_id.lstrip("-").isdigit():
            QMessageBox.warning(self, "使用者 ID 錯誤", "請輸入 numeric Telegram user ID。")
            return
        cfg = load_config()
        entries = cfg.setdefault(list_type, {})
        if action == "add":
            entries[user_id] = {
                "added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "username": "",
                "reason": "desktop",
            }
            self.log_view.append(f"✅ 已加入 {list_type}: {user_id}")
        else:
            entries.pop(user_id, None)
            self.log_view.append(f"✅ 已從 {list_type} 移除: {user_id}")
        save_config(cfg)
        self.user_id_edit.clear()
        self.refresh_status()

    def update_startup(self, enabled: bool) -> None:
        try:
            set_start_on_login(enabled)
        except Exception as exc:
            self.auto_start_checkbox.blockSignals(True)
            self.auto_start_checkbox.setChecked(not enabled)
            self.auto_start_checkbox.blockSignals(False)
            QMessageBox.warning(self, "開機啟動設定失敗", str(exc))
            return
        self.log_view.append(f"{'✅ 已啟用' if enabled else '⏹ 已停用'} 開機自動啟動")

    def update_auto_protection(self, enabled: bool) -> None:
        cfg = load_config()
        cfg["auto_start_protection"] = enabled
        save_config(cfg)

    def maybe_auto_start(self) -> None:
        if load_config().get("auto_start_protection") and load_config().get("api_id"):
            QTimer.singleShot(250, self.start_protection)
        elif self.background_start:
            self.tray.showMessage(
                "TeleShield",
                "已在系統匣啟動；請開啟視窗完成 Telegram 設定。",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def closeEvent(self, event) -> None:
        if self.allow_close:
            event.accept()
            return
        self.hide()
        self.tray.showMessage(
            "TeleShield",
            "視窗已隱藏，防護仍在背景執行。從系統匣重新開啟或選擇「結束」。",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )
        event.ignore()

    def quit_application(self) -> None:
        self.allow_close = True
        self.stop_protection()
        self.tray.hide()
        QApplication.instance().quit()

    @staticmethod
    def now() -> str:
        return datetime.now().strftime("%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="TeleShield desktop application")
    parser.add_argument("--background", action="store_true", help="start hidden in the system tray")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("TeleShield")
    app.setOrganizationName("TeleShield")
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(background=args.background)
    window.maybe_auto_start()
    if not args.background:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
