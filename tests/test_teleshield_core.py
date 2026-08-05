import asyncio
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import telethon
import teleshield
from desktop_platform import application_command
from telethon.tl.types import User


def configure_temp_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(teleshield, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(teleshield, "SESSION_FILE", tmp_path / "user.session")
    monkeypatch.setattr(teleshield, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(teleshield, "BLOCK_LOG", tmp_path / "block_log.json")


def _identity_update_process(root, account_id, start_event, results):
    """Widen the pre-write race so cross-process registry locking is deterministic."""
    import time

    original_write = teleshield._write_account_registry

    def slow_write(data, root=None):
        time.sleep(0.2)
        return original_write(data, root)

    teleshield._write_account_registry = slow_write
    start_event.wait(10)
    try:
        teleshield.update_account_identity(
            account_id,
            SimpleNamespace(id=9090, username="shared", first_name="Shared"),
            root=Path(root),
        )
        results.put("ok")
    except ValueError:
        results.put("duplicate")
    except Exception as exc:
        results.put(f"error:{type(exc).__name__}:{exc}")


def test_default_session_dir_honours_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TELESHIELD_DATA_DIR", str(tmp_path / "data"))
    assert teleshield.default_session_dir() == tmp_path / "data"


def test_authenticate_reports_telegram_code_delivery(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)

    class SentCodeTypeApp:
        pass

    class FakeClient:
        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return False

        async def send_code_request(self, phone):
            return SimpleNamespace(type=SentCodeTypeApp())

        async def sign_in(self, **kwargs):
            pass

        async def get_me(self):
            return SimpleNamespace(id=42, username="test_user", first_name="Test")

    delivery = []
    with patch.object(telethon, "TelegramClient", FakeClient):
        me = asyncio.run(
            teleshield.authenticate(
                "1234",
                "[REDACTED]",
                "+10000000000",
                lambda: asyncio.sleep(0, result="[REDACTED]"),
                lambda: asyncio.sleep(0, result="[REDACTED]"),
                delivery.append,
            )
        )

    assert me.id == 42
    assert delivery == ["App"]
    assert teleshield.load_config()["user_id"] == 42


def test_macos_launch_command_starts_swiftui_app_hidden(monkeypatch):
    monkeypatch.setenv("TELESHIELD_STARTUP_APP", "/Applications/TeleShield.app")
    command = application_command()
    assert command[-1] == "--background"
    assert command


def test_scan_history_private_dry_run_reports_match_without_blocking(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "user_id": 1,
        "blocked_count": 0,
        "whitelist": {},
        "blacklist": {},
        "moderation_policy": {
            "delete_private_history_after_block": True,
            "delete_private_history_scope": "both",
        },
    })

    spammer = User(id=2, is_self=False, bot=False, first_name="Spam")

    class FakeClient:
        delete_requests = 0

        def __init__(self, *args):
            self.block_requests = 0

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_dialogs(self, limit):
            assert limit == 30
            return [SimpleNamespace(entity=spammer)]

        async def get_messages(self, entity, limit):
            assert entity is spammer
            assert limit == 5
            return [SimpleNamespace(
                date=datetime.now(timezone.utc),
                text="投資穩賺，立即加入",
                photo=None,
            )]

        async def delete_dialog(self, entity, revoke=False):
            type(self).delete_requests += 1

        async def __call__(self, request):
            return SimpleNamespace(users=[])

    with patch.object(telethon, "TelegramClient", FakeClient):
        result = asyncio.run(teleshield.scan_history("private", dry_run=True))

    assert result["matched"] == 1
    assert result["acted"] == 0
    assert result["dry_run"] is True
    assert result["cancelled"] is False
    assert teleshield.load_config()["blocked_count"] == 0
    assert FakeClient.delete_requests == 0


def test_scan_history_paginates_and_filters_before_private_dialog_limit(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "user_id": 1,
        "blocked_count": 0,
        "whitelist": {},
        "blacklist": {},
    }, account_id="account-a")

    contact = User(id=3, is_self=False, bot=False, first_name="Contact")
    spammer = User(id=2, is_self=False, bot=False, first_name="Spam")
    group_dialogs = [SimpleNamespace(entity=SimpleNamespace()) for _ in range(35)]
    dialogs = [
        *group_dialogs,
        SimpleNamespace(entity=contact),
        # The folder marker represents an archived dialog. The scanner should
        # still reach it after skipping unrelated dialogs.
        SimpleNamespace(entity=spammer, folder=1),
    ]

    class FakeClient:
        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def iter_dialogs(self, limit=None):
            assert limit is None
            for dialog in dialogs:
                yield dialog

        async def get_messages(self, entity, limit):
            assert entity is spammer
            return [SimpleNamespace(
                date=datetime.now(timezone.utc),
                text="投資穩賺，立即加入",
                photo=None,
            )]

        async def __call__(self, request):
            return SimpleNamespace(users=[contact])

    with patch.object(telethon, "TelegramClient", FakeClient):
        result = asyncio.run(
            teleshield.scan_history("private", dry_run=True, account_id="account-a")
        )

    assert result["account_id"] == "account-a"
    assert result["dialogs_seen"] == len(dialogs)
    assert result["dialogs_scanned"] == 1
    assert result["matched"] == 1


def test_scan_history_private_applies_block_and_records_result(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "user_id": 1,
        "blocked_count": 0,
        "whitelist": {},
        "blacklist": {},
    })

    spammer = User(id=2, is_self=False, bot=False, first_name="Spam")

    class FakeClient:
        block_requests = 0

        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_dialogs(self, limit):
            return [SimpleNamespace(entity=spammer)]

        async def get_messages(self, entity, limit):
            return [SimpleNamespace(
                date=datetime.now(timezone.utc),
                text="投資穩賺，立即加入",
                photo=None,
            )]

        async def __call__(self, request):
            if type(request).__name__ == "BlockRequest":
                type(self).block_requests += 1
            return SimpleNamespace(users=[])

    with patch.object(telethon, "TelegramClient", FakeClient):
        result = asyncio.run(teleshield.scan_history("private", dry_run=False))

    assert result["matched"] == 1
    assert result["acted"] == 1
    assert FakeClient.block_requests == 1
    assert teleshield.load_config()["blocked_count"] == 1
    assert teleshield.load_block_log()["blocks"][0]["source"] == "scan"


def test_strict_mode_scans_and_blocks_non_contacts_without_waiting_for_text(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "user_id": 1,
        "blocked_count": 0,
        "whitelist": {},
        "blacklist": {},
        "moderation_policy": {"protection_mode": "strict"},
    })

    contact = User(id=3, is_self=False, bot=False, first_name="Contact")
    stranger = User(id=2, is_self=False, bot=False, first_name="Stranger")

    class FakeClient:
        block_requests = 0

        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_dialogs(self, limit):
            return [SimpleNamespace(entity=contact), SimpleNamespace(entity=stranger)]

        async def get_messages(self, entity, limit):
            raise AssertionError("嚴格模式不應讀取歷史訊息內容")

        async def __call__(self, request):
            if type(request).__name__ == "BlockRequest":
                type(self).block_requests += 1
                return SimpleNamespace()
            return SimpleNamespace(users=[contact])

    with patch.object(telethon, "TelegramClient", FakeClient):
        result = asyncio.run(teleshield.scan_history("private", dry_run=False))

    assert result["dialogs_scanned"] == 1
    assert result["messages_scanned"] == 1
    assert result["matched"] == 1
    assert result["acted"] == 1
    assert FakeClient.block_requests == 1
    assert teleshield.load_block_log()["blocks"][0]["reason"] == "嚴格模式：非聯絡人"


def test_realtime_first_message_blocks_homophone_without_url(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "user_id": 1,
        "blocked_count": 0,
        "whitelist": {},
        "blacklist": {},
    })
    stranger = User(id=2, is_self=False, bot=False, first_name="Stranger")
    registered_handlers = []

    class FakeEvent:
        message = SimpleNamespace(sender_id=2, text="偷資群組", photo=None)

        async def get_chat(self):
            return stranger

        async def get_sender(self):
            return stranger

    class FakeClient:
        block_requests = 0

        def __init__(self, *args):
            pass

        def on(self, event):
            def register(handler):
                registered_handlers.append(handler)
                return handler
            return register

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def run_until_disconnected(self):
            await registered_handlers[0](FakeEvent())

        async def __call__(self, request):
            if type(request).__name__ == "BlockRequest":
                type(self).block_requests += 1
                return SimpleNamespace()
            return SimpleNamespace(users=[])

    with patch.object(telethon, "TelegramClient", FakeClient):
        result = asyncio.run(teleshield.listen())

    assert result is True
    assert FakeClient.block_requests == 1
    assert teleshield.load_config()["blocked_count"] == 1
    record = teleshield.load_block_log()["blocks"][0]
    assert record["reason"] == "高信心垃圾訊息"
    assert record["details"]["analysis"]["content_excerpt"] == "偷資群組"
    assert record["details"]["analysis"]["category_labels"] == ["投資"]


def test_private_history_policy_is_account_scoped_and_requires_successful_block(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")

    assert teleshield.get_moderation_policy(account_id="account-a") == {
        "protection_mode": "normal",
        "delete_private_history_after_block": False,
        "delete_private_history_scope": "self",
        "telegram_notification": {
            "enabled": False,
            "bot_token": "",
            "channel_id": "",
        },
    }
    assert teleshield.update_moderation_policy(
        {
            "protection_mode": "normal",
            "delete_private_history_after_block": True,
            "delete_private_history_scope": "both",
            "telegram_notification": {
                "enabled": True,
                "bot_token": "123456:ABC",
                "channel_id": "-1001234567890",
            },
        },
        account_id="account-a",
    ) == {
        "protection_mode": "normal",
        "delete_private_history_after_block": True,
        "delete_private_history_scope": "both",
        "telegram_notification": {
            "enabled": True,
            "bot_token": "123456:ABC",
            "channel_id": "-1001234567890",
        },
    }
    assert teleshield.get_moderation_policy(account_id="account-b")[
        "delete_private_history_after_block"
    ] is False

    assert teleshield.update_moderation_policy(
        {"protection_mode": "strict"}, account_id="account-b"
    )["protection_mode"] == "strict"
    with pytest.raises(ValueError, match="protection_mode"):
        teleshield.update_moderation_policy(
            {"protection_mode": "unknown"}, account_id="account-b"
        )

    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "user_id": 1,
        "blocked_count": 0,
        "whitelist": {},
        "blacklist": {},
        "moderation_policy": {
            "delete_private_history_after_block": True,
            "delete_private_history_scope": "both",
        },
    }, account_id="account-a")
    spammer = User(id=2, is_self=False, bot=False, first_name="Spam")

    class FakeClient:
        block_requests = 0
        delete_requests = []

        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_dialogs(self, limit):
            return [SimpleNamespace(entity=spammer)]

        async def get_messages(self, entity, limit):
            return [SimpleNamespace(
                date=datetime.now(timezone.utc),
                text="投資穩賺，立即加入",
                photo=None,
            )]

        async def delete_dialog(self, entity, revoke=False):
            self.delete_requests.append((entity.id, revoke))

        async def __call__(self, request):
            if type(request).__name__ == "BlockRequest":
                type(self).block_requests += 1
            return SimpleNamespace(users=[])

    with patch.object(telethon, "TelegramClient", FakeClient):
        result = asyncio.run(teleshield.scan_history("private", dry_run=False, account_id="account-a"))

    assert result["acted"] == 1
    assert result["private_history_deletions"] == 1
    assert result["private_history_deletions_succeeded"] == 1
    assert FakeClient.block_requests == 1
    assert FakeClient.delete_requests == [(2, True)]
    record = teleshield.load_block_log(account_id="account-a")["blocks"][0]
    assert record["details"]["private_history_deletion"] == {
        "requested": True,
        "scope": "both",
        "succeeded": True,
    }
    assert teleshield.load_block_log(account_id="account-b")["blocks"] == []


def test_private_history_deletion_failure_keeps_block_result_and_is_logged(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "user_id": 1,
        "blocked_count": 0,
        "whitelist": {},
        "blacklist": {},
        "moderation_policy": {
            "delete_private_history_after_block": True,
            "delete_private_history_scope": "self",
        },
    })
    spammer = User(id=2, is_self=False, bot=False, first_name="Spam")

    class FakeClient:
        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_dialogs(self, limit):
            return [SimpleNamespace(entity=spammer)]

        async def get_messages(self, entity, limit):
            return [SimpleNamespace(
                date=datetime.now(timezone.utc),
                text="投資穩賺，立即加入",
                photo=None,
            )]

        async def delete_dialog(self, entity, revoke=False):
            raise RuntimeError("Telegram 不允許刪除")

        async def __call__(self, request):
            return SimpleNamespace(users=[])

    with patch.object(telethon, "TelegramClient", FakeClient):
        result = asyncio.run(teleshield.scan_history("private", dry_run=False))

    assert result["acted"] == 1
    assert result["errors"]
    record = teleshield.load_block_log()["blocks"][0]
    deletion = record["details"]["private_history_deletion"]
    assert deletion["succeeded"] is False
    assert deletion["scope"] == "self"
    assert "不允許刪除" in deletion["error"]


def test_telegram_notification_formats_and_sends_bot_api_message(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    captured = {}
    account_id = "account-owner"
    teleshield.create_account(account_id)
    teleshield.update_account_identity(
        account_id,
        SimpleNamespace(
            id=101,
            username="caryyu",
            first_name="Cary",
            last_name="Yu",
        ),
        root=tmp_path,
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok":true,"result":{"message_id":7}}'

    def fake_urlopen(request, timeout, context=None):
        captured["request"] = request
        captured["timeout"] = timeout
        captured["context"] = context
        return FakeResponse()

    monkeypatch.setattr(teleshield.urllib.request, "urlopen", fake_urlopen)

    result = teleshield.test_telegram_notification(
        "123456:ABC",
        "-1001234567890",
        account_id=account_id,
    )

    assert result == {"sent": True}
    assert captured["timeout"] == teleshield.TELEGRAM_BOT_API_TIMEOUT
    assert isinstance(captured["context"], teleshield.ssl.SSLContext)
    assert captured["request"].full_url.endswith("/bot123456:ABC/sendMessage")
    payload = teleshield.urllib.parse.parse_qs(captured["request"].data.decode("utf-8"))
    assert payload["chat_id"] == ["-1001234567890"]
    assert "帳號: Cary Yu (101)" in payload["text"][0]
    assert "Bot Token 與 Channel ID 已成功驗證。" in payload["text"][0]


def test_block_notification_uses_the_selected_account_identity(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    first_account = "account-first"
    second_account = "account-second"
    teleshield.create_account(first_account)
    teleshield.create_account(second_account)
    teleshield.update_account_identity(
        first_account,
        SimpleNamespace(id=101, username="caryyu", first_name="Cary", last_name="Yu"),
        root=tmp_path,
    )
    teleshield.update_account_identity(
        second_account,
        SimpleNamespace(id=202, username="other", first_name="Other", last_name="User"),
        root=tmp_path,
    )

    first_message = teleshield.build_telegram_block_notification(
        134037075,
        "Blocked User",
        "廣告內容",
        None,
        account_id=first_account,
    )
    second_message = teleshield.build_telegram_block_notification(
        134037075,
        "Blocked User",
        "廣告內容",
        None,
        account_id=second_account,
    )

    assert "帳號: Cary Yu (101)" in first_message
    assert "帳號: Other User (202)" in second_message
    assert "帳號: Other User (202)" not in first_message
    assert "帳號: Cary Yu (101)" not in second_message


def test_structured_block_notification_uses_analysis_reason_and_dynamic_ids():
    decision = teleshield.analyze_spam(
        "投資穩賺，立即加入",
        sender_context={"new_sender": True},
    )
    analysis = teleshield.build_block_analysis(
        decision,
        source="text",
        evidence=decision["text"],
        sender_context={"new_sender": True},
    )

    message = teleshield.build_telegram_block_notification(
        134037075,
        "Blocked User",
        analysis["reason"],
        None,
        analysis=analysis,
        account_cfg={"display_name": "Cary Yu", "user_id": 5668505643},
    )

    assert "帳號: Cary Yu (5668505643)" in message
    assert "封鎖名稱: Blocked User (134037075)" in message
    assert "封鎖原因：高信心垃圾訊息" in message
    assert "分類：投資" in message
    assert "意圖：獲利承諾、立即操作" in message
    assert "分數：13 / 4（垃圾訊息）" in message
    assert "來源：文字" in message
    assert "內容摘要：投資穩賺，立即加入" in message


def test_private_block_notification_includes_deletion_result(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    cfg = {
        "moderation_policy": {
            "delete_private_history_after_block": True,
            "delete_private_history_scope": "self",
            "telegram_notification": {
                "enabled": True,
                "bot_token": "123456:ABC",
                "channel_id": "-1001234567890",
            },
        }
    }
    spammer = User(id=42, is_self=False, bot=False, first_name="Spam")

    class FakeClient:
        async def delete_dialog(self, entity, revoke=False):
            assert entity is spammer
            assert revoke is False

        async def __call__(self, request):
            assert type(request).__name__ == "BlockRequest"
            return SimpleNamespace()

    with patch.object(teleshield, "send_telegram_bot_message", return_value={"ok": True}) as send:
        deletion = asyncio.run(
            teleshield.block_private_user(
                FakeClient(),
                spammer,
                "Spam User",
                "投資穩賺，立即加入",
                "private",
                cfg,
            )
        )

    assert deletion == {"requested": True, "scope": "self", "succeeded": True}
    send.assert_called_once()
    message = send.call_args.args[2]
    assert "封鎖名稱: Spam User (42)" in message
    assert "封鎖原因：投資穩賺，立即加入" in message
    assert "內容摘要：投資穩賺，立即加入" in message
    assert "是否開啟刪除對話: 是" in message
    assert "是否已經刪除對話: 是" in message
    record = teleshield.load_block_log()["blocks"][0]
    assert record["details"]["telegram_notification"] == {"enabled": True, "sent": True}


def test_telegram_notification_failure_does_not_cancel_private_block(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    cfg = {
        "moderation_policy": {
            "delete_private_history_after_block": False,
            "delete_private_history_scope": "self",
            "telegram_notification": {
                "enabled": True,
                "bot_token": "123456:ABC",
                "channel_id": "-1001234567890",
            },
        }
    }
    spammer = User(id=43, is_self=False, bot=False, first_name="Spam")

    class FakeClient:
        async def __call__(self, request):
            assert type(request).__name__ == "BlockRequest"
            return SimpleNamespace()

    with patch.object(
        teleshield,
        "send_telegram_bot_message",
        side_effect=RuntimeError("頻道沒有發送權限"),
    ):
        deletion = asyncio.run(
            teleshield.block_private_user(
                FakeClient(),
                spammer,
                "Spam User",
                "廣告內容",
                "private",
                cfg,
            )
        )

    assert deletion is None
    record = teleshield.load_block_log()["blocks"][0]
    assert record["user_id"] == 43
    assert record["details"]["telegram_notification"] == {
        "enabled": True,
        "sent": False,
        "error": "頻道沒有發送權限",
    }


def test_scan_history_honours_cancellation_before_history_fetch(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({"api_id": 1234, "api_hash": "[REDACTED]"})
    cancel_event = threading.Event()
    cancel_event.set()

    class FakeClient:
        history_requested = False

        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def __call__(self, request):
            return SimpleNamespace(users=[])

        async def get_dialogs(self, limit):
            type(self).history_requested = True
            return []

    with patch.object(telethon, "TelegramClient", FakeClient):
        result = asyncio.run(
            teleshield.scan_history("private", dry_run=True, cancel_event=cancel_event)
        )

    assert result["cancelled"] is True
    assert FakeClient.history_requested is False


def test_learn_text_returns_changes_and_persists_rules(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({"learned_patterns": {"keywords": [], "patterns": []}})

    result = teleshield.learn_text("加微信 spam_account 投資穩賺")

    assert result["added_keywords"]
    assert result["added_patterns"]
    cfg = teleshield.load_config()
    assert "投資穩賺" in cfg["learned_patterns"]["keywords"]
    learned_file = teleshield._legacy_account_store().learned_patterns_file
    assert json.loads(learned_file.read_text(encoding="utf-8")) == cfg["learned_patterns"]
    assert teleshield.is_spam("這是投資穩賺廣告", cfg)


def test_simplified_text_is_normalized_for_rules_and_learning(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    cfg = {"learned_patterns": {"keywords": ["投資"], "patterns": []}}

    assert teleshield.normalize_traditional("投资稳赚") == "投資穩賺"
    assert teleshield.is_spam("這是投资廣告", cfg) is True

    result = teleshield.learn_text("加微信 投资稳赚")

    assert result["normalized_text"] == "加微信 投資穩賺"
    learned = teleshield.load_config()["learned_patterns"]["keywords"]
    assert "投資穩賺" in learned


@pytest.mark.parametrize(
    "ordinary_text",
    [
        "我想出去買菜",
        "彩色筆很好用",
        "請出示文件",
        "我賣出了舊手機",
        "大家來討論博物館",
    ],
)
def test_common_chinese_text_does_not_match_single_character_spam_rules(ordinary_text):
    decision = teleshield.analyze_spam(ordinary_text)
    assert decision["should_block"] is False
    assert decision["categories"] == []
    assert decision["intents"] == []


@pytest.mark.parametrize(
    "variant",
    [
        "投資群組",
        "偷資群組",
        "頭姿",
        "頭茲",
        "頭資",
        "投 資 群 組",
        "投\u200b資群組",
        "tou zi qun zu",
        "ㄊㄡˊ ㄗ ㄑㄩㄣˊ ㄗㄨˇ",
        "ｔｏｕ　ｚｉ　ｑｕｎ　ｚｕ",
    ],
)
def test_spam_rules_match_traditional_simplified_homophone_and_phonetic_variants(variant):
    decision = teleshield.analyze_spam(variant)
    assert decision["should_block"] is True
    assert "investment" in decision["categories"]


def test_first_message_without_url_uses_content_and_sender_signals():
    decision = teleshield.analyze_spam(
        "保證獲利，私訊我加 LINE",
        sender_context={"new_sender": True},
    )
    assert decision["should_block"] is True
    assert decision["score"] >= decision["threshold"]
    assert {"guarantee", "contact"}.issubset(decision["intents"])


def test_contact_regex_does_not_join_unrelated_ocr_english_words():
    recognized = (
        "編輯圖像\n取消\nMAKE A SPLASH!\nThe Waterproof Ball\n"
        "Made for Summer\nPool days just got more exciting.\n"
        "Float it. Fetch it. Splash all day.\n"
        "SUMMER IS BETTER WITH PAWS & PLAY"
    )

    decision = teleshield.analyze_spam(
        recognized,
        sender_context={"new_sender": True},
    )

    assert decision["should_block"] is False
    assert decision["score"] == 1
    assert decision["intents"] == []
    assert decision["obfuscation"] == []


@pytest.mark.parametrize("recognized", ["tg @abc123", "telegram: abc123", "@abc1234"])
def test_explicit_contact_handle_remains_high_confidence_for_new_sender(recognized):
    decision = teleshield.analyze_spam(
        recognized,
        sender_context={"new_sender": True},
    )

    assert decision["should_block"] is True
    assert decision["score"] >= decision["threshold"]
    assert "contact" in decision["intents"]
    assert any(
        rule.get("confidence") == "contact_handle"
        for rule in decision["matched_rules"]
    )


@pytest.mark.parametrize(
    "ordinary_text",
    [
        "CeraVe Blemish Control Cleanser",
        "Avoid contact with eyes",
        "productUSDTlabel",
        "sNFTlabel",
        "Consumer Advice Line 1300 659 359",
    ],
)
def test_builtin_english_rules_do_not_match_inside_words_or_phone_lines(ordinary_text):
    decision = teleshield.analyze_spam(
        ordinary_text,
        sender_context={"new_sender": True},
        protect_ocr_line_breaks=True,
    )

    assert decision["should_block"] is False
    assert decision["categories"] == []
    assert decision["intents"] == []


@pytest.mark.parametrize("recognized", ["AV", "A V", "A.V.", "ＡＶ", "AV影片"])
def test_builtin_english_short_tokens_keep_supported_variants(recognized):
    decision = teleshield.analyze_spam(
        recognized,
        sender_context={"new_sender": True},
    )

    assert decision["should_block"] is True
    assert "adult" in decision["categories"]


@pytest.mark.parametrize("recognized", ["USDT", "NFT", "free bitcoin"])
def test_builtin_english_crypto_tokens_keep_supported_variants(recognized):
    decision = teleshield.analyze_spam(recognized)

    assert decision["should_block"] is True
    assert "crypto" in decision["categories"]


def test_ocr_line_break_does_not_join_unrelated_two_character_homophones():
    decision = teleshield.analyze_spam(
        "太陽下\n主意事項",
        sender_context={"new_sender": True},
        protect_ocr_line_breaks=True,
    )

    assert decision["should_block"] is False
    assert "gambling" not in decision["categories"]
    assert decision["intents"] == []


def test_ocr_line_guard_keeps_same_line_mixed_cjk_obfuscation():
    decision = teleshield.analyze_spam(
        "偷price姿321",
        protect_ocr_line_breaks=True,
    )

    assert decision["should_block"] is True
    assert "investment" in decision["categories"]


def test_ocr_line_guard_does_not_override_explicit_learned_rules():
    cfg = {
        "learned_patterns": {
            "keywords": ["下注"],
            "patterns": [],
        }
    }

    decision = teleshield.analyze_spam(
        "太陽下\n主意事項",
        cfg,
        protect_ocr_line_breaks=True,
    )

    assert decision["should_block"] is True
    assert decision["learned_matches"]


def test_ocr_content_applies_line_guard_before_the_shared_decision(monkeypatch):
    message = SimpleNamespace(text="", photo=True)

    async def fake_check_photo(client, msg):
        assert msg is message
        return "太陽下\n主意事項"

    monkeypatch.setattr(teleshield, "check_photo", fake_check_photo)
    result = asyncio.run(
        teleshield.analyze_message_content(
            object(),
            message,
            sender_context={"new_sender": True},
        )
    )

    assert result["source"] == "text"
    assert result["decision"]["should_block"] is False
    assert result["decision"]["categories"] == []


def test_block_analysis_uses_phishing_score_and_thirty_character_excerpt():
    recognized = (
        "功能狀態說明\n我們監測到，您的帳號近期有一些未預期的活躍波動。\n"
        "為了保障安全，系統暫時限制了部分功能的使用。\n請盡快點擊下方按鈕，完成問題處理。\n"
        "申請恢復帳號\n立即申請恢復"
    )
    decision = teleshield.analyze_spam(recognized)
    evidence = "帳號異常，恢復帳號，輸入驗證碼，請立即處理。這是安全通知的完整內容"
    analysis = teleshield.build_block_analysis(
        decision,
        source="ocr",
        evidence=evidence,
    )

    assert decision["score"] == 0
    assert decision["phishing_score"] == 8
    assert analysis["score"] == 8
    assert analysis["score_type_label"] == "釣魚風險"
    assert len(analysis["content_excerpt"]) == 30
    assert analysis["content_excerpt"] == evidence[:30]

    message = teleshield.build_telegram_block_notification(
        134037075,
        "Blocked User",
        analysis["reason"],
        None,
        analysis=analysis,
        account_cfg={"display_name": "Owner", "user_id": 1},
    )
    assert "分數：8 / 4（釣魚風險）" in message
    assert f"內容摘要：{evidence[:30]}" in message


def test_get_block_records_repairs_legacy_phishing_score(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_block_log({
        "blocks": [{
            "time": "2026-08-04T04:29:01+00:00",
            "source": "private",
            "user_id": 134037075,
            "name": "Blocked User",
            "reason": "疑似帳號釣魚",
            "details": {
                "analysis": {
                    "reason_code": "account_takeover",
                    "score": 0,
                    "threshold": 4,
                    "phishing_signals": [
                        "account_state",
                        "recovery",
                        "credential_action",
                        "urgency",
                    ],
                    "content_excerpt": "an official",
                }
            },
        }]
    })

    record = teleshield.get_block_records()[0]
    analysis = record["details"]["analysis"]
    assert analysis["score"] == 8
    assert analysis["score_type_label"] == "釣魚風險"
    assert analysis["threshold"] == 4


def test_block_analysis_keeps_reason_separate_from_excerpt():
    decision = teleshield.analyze_spam(
        "投資穩賺，立即加入",
        sender_context={"new_sender": True},
    )
    analysis = teleshield.build_block_analysis(
        decision,
        source="text",
        evidence=decision["text"],
        sender_context={"new_sender": True},
    )

    assert analysis["reason"] == "高信心垃圾訊息"
    assert analysis["category_labels"] == ["投資"]
    assert "獲利承諾" in analysis["intent_labels"]
    assert "立即操作" in analysis["intent_labels"]
    assert len(analysis["content_excerpt"]) <= 30


@pytest.mark.parametrize(
    "canonical",
    [
        "投資",
        "賭博",
        "色情",
        "詐騙",
        "兼職",
        "比特幣",
        "加微信",
        "立即加入",
        "保證獲利",
        "付款",
        "轉帳",
    ],
)
def test_default_high_risk_rules_match_pinyin_and_zhuyin(canonical):
    forms = teleshield.build_message_forms(canonical)

    pinyin_decision = teleshield.analyze_spam(forms["pinyin"])
    zhuyin_decision = teleshield.analyze_spam(forms["zhuyin"])

    assert pinyin_decision["should_block"] is True
    assert zhuyin_decision["should_block"] is True


def test_phonetic_matching_does_not_create_partial_syllable_category_hits():
    decision = teleshield.analyze_spam("詐騙")

    assert decision["should_block"] is True
    assert decision["categories"] == ["fraud"]
    assert "adult" not in decision["categories"]


def test_message_forms_keep_tones_and_tone_less_phonetic_channels():
    forms = teleshield.build_message_forms("投資群組")

    assert forms["pinyin_tone"] == "tou2 zi1 qun2 zu3"
    assert forms["pinyin"] == "tou zi qun zu"
    assert forms["zhuyin_tone"] == "ㄊㄡˊ ㄗ ㄑㄩㄣˊ ㄗㄨˇ"
    assert forms["zhuyin"] == "ㄊㄡ ㄗ ㄑㄩㄣ ㄗㄨ"


def test_phonetic_normalization_does_not_block_ambiguous_homophones():
    decision = teleshield.analyze_spam("請出示文件")
    assert decision["should_block"] is False
    assert decision["categories"] == []


@pytest.mark.parametrize("ordinary_text", ["專長", "專長給我", "工程人生"])
def test_natural_two_character_phonetic_fragments_do_not_block(ordinary_text):
    decision = teleshield.analyze_spam(
        ordinary_text,
        sender_context={"new_sender": True},
    )

    assert decision["should_block"] is False
    assert decision["categories"] == []
    assert decision["intents"] == []


def test_tone_aware_short_homophone_still_blocks_payment_obfuscation():
    decision = teleshield.analyze_spam("転丈給我")

    assert decision["should_block"] is True
    assert "payment" in decision["intents"]
    assert any(
        rule.get("confidence") == "pinyin_tone"
        for rule in decision["matched_rules"]
    )


def test_long_ocr_text_does_not_promote_natural_phonetic_fragments():
    recognized = "Cary 的超爆肝工程人生\n監工犬 Cody\n專長：盯著你不放"

    decision = teleshield.analyze_spam(recognized)

    assert decision["should_block"] is False
    assert decision["categories"] == []
    assert decision["intents"] == []


@pytest.mark.parametrize(
    "variant, skeleton, category",
    [
        ("偷price姿321", "偷姿", "investment"),
        ("偷12g姿pri22", "偷姿", "investment"),
        ("詐asg片333", "詐片", "fraud"),
        ("堵888伯ccc", "堵伯", "gambling"),
        ("頭🤑3茲price", "頭茲", "investment"),
    ],
)
def test_cjk_skeleton_matches_mixed_english_numbers_and_emoji(
    variant, skeleton, category
):
    forms = teleshield.build_message_forms(variant)
    decision = teleshield.analyze_spam(variant)

    assert forms["cjk_skeleton"] == skeleton
    assert forms["cjk_skeleton_pinyin"]
    assert forms["cjk_skeleton_zhuyin"]
    assert decision["should_block"] is True
    assert category in decision["categories"]
    assert "alphanumeric_between_chinese" in decision["obfuscation"]


def test_cjk_skeleton_is_the_match_source_when_alphanumeric_breaks_phonetics():
    decision = teleshield.analyze_spam("偷price姿321")

    assert any(
        rule.get("channel", "").startswith("cjk_skeleton_")
        for rule in decision["matched_rules"]
    )


def test_cjk_skeleton_keeps_generic_homophones_ambiguous():
    decision = teleshield.analyze_spam("文price件")

    assert decision["should_block"] is False
    assert decision["categories"] == []
    assert decision["intents"] == []


@pytest.mark.parametrize(
    "learned_keyword, variant",
    [
        ("投資", "偷price姿321"),
        ("詐騙", "詐asg片333"),
    ],
)
def test_learned_chinese_keyword_uses_cjk_skeleton(learned_keyword, variant):
    cfg = {"learned_patterns": {"keywords": [learned_keyword], "patterns": []}}

    decision = teleshield.analyze_spam(variant, cfg)

    assert decision["should_block"] is True
    assert any(
        match.get("kind") == "keywords"
        and match.get("channel", "").startswith("cjk_skeleton_")
        for match in decision["learned_matches"]
    )


def test_learned_literal_pattern_uses_cjk_skeleton():
    cfg = {
        "learned_patterns": {
            "keywords": [],
            "patterns": [r"投資"],
        }
    }

    decision = teleshield.analyze_spam("偷12g姿pri22", cfg)

    assert decision["should_block"] is True
    assert any(
        match.get("kind") == "patterns"
        and match.get("channel", "").startswith("cjk_skeleton_")
        for match in decision["learned_matches"]
    )


def test_learning_mixed_cjk_sample_stores_a_reusable_skeleton(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({"learned_patterns": {"keywords": [], "patterns": []}})

    result = teleshield.learn_text("藍price星321")

    assert "藍星" in result["added_keywords"]
    assert teleshield.is_spam("蘭foo星999", teleshield.load_config()) is True


def test_cjk_skeleton_does_not_change_english_rule_forms():
    forms = teleshield.build_message_forms("free bitcoin")
    decision = teleshield.analyze_spam("free bitcoin")

    assert "cjk_skeleton" not in forms
    assert decision["should_block"] is True
    assert "crypto" in decision["categories"]


def test_learned_keyword_uses_the_same_phonetic_forms(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    cfg = {"learned_patterns": {"keywords": ["投資群組"], "patterns": []}}

    assert teleshield.is_spam("偷資群組", cfg) is True
    assert teleshield.is_spam("tou zi qun zu", cfg) is True
    assert teleshield.is_spam("ㄊㄡˊ ㄗ ㄑㄩㄣˊ ㄗㄨˇ", cfg) is True


def test_learned_single_character_rule_is_not_dropped():
    cfg = {"learned_patterns": {"keywords": ["賭"], "patterns": []}}

    assert teleshield.is_spam("賭", cfg) is True


def test_user_learned_keyword_generates_runtime_homophone_match(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({"learned_patterns": {"keywords": [], "patterns": []}})

    result = teleshield.learn_text("藍星財富")

    assert "藍星財富" in result["added_keywords"]
    assert teleshield.is_spam("蘭星財富", teleshield.load_config()) is True


def test_user_learned_literal_pattern_uses_phonetic_forms():
    cfg = {
        "learned_patterns": {
            "keywords": [],
            "patterns": [r"藍星財富"],
        }
    }

    decision = teleshield.analyze_spam("蘭星財富", cfg)

    assert decision["should_block"] is True
    assert decision["learned_matches"][0]["channel"] == "pinyin"


def test_ocr_content_uses_the_same_homophone_pipeline(monkeypatch):
    message = SimpleNamespace(text="", photo=True)

    async def fake_check_photo(client, msg):
        assert msg is message
        return "頭姿\n頭茲\n頭資"

    monkeypatch.setattr(teleshield, "check_photo", fake_check_photo)
    result = asyncio.run(
        teleshield.analyze_message_content(
            object(),
            message,
            sender_context={"new_sender": True},
        )
    )

    assert result["source"] == "ocr"
    assert result["text"] == "[OCR] 頭姿\n頭茲\n頭資"
    assert result["decision"]["should_block"] is True
    assert any(
        rule.get("channel") == "pinyin"
        for rule in result["decision"]["matched_rules"]
    )


@pytest.mark.parametrize(
    "recognized",
    [
        "功能狀態說明\n我們監測到，您的帳號近期有一些未預期的活躍波動。\n"
        "為了保障安全，系統暫時限制了部分功能的使用。\n請盡快點擊下方按鈕，完成問題處理。\n"
        "申請恢復帳號\n立即申請恢復",
        "功能状态说明\n我们监测到，您的账号近期有一些未预期的活跃波动。\n"
        "为了保障安全，系统暂时限制了部分功能的使用。\n请尽快点击下方按钮，完成问题处理。\n"
        "申请恢复账号\n立即申请恢复",
    ],
)
def test_account_takeover_phishing_matches_traditional_and_simplified(recognized):
    decision = teleshield.analyze_spam(recognized)

    assert decision["should_block"] is True
    assert decision["phishing_should_block"] is True
    assert decision["phishing_group_count"] >= 2
    assert {
        "account_state",
        "recovery",
        "credential_action",
        "urgency",
    }.issubset(decision["phishing_signals"])
    assert "account_takeover" in decision["categories"]


@pytest.mark.parametrize(
    "recognized",
    [
        "Telegram Security Alert: Your account has been restricted. "
        "Restore your account by scanning the QR code within 24 hours.",
        "Your account has been restricted. Click here to restore access "
        "and enter the verification code.",
        "Help me vote for my friend in the contest and scan the QR code.",
    ],
)
def test_account_takeover_phishing_matches_english_messages(recognized):
    decision = teleshield.analyze_spam(recognized)

    assert decision["should_block"] is True
    assert decision["phishing_should_block"] is True
    assert "account_takeover" in decision["categories"]


@pytest.mark.parametrize("channel", ["pinyin_tone", "pinyin", "pinyin_loose", "zhuyin_tone", "zhuyin"])
def test_phishing_rules_keep_chinese_phonetic_channels(channel):
    canonical = "帳號異常，申請恢復帳號"
    phonetic_text = teleshield.build_message_forms(canonical)[channel]

    decision = teleshield.analyze_spam(phonetic_text)

    assert decision["should_block"] is True
    assert {"account_state", "recovery"}.issubset(decision["phishing_signals"])


@pytest.mark.parametrize(
    "ordinary_text",
    [
        "Telegram support",
        "Please vote for me in a contest",
        "Please verify your account",
    ],
)
def test_single_phishing_signal_does_not_block_by_itself(ordinary_text):
    decision = teleshield.analyze_spam(
        ordinary_text,
        sender_context={"new_sender": True},
    )

    assert decision["phishing_group_count"] == 1
    assert decision["phishing_should_block"] is False
    assert decision["should_block"] is False


def test_phishing_cjk_skeleton_matches_inserted_english_and_numbers():
    decision = teleshield.analyze_spam("功能price受限，申請foo恢復帳號")

    assert decision["should_block"] is True
    assert decision["phishing_should_block"] is True
    assert {"account_state", "recovery"}.issubset(decision["phishing_signals"])
    assert any(
        rule.get("group") == "phishing_recovery"
        and rule.get("channel", "").startswith("cjk_skeleton_")
        for rule in decision["matched_rules"]
    )


def test_learned_phishing_rule_keeps_simplified_and_mixed_matching():
    cfg = {
        "learned_patterns": {
            "keywords": ["恢復帳號"],
            "patterns": [],
        }
    }

    decision = teleshield.analyze_spam("恢复foo账号", cfg)

    assert decision["should_block"] is True
    assert decision["learned_matches"]
    assert decision["learned_matches"][0]["kind"] == "keywords"

    english_cfg = {
        "learned_patterns": {
            "keywords": ["restore your account"],
            "patterns": [],
        }
    }
    english_decision = teleshield.analyze_spam("restore-your-account", english_cfg)

    assert english_decision["should_block"] is True
    assert english_decision["learned_matches"]


def test_ocr_phishing_text_uses_the_same_pipeline(monkeypatch):
    message = SimpleNamespace(text="", photo=True)

    async def fake_check_photo(client, msg):
        assert msg is message
        return "检测到异常活动，您的账号已被限制。申请恢复账号，立即扫描 QR Code。"

    monkeypatch.setattr(teleshield, "check_photo", fake_check_photo)
    result = asyncio.run(
        teleshield.analyze_message_content(
            object(),
            message,
            sender_context={"new_sender": True},
        )
    )

    assert result["source"] == "ocr"
    assert result["decision"]["should_block"] is True
    assert result["decision"]["phishing_should_block"] is True


@pytest.mark.parametrize(
    "recognized",
    [
        "少年董雹樂城\n簡單刺激\n又好玩\n推筒子\nBAR\n嵩倍體驗",
        "線上娛樂城 真人百家樂 返水優惠",
        "球版 現金版 高賠率，立即下注",
        "老虎機 保證獲利，彩金快速入帳",
    ],
)
def test_gambling_ocr_terms_match_spam_rules(recognized):
    assert teleshield.is_spam(recognized) is True


def test_learned_pattern_listing_and_removal(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({
        "learned_patterns": {"keywords": ["投資"], "patterns": ["spam\\d+"]}
    })

    assert teleshield.get_learned_patterns() == {
        "keywords": ["投資"],
        "patterns": ["spam\\d+"],
    }
    assert teleshield.remove_learned_pattern("keywords", "投資") is True
    assert teleshield.remove_learned_pattern("patterns", "missing") is False
    assert teleshield.get_learned_patterns() == {"keywords": [], "patterns": ["spam\\d+"]}


def test_sidecar_learned_patterns_are_used_by_spam_enforcement(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.save_config(
        {"learned_patterns": {"keywords": [], "patterns": []}},
        account_id="account-a",
    )
    teleshield.save_learned_patterns(
        {"keywords": ["side-only"], "patterns": []},
        account_id="account-a",
    )

    assert teleshield.is_spam("這是 side-only 廣告", teleshield.load_config("account-a")) is True


def test_spam_matching_can_use_explicit_account_scope(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    teleshield.save_config({"learned_patterns": {"keywords": [], "patterns": []}}, "account-a")
    teleshield.save_config({"learned_patterns": {"keywords": [], "patterns": []}}, "account-b")
    teleshield.save_learned_patterns(
        {"keywords": ["account-a-only"], "patterns": []},
        account_id="account-a",
    )
    teleshield.set_active_account("account-b")

    account_a_cfg = teleshield.load_config("account-a")
    assert teleshield.is_spam(
        "這是 account-a-only 廣告",
        account_a_cfg,
        account_id="account-a",
    ) is True
    assert teleshield.is_spam("這是 account-a-only 廣告", account_a_cfg) is False


def test_build_report_returns_structured_summary_and_trend(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    now = datetime(2026, 1, 8, 12, tzinfo=timezone.utc)
    teleshield.save_block_log({
        "blocks": [
            {
                "user_id": 1,
                "name": "Private Spam",
                "reason": "投資穩賺",
                "source": "private",
                "time": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "user_id": 2,
                "name": "Group Spam",
                "reason": "加微信",
                "source": "group",
                "time": (now - timedelta(days=2)).isoformat(),
            },
            {
                "user_id": 3,
                "name": "Old Spam",
                "reason": "過期",
                "source": "private",
                "time": (now - timedelta(days=10)).isoformat(),
            },
        ]
    })

    result = teleshield.build_report("week", now=now)

    assert result["period"] == "week"
    assert result["total"] == 2
    assert result["by_source"] == {"private": 1, "group": 1}
    assert result["trend"] == {"2026-01-06": 1, "2026-01-08": 1}
    assert len(result["records"]) == 2


def test_list_entries_filter_and_update_scan_settings(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.save_config({
        "scan_settings": {
            "group_dialog_limit": 12,
            "group_message_limit": 40,
            "group_days": 2,
        },
        "managed_groups": [{"id": "-100", "title": "Legacy group", "enabled": True}],
    })
    teleshield.upsert_list_entry("whitelist", "123", "trusted_user", "manual")
    teleshield.upsert_list_entry("blacklist", "456", "bad_user", "spam")

    assert teleshield.list_entries("whitelist", "trusted") == [
        {"user_id": "123", "username": "trusted_user", "added": teleshield.load_config()["whitelist"]["123"]["added"], "reason": "manual"}
    ]
    teleshield.remove_list_entry("blacklist", "456")
    assert teleshield.list_entries("blacklist") == []

    settings = teleshield.update_scan_settings({
        "private_dialog_limit": 9999,
        "private_message_limit": 0,
        "private_days": 30,
    })
    assert settings == {
        "private_dialog_limit": 100,
        "private_message_limit": 1,
        "private_days": 30,
    }
    assert teleshield.get_scan_settings()["private_dialog_limit"] == 100
    assert teleshield.load_config()["scan_settings"] == {
        "group_dialog_limit": 12,
        "group_message_limit": 40,
        "group_days": 2,
        "private_dialog_limit": 100,
        "private_message_limit": 1,
        "private_days": 30,
    }
    assert teleshield.load_config()["managed_groups"] == [
        {"id": "-100", "title": "Legacy group", "enabled": True}
    ]


def test_list_entries_can_export_and_import_json_and_csv(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.upsert_list_entry("whitelist", "123", "trusted_user", "manual")
    json_path = tmp_path / "whitelist.json"
    csv_path = tmp_path / "whitelist.csv"

    assert teleshield.export_list_entries(str(json_path), "whitelist") == 1
    assert teleshield.export_list_entries(str(csv_path), "whitelist") == 1

    teleshield.clear_local_session()
    assert teleshield.import_list_entries(str(json_path), "whitelist", replace=True) == 1
    assert teleshield.list_entries("whitelist")[0]["username"] == "trusted_user"


def test_clear_local_session_removes_auth_identity_without_logging_secrets(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.SESSION_FILE.write_bytes(b"fake-session")
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "phone": "+100****0000",
        "user_id": 42,
        "username": "test_user",
        "blocked_count": 3,
        "learned_patterns": {"keywords": [], "patterns": []},
    })

    teleshield.clear_local_session(remove_credentials=True)

    assert not teleshield.SESSION_FILE.exists()
    cfg = teleshield.load_config()
    assert "api_id" not in cfg
    assert "api_hash" not in cfg
    assert "phone" not in cfg
    assert "user_id" not in cfg
    assert cfg["blocked_count"] == 3


def test_ocr_status_uses_configured_vision_helper_without_logging_path(monkeypatch, tmp_path):
    executable = tmp_path / "VisionOCR"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setenv("TELESHIELD_VISION_OCR_PATH", str(executable))

    status = teleshield.get_ocr_status()

    assert status["available"] is True
    assert status["bundled"] is False
    assert status["languages"] == ["zh-Hant", "zh-Hans", "en"]


def test_ocr_image_uses_configured_vision_helper(monkeypatch, tmp_path):
    executable = tmp_path / "VisionOCR"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json\n"
        "print(json.dumps({'text': '投资 稳赚'}, ensure_ascii=False))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("TELESHIELD_VISION_OCR_PATH", str(executable))

    recognized = teleshield.ocr_image(str(tmp_path / "message.jpg"))
    assert recognized == "投資 穩賺"
    assert teleshield.is_spam(recognized) is True


def test_logout_account_calls_telegram_and_clears_local_identity(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.SESSION_FILE.write_bytes(b"fake-session")
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "user_id": 42,
        "username": "test_user",
    })

    class FakeClient:
        logged_out = False

        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def log_out(self):
            type(self).logged_out = True

    with patch.object(telethon, "TelegramClient", FakeClient):
        asyncio.run(teleshield.logout_account(remove_credentials=True))

    assert FakeClient.logged_out is True
    assert not teleshield.SESSION_FILE.exists()
    assert "user_id" not in teleshield.load_config()
    assert "api_hash" not in teleshield.load_config()


def test_create_account_initializes_isolated_json_files(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    store = teleshield.account_store("account-a")

    assert store.root.is_dir()
    assert json.loads(store.config_file.read_text(encoding="utf-8")) == {}
    assert json.loads(store.block_log.read_text(encoding="utf-8")) == {"blocks": []}
    assert json.loads(store.learned_patterns_file.read_text(encoding="utf-8")) == {
        "keywords": [],
        "patterns": [],
    }
    for path in (store.config_file, store.block_log, store.learned_patterns_file):
        assert path.stat().st_mode & 0o777 == 0o600


def test_account_storage_isolated_for_config_session_and_logs(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)

    account_a = teleshield.create_account("account-a")
    account_b = teleshield.create_account("account-b")
    teleshield.save_config({"user_id": 101, "username": "alpha"}, account_id="account-a")
    teleshield.save_config({"user_id": 202, "username": "beta"}, account_id="account-b")
    teleshield.save_block_log({"blocks": [{"user_id": 101, "source": "private"}]}, account_id="account-a")
    teleshield.save_block_log({"blocks": [{"user_id": 202, "source": "group"}]}, account_id="account-b")

    assert account_a["id"] == "account-a"
    assert account_b["id"] == "account-b"
    assert teleshield.load_config(account_id="account-a")["user_id"] == 101
    assert teleshield.load_config(account_id="account-b")["user_id"] == 202
    assert teleshield.load_block_log(account_id="account-a")["blocks"][0]["user_id"] == 101
    assert teleshield.load_block_log(account_id="account-b")["blocks"][0]["user_id"] == 202
    assert teleshield.account_store("account-a").session_file != teleshield.account_store("account-b").session_file
    assert teleshield.account_store("account-a").config_file.parent != teleshield.account_store("account-b").config_file.parent


def test_active_account_context_selects_only_one_default_without_cross_contamination(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    teleshield.save_config({"user_id": 101}, account_id="account-a")
    teleshield.save_config({"user_id": 202}, account_id="account-b")

    teleshield.set_active_account("account-b")
    assert teleshield.get_active_account_id() == "account-b"
    assert teleshield.load_config()["user_id"] == 202

    with teleshield.account_context("account-a"):
        assert teleshield.load_config()["user_id"] == 101
        teleshield.save_config({"user_id": 111})

    assert teleshield.load_config(account_id="account-a")["user_id"] == 111
    assert teleshield.load_config(account_id="account-b")["user_id"] == 202


def test_legacy_single_account_is_migrated_to_isolated_account_store(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.SESSION_FILE.write_bytes(b"legacy-session")
    teleshield.save_config({
        "api_id": 1234,
        "api_hash": "[REDACTED]",
        "phone": "+100****0000",
        "user_id": 42,
        "username": "legacy_user",
    })
    teleshield.save_block_log({"blocks": [{"user_id": 7, "source": "private"}]})

    records = teleshield.ensure_account_registry()

    assert len(records) == 1
    account_id = records[0]["id"]
    assert records[0]["user_id"] == 42
    assert teleshield.get_active_account_id() == account_id
    store = teleshield.account_store(account_id)
    assert store.session_file.read_bytes() == b"legacy-session"
    assert teleshield.load_config()["username"] == "legacy_user"
    assert teleshield.load_block_log()["blocks"][0]["user_id"] == 7
    assert not teleshield.SESSION_FILE.exists()
    assert not teleshield.CONFIG_FILE.exists()


def test_authenticate_uses_the_selected_account_session_path(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    session_paths = []

    class FakeClient:
        def __init__(self, session, *args):
            session_paths.append(session)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return False

        async def send_code_request(self, phone):
            return SimpleNamespace(type=SimpleNamespace())

        async def sign_in(self, **kwargs):
            pass

        async def get_me(self):
            return SimpleNamespace(id=len(session_paths), username="account_user", first_name="Account")

    with patch.object(telethon, "TelegramClient", FakeClient):
        for account_id in ("account-a", "account-b"):
            asyncio.run(
                teleshield.authenticate(
                    "1234",
                    "[REDACTED]",
                    "+100****0000",
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    account_id=account_id,
                )
            )

    assert len(session_paths) == 2
    assert session_paths[0] != session_paths[1]
    assert str(teleshield.account_store("account-a").session_file) == session_paths[0]
    assert str(teleshield.account_store("account-b").session_file) == session_paths[1]


def test_authenticate_rejects_duplicate_identity_without_overwriting_existing_account(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    teleshield.save_config({"user_id": 101, "username": "account-a"}, account_id="account-a")
    teleshield.save_config({"user_id": 202, "username": "account-b"}, account_id="account-b")
    teleshield.update_account_identity(
        "account-a",
        SimpleNamespace(id=101, username="account-a", first_name="A"),
    )
    teleshield.update_account_identity(
        "account-b",
        SimpleNamespace(id=202, username="account-b", first_name="B"),
    )
    original_session = teleshield.account_store("account-a").session_file
    original_session.write_bytes(b"original-account-a-session")

    class FakeClient:
        def __init__(self, session, *args):
            self.session = Path(session)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return False

        async def send_code_request(self, phone):
            return SimpleNamespace(type=SimpleNamespace())

        async def sign_in(self, **kwargs):
            self.session.write_bytes(b"duplicate-account-b-session")

        async def get_me(self):
            return SimpleNamespace(id=202, username="account-b", first_name="B")

    with patch.object(telethon, "TelegramClient", FakeClient):
        with pytest.raises(ValueError, match="已經存在"):
            asyncio.run(
                teleshield.authenticate(
                    "1234",
                    "[REDACTED]",
                    "+100****0000",
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    account_id="account-a",
                )
            )

    assert teleshield.load_config(account_id="account-a")["user_id"] == 101
    assert teleshield.get_account("account-a")["user_id"] == 101
    assert original_session.read_bytes() == b"original-account-a-session"


def test_fresh_duplicate_identity_rejection_removes_new_session_and_identity_config(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    teleshield.update_account_identity(
        "account-a",
        SimpleNamespace(id=101, username="account-a", first_name="A"),
    )

    class FakeClient:
        def __init__(self, session, *args):
            self.session = Path(session)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return False

        async def send_code_request(self, phone):
            return SimpleNamespace(type=SimpleNamespace())

        async def sign_in(self, **kwargs):
            self.session.write_bytes(b"new-duplicate-session")

        async def get_me(self):
            return SimpleNamespace(id=101, username="account-a", first_name="A")

    with patch.object(telethon, "TelegramClient", FakeClient):
        with pytest.raises(ValueError, match="已經存在"):
            asyncio.run(
                teleshield.authenticate(
                    "1234",
                    "[REDACTED]",
                    "+100****0000",
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    account_id="account-b",
                )
            )

    assert not teleshield.account_store("account-b").session_file.exists()
    assert teleshield.load_config("account-b").get("user_id") is None
    assert teleshield.get_account("account-b").get("user_id") is None


def test_create_account_rejects_duplicate_metadata_identity(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.update_account_identity(
        "account-a",
        SimpleNamespace(id=101, username="account-a", first_name="A"),
    )

    with pytest.raises(ValueError, match="已經存在"):
        teleshield.create_account("account-b", metadata={"user_id": 101})

    assert [record["id"] for record in teleshield.list_accounts()] == ["account-a"]


def test_logout_one_account_does_not_touch_another_account(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    for account_id, user_id in (("account-a", 101), ("account-b", 202)):
        teleshield.save_config({
            "api_id": 1234,
            "api_hash": "[REDACTED]",
            "phone": "+100****0000",
            "user_id": user_id,
            "username": account_id,
        }, account_id=account_id)
        teleshield.update_account_identity(
            account_id,
            SimpleNamespace(id=user_id, username=account_id, first_name=account_id),
            "+100****0000",
        )
        teleshield.account_store(account_id).session_file.write_bytes(account_id.encode())

    class FakeClient:
        def __init__(self, session, *args):
            self.session = session

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def log_out(self):
            pass

    with patch.object(telethon, "TelegramClient", FakeClient):
        assert asyncio.run(teleshield.logout_account(remove_credentials=True, account_id="account-a")) is True

    assert not teleshield.account_store("account-a").session_file.exists()
    assert teleshield.account_store("account-b").session_file.read_bytes() == b"account-b"
    assert "api_id" not in teleshield.load_config(account_id="account-a")
    assert teleshield.load_config(account_id="account-b")["user_id"] == 202
    assert teleshield.get_account("account-b")["user_id"] == 202


def test_remove_account_keeps_registry_when_data_deletion_fails(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")

    def fail_rmtree(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(teleshield.shutil, "rmtree", fail_rmtree)

    assert teleshield.remove_account("account-a") is False
    assert teleshield.get_account("account-a") is not None


def test_authenticate_binds_identity_update_to_store_selected_before_await(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    teleshield.set_active_account("account-a")

    class FakeClient:
        def __init__(self, *args):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return False

        async def send_code_request(self, phone):
            return SimpleNamespace(type=SimpleNamespace())

        async def sign_in(self, **kwargs):
            pass

        async def get_me(self):
            return SimpleNamespace(id=101, username="account-a", first_name="A")

    async def code_callback():
        teleshield.set_active_account("account-b")
        return "[REDACTED]"

    with patch.object(telethon, "TelegramClient", FakeClient):
        asyncio.run(
            teleshield.authenticate(
                "1234",
                "[REDACTED]",
                "+100****0000",
                code_callback,
                lambda: asyncio.sleep(0, result="[REDACTED]"),
            )
        )

    assert teleshield.get_account("account-a")["user_id"] == 101
    assert teleshield.get_account("account-b")["user_id"] is None


def test_relogin_with_different_phone_does_not_reuse_authorized_session(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.save_config(
        {
            "api_id": 1234,
            "api_hash": "[REDACTED]",
            "phone": "+1111111111",
            "user_id": 101,
        },
        account_id="account-a",
    )
    teleshield.update_account_identity(
        "account-a",
        SimpleNamespace(id=101, username="old", first_name="Old"),
    )
    old_session = teleshield.account_store("account-a").session_file
    old_session.write_bytes(b"old-authorized-session")
    observed = []

    class FakeClient:
        def __init__(self, session, *args):
            self.session = Path(session)
            observed.append(self.session.exists())

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return False

        async def send_code_request(self, phone):
            return SimpleNamespace(type=SimpleNamespace())

        async def sign_in(self, **kwargs):
            self.session.write_bytes(b"new-authorized-session")

        async def get_me(self):
            return SimpleNamespace(id=202, username="new", first_name="New")

    with patch.object(telethon, "TelegramClient", FakeClient):
        asyncio.run(
            teleshield.authenticate(
                "1234",
                "[REDACTED]",
                "+2222222222",
                lambda: asyncio.sleep(0, result="[REDACTED]"),
                lambda: asyncio.sleep(0, result="[REDACTED]"),
                account_id="account-a",
            )
        )

    assert observed == [False]
    assert teleshield.load_config("account-a")["user_id"] == 202
    assert teleshield.load_config("account-a")["phone"] == "+2222222222"


def test_concurrent_listeners_keep_account_context_and_session_isolated(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    for account_id, user_id in (("account-a", 101), ("account-b", 202)):
        teleshield.save_config({
            "api_id": 1234,
            "api_hash": "[REDACTED]",
            "user_id": user_id,
            "username": account_id,
            "whitelist": {},
            "blacklist": {},
        }, account_id=account_id)

    session_paths = []

    class FakeClient:
        def __init__(self, session, *args):
            self.session = session
            self.disconnected = asyncio.Event()
            session_paths.append(session)

        def on(self, event):
            return lambda handler: handler

        async def connect(self):
            Path(self.session).write_bytes(b"listener-session")
            Path(self.session).chmod(0o644)

        async def is_user_authorized(self):
            return True

        async def run_until_disconnected(self):
            await self.disconnected.wait()

        async def disconnect(self):
            self.disconnected.set()

    async def run_listener(account_id):
        stop_event = asyncio.Event()
        result = await teleshield.listen(
            stop_event=stop_event,
            ready_callback=stop_event.set,
            account_id=account_id,
        )
        return result

    async def run_all():
        return await asyncio.gather(run_listener("account-a"), run_listener("account-b"))

    with patch.object(telethon, "TelegramClient", FakeClient):
        results = asyncio.run(run_all())

    assert results == [True, True]
    assert sorted(session_paths) == sorted([
        str(teleshield.account_store("account-a").session_file),
        str(teleshield.account_store("account-b").session_file),
    ])
    for account_id in ("account-a", "account-b"):
        assert teleshield.account_store(account_id).session_file.stat().st_mode & 0o777 == 0o600


def test_account_store_hardens_account_directory_and_known_data_files(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    store = teleshield.account_store("account-a")
    store.ensure()
    for path in (
        store.session_file,
        Path(f"{store.session_file}-journal"),
        store.config_file,
        store.block_log,
        store.learned_patterns_file,
    ):
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o644)

    store.ensure()

    assert store.root.stat().st_mode & 0o777 == 0o700
    for path in (
        store.session_file,
        Path(f"{store.session_file}-journal"),
        store.config_file,
        store.block_log,
        store.learned_patterns_file,
    ):
        assert path.stat().st_mode & 0o777 == 0o600


def test_account_store_hardens_data_root_and_accounts_parent(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    root = tmp_path / "nested-data"
    teleshield.create_account("account-a", root=root)

    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "accounts").stat().st_mode & 0o777 == 0o700


def test_existing_registry_accounts_are_rehardened_by_registry_initialization(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    store = teleshield.account_store("account-a")
    for path, mode in (
        (tmp_path, 0o755),
        (tmp_path / "accounts", 0o755),
        (store.root, 0o755),
        (store.config_file, 0o644),
        (store.block_log, 0o644),
        (store.learned_patterns_file, 0o644),
    ):
        path.chmod(mode)

    teleshield.ensure_account_registry()

    assert tmp_path.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "accounts").stat().st_mode & 0o777 == 0o700
    assert store.root.stat().st_mode & 0o777 == 0o700
    for path in (store.config_file, store.block_log, store.learned_patterns_file):
        assert path.stat().st_mode & 0o777 == 0o600


def test_legacy_migration_rolls_back_registry_and_retries_after_copy_failure(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.SESSION_FILE.write_bytes(b"legacy-session")
    teleshield.save_config({"user_id": 42, "username": "legacy"})
    original_copy2 = teleshield.shutil.copy2
    calls = {"count": 0}

    def fail_first_copy(source, destination, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("copy failed")
        return original_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(teleshield.shutil, "copy2", fail_first_copy)
    with pytest.raises(RuntimeError, match="遷移"):
        teleshield.ensure_account_registry()

    assert teleshield.list_accounts() == []
    assert teleshield.SESSION_FILE.exists()
    assert teleshield.CONFIG_FILE.exists()

    monkeypatch.setattr(teleshield.shutil, "copy2", original_copy2)
    assert len(teleshield.ensure_account_registry()) == 1


def test_partial_legacy_registry_without_user_id_is_retried_in_place(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    partial = teleshield.create_account("partial-account")
    teleshield.SESSION_FILE.write_bytes(b"legacy-session")
    teleshield.CONFIG_FILE.write_text(json.dumps({"api_id": 123, "marker": "legacy"}), encoding="utf-8")

    records = teleshield.ensure_account_registry()

    assert [item["id"] for item in records] == [partial["id"]]
    assert teleshield.account_store(partial["id"]).config_file.read_text(encoding="utf-8").find("legacy") >= 0
    assert not teleshield.SESSION_FILE.exists()


def test_auto_start_account_is_persisted_and_cleared_on_remove(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    first = teleshield.create_account("account-a")
    second = teleshield.create_account("account-b")
    legacy_cfg = teleshield.load_config(first["id"])
    legacy_cfg["auto_start_protection"] = True
    teleshield.save_config(legacy_cfg, first["id"])

    assert teleshield.get_auto_start_account_id() == first["id"]
    assert teleshield.set_auto_start_account(None) is None
    assert teleshield.get_auto_start_account_id() is None
    assert teleshield.set_auto_start_account(second["id"]) == second["id"]
    assert teleshield.get_auto_start_account_id() == second["id"]
    assert first["auto_start_protection"] is False

    assert teleshield.set_auto_start_accounts([first["id"], second["id"]]) == [first["id"], second["id"]]
    assert teleshield.get_auto_start_account_ids() == [first["id"], second["id"]]
    assert first["id"] in teleshield._read_account_registry()["auto_start_account_ids"]

    assert teleshield.remove_account(second["id"], delete_files=True) is True
    assert teleshield.get_auto_start_account_ids() == [first["id"]]


def test_concurrent_identity_updates_reject_duplicate_user_id(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    barrier = threading.Barrier(2)
    me = SimpleNamespace(id=777, username="same", first_name="Same", last_name="User")

    def update(account_id):
        barrier.wait()
        try:
            teleshield.update_account_identity(account_id, me, "+886900000000")
            return "ok"
        except ValueError:
            return "duplicate"

    results = []
    workers = [threading.Thread(target=lambda account_id=account_id: results.append(update(account_id))) for account_id in ("account-a", "account-b")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert sorted(results) == ["duplicate", "ok"]
    assert sum(item.get("user_id") == 777 for item in teleshield.list_accounts()) == 1


def test_cross_process_identity_updates_reject_one_duplicate(tmp_path):
    teleshield.create_account("account-a", root=tmp_path)
    teleshield.create_account("account-b", root=tmp_path)
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_identity_update_process,
            args=(str(tmp_path), account_id, start_event, results),
        )
        for account_id in ("account-a", "account-b")
    ]
    for worker in workers:
        worker.start()
    start_event.set()
    outcomes = [results.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0

    assert sorted(outcomes) == ["duplicate", "ok"]
    assert sum(item.get("user_id") == 9090 for item in teleshield.list_accounts(tmp_path)) == 1


def test_connect_failure_still_hardens_new_session_file(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    store = teleshield.account_store("account-a")
    teleshield.save_config({"api_id": 123, "api_hash": "[REDACTED]"}, "account-a")

    class FakeClient:
        def __init__(self, session, *args):
            self.session = Path(session)

        async def connect(self):
            self.session.write_bytes(b"partially-created-session")
            self.session.chmod(0o644)
            raise OSError("connect failed")

        async def disconnect(self):
            pass

    with patch.object(telethon, "TelegramClient", FakeClient):
        with pytest.raises(OSError, match="connect failed"):
            asyncio.run(teleshield.scan_history("private", dry_run=True, account_id="account-a"))

    assert store.session_file.stat().st_mode & 0o777 == 0o600


def test_account_store_permission_failure_is_not_ignored(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    store = teleshield.account_store("account-a")
    store.session_file.write_bytes(b"session")
    original_chmod = Path.chmod

    def deny_session_chmod(path, mode, *args, **kwargs):
        if path == store.session_file:
            raise PermissionError("chmod denied")
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", deny_session_chmod)
    with pytest.raises(PermissionError, match="chmod denied"):
        store.ensure()


def test_registry_permission_failure_is_not_ignored(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    original_chmod = Path.chmod

    def deny_registry_chmod(path, mode, *args, **kwargs):
        if path == tmp_path / "accounts.json":
            raise PermissionError("registry chmod denied")
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", deny_registry_chmod)
    with pytest.raises(PermissionError, match="registry chmod denied"):
        teleshield.set_active_account("account-a")


def test_config_permission_failure_is_not_ignored(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    store = teleshield.account_store("account-a")
    original_chmod = Path.chmod

    def deny_config_chmod(path, mode, *args, **kwargs):
        if path == store.config_file:
            raise PermissionError("config chmod denied")
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", deny_config_chmod)
    with pytest.raises(PermissionError, match="config chmod denied"):
        teleshield.save_config({"api_id": 123}, "account-a")


def test_config_failure_does_not_commit_registry_identity(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    store = teleshield.account_store("account-a")
    original_config = store.config_file.read_bytes()

    class FakeClient:
        def __init__(self, session, *args):
            self.session = Path(session)

        async def connect(self):
            self.session.write_bytes(b"new-session")

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_me(self):
            return SimpleNamespace(id=7070, username="new-user", first_name="New")

    def fail_config_write(*args, **kwargs):
        raise OSError("config write failed")

    monkeypatch.setattr(teleshield, "save_config", fail_config_write)
    with patch.object(telethon, "TelegramClient", FakeClient):
        with pytest.raises(OSError, match="config write failed"):
            asyncio.run(
                teleshield.authenticate(
                    "123",
                    "[REDACTED]",
                    "+100****0000",
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    account_id="account-a",
                )
            )

    record = teleshield.get_account("account-a")
    assert record is not None
    assert record.get("user_id") is None
    assert store.config_file.read_bytes() == original_config
    assert not store.session_file.exists()


def test_failed_session_cleanup_is_loud_and_scrubs_credentials(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.create_account("account-b")
    teleshield.update_account_identity(
        "account-a",
        SimpleNamespace(id=8080, username="existing", first_name="Existing"),
    )
    store = teleshield.account_store("account-b")

    class FakeClient:
        def __init__(self, session, *args):
            self.session = Path(session)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_me(self):
            self.session.write_bytes(b"authorized-session")
            return SimpleNamespace(id=8080, username="existing", first_name="Existing")

    original_unlink = Path.unlink

    def deny_session_unlink(path, *args, **kwargs):
        if path == store.session_file:
            raise PermissionError("unlink denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_session_unlink)
    with patch.object(telethon, "TelegramClient", FakeClient):
        with pytest.raises(RuntimeError, match="Session.*清理"):
            asyncio.run(
                teleshield.authenticate(
                    "123",
                    "[REDACTED]",
                    "+100****0000",
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    lambda: asyncio.sleep(0, result="[REDACTED]"),
                    account_id="account-b",
                )
            )

    assert store.session_file.read_bytes() == b""
    assert store.session_file.stat().st_mode & 0o777 == 0o600
    record = teleshield.get_account("account-b")
    assert record is not None
    assert record.get("user_id") is None


def test_legacy_sources_are_hidden_before_registry_commit(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.SESSION_FILE.write_bytes(b"legacy-session")
    teleshield.save_config({"user_id": 42, "username": "legacy"})
    legacy_paths = {teleshield.SESSION_FILE, teleshield.CONFIG_FILE}
    original_unlink = Path.unlink

    def deny_direct_legacy_unlink(path, *args, **kwargs):
        if path in legacy_paths:
            raise PermissionError("legacy unlink denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_direct_legacy_unlink)
    records = teleshield.ensure_account_registry()

    assert len(records) == 1
    assert not teleshield.SESSION_FILE.exists()
    assert not teleshield.CONFIG_FILE.exists()


def test_registry_commit_failure_restores_legacy_layout(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.SESSION_FILE.write_bytes(b"legacy-session")
    teleshield.save_config({"user_id": 42, "username": "legacy"})
    original_write = teleshield._write_account_registry

    def write_then_fail(data, root=None):
        original_write(data, root)
        raise OSError("registry commit failed")

    monkeypatch.setattr(teleshield, "_write_account_registry", write_then_fail)
    with pytest.raises(RuntimeError, match="遷移"):
        teleshield.ensure_account_registry()

    assert teleshield.SESSION_FILE.read_bytes() == b"legacy-session"
    assert json.loads(teleshield.CONFIG_FILE.read_text(encoding="utf-8"))["user_id"] == 42
    assert list((tmp_path / "accounts").glob("*")) == []


def test_running_listener_blocks_local_session_deletion(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    store = teleshield.account_store("account-a")
    store.session_file.write_bytes(b"live-session")
    teleshield.save_config({"api_id": 123, "api_hash": "[REDACTED]"}, "account-a")
    ready = threading.Event()
    release = threading.Event()

    class FakeClient:
        def __init__(self, *args):
            pass

        def on(self, *args, **kwargs):
            return lambda handler: handler

        async def connect(self):
            ready.set()

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def run_until_disconnected(self):
            await asyncio.to_thread(release.wait, 10)

    result = []

    def run_listener():
        with patch.object(telethon, "TelegramClient", FakeClient):
            result.append(asyncio.run(teleshield.listen(account_id="account-a")))

    worker = threading.Thread(target=run_listener)
    worker.start()
    assert ready.wait(10)
    try:
        with pytest.raises(RuntimeError, match="使用中"):
            teleshield.remove_account("account-a")
        with pytest.raises(RuntimeError, match="使用中"):
            teleshield.clear_local_session(account_id="account-a")
        assert store.session_file.read_bytes() == b"live-session"
    finally:
        release.set()
        worker.join(15)
    assert not worker.is_alive()


def test_privacy_profile_gates_premium_and_restores_previous_settings(monkeypatch, tmp_path):
    from telethon.tl import functions, types

    configure_temp_storage(monkeypatch, tmp_path)
    teleshield.create_account("account-a")
    teleshield.save_config(
        {"api_id": 1234, "api_hash": "api-hash", "user_id": 1},
        account_id="account-a",
    )

    class FakePrivacyClient:
        def __init__(self):
            self.privacy_writes = 0
            self.privacy_write_types = []
            self.privacy_write_keys = []
            self.global_writes = 0
            self.global_settings = types.GlobalPrivacySettings(
                archive_and_mute_new_noncontact_peers=False,
                new_noncontact_peers_require_premium=False,
            )

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_me(self):
            return types.User(id=1, is_self=True, username="alice", premium=False)

        async def __call__(self, request):
            if isinstance(request, functions.account.GetPrivacyRequest):
                return types.account.PrivacyRules(
                    rules=[types.PrivacyValueAllowAll()],
                    chats=[],
                    users=[],
                )
            if isinstance(request, functions.account.SetPrivacyRequest):
                self.privacy_writes += 1
                self.privacy_write_types.append(type(request.rules[0]))
                self.privacy_write_keys.append(type(request.key).__name__)
                return None
            if isinstance(request, functions.account.GetGlobalPrivacySettingsRequest):
                return self.global_settings
            if isinstance(request, functions.account.SetGlobalPrivacySettingsRequest):
                self.global_writes += 1
                self.global_settings = request.settings
                return None
            if isinstance(request, functions.account.UpdateUsernameRequest):
                return None
            if isinstance(request, functions.account.GetPasswordRequest):
                return SimpleNamespace(has_password=False, has_recovery=False, hint="")
            if isinstance(request, functions.account.GetAuthorizationsRequest):
                return types.account.Authorizations(
                    authorization_ttl_days=365,
                    authorizations=[types.Authorization(
                        hash=1,
                        device_model="Mac",
                        platform="macOS",
                        system_version="14",
                        api_id=1234,
                        app_name="TeleShield",
                        app_version="1.3.1",
                        date_created=None,
                        date_active=datetime.now(timezone.utc),
                        ip="127.0.0.1",
                        country="TW",
                        region="TW",
                        current=True,
                        official_app=False,
                    )],
                )
            raise AssertionError(type(request).__name__)

    client = FakePrivacyClient()
    monkeypatch.setattr(teleshield, "_privacy_client", lambda: client)

    with pytest.raises(teleshield.PremiumAccountRequiredError, match="沒有購買 Telegram Premium"):
        asyncio.run(teleshield.apply_privacy_profile(True, account_id="account-a"))
    assert client.privacy_writes == 0
    assert teleshield.load_privacy_backup("account-a") is None

    applied = asyncio.run(teleshield.apply_privacy_profile(False, account_id="account-a"))
    assert applied["backup_available"] is True
    assert client.privacy_writes == len(teleshield.PRIVACY_PROFILE_ITEMS)
    assert len(client.privacy_write_types) == len(teleshield.PRIVACY_PROFILE_ITEMS)
    assert types.InputPrivacyValueDisallowAll in client.privacy_write_types
    assert types.InputPrivacyValueAllowContacts in client.privacy_write_types
    assert types.InputPrivacyValueAllowAll in client.privacy_write_types
    assert "InputPrivacyKeyPhoneNumber" in client.privacy_write_keys
    assert teleshield.load_privacy_backup("account-a") is not None

    with pytest.raises(teleshield.PremiumAccountRequiredError, match="沒有購買 Telegram Premium"):
        asyncio.run(
            teleshield.update_privacy_settings(
                {
                    "privacy": {},
                    "global": {"noncontact_peers_paid_stars": 1},
                },
                account_id="account-a",
            )
        )
    assert client.global_writes == 1

    restored = asyncio.run(teleshield.restore_privacy_settings(account_id="account-a"))
    assert restored["backup_available"] is False
    assert teleshield.load_privacy_backup("account-a") is None
