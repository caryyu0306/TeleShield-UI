import io
import json
import threading
import time
from types import SimpleNamespace

from core_service import AuthRuntime, CoreService


class FakeCore:
    def __init__(self):
        self.accounts = [
            {
                "id": "account-a",
                "user_id": 101,
                "username": "alice",
                "display_name": "Alice",
                "phone_masked": "+12****90",
            }
        ]
        self.active_account_id = "account-a"
        self.config = {
            "api_id": 1234,
            "user_id": 101,
            "username": "alice",
            "blocked_count": 4,
            "whitelist": {"9": {}},
            "blacklist": {"8": {}},
        }
        self.stop_seen = threading.Event()

    def ensure_account_registry(self):
        return self.accounts

    def list_accounts(self):
        return list(self.accounts)

    def get_active_account_id(self):
        return self.active_account_id

    def load_config(self, account_id=None):
        return dict(self.config)

    def load_block_log(self, account_id=None):
        return {"blocks": []}

    def get_ocr_status(self):
        return {"available": False, "bundled": False, "languages": []}

    async def listen(self, stop_event=None, ready_callback=None, account_id=None):
        if ready_callback:
            ready_callback()
        await stop_event.wait()
        self.stop_seen.set()
        return True


def test_get_status_exposes_safe_core_summary_and_selected_account():
    service = CoreService(core=FakeCore())

    result = service.dispatch("get_status")

    assert result["active_account_id"] == "account-a"
    assert result["selected_account"]["username"] == "alice"
    assert result["selected_account"]["blocked_count"] == 4
    assert "kicked_count" not in result["selected_account"]
    assert result["selected_account"]["whitelist_count"] == 1
    assert result["selected_account"]["blacklist_count"] == 1
    assert result["selected_account"]["configured"] is True
    assert result["ocr"]["available"] is False
    assert "api_hash" not in result["selected_account"]


def test_stdio_protocol_returns_json_responses_and_structured_errors():
    service = CoreService(core=FakeCore())
    stdin = io.StringIO(
        '{"id": 1, "method": "list_accounts"}\n'
        '{"id": 2, "method": "does_not_exist"}\n'
    )
    stdout = io.StringIO()

    service.run(stdin, stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]

    assert responses[0] == {
        "id": 1,
        "ok": True,
        "result": [
            {
                "id": "account-a",
                "user_id": 101,
                "username": "alice",
                "display_name": "Alice",
                "phone_masked": "+12****90",
            }
        ],
    }
    assert responses[1]["id"] == 2
    assert responses[1]["ok"] is False
    assert responses[1]["error"]["type"] == "UnknownMethodError"


def test_start_and_stop_protection_emit_ready_event_and_join_worker():
    events = []
    core = FakeCore()
    service = CoreService(core=core, emit_event=events.append)

    started = service.dispatch("start_protection", {"account_id": "account-a"})
    assert started["account_id"] == "account-a"
    assert started["running"] is True

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not any(event.get("state") == "ready" for event in events):
        time.sleep(0.01)

    assert {event.get("state") for event in events} >= {"starting", "ready"}

    stopped = service.dispatch("stop_protection", {"account_id": "account-a"})
    assert stopped == {"account_id": "account-a", "running": False}
    assert core.stop_seen.wait(1)
    assert service.dispatch("get_status")["selected_account"]["running"] is False


def test_invalid_json_is_reported_without_breaking_following_requests():
    service = CoreService(core=FakeCore())
    stdin = io.StringIO("not-json\n{\"id\": 3, \"method\": \"get_ocr_status\"}\n")
    stdout = io.StringIO()

    service.run(stdin, stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]

    assert responses[0]["id"] is None
    assert responses[0]["ok"] is False
    assert responses[0]["error"]["type"] == "InvalidRequestError"
    assert responses[1] == {
        "id": 3,
        "ok": True,
        "result": {"available": False, "bundled": False, "languages": []},
    }


class FakeAuthCore(FakeCore):
    async def authenticate(
        self,
        api_id,
        api_hash,
        phone,
        code_callback,
        password_callback,
        status_callback=None,
        account_id=None,
    ):
        if status_callback:
            status_callback("App")
        code = await code_callback()
        password = await password_callback()
        assert (api_id, api_hash, phone, code, password, account_id) == (
            "1234",
            "hash",
            "+100****0000",
            "2468",
            "secret",
            "account-a",
        )
        return SimpleNamespace(id=101, username="alice", first_name="Alice")


def test_auth_flow_round_trips_code_and_two_step_password_without_blocking_stdio():
    events = []
    service = CoreService(core=FakeAuthCore(), emit_event=events.append)

    started = service.dispatch(
        "start_auth",
        {
            "api_id": "1234",
            "api_hash": "hash",
            "phone": "+100****0000",
            "account_id": "account-a",
        },
    )
    flow_id = started["flow_id"]
    assert started["running"] is True

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not any(
        event.get("event") == "auth_challenge" and event.get("kind") == "code"
        for event in events
    ):
        time.sleep(0.01)
    assert any(event.get("kind") == "code" for event in events)

    assert service.dispatch(
        "submit_auth_code", {"flow_id": flow_id, "value": "2468"}
    ) == {"flow_id": flow_id, "accepted": True}

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not any(
        event.get("event") == "auth_challenge" and event.get("kind") == "password"
        for event in events
    ):
        time.sleep(0.01)
    assert any(event.get("kind") == "password" for event in events)

    assert service.dispatch(
        "submit_auth_password", {"flow_id": flow_id, "value": "secret"}
    ) == {"flow_id": flow_id, "accepted": True}

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not any(
        event.get("event") == "auth_succeeded" for event in events
    ):
        time.sleep(0.01)
    assert any(event.get("event") == "auth_succeeded" for event in events)


class FakePlatform:
    def __init__(self):
        self.enabled = False

    def is_start_on_login_enabled(self):
        return self.enabled

    def set_start_on_login(self, enabled):
        self.enabled = bool(enabled)


class FakeParityCore(FakeCore):
    def __init__(self):
        super().__init__()
        self.startup_enabled = False
        self.learned = {"keywords": ["spam"], "patterns": ["https://"]}
        self.calls = []
        self.auto_start_ids = ["account-a"]
        self.policies = {}

    def create_account(self):
        self.calls.append("create_account")
        return {"id": "account-b", "display_name": ""}

    def remove_account(self, account_id, delete_files=True):
        self.calls.append(("remove_account", account_id, delete_files))
        return True

    def get_learned_patterns(self, account_id=None):
        return dict(self.learned)

    def remove_learned_pattern(self, kind, value, account_id=None):
        self.calls.append(("remove_learned_pattern", kind, value))
        return True

    async def logout_account(self, remove_credentials=False, account_id=None):
        self.calls.append(("logout_account", remove_credentials, account_id))
        return True

    def clear_local_session(self, remove_credentials=False, account_id=None):
        self.calls.append(("clear_local_session", remove_credentials, account_id))

    def get_scan_settings(self, account_id=None):
        return {"private_dialog_limit": 1}

    def update_scan_settings(self, updates, account_id=None):
        self.calls.append(("update_scan_settings", updates, account_id))
        return dict(updates)

    def get_moderation_policy(self, account_id=None):
        return dict(self.policies.get(account_id, {
            "protection_mode": "normal",
            "delete_private_history_after_block": False,
            "delete_private_history_scope": "self",
            "telegram_notification": {
                "enabled": False,
                "bot_token": "",
                "channel_id": "",
            },
        }))

    def update_moderation_policy(self, updates, account_id=None):
        current = self.get_moderation_policy(account_id)
        current.update(updates)
        self.policies[account_id] = current
        self.calls.append(("update_moderation_policy", dict(updates), account_id))
        return dict(current)

    def test_telegram_notification(self, bot_token, channel_id, account_id=None):
        self.calls.append(("test_telegram_notification", bot_token, channel_id, account_id))
        return {"sent": bool(bot_token and channel_id)}

    def import_list_entries(self, path, list_type, replace=False, account_id=None):
        self.calls.append(("import_list_entries", path, list_type, replace, account_id))
        return 2

    def export_list_entries(self, path, list_type, fmt="", account_id=None):
        self.calls.append(("export_list_entries", path, list_type, fmt, account_id))
        return 3

    def export_block_records(self, path, query="", source="all", fmt="json", account_id=None):
        self.calls.append(("export_block_records", path, query, source, fmt, account_id))
        return 4

    def get_auto_start_account_id(self):
        return self.auto_start_ids[0] if self.auto_start_ids else None

    def get_auto_start_account_ids(self):
        return list(self.auto_start_ids)

    def set_auto_start_accounts(self, account_ids):
        self.auto_start_ids = list(account_ids)
        return list(self.auto_start_ids)


class FakePrivacyCore(FakeCore):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def get_privacy_audit(self, account_id=None):
        self.calls.append(("get_privacy_audit", account_id))
        return {"account_id": account_id, "premium": False}

    async def apply_privacy_profile(self, include_premium=False, account_id=None):
        self.calls.append(("apply_privacy_profile", include_premium, account_id))
        return {"account_id": account_id, "premium": include_premium}

    async def update_privacy_settings(
        self, privacy_settings, username=None, account_id=None
    ):
        self.calls.append(("update_privacy_settings", privacy_settings, username, account_id))
        return {"account_id": account_id, "updated": True}

    async def restore_privacy_settings(self, account_id=None):
        self.calls.append(("restore_privacy_settings", account_id))
        return {"account_id": account_id, "restored": True}

    async def revoke_authorization(self, session_hash, account_id=None):
        self.calls.append(("revoke_authorization", session_hash, account_id))
        return {"account_id": account_id, "revoked": session_hash}

    async def update_two_factor(
        self,
        current_password="",
        new_password="",
        hint="",
        account_id=None,
    ):
        self.calls.append(("update_two_factor", current_password, new_password, hint, account_id))
        return {"account_id": account_id, "two_factor": bool(new_password)}


def _wait_for_event(events, name, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in events:
            if event.get("event") == name:
                return event
        time.sleep(0.01)
    raise AssertionError(f"event not emitted: {name}")


def test_management_surface_covers_account_rules_exports_and_startup():
    events = []
    core = FakeParityCore()
    service = CoreService(core=core, emit_event=events.append, platform=FakePlatform())

    assert service.dispatch("create_account")["id"] == "account-b"
    assert service.dispatch("remove_account", {"account_id": "account-b"})["removed"] is True
    assert service.dispatch("get_learned_patterns")["keywords"] == ["spam"]
    assert service.dispatch(
        "remove_learned_pattern", {"kind": "keywords", "value": "spam"}
    )["removed"] is True
    assert service.dispatch("get_startup_status")["enabled"] is False
    assert service.dispatch("set_startup", {"enabled": True})["enabled"] is True
    assert service.dispatch("import_list", {"path": "/tmp/in.json", "list_type": "blacklist"})["count"] == 2
    assert service.dispatch("export_list", {"path": "/tmp/out.json", "list_type": "blacklist"})["count"] == 3
    assert service.dispatch("export_blocks", {"path": "/tmp/log.csv", "fmt": "csv"})["count"] == 4
    assert service.dispatch("get_scan_settings")["private_dialog_limit"] == 1
    assert service.dispatch("update_scan_settings", {"updates": {"private_dialog_limit": 4}})["private_dialog_limit"] == 4

    logout_job = service.dispatch("logout", {"remove_credentials": False})
    assert logout_job["running"] is True
    assert _wait_for_event(events, "account_operation_finished")["operation"] == "logout"


def test_account_details_exposes_global_auto_start_and_omits_credentials():
    core = FakeParityCore()
    service = CoreService(core=core, platform=FakePlatform())

    details = service.dispatch("get_account_details", {"account_id": "account-a"})

    assert details["account_id"] == "account-a"
    assert details["auto_start"] is True
    assert details["auto_start_account_id"] == "account-a"
    assert details["auto_start_account_ids"] == ["account-a"]
    assert "api_id" not in details
    assert "api_hash" not in details
    assert details["moderation_policy"] == {
        "protection_mode": "normal",
        "delete_private_history_after_block": False,
        "delete_private_history_scope": "self",
        "telegram_notification": {
            "enabled": False,
            "bot_token": "",
            "channel_id": "",
        },
    }

    assert service.dispatch("set_auto_start", {"account_ids": ["account-a", "account-b"]}) == {
        "account_ids": ["account-a", "account-b"]
    }

    assert service.dispatch(
        "test_telegram_notification",
        {
            "account_id": "account-a",
            "bot_token": "123456:ABC",
            "channel_id": "-1001234567890",
        },
    ) == {"sent": True}
    assert (
        "test_telegram_notification",
        "123456:ABC",
        "-1001234567890",
        "account-a",
    ) in core.calls


def test_privacy_rpc_is_account_scoped_and_preserves_premium_choice():
    core = FakePrivacyCore()
    service = CoreService(core=core, platform=FakePlatform())

    assert service.dispatch("get_privacy_audit", {"account_id": "account-a"}) == {
        "account_id": "account-a",
        "premium": False,
    }
    assert service.dispatch(
        "apply_privacy_profile",
        {"account_id": "account-a", "include_premium": "false"},
    ) == {"account_id": "account-a", "premium": False}
    assert service.dispatch(
        "apply_privacy_profile",
        {"account_id": "account-a", "include_premium": True},
    ) == {"account_id": "account-a", "premium": True}
    settings = {
        "privacy": {"phone_number": {"mode": "disallow_all"}},
        "global": {"hide_read_marks": True},
    }
    assert service.dispatch(
        "update_privacy_settings",
        {"account_id": "account-a", "settings": settings, "username": "alice"},
    ) == {"account_id": "account-a", "updated": True}
    assert service.dispatch("restore_privacy_settings", {"account_id": "account-a"}) == {
        "account_id": "account-a",
        "restored": True,
    }
    assert service.dispatch(
        "revoke_authorization",
        {"account_id": "account-a", "session_hash": 9876},
    ) == {"account_id": "account-a", "revoked": "9876"}
    assert service.dispatch(
        "update_two_factor",
        {
            "account_id": "account-a",
            "current_password": "old password",
            "new_password": "new password",
            "hint": "hint",
        },
    ) == {"account_id": "account-a", "two_factor": True}

    assert core.calls == [
        ("get_privacy_audit", "account-a"),
        ("apply_privacy_profile", False, "account-a"),
        ("apply_privacy_profile", True, "account-a"),
        ("update_privacy_settings", settings, "alice", "account-a"),
        ("restore_privacy_settings", "account-a"),
        ("revoke_authorization", "9876", "account-a"),
        ("update_two_factor", "old password", "new password", "hint", "account-a"),
    ]


def test_privacy_rpc_temporarily_pauses_active_listener():
    core = FakePrivacyCore()
    service = CoreService(core=core, platform=FakePlatform())
    lifecycle = []

    service._runtime = lambda _account_id: SimpleNamespace(running=True)
    service._stop_protection = lambda params: (
        lifecycle.append(("stop", params["account_id"])) or {"running": False}
    )
    service._start_protection = lambda params: (
        lifecycle.append(("start", params["account_id"])) or {"running": True}
    )

    assert service.dispatch("get_privacy_audit", {"account_id": "account-a"}) == {
        "account_id": "account-a",
        "premium": False,
    }
    assert lifecycle == [("stop", "account-a"), ("start", "account-a")]


def test_moderation_policy_rpc_is_explicitly_account_scoped():
    core = FakeParityCore()
    service = CoreService(core=core, platform=FakePlatform())

    assert service.dispatch("get_moderation_policy", {"account_id": "account-a"}) == {
        "protection_mode": "normal",
        "delete_private_history_after_block": False,
        "delete_private_history_scope": "self",
        "telegram_notification": {
            "enabled": False,
            "bot_token": "",
            "channel_id": "",
        },
    }
    assert service.dispatch(
        "update_moderation_policy",
        {
            "account_id": "account-b",
            "updates": {
                "protection_mode": "strict",
                "delete_private_history_after_block": True,
                "delete_private_history_scope": "both",
                "telegram_notification": {
                    "enabled": True,
                    "bot_token": "123456:ABC",
                    "channel_id": "-1001234567890",
                },
            },
        },
    ) == {
        "protection_mode": "strict",
        "delete_private_history_after_block": True,
        "delete_private_history_scope": "both",
        "telegram_notification": {
            "enabled": True,
            "bot_token": "123456:ABC",
            "channel_id": "-1001234567890",
        },
    }
    assert service.dispatch("get_moderation_policy", {"account_id": "account-a"}) == {
        "protection_mode": "normal",
        "delete_private_history_after_block": False,
        "delete_private_history_scope": "self",
        "telegram_notification": {
            "enabled": False,
            "bot_token": "",
            "channel_id": "",
        },
    }
    assert ("update_moderation_policy", {
        "protection_mode": "strict",
        "delete_private_history_after_block": True,
        "delete_private_history_scope": "both",
        "telegram_notification": {
            "enabled": True,
            "bot_token": "123456:ABC",
            "channel_id": "-1001234567890",
        },
    }, "account-b") in core.calls


def test_scan_protocol_preserves_explicit_false_dry_run():
    class ScanCore(FakeCore):
        def __init__(self):
            super().__init__()
            self.seen_dry_run = None

        async def scan_history(
            self,
            scope,
            dry_run=True,
            progress_callback=None,
            cancel_event=None,
            account_id=None,
        ):
            self.seen_dry_run = dry_run
            if progress_callback:
                progress_callback("scan started")
            return {"scope": scope, "dry_run": dry_run, "findings": []}

    events = []
    core = ScanCore()
    service = CoreService(core=core, emit_event=events.append)
    job = service.dispatch("start_scan", {"scope": "private", "dry_run": "false"})
    assert job["running"] is True
    finished = _wait_for_event(events, "scan_finished")
    result = finished["result"]
    assert finished["account_id"] == "account-a"
    assert any(
        event.get("event") == "scan_progress" and event.get("account_id") == "account-a"
        for event in events
    )
    assert result["dry_run"] is False
    assert core.seen_dry_run is False


def test_unexpected_listener_exit_cleans_runtime_thread():
    class UnexpectedExitCore(FakeCore):
        async def listen(self, stop_event=None, ready_callback=None, account_id=None):  # type: ignore[override]
            return False

    events = []
    service = CoreService(core=UnexpectedExitCore(), emit_event=events.append)
    service.dispatch("start_protection", {"account_id": "account-a"})
    runtime = service._listeners["account-a"]
    _wait_for_event(events, "status")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and runtime.thread and runtime.thread.is_alive():
        time.sleep(0.01)
    try:
        assert runtime.thread is not None
        assert runtime.thread.is_alive() is False
    finally:
        runtime.stop_event.set()
        if runtime.thread:
            runtime.thread.join(timeout=1)


def test_auth_error_and_event_log_redact_credentials():
    class SecretErrorCore(FakeCore):
        async def authenticate(
            self,
            api_id,
            api_hash,
            phone,
            code_callback,
            password_callback,
            status_callback=None,
            account_id=None,
        ):
            raise RuntimeError(
                "api_hash=super-secret-hash password=two-step-pass phone=+15551234567"
            )

    events = []
    service = CoreService(core=SecretErrorCore(), emit_event=events.append)
    service.dispatch(
        "start_auth",
        {
            "api_id": "1234",
            "api_hash": "super-secret-hash",
            "phone": "+15551234567",
            "account_id": "account-a",
        },
    )
    failure = _wait_for_event(events, "auth_failed")
    serialized = json.dumps(failure, ensure_ascii=False)
    assert "super-secret-hash" not in serialized
    assert "two-step-pass" not in serialized
    assert "+15551234567" not in serialized
    assert "[REDACTED]" in serialized


def test_close_joins_auth_and_job_workers():
    class SpyThread:
        def __init__(self):
            self.join_calls = []

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.join_calls.append(timeout)

    service = CoreService(core=FakeCore())
    auth_thread = SpyThread()
    job_thread = SpyThread()
    auth = service._auth_flows["flow"] = AuthRuntime(  # type: ignore[arg-type]
        flow_id="flow", account_id="account-a", thread=auth_thread
    )
    job_cancel = threading.Event()
    service._jobs["job"] = (job_thread, job_cancel)  # type: ignore[assignment]

    service.close()

    assert auth.cancel_event.is_set()
    assert job_cancel.is_set()
    assert auth_thread.join_calls
    assert job_thread.join_calls
