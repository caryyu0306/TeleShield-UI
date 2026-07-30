import io
import json
import threading
import time
from types import SimpleNamespace

from core_service import CoreService


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
            "kicked_count": 2,
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
    assert result["selected_account"]["kicked_count"] == 2
    assert result["selected_account"]["whitelist_count"] == 1
    assert result["selected_account"]["blacklist_count"] == 1
    assert result["selected_account"]["configured"] is True
    assert result["ocr"]["available"] is False


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
            "+10000000000",
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
            "phone": "+10000000000",
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
