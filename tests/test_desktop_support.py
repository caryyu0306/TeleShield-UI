import asyncio

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


def test_desktop_launch_command_starts_hidden():
    command = application_command()
    assert command[-1] == "--background"
    assert command
