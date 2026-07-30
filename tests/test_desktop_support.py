import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import telethon
import teleshield
from desktop_platform import application_command


def configure_temp_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(teleshield, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(teleshield, "SESSION_FILE", tmp_path / "user.session")
    monkeypatch.setattr(teleshield, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(teleshield, "BLOCK_LOG", tmp_path / "block_log.json")


def test_default_session_dir_honours_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TELESHIELD_DATA_DIR", str(tmp_path / "data"))
    assert teleshield.default_session_dir() == tmp_path / "data"


def test_cli_lists_use_the_same_keys_as_runtime(monkeypatch, tmp_path):
    configure_temp_storage(monkeypatch, tmp_path)

    asyncio.run(teleshield.manage_list("add", "whitelist", "123456"))
    asyncio.run(teleshield.manage_list("add", "blacklist", "987654"))

    cfg = teleshield.load_config()
    assert "whitelist" in cfg
    assert "blacklist" in cfg
    assert "whitelist_list" not in cfg
    assert "blacklist_list" not in cfg
    assert teleshield.is_whitelisted(123456, cfg)
    assert teleshield.is_blacklisted(987654, cfg)


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
                "hash",
                "+10000000000",
                lambda: asyncio.sleep(0, result="12345"),
                lambda: asyncio.sleep(0, result="password"),
                delivery.append,
            )
        )

    assert me.id == 42
    assert delivery == ["App"]
    assert teleshield.load_config()["user_id"] == 42


def test_desktop_launch_command_starts_hidden():
    command = application_command()
    assert command[-1] == "--background"
    assert command
