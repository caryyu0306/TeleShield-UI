#!/usr/bin/env python3
"""TeleShield Telegram protection core used by the native macOS app.

The SwiftUI shell talks to this module through ``core_service.py``.  The
module intentionally keeps its reusable account, Telegram, scanning, OCR,
and reporting implementation in one place, but it no longer exposes a
command-line entry point.
"""

import asyncio, csv, errno, json, os, random, re, shutil, ssl, sys, tempfile, threading, time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4
from telethon import events
from collections import defaultdict

try:
    import certifi
except ImportError:  # pragma: no cover - requirements include certifi for packaged builds
    certifi = None

# ──────────── 設定 ────────────

def default_session_dir() -> Path:
    """Return a writable per-user data directory on every supported OS."""
    override = os.getenv("TELESHIELD_DATA_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        root = Path(os.getenv("APPDATA", Path.home() / "AppData/Roaming"))
        return root / "TeleShield"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TeleShield"

    root = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local/share"))
    return root / "TeleShield"


SESSION_DIR = default_session_dir()
SESSION_FILE = SESSION_DIR / "user.session"
CONFIG_FILE = SESSION_DIR / "config.json"
BLOCK_LOG = SESSION_DIR / "block_log.json"


class AccountStore:
    """Filesystem paths belonging to exactly one Telegram account."""

    def __init__(
        self,
        root: Path,
        session_file: Optional[Path] = None,
        config_file: Optional[Path] = None,
        block_log: Optional[Path] = None,
        account_id: Optional[str] = None,
        data_root: Optional[Path] = None,
    ):
        self.root = Path(root)
        self.account_id = account_id
        self.data_root = Path(data_root) if data_root is not None else (
            self.root.parent.parent if account_id else self.root
        )
        self.session_file = Path(session_file) if session_file is not None else self.root / "user.session"
        self.config_file = Path(config_file or self.root / "config.json")
        self.block_log = Path(block_log or self.root / "block_log.json")
        self.learned_patterns_file = self.root / "learned_patterns.json"

    def ensure(self) -> None:
        directories = [self.data_root]
        if self.account_id:
            directories.append(self.data_root / "accounts")
        directories.append(self.root)
        for directory in directories:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        for path in (
            self.session_file,
            Path(f"{self.session_file}-journal"),
            Path(f"{self.session_file}-wal"),
            Path(f"{self.session_file}-shm"),
            self.config_file,
            self.block_log,
            self.learned_patterns_file,
        ):
            if not path.exists():
                continue
            path.chmod(0o600)
        defaults = (
            (self.config_file, {}),
            (self.block_log, {"blocks": []}),
            (self.learned_patterns_file, {"keywords": [], "patterns": []}),
        )
        for path, data in defaults:
            if path.exists():
                continue
            _write_private_bytes(
                path,
                json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
            )


_CURRENT_ACCOUNT_STORE: ContextVar[Optional[AccountStore]] = ContextVar(
    "teleshield_current_account_store",
    default=None,
)
_ACCOUNT_REGISTRY_LOCK = threading.RLock()
_ACCOUNT_REGISTRY_LOCK_STATE = threading.local()
_ACCOUNT_SESSION_MUTEX_GUARD = threading.Lock()
_ACCOUNT_SESSION_MUTEXES = {}
_ACCOUNT_SESSION_LOCK_STATE = threading.local()

_OPENCC_S2T = None


def normalize_traditional(text: str) -> str:
    """Normalize Simplified Chinese to Traditional Chinese for matching."""
    if not text:
        return text
    global _OPENCC_S2T
    try:
        if _OPENCC_S2T is None:
            from opencc import OpenCC

            _OPENCC_S2T = OpenCC("s2t")
        return _OPENCC_S2T.convert(text)
    except Exception:
        # Keep text processing available if an older external install lacks OpenCC.
        return text


class AccountSessionBusyError(RuntimeError):
    """Raised when another operation owns an account's Telegram Session."""


def _account_session_mutex(key: str):
    with _ACCOUNT_SESSION_MUTEX_GUARD:
        return _ACCOUNT_SESSION_MUTEXES.setdefault(key, threading.RLock())


@contextmanager
def _private_file_lock(path: Path, blocking: bool = True):
    """Hold one cross-platform, owner-only advisory file lock."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(fd, "r+b", buffering=0)
    acquired = False
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), 0o600)
        else:
            path.chmod(0o600)
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
            handle.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(handle.fileno(), mode, 1)
        else:
            import fcntl

            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), flags)
        acquired = True
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def _account_registry_transaction(root: Optional[Path] = None):
    """Serialize a complete registry read/check/write transaction across processes."""
    root_path = _account_data_root(root)
    key = str(root_path.resolve())
    with _ACCOUNT_REGISTRY_LOCK:
        held = getattr(_ACCOUNT_REGISTRY_LOCK_STATE, "held", None)
        if held is None:
            held = {}
            _ACCOUNT_REGISTRY_LOCK_STATE.held = held
        if key in held:
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return
        with _private_file_lock(root_path / ".accounts.lock"):
            held[key] = 1
            try:
                yield
            finally:
                held.pop(key, None)


@contextmanager
def _account_session_lease(
    account_id: Optional[str] = None,
    store: Optional[AccountStore] = None,
    blocking: bool = False,
):
    """Exclusively lease one account's Session across threads and processes."""
    target = store or _resolve_account_store(account_id)
    target.ensure()
    key = str(target.root.resolve())
    mutex = _account_session_mutex(key)
    if not mutex.acquire(blocking=blocking):
        raise AccountSessionBusyError("此 Telegram 帳號正在使用中，請先停止其他操作")
    depths = getattr(_ACCOUNT_SESSION_LOCK_STATE, "depths", None)
    if depths is None:
        depths = {}
        _ACCOUNT_SESSION_LOCK_STATE.depths = depths
    try:
        if key in depths:
            depths[key] += 1
            try:
                yield target
            finally:
                depths[key] -= 1
            return
        try:
            lock_id = _validate_account_id(target.account_id) if target.account_id else "legacy"
            lock_path = target.data_root / ".session-locks" / f"{lock_id}.lock"
            with _private_file_lock(lock_path, blocking=blocking):
                depths[key] = 1
                try:
                    yield target
                finally:
                    depths.pop(key, None)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise AccountSessionBusyError("此 Telegram 帳號正在使用中，請先停止其他操作") from exc
            raise
    finally:
        mutex.release()


def _session_leased(function):
    """Wrap an async Telegram operation in one account context and Session lease."""
    function_signature = signature(function)

    @wraps(function)
    async def leased(*args, **kwargs):
        arguments = function_signature.bind_partial(*args, **kwargs).arguments
        with account_context(arguments.get("account_id")) as store:
            with _account_session_lease(store=store):
                return await function(*args, **kwargs)

    return leased


def _registry_locked(function):
    function_signature = signature(function)

    @wraps(function)
    def locked(*args, **kwargs):
        arguments = function_signature.bind_partial(*args, **kwargs).arguments
        with _account_registry_transaction(arguments.get("root")):
            return function(*args, **kwargs)
    return locked


def _account_data_root(root: Optional[Path] = None) -> Path:
    return Path(root) if root is not None else Path(SESSION_DIR)


def _account_registry_path(root: Optional[Path] = None) -> Path:
    return _account_data_root(root) / "accounts.json"


def _empty_account_registry() -> dict:
    return {
        "version": 1,
        "active_account_id": None,
        "auto_start_account_id": None,
        "auto_start_account_configured": False,
        "auto_start_account_ids": [],
        "auto_start_accounts_configured": False,
        "accounts": [],
    }


@_registry_locked
def _read_account_registry(root: Optional[Path] = None) -> dict:
    path = _account_registry_path(root)
    if not path.exists():
        return _empty_account_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"帳號索引無法讀取：{exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("帳號索引格式無效")
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        accounts = []
    raw_account_ids = data.get("auto_start_account_ids")
    if isinstance(raw_account_ids, list):
        auto_start_account_ids = [str(item) for item in raw_account_ids if item]
    elif data.get("auto_start_account_id"):
        auto_start_account_ids = [str(data["auto_start_account_id"])]
    else:
        auto_start_account_ids = []
    if "auto_start_accounts_configured" in data:
        auto_start_accounts_configured = bool(data["auto_start_accounts_configured"])
    else:
        auto_start_accounts_configured = bool(data.get("auto_start_account_configured", False))
    return {
        "version": int(data.get("version", 1)),
        "active_account_id": data.get("active_account_id"),
        "auto_start_account_id": data.get("auto_start_account_id"),
        "auto_start_account_configured": bool(
            data.get("auto_start_account_configured", "auto_start_account_id" in data)
        ),
        "auto_start_account_ids": auto_start_account_ids,
        "auto_start_accounts_configured": auto_start_accounts_configured,
        "accounts": [dict(item) for item in accounts if isinstance(item, dict)],
    }


@_registry_locked
def _write_account_registry(data: dict, root: Optional[Path] = None) -> None:
    path = _account_registry_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp_file = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        tmp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_file.chmod(0o600)
        os.replace(tmp_file, path)
        path.chmod(0o600)
    finally:
        try:
            tmp_file.unlink()
        except FileNotFoundError:
            pass


def _write_private_bytes(path: Path, data: bytes) -> None:
    """Atomically replace one owner-only file without a permissive creation window."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.restore-{uuid4().hex}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _restore_private_file(path: Path, snapshot: Optional[bytes]) -> None:
    if snapshot is None:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass
        return
    _write_private_bytes(Path(path), snapshot)


def _validate_account_id(account_id: str) -> str:
    account_id = str(account_id or "").strip()
    if not account_id or account_id in {".", ".."} or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in account_id):
        raise ValueError("帳號 ID 格式無效")
    return account_id


def account_store(account_id: str, root: Optional[Path] = None) -> AccountStore:
    account_id = _validate_account_id(account_id)
    data_root = _account_data_root(root)
    return AccountStore(data_root / "accounts" / account_id, account_id=account_id, data_root=data_root)


def _legacy_account_store() -> AccountStore:
    return AccountStore(SESSION_DIR, SESSION_FILE, CONFIG_FILE, BLOCK_LOG, data_root=SESSION_DIR)


def _resolve_account_store(account_id: Optional[str] = None) -> AccountStore:
    if account_id:
        return account_store(account_id)
    current = _CURRENT_ACCOUNT_STORE.get()
    if current is not None:
        return current
    active_account_id = get_active_account_id()
    if active_account_id:
        return account_store(active_account_id)
    return _legacy_account_store()


@contextmanager
def account_context(account_id: Optional[str] = None, store: Optional[AccountStore] = None):
    """Bind all implicit core storage calls to one account for this task/thread."""
    target = store or _resolve_account_store(account_id)
    token = _CURRENT_ACCOUNT_STORE.set(target)
    try:
        yield target
    finally:
        _CURRENT_ACCOUNT_STORE.reset(token)


def _mask_phone(phone: str) -> str:
    phone = str(phone or "")
    if len(phone) <= 4:
        return "" if not phone else "*" * len(phone)
    return f"{phone[:3]}{'*' * max(1, len(phone) - 5)}{phone[-2:]}"


@_registry_locked
def list_accounts(root: Optional[Path] = None) -> list:
    return list(_read_account_registry(root).get("accounts", []))


@_registry_locked
def get_account(account_id: str, root: Optional[Path] = None) -> Optional[dict]:
    account_id = _validate_account_id(account_id)
    return next((record for record in list_accounts(root) if str(record.get("id")) == account_id), None)


@_registry_locked
def get_active_account_id(root: Optional[Path] = None) -> Optional[str]:
    registry = _read_account_registry(root)
    active = registry.get("active_account_id")
    if not active:
        return None
    if any(str(item.get("id")) == str(active) for item in registry.get("accounts", [])):
        return str(active)
    return None


@_registry_locked
def create_account(account_id: Optional[str] = None, root: Optional[Path] = None, metadata: Optional[dict] = None) -> dict:
    root_path = _account_data_root(root)
    registry = _read_account_registry(root_path)
    account_id = _validate_account_id(account_id or f"account-{uuid4().hex[:12]}")
    existing = next((item for item in registry["accounts"] if str(item.get("id")) == account_id), None)
    if existing is not None:
        account_store(account_id, root_path).ensure()
        return dict(existing)
    _assert_unique_account_identity(account_id, (metadata or {}).get("user_id"), root_path)
    store = account_store(account_id, root_path)
    store.ensure()
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "id": account_id,
        "user_id": None,
        "username": "",
        "display_name": "",
        "phone_masked": "",
        "created_at": now,
        "last_used_at": now,
        "auto_start_protection": False,
    }
    for key in record:
        if metadata and key in metadata and key not in {"id", "created_at"}:
            record[key] = metadata[key]
    store.ensure()
    registry["accounts"].append(record)
    if not registry.get("active_account_id"):
        registry["active_account_id"] = account_id
    _write_account_registry(registry, root_path)
    return dict(record)


@_registry_locked
def set_active_account(account_id: str, root: Optional[Path] = None) -> dict:
    root_path = _account_data_root(root)
    registry = _read_account_registry(root_path)
    account_id = _validate_account_id(account_id)
    record = next((item for item in registry["accounts"] if str(item.get("id")) == account_id), None)
    if record is None:
        raise ValueError("找不到指定 Telegram 帳號")
    record["last_used_at"] = datetime.now(timezone.utc).isoformat()
    registry["active_account_id"] = account_id
    _write_account_registry(registry, root_path)
    return dict(record)


def _config_requests_legacy_auto_start(account_id: str) -> bool:
    try:
        return bool(load_config(account_id).get("auto_start_protection"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False


@_registry_locked
def get_auto_start_account_ids(root: Optional[Path] = None) -> list[str]:
    """Return accounts selected for automatic protection at app startup."""
    registry = _read_account_registry(root)
    account_ids = {str(item.get("id")) for item in registry.get("accounts", []) if item.get("id")}
    if registry.get("auto_start_accounts_configured"):
        selected = []
        for raw_id in registry.get("auto_start_account_ids", []):
            try:
                candidate_id = _validate_account_id(str(raw_id))
            except ValueError:
                continue
            if candidate_id in account_ids and candidate_id not in selected:
                selected.append(candidate_id)
        return selected
    # Backward compatibility for registries created before the global selector.
    legacy = []
    for item in registry.get("accounts", []):
        raw_id = item.get("id")
        if not raw_id:
            continue
        try:
            candidate_id = _validate_account_id(str(raw_id))
        except ValueError:
            continue
        if item.get("auto_start_protection") or _config_requests_legacy_auto_start(candidate_id):
            legacy.append(candidate_id)
    active = str(registry.get("active_account_id")) if registry.get("active_account_id") else ""
    if active in legacy:
        return [active] + [item for item in legacy if item != active]
    return legacy


@_registry_locked
def get_auto_start_account_id(root: Optional[Path] = None) -> Optional[str]:
    """Return the first selected account for legacy single-account callers."""
    return next(iter(get_auto_start_account_ids(root)), None)


@_registry_locked
def set_auto_start_accounts(account_ids: list[str] | tuple[str, ...], root: Optional[Path] = None) -> list[str]:
    """Persist the accounts whose protection should start with the app."""
    root_path = _account_data_root(root)
    registry = _read_account_registry(root_path)
    selected = []
    account_ids = account_ids or []
    for raw_id in account_ids:
        if raw_id in (None, ""):
            continue
        account_id = _validate_account_id(str(raw_id))
        if not any(str(item.get("id")) == account_id for item in registry["accounts"]):
            raise ValueError("找不到指定 Telegram 帳號")
        if account_id not in selected:
            selected.append(account_id)
    registry["auto_start_account_ids"] = selected
    registry["auto_start_accounts_configured"] = True
    # Keep the old fields in sync for older desktop callers and registries.
    registry["auto_start_account_id"] = selected[0] if selected else None
    registry["auto_start_account_configured"] = True
    selected_set = set(selected)
    for record in registry["accounts"]:
        record["auto_start_protection"] = str(record.get("id")) in selected_set
    _write_account_registry(registry, root_path)
    return list(selected)


@_registry_locked
def set_auto_start_account(account_id: Optional[str], root: Optional[Path] = None) -> Optional[str]:
    """Persist one account for legacy single-account callers."""
    selected = set_auto_start_accounts([] if account_id in (None, "") else [str(account_id)], root)
    return selected[0] if selected else None


@_registry_locked
def _assert_unique_account_identity(account_id: str, user_id, root: Optional[Path] = None) -> None:
    if user_id is None:
        return
    account_id = _validate_account_id(account_id)
    for item in list_accounts(root):
        if str(item.get("id")) == account_id:
            continue
        existing_user_id = item.get("user_id")
        if existing_user_id is not None and str(existing_user_id) == str(user_id):
            raise ValueError("這個 Telegram 帳號已經存在，不能建立第二個共用 Session")


@_registry_locked
def update_account_identity(account_id: str, me, phone: str = "", root: Optional[Path] = None) -> dict:
    root_path = _account_data_root(root)
    account_id = _validate_account_id(account_id)
    registry = _read_account_registry(root_path)
    record = next((item for item in registry["accounts"] if str(item.get("id")) == account_id), None)
    if record is None:
        record = create_account(account_id, root_path)
        registry = _read_account_registry(root_path)
        record = next(item for item in registry["accounts"] if str(item.get("id")) == account_id)
    user_id = getattr(me, "id", None)
    _assert_unique_account_identity(account_id, user_id, root_path)
    record.update({
        "user_id": user_id,
        "username": getattr(me, "username", None) or "",
        "display_name": " ".join(filter(None, [getattr(me, "first_name", ""), getattr(me, "last_name", "")])).strip(),
        "phone_masked": _mask_phone(phone),
        "last_used_at": datetime.now(timezone.utc).isoformat(),
    })
    _write_account_registry(registry, root_path)
    return dict(record)


@_registry_locked
def _commit_account_identity_and_config(
    account_id: str,
    me,
    phone: str,
    cfg: dict,
    root: Optional[Path] = None,
) -> dict:
    """Commit identity metadata and config as one rollback-capable registry transaction."""
    root_path = _account_data_root(root)
    account_id = _validate_account_id(account_id)
    store = account_store(account_id, root_path)
    store.ensure()
    registry_path = _account_registry_path(root_path)
    registry_snapshot = registry_path.read_bytes() if registry_path.exists() else None
    config_snapshot = store.config_file.read_bytes() if store.config_file.exists() else None
    try:
        _assert_unique_account_identity(account_id, getattr(me, "id", None), root_path)
        with account_context(store=store):
            save_config(cfg)
        registry = _read_account_registry(root_path)
        record = next((item for item in registry["accounts"] if str(item.get("id")) == account_id), None)
        if record is None:
            raise ValueError("找不到指定 Telegram 帳號")
        record.update({
            "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None) or "",
            "display_name": " ".join(
                filter(None, [getattr(me, "first_name", ""), getattr(me, "last_name", "")])
            ).strip(),
            "phone_masked": _mask_phone(phone),
            "last_used_at": datetime.now(timezone.utc).isoformat(),
        })
        _write_account_registry(registry, root_path)
        return dict(record)
    except Exception:
        _restore_private_file(store.config_file, config_snapshot)
        _restore_private_file(registry_path, registry_snapshot)
        raise


@_registry_locked
def remove_account(account_id: str, delete_files: bool = True, root: Optional[Path] = None) -> bool:
    root_path = _account_data_root(root)
    account_id = _validate_account_id(account_id)
    registry = _read_account_registry(root_path)
    before = len(registry["accounts"])
    registry["accounts"] = [item for item in registry["accounts"] if str(item.get("id")) != account_id]
    if len(registry["accounts"]) == before:
        return False

    def commit_registry_removal() -> None:
        if registry.get("active_account_id") == account_id:
            registry["active_account_id"] = registry["accounts"][0]["id"] if registry["accounts"] else None
        selected_auto_start_ids = [
            str(item) for item in registry.get("auto_start_account_ids", [])
            if str(item) != account_id
        ]
        if len(selected_auto_start_ids) != len(registry.get("auto_start_account_ids", [])):
            registry["auto_start_account_ids"] = selected_auto_start_ids
            registry["auto_start_account_id"] = selected_auto_start_ids[0] if selected_auto_start_ids else None
        _write_account_registry(registry, root_path)

    if delete_files:
        store = account_store(account_id, root_path)
        with _account_session_lease(store=store, blocking=False):
            account_root = store.root
            if account_root.exists():
                try:
                    shutil.rmtree(account_root, ignore_errors=False)
                except OSError:
                    return False
                if account_root.exists():
                    return False
            commit_registry_removal()
    else:
        commit_registry_removal()
    return True


@_registry_locked
def _migrate_files_transactionally(
    sources,
    destinations,
    registry: dict,
    root: Path,
    target_root: Path,
    target_existed: bool,
) -> None:
    """Commit migrated files, hidden legacy sources, and registry as one transaction."""
    staged = []
    committed = []
    hidden_sources = []
    registry_path = _account_registry_path(root)
    registry_snapshot = registry_path.read_bytes() if registry_path.exists() else None
    try:
        for source, destination in zip(sources, destinations):
            if not source.exists():
                continue
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.parent.chmod(0o700)
            temporary = destination.with_name(f".{destination.name}.migrate-{uuid4().hex}")
            shutil.copy2(source, temporary)
            temporary.chmod(0o600)
            staged.append((source, temporary, destination))

        for _, temporary, destination in staged:
            backup = None
            if destination.exists():
                backup = destination.with_name(f".{destination.name}.backup-{uuid4().hex}")
                os.replace(destination, backup)
                backup.chmod(0o600)
            committed.append((destination, backup))
            os.replace(temporary, destination)
            destination.chmod(0o600)

        for source, _, _ in staged:
            tombstone = source.with_name(f".{source.name}.migrated-{uuid4().hex}")
            os.replace(source, tombstone)
            tombstone.chmod(0o600)
            hidden_sources.append((source, tombstone))

        _write_account_registry(registry, root)
    except Exception as exc:
        rollback_errors = []
        try:
            _restore_private_file(registry_path, registry_snapshot)
        except Exception as rollback_exc:
            rollback_errors.append(rollback_exc)
        for source, tombstone in reversed(hidden_sources):
            try:
                if tombstone.exists():
                    os.replace(tombstone, source)
                    source.chmod(0o600)
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
        for destination, backup in reversed(committed):
            try:
                if destination.exists():
                    destination.unlink()
                if backup is not None and backup.exists():
                    os.replace(backup, destination)
                    destination.chmod(0o600)
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
        for _, temporary, _ in staged:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
        if not target_existed and target_root.exists():
            try:
                shutil.rmtree(target_root, ignore_errors=False)
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
        if rollback_errors:
            raise RuntimeError(
                f"舊版資料遷移失敗且 rollback 不完整：{rollback_errors[0]}"
            ) from exc
        raise RuntimeError(f"舊版資料遷移失敗：{exc}") from exc

    cleanup_errors = []
    for _, tombstone in hidden_sources:
        try:
            tombstone.unlink()
        except FileNotFoundError:
            pass
        except Exception as cleanup_exc:
            cleanup_errors.append(cleanup_exc)
    for _, backup in committed:
        if backup is None:
            continue
        try:
            backup.unlink()
        except FileNotFoundError:
            pass
        except Exception as cleanup_exc:
            cleanup_errors.append(cleanup_exc)
    if cleanup_errors:
        raise RuntimeError(f"舊版資料已遷移，但敏感暫存檔清理失敗：{cleanup_errors[0]}")


@_registry_locked
def ensure_account_registry(root: Optional[Path] = None) -> list:
    """Create the account registry and migrate the old single-account layout once."""
    root_path = _account_data_root(root)
    registry = _read_account_registry(root_path)
    for directory in (root_path, root_path / "accounts"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    for item in registry["accounts"]:
        raw_id = item.get("id")
        if raw_id:
            account_store(str(raw_id), root_path).ensure()
    legacy = _legacy_account_store() if root is None else AccountStore(
        root_path,
        root_path / "user.session",
        root_path / "config.json",
        root_path / "block_log.json",
        data_root=root_path,
    )
    sources = [
        legacy.session_file,
        Path(f"{legacy.session_file}-journal"),
        legacy.config_file,
        legacy.block_log,
        legacy.learned_patterns_file,
    ]
    if not any(path.exists() for path in sources):
        if registry["accounts"]:
            return list(registry["accounts"])
        _write_account_registry(registry, root_path)
        return []

    legacy_cfg = {}
    if legacy.config_file.exists():
        try:
            legacy_cfg = json.loads(legacy.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            legacy_cfg = {}
    user_id = legacy_cfg.get("user_id")
    derived_account_id = f"user-{user_id}" if str(user_id or "").lstrip("-").isdigit() else ""
    existing_record = next(
        (
            item for item in registry["accounts"]
            if (derived_account_id and str(item.get("id")) == derived_account_id)
            or (user_id is not None and str(item.get("user_id")) == str(user_id))
        ),
        None,
    )
    if existing_record is None and user_id is None and len(registry["accounts"]) == 1:
        existing_record = registry["accounts"][0]
    account_id = str(existing_record["id"]) if existing_record else (
        derived_account_id or f"account-{uuid4().hex[:12]}"
    )
    if any(str(item.get("id")) == account_id for item in registry["accounts"]) and not existing_record:
        account_id = f"account-{uuid4().hex[:12]}"
    target = account_store(account_id, root_path)
    target_existed = target.root.exists()
    target.ensure()
    target_files = [
        target.session_file,
        Path(f"{target.session_file}-journal"),
        target.config_file,
        target.block_log,
        target.learned_patterns_file,
    ]
    next_registry = {
        **registry,
        "accounts": [dict(item) for item in registry["accounts"]],
    }
    if existing_record is not None:
        record = next(
            item for item in next_registry["accounts"]
            if str(item.get("id")) == account_id
        )
    else:
        _assert_unique_account_identity(account_id, user_id, root_path)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": account_id,
            "user_id": user_id,
            "username": legacy_cfg.get("username", "") or "",
            "display_name": legacy_cfg.get("display_name", "") or "",
            "phone_masked": _mask_phone(legacy_cfg.get("phone", "")),
            "created_at": now,
            "last_used_at": now,
            "auto_start_protection": bool(legacy_cfg.get("auto_start_protection", False)),
        }
        next_registry["accounts"].append(record)
    next_registry["active_account_id"] = record["id"]
    _migrate_files_transactionally(
        sources,
        target_files,
        next_registry,
        root_path,
        target.root,
        target_existed,
    )
    return list(next_registry["accounts"])
SPAM_PATTERNS = [
    # 中文廣告常見模式
    r"加[\s\-]*[LlvVXx]|[LlvVXx][\s\-]*信",
    r"tg[\s\-]*@?[a-zA-Z0-9_]{3,}",
    r"https?://t\.me/",
    r"@\w{4,}",
    r"兼職|刷單|日入|月入|躺賺|被動收入|在家工作|輕鬆賺",
    r"投資|理財|帶單|跟單|量化|穩賺|穩健|高回報|高收益",
    r"色情|A片|av|成人|裸聊|約炮|援交|包養",
    r"賭|博|彩|casino|betting",
    r"註冊送|免費領|紅包|禮金|優惠碼|推廣碼",
    r"點贊|關注|刷粉|刷讚|漲粉",
    r"售|賣|出|供應|批發|代購|代發",
    # English patterns
    r"promote|promotion|advertisement|sponsor",
    r"click\s*(here|this\s*link|the\s*link)",
    r"earn\s*money|work\s*from\s*home|passive\s*income",
    r"free\s*crypto|free\s*bitcoin|airdrop|giveaway",
    r"limited\s*offer|discount\s*\d{2,}%|buy\s*now",
]

DEFAULT_SCAN_SETTINGS = {
    "private_dialog_limit": 30,
    "private_message_limit": 5,
    "private_days": 14,
}

DEFAULT_MODERATION_POLICY = {
    "delete_private_history_after_block": False,
    "delete_private_history_scope": "self",
    "telegram_notification": {
        "enabled": False,
        "bot_token": "",
        "channel_id": "",
    },
}

TELEGRAM_BOT_API_TIMEOUT = 15

MODERATION_POLICY_SCOPES = {"self", "both"}

SCAN_SETTING_BOUNDS = {
    "private_dialog_limit": (1, 100),
    "private_message_limit": (1, 100),
    "private_days": (1, 365),
}

# ──────────── 工具函式 ────────────

def load_config(account_id: Optional[str] = None):
    config_file = _resolve_account_store(account_id).config_file
    if config_file.exists():
        return json.loads(config_file.read_text(encoding="utf-8"))
    return {}

def save_config(cfg, account_id: Optional[str] = None):
    config_file = _resolve_account_store(account_id).config_file
    config_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private_bytes(
        config_file,
        json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"),
    )

def load_block_log(account_id: Optional[str] = None):
    block_log = _resolve_account_store(account_id).block_log
    if block_log.exists():
        return json.loads(block_log.read_text(encoding="utf-8"))
    return {"blocks": []}

def save_block_log(log, account_id: Optional[str] = None):
    block_log = _resolve_account_store(account_id).block_log
    block_log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private_bytes(
        block_log,
        json.dumps(log, indent=2, ensure_ascii=False).encode("utf-8"),
    )

def load_learned_patterns(account_id: Optional[str] = None):
    f = _resolve_account_store(account_id).learned_patterns_file
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"keywords": [], "patterns": []}

def save_learned_patterns(data, account_id: Optional[str] = None):
    learned_file = _resolve_account_store(account_id).learned_patterns_file
    learned_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private_bytes(
        learned_file,
        json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
    )


def get_learned_patterns(cfg: dict = None, account_id: Optional[str] = None) -> dict:
    cfg = cfg if cfg is not None else load_config(account_id)
    configured = cfg.get("learned_patterns", {}) if isinstance(cfg, dict) else {}
    learned_file = _resolve_account_store(account_id).learned_patterns_file
    stored = {}
    if learned_file.exists():
        stored = json.loads(learned_file.read_text(encoding="utf-8"))
    return {
        "keywords": list(dict.fromkeys([
            *list(configured.get("keywords", [])),
            *list(stored.get("keywords", [])),
        ])),
        "patterns": list(dict.fromkeys([
            *list(configured.get("patterns", [])),
            *list(stored.get("patterns", [])),
        ])),
    }


def remove_learned_pattern(kind: str, value: str, account_id: Optional[str] = None) -> bool:
    if kind not in {"keywords", "patterns"}:
        raise ValueError("kind 必須是 keywords 或 patterns")
    cfg = load_config(account_id)
    learned = get_learned_patterns(cfg, account_id)
    if value not in learned[kind]:
        return False
    learned[kind].remove(value)
    cfg["learned_patterns"] = learned
    save_config(cfg, account_id)
    save_learned_patterns(learned, account_id)
    return True


def get_scan_settings(cfg: dict = None, account_id: Optional[str] = None) -> dict:
    """Return validated scan limits for the SwiftUI sidecar and core callers."""
    cfg = cfg if cfg is not None else load_config(account_id)
    stored = cfg.get("scan_settings", {})
    settings = {}
    for key, default in DEFAULT_SCAN_SETTINGS.items():
        low, high = SCAN_SETTING_BOUNDS[key]
        try:
            value = int(stored.get(key, default))
        except (TypeError, ValueError):
            value = default
        settings[key] = max(low, min(high, value))
    return settings


def update_scan_settings(updates: dict, account_id: Optional[str] = None) -> dict:
    """Validate and persist user-editable scan limits."""
    cfg = load_config(account_id)
    settings = get_scan_settings(cfg)
    stored = cfg.get("scan_settings", {})
    legacy_settings = dict(stored) if isinstance(stored, dict) else {}
    for key, value in (updates or {}).items():
        if key not in SCAN_SETTING_BOUNDS:
            continue
        low, high = SCAN_SETTING_BOUNDS[key]
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        settings[key] = max(low, min(high, value))
    # Keep obsolete group scan keys on disk for backward compatibility, while
    # exposing and updating only the private-message settings.
    cfg["scan_settings"] = {**legacy_settings, **settings}
    save_config(cfg, account_id)
    return settings


def _coerce_config_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off", "null", "none"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def get_moderation_policy(cfg: dict = None, account_id: Optional[str] = None) -> dict:
    """Return the account-scoped post-block private-history policy."""
    cfg = cfg if cfg is not None else load_config(account_id)
    stored = cfg.get("moderation_policy", {}) if isinstance(cfg, dict) else {}
    if not isinstance(stored, dict):
        stored = {}
    scope = stored.get(
        "delete_private_history_scope",
        DEFAULT_MODERATION_POLICY["delete_private_history_scope"],
    )
    if scope not in MODERATION_POLICY_SCOPES:
        scope = DEFAULT_MODERATION_POLICY["delete_private_history_scope"]
    notification = stored.get("telegram_notification", {})
    if not isinstance(notification, dict):
        notification = {}
    return {
        "delete_private_history_after_block": _coerce_config_bool(
            stored.get(
                "delete_private_history_after_block",
                DEFAULT_MODERATION_POLICY["delete_private_history_after_block"],
            )
        ),
        "delete_private_history_scope": scope,
        "telegram_notification": {
            "enabled": _coerce_config_bool(notification.get("enabled"), False),
            "bot_token": str(notification.get("bot_token") or "").strip(),
            "channel_id": str(notification.get("channel_id") or "").strip(),
        },
    }


def update_moderation_policy(updates: dict, account_id: Optional[str] = None) -> dict:
    """Validate and persist the account-scoped post-block policy."""
    if not isinstance(updates, dict):
        raise ValueError("updates 必須是 JSON object")
    cfg = load_config(account_id)
    policy = get_moderation_policy(cfg)
    if "delete_private_history_after_block" in updates:
        policy["delete_private_history_after_block"] = _coerce_config_bool(
            updates["delete_private_history_after_block"]
        )
    if "delete_private_history_scope" in updates:
        scope = str(updates["delete_private_history_scope"] or "").strip().lower()
        if scope not in MODERATION_POLICY_SCOPES:
            raise ValueError("delete_private_history_scope 必須是 self 或 both")
        policy["delete_private_history_scope"] = scope
    if "telegram_notification" in updates:
        notification_updates = updates["telegram_notification"]
        if not isinstance(notification_updates, dict):
            raise ValueError("telegram_notification 必須是 JSON object")
        notification = dict(policy["telegram_notification"])
        if "enabled" in notification_updates:
            notification["enabled"] = _coerce_config_bool(notification_updates["enabled"])
        if "bot_token" in notification_updates:
            notification["bot_token"] = str(notification_updates["bot_token"] or "").strip()
        if "channel_id" in notification_updates:
            notification["channel_id"] = str(notification_updates["channel_id"] or "").strip()
        policy["telegram_notification"] = notification
    cfg["moderation_policy"] = policy
    save_config(cfg, account_id)
    return policy


def _telegram_api_error_message(payload: str) -> str:
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, dict) and decoded.get("description"):
        return str(decoded["description"])
    return "Telegram Bot API 回傳錯誤"


def _telegram_ssl_context() -> ssl.SSLContext:
    """Use a bundled CA set so the frozen Python sidecar can verify HTTPS."""
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def send_telegram_bot_message(
    bot_token: str,
    channel_id: str,
    text: str,
    timeout: int = TELEGRAM_BOT_API_TIMEOUT,
) -> dict:
    """Send one message through the Telegram Bot API without exposing credentials."""
    bot_token = str(bot_token or "").strip()
    channel_id = str(channel_id or "").strip()
    if not bot_token:
        raise ValueError("Bot Token 不可為空")
    if not channel_id:
        raise ValueError("Channel ID 不可為空")

    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": channel_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=_telegram_ssl_context(),
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram Bot API 請求失敗：{_telegram_api_error_message(body)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram Bot API 連線失敗：{exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Telegram Bot API 連線逾時") from exc

    try:
        result = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Telegram Bot API 回傳格式無效") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            f"Telegram Bot API 請求失敗：{_telegram_api_error_message(raw)}"
        )
    return result


def test_telegram_notification(bot_token: str, channel_id: str) -> dict:
    """Send a test message to validate the Bot Token and Channel ID."""
    send_telegram_bot_message(
        bot_token,
        channel_id,
        "✅ TeleShield 測試通知\n\nBot Token 與 Channel ID 已成功驗證。",
    )
    return {"sent": True}


def _format_notification_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone()
    raw_offset = local.strftime("%z")
    offset = f"{raw_offset[:3]}:{raw_offset[3:]}" if len(raw_offset) == 5 else raw_offset
    return f"{local.strftime('%Y-%m-%d %H:%M:%S')} {offset}"


def build_telegram_block_notification(
    user_id: int,
    name: str,
    reason: str,
    deletion: Optional[dict],
    block_time: Optional[datetime] = None,
) -> str:
    """Build the stable notification text sent after a private block."""
    block_time = block_time or datetime.now(timezone.utc)
    display_name = " ".join(str(name or "").split()) or "未知名稱"
    display_reason = " ".join(str(reason or "未記錄原因").split())[:1000]
    delete_enabled = deletion is not None
    if deletion is None:
        deletion_status = "未執行"
    else:
        deletion_status = "是" if deletion.get("succeeded") else "否"
    return "\n".join(
        [
            "🚫 TeleShield 封鎖通知",
            "",
            f"封鎖名稱 & ID: {display_name} ({user_id})",
            f"封鎖原因: {display_reason}",
            f"封鎖時間: {_format_notification_time(block_time)}",
            f"是否開啟刪除對話: {'是' if delete_enabled else '否'}",
            f"是否已經刪除對話: {deletion_status}",
        ]
    )


async def _notify_telegram_after_block(
    user_id: int,
    name: str,
    reason: str,
    deletion: Optional[dict],
    cfg: Optional[dict],
    block_time: datetime,
) -> dict:
    policy = get_moderation_policy(cfg)
    notification_policy = policy["telegram_notification"]
    result = {
        "enabled": bool(notification_policy["enabled"]),
        "sent": False,
    }
    if not result["enabled"]:
        return result
    if not notification_policy["bot_token"] or not notification_policy["channel_id"]:
        result["error"] = "Bot Token 或 Channel ID 未設定"
        return result

    message = build_telegram_block_notification(
        user_id,
        name,
        reason,
        deletion,
        block_time=block_time,
    )
    try:
        await asyncio.to_thread(
            send_telegram_bot_message,
            notification_policy["bot_token"],
            notification_policy["channel_id"],
            message,
        )
        result["sent"] = True
    except Exception as exc:
        # Notification failures must never roll back or hide a successful block.
        result["error"] = str(exc)[:200]
    return result


def learn_text(text: str, account_id: Optional[str] = None) -> dict:
    """Learn a user-supplied spam example without printing to stdout."""
    text = (text or "").strip()
    if not text:
        raise ValueError("請提供要學習的廣告文字")
    source_text = text
    text = normalize_traditional(text)

    cfg = load_config(account_id)
    learned = get_learned_patterns(cfg, account_id)

    tokens = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
    stop_words = {
        "我們", "他們", "可以", "沒有", "這個", "那個", "什麼", "因為", "所以", "但是",
        "如果", "雖然", "然後", "而且", "或者", "不過", "還是", "就是", "不是", "一個",
    }
    added_keywords = []
    for token in tokens:
        if token not in stop_words and token not in learned["keywords"]:
            learned["keywords"].append(token)
            added_keywords.append(token)

    added_patterns = []
    match = re.search(r"(加微信|加\s*(?:V|v)|加|薇|威|wechat|line|whatsapp)[-:\s]*([a-zA-Z0-9_]{4,})", text)
    if match:
        pattern = re.escape(match.group(2))
        if pattern not in learned["patterns"]:
            learned["patterns"].append(pattern)
            added_patterns.append(pattern)

    for url in re.findall(r"https?://[^\s]{4,}", text):
        pattern = re.escape(url[:20])
        if pattern not in learned["patterns"]:
            learned["patterns"].append(pattern)
            added_patterns.append(pattern)

    if not added_keywords and not added_patterns:
        pattern = re.escape(text[:30])
        if pattern not in learned["patterns"]:
            learned["patterns"].append(pattern)
            added_patterns.append(pattern)

    cfg["learned_patterns"] = learned
    save_config(cfg, account_id)
    save_learned_patterns(learned, account_id)
    return {
        "text": source_text,
        "normalized_text": text,
        "added_keywords": added_keywords,
        "added_patterns": added_patterns,
        "total_keywords": len(learned["keywords"]),
        "total_patterns": len(learned["patterns"]),
    }


def _parse_log_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_report(period: str = "day", now: datetime = None, account_id: Optional[str] = None) -> dict:
    """Return a structured report suitable for a GUI or an export."""
    period = period if period in {"day", "week", "all"} else "day"
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if period == "day":
        cutoff = now - timedelta(days=1)
        label = "過去 24 小時"
    elif period == "week":
        cutoff = now - timedelta(days=7)
        label = "過去 7 天"
    else:
        cutoff = datetime.min.replace(tzinfo=timezone.utc)
        label = "全部記錄"

    records = []
    for record in load_block_log(account_id).get("blocks", []):
        try:
            record_time = _parse_log_time(record["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if record_time > cutoff:
            records.append(dict(record))
    records.sort(key=lambda item: item.get("time", ""), reverse=True)

    by_source = defaultdict(int)
    by_reason = defaultdict(int)
    trend = defaultdict(int)
    for record in records:
        raw_source = record.get("source", "private")
        source = "private" if raw_source == "scan" else raw_source
        by_source[source] += 1
        reason = record.get("reason", "")
        by_reason[reason[:20] or "未分類"] += 1
        trend[record.get("time", "")[:10]] += 1

    return {
        "period": period,
        "label": label,
        "total": len(records),
        "by_source": dict(sorted(by_source.items())),
        "by_reason": dict(sorted(by_reason.items(), key=lambda item: (-item[1], item[0]))[:5]),
        "trend": dict(sorted(trend.items())),
        "records": records,
    }


def get_block_records(query: str = "", source: str = "all", limit: int = 500, account_id: Optional[str] = None) -> list:
    """Filter persisted block records for the history table."""
    query = (query or "").strip().lower()
    source = source or "all"
    records = []
    for record in reversed(load_block_log(account_id).get("blocks", [])):
        raw_source = record.get("source")
        if source == "private":
            matches_source = raw_source in {"private", "scan"}
        else:
            matches_source = source == "all" or raw_source == source
        if not matches_source:
            continue
        haystack = " ".join(str(record.get(key, "")) for key in ("user_id", "name", "reason", "source"))
        if query and query not in haystack.lower():
            continue
        records.append(dict(record))
        if len(records) >= max(1, int(limit)):
            break
    return records


def export_block_records(path: str, query: str = "", source: str = "all", fmt: str = "json", account_id: Optional[str] = None) -> int:
    """Export filtered block records as JSON or CSV; return row count."""
    records = get_block_records(query, source, account_id=account_id)
    output = Path(path).expanduser()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if fmt.lower() == "csv" or output.suffix.lower() == ".csv":
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["time", "source", "user_id", "name", "reason"])
            writer.writeheader()
            writer.writerows({key: row.get(key, "") for key in writer.fieldnames} for row in records)
    else:
        output.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    output.chmod(0o600)
    return len(records)


def list_entries(list_type: str, query: str = "", account_id: Optional[str] = None) -> list:
    if list_type not in {"whitelist", "blacklist"}:
        raise ValueError("list_type 必須是 whitelist 或 blacklist")
    query = (query or "").strip().lower()
    result = []
    for user_id, info in sorted(load_config(account_id).get(list_type, {}).items()):
        row = {
            "user_id": str(user_id),
            "username": info.get("username", ""),
            "added": info.get("added", ""),
            "reason": info.get("reason", ""),
        }
        if query and query not in " ".join(str(value) for value in row.values()).lower():
            continue
        result.append(row)
    return result


def upsert_list_entry(list_type: str, user_id: str, username: str = "", reason: str = "manual", account_id: Optional[str] = None) -> dict:
    if list_type not in {"whitelist", "blacklist"}:
        raise ValueError("list_type 必須是 whitelist 或 blacklist")
    user_id = str(user_id).strip()
    if not user_id or not user_id.lstrip("-").isdigit():
        raise ValueError("使用者 ID 必須是 numeric Telegram user ID")
    cfg = load_config(account_id)
    entries = cfg.setdefault(list_type, {})
    previous = entries.get(user_id, {})
    entries[user_id] = {
        "added": previous.get("added") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "username": (username or previous.get("username", "")).lstrip("@"),
        "reason": reason or previous.get("reason", "manual"),
    }
    save_config(cfg, account_id)
    return list_entries(list_type, user_id, account_id=account_id)[0]


def remove_list_entry(list_type: str, user_id: str, account_id: Optional[str] = None) -> bool:
    if list_type not in {"whitelist", "blacklist"}:
        raise ValueError("list_type 必須是 whitelist 或 blacklist")
    cfg = load_config(account_id)
    entries = cfg.setdefault(list_type, {})
    existed = str(user_id) in entries
    entries.pop(str(user_id), None)
    if existed:
        save_config(cfg, account_id)
    return existed


def export_list_entries(path: str, list_type: str, fmt: str = "", account_id: Optional[str] = None) -> int:
    rows = list_entries(list_type, account_id=account_id)
    output = Path(path).expanduser()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (fmt or output.suffix.lstrip(".")).lower() == "csv":
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["user_id", "username", "added", "reason"])
            writer.writeheader()
            writer.writerows(rows)
    else:
        output.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    output.chmod(0o600)
    return len(rows)


def import_list_entries(path: str, list_type: str, replace: bool = False, account_id: Optional[str] = None) -> int:
    if list_type not in {"whitelist", "blacklist"}:
        raise ValueError("list_type 必須是 whitelist 或 blacklist")
    source = Path(path).expanduser()
    if source.suffix.lower() == ".csv":
        with source.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    else:
        data = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            rows = [dict(info, user_id=user_id) for user_id, info in data.items()]
        else:
            rows = data
    cfg = load_config(account_id)
    if replace:
        cfg[list_type] = {}
        save_config(cfg, account_id)
    imported = 0
    for row in rows:
        try:
            upsert_list_entry(list_type, row.get("user_id", ""), row.get("username", ""), row.get("reason", "import"), account_id=account_id)
            imported += 1
        except ValueError:
            continue
    return imported


def clear_local_session(remove_credentials: bool = False, account_id: Optional[str] = None) -> None:
    """Delete one account's local Telegram session and identity fields only."""
    store = _resolve_account_store(account_id)
    with _account_session_lease(store=store):
        effective_account_id = account_id or store.account_id
        for path in (store.session_file, Path(f"{store.session_file}-journal")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        cfg = load_config(account_id)
        for key in ("phone", "user_id", "username", "last_scan"):
            cfg.pop(key, None)
        if remove_credentials:
            cfg.pop("api_id", None)
            cfg.pop("api_hash", None)
        save_config(cfg, account_id)
        if effective_account_id:
            root_path = store.data_root
            with _account_registry_transaction(root_path):
                registry = _read_account_registry(root_path)
                for record in registry["accounts"]:
                    if str(record.get("id")) == str(effective_account_id):
                        record.update({"user_id": None, "username": "", "display_name": "", "phone_masked": ""})
                        break
                _write_account_registry(registry, root_path)


def find_tesseract() -> Optional[str]:
    """Find a system or bundled Tesseract executable without exposing secrets."""
    candidates = []
    configured = os.getenv("TELESHIELD_TESSERACT_PATH")
    if configured:
        candidates.append(Path(configured).expanduser())
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundle = Path(bundle_root)
        candidates.extend([
            bundle / "tesseract-runtime" / "bin" / "tesseract",
            bundle / "tesseract-runtime" / "tesseract",
            bundle / "tesseract",
        ])
    system_path = shutil.which("tesseract")
    if system_path:
        candidates.append(Path(system_path))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def get_ocr_status() -> dict:
    path = find_tesseract()
    bundled = bool(path and getattr(sys, "_MEIPASS", None) and str(path).startswith(str(sys._MEIPASS)))
    return {"available": bool(path), "bundled": bundled, "languages": ["chi_sim", "chi_tra", "eng"] if path else []}

def is_spam(text: str, cfg: dict = None, account_id: Optional[str] = None) -> bool:
    """檢查文字是否包含廣告模式（含自訂模式）"""
    if not text:
        return False
    text = normalize_traditional(text)
    # 內建模式
    for pattern in SPAM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    # 自訂學習模式
    if cfg is not None:
        lp = get_learned_patterns(cfg, account_id=account_id)
        for p in lp.get("patterns", []):
            try:
                if re.search(normalize_traditional(p), text, re.IGNORECASE):
                    return True
            except:
                continue
        for kw in lp.get("keywords", []):
            if normalize_traditional(kw).lower() in text.lower():
                return True
    return False

def is_blacklisted(user_id: int, cfg: dict) -> bool:
    return str(user_id) in cfg.get("blacklist", {})

def is_whitelisted(user_id: int, cfg: dict) -> bool:
    return str(user_id) in cfg.get("whitelist", {})

def log_block(
    user_id: int,
    name: str,
    reason: str,
    source: str = "private",
    details: Optional[dict] = None,
    timestamp: Optional[datetime] = None,
):
    log = load_block_log()
    timestamp = timestamp or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    record = {
        "user_id": user_id,
        "name": name,
        "reason": reason[:200],
        "source": source,
        "time": timestamp.isoformat(),
    }
    if details:
        record["details"] = details
    log["blocks"].append(record)
    # 保留最近 500 筆
    if len(log["blocks"]) > 500:
        log["blocks"] = log["blocks"][-500:]
    save_block_log(log)


async def delete_private_history_after_block(client, entity, cfg: Optional[dict] = None) -> Optional[dict]:
    """Delete one private dialog only after its block request succeeded."""
    policy = get_moderation_policy(cfg)
    if not policy["delete_private_history_after_block"]:
        return None
    scope = policy["delete_private_history_scope"]
    result = {
        "requested": True,
        "scope": scope,
        "succeeded": False,
    }
    try:
        await client.delete_dialog(entity, revoke=scope == "both")
        result["succeeded"] = True
    except Exception as exc:
        # The block is intentionally not rolled back. The caller records this
        # result so the user can see that the cleanup request failed.
        result["error"] = str(exc)
    return result


async def block_private_user(
    client,
    entity,
    name: str,
    reason: str,
    source: str,
    cfg: Optional[dict] = None,
) -> Optional[dict]:
    """Block a private user and then apply the account's cleanup policy."""
    from telethon.tl.functions.contacts import BlockRequest

    await client(BlockRequest(id=entity.id))
    block_time = datetime.now(timezone.utc)
    deletion = await delete_private_history_after_block(client, entity, cfg)
    notification = await _notify_telegram_after_block(
        entity.id,
        name,
        reason,
        deletion,
        cfg,
        block_time,
    )
    details = {}
    if deletion:
        details["private_history_deletion"] = deletion
    if notification["enabled"]:
        details["telegram_notification"] = notification
    log_block(
        entity.id,
        name,
        reason,
        source,
        details=details or None,
        timestamp=block_time,
    )
    return deletion

def ocr_image(image_path: str) -> str:
    """Extract Chinese/English text when a system or bundled Tesseract exists."""
    try:
        from PIL import Image
        import pytesseract

        tesseract_path = find_tesseract()
        if not tesseract_path:
            return ""
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        config = ""
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            tessdata = Path(bundle_root) / "tesseract-runtime" / "share" / "tessdata"
            if tessdata.is_dir():
                config = f'--tessdata-dir "{tessdata}"'
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang="chi_sim+chi_tra+eng", config=config)
        return normalize_traditional(text.strip())
    except Exception:
        return ""


async def check_photo(client, msg) -> str:
    if not msg or not msg.photo:
        return ""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp = f.name
        await client.download_media(msg, file=tmp)
        return ocr_image(tmp)
    except Exception:
        return ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

# ──────────── Telegram authentication ────────────

@_session_leased
async def authenticate(
    api_id: str,
    api_hash: str,
    phone: str,
    code_callback,
    password_callback,
    status_callback=None,
    account_id: Optional[str] = None,
):
    """Authenticate one personal Telegram account in its isolated store."""
    with account_context(account_id):
        return await _authenticate(api_id, api_hash, phone, code_callback, password_callback, status_callback)


async def _authenticate(
    api_id: str,
    api_hash: str,
    phone: str,
    code_callback,
    password_callback,
    status_callback=None,
):
    """Authenticate the personal Telegram client through app callbacks."""
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    store = _resolve_account_store()
    store.ensure()
    session_paths = [store.session_file, Path(f"{store.session_file}-journal")]
    session_backups = {}
    for path in session_paths:
        if not path.exists():
            continue
        backup = path.with_name(f".{path.name}.backup-{uuid4().hex}")
        shutil.copy2(path, backup)
        backup.chmod(0o600)
        session_backups[path] = backup
    restore_session = True
    client = None
    try:
        previous_cfg = load_config()
        previous_phone = str(previous_cfg.get("phone", "") or "").strip()
        requested_phone = str(phone or "").strip()
        if previous_phone and requested_phone and previous_phone != requested_phone:
            for path in session_paths:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        client = TelegramClient(str(store.session_file), int(api_id), api_hash)
        await client.connect()
        store.ensure()
        if not await client.is_user_authorized():
            sent_code = await client.send_code_request(phone)
            if status_callback:
                delivery = type(getattr(sent_code, "type", None)).__name__
                delivery = delivery.replace("SentCodeType", "") or "未知方式"
                details = []
                next_type = getattr(sent_code, "next_type", None)
                if next_type is not None:
                    next_delivery = type(next_type).__name__.replace("SentCodeType", "")
                    if next_delivery:
                        details.append(f"下一個可用方式：{next_delivery}")
                timeout = getattr(sent_code, "timeout", None)
                if timeout is not None:
                    details.append(f"等待時間：{timeout} 秒")
                status_callback("；".join([delivery, *details]))
            code_value = await code_callback()
            if not code_value:
                raise ValueError("Telegram 驗證碼不可為空")
            try:
                await client.sign_in(phone=phone, code=code_value)
            except SessionPasswordNeededError:
                password_value = await password_callback()
                if not password_value:
                    raise ValueError("Telegram 兩步驟驗證密碼不可為空")
                await client.sign_in(password=password_value)

        me = await client.get_me()
        cfg = load_config()
        cfg.update({
            "api_id": int(api_id),
            "api_hash": api_hash,
            "phone": phone,
            "user_id": me.id,
            "username": me.username,
        })
        cfg.setdefault("blocked_count", 0)
        cfg.setdefault("last_scan", None)
        cfg.setdefault("whitelist", {})
        cfg.setdefault("blacklist", {})
        cfg.setdefault("learned_patterns", {"keywords": [], "patterns": []})
        cfg.setdefault("scan_settings", DEFAULT_SCAN_SETTINGS.copy())
        cfg.setdefault(
            "moderation_policy",
            {
                **DEFAULT_MODERATION_POLICY,
                "telegram_notification": dict(DEFAULT_MODERATION_POLICY["telegram_notification"]),
            },
        )
        cfg.setdefault("auto_start_protection", False)
        if store.account_id:
            _commit_account_identity_and_config(
                store.account_id,
                me,
                phone,
                cfg,
                root=store.data_root,
            )
        else:
            save_config(cfg)
        restore_session = False
        return me
    finally:
        try:
            if client is not None:
                await client.disconnect()
        finally:
            cleanup_failure = None
            if restore_session:
                for path in session_paths:
                    backup = session_backups.get(path)
                    if backup is not None and backup.exists():
                        shutil.copy2(backup, path)
                        path.chmod(0o600)
                    elif path.exists():
                        try:
                            path.unlink()
                        except OSError as exc:
                            try:
                                _write_private_bytes(path, b"")
                            except OSError as scrub_exc:
                                raise RuntimeError("Session 清理失敗，而且無法清空授權資料") from scrub_exc
                            cleanup_failure = exc
            for backup in session_backups.values():
                try:
                    backup.unlink()
                except FileNotFoundError:
                    pass
            store.ensure()
            if cleanup_failure is not None:
                raise RuntimeError("Session 清理失敗；授權資料已清空，請手動刪除空檔") from cleanup_failure


@_session_leased
async def logout_account(remove_credentials: bool = False, account_id: Optional[str] = None) -> bool:
    """Log out one Telegram session and then clear only that account's identity data."""
    with account_context(account_id):
        return await _logout_account(remove_credentials)


async def _logout_account(remove_credentials: bool = False) -> bool:
    """Log out the Telegram session and then clear local identity data."""
    from telethon import TelegramClient

    store = _resolve_account_store()
    store.ensure()
    cfg = load_config()
    if not cfg.get("api_id"):
        clear_local_session(remove_credentials=remove_credentials)
        return False
    client = TelegramClient(str(store.session_file), cfg["api_id"], cfg["api_hash"])
    connected = False
    logged_out = False
    try:
        await client.connect()
        connected = True
        store.ensure()
        if await client.is_user_authorized():
            await client.log_out()
            logged_out = True
    finally:
        try:
            if connected:
                await client.disconnect()
        finally:
            store.ensure()
    clear_local_session(remove_credentials=remove_credentials)
    return logged_out


# ──────────── 歷史訊息掃描核心 ────────────

@_session_leased
async def scan_history(
    scope: str = "private",
    dry_run: bool = False,
    progress_callback=None,
    cancel_event=None,
    account_id: Optional[str] = None,
):
    """Scan recent history using one account's isolated client and storage."""
    with account_context(account_id):
        return await _scan_history(
            scope,
            dry_run,
            progress_callback,
            cancel_event,
            account_id=account_id,
        )


async def _scan_history(
    scope: str = "private",
    dry_run: bool = False,
    progress_callback=None,
    cancel_event=None,
    account_id: Optional[str] = None,
):
    """Scan recent private history and optionally apply moderation.

    This API is UI-friendly: it never prompts on stdin, reports progress through
    ``progress_callback``, and checks ``cancel_event`` between Telegram calls.
    The app-facing API never prompts on stdin and reports progress through the
    sidecar event stream.
    """
    from telethon import TelegramClient
    from telethon.tl.functions.contacts import GetContactsRequest
    from telethon.tl.types import User

    if scope != "private":
        raise ValueError("目前只支援 private 歷史訊息掃描")

    store = _resolve_account_store()
    store.ensure()
    cfg = load_config()
    if not cfg.get("api_id"):
        raise RuntimeError("尚未登入 Telegram")

    result = {
        "account_id": account_id or store.account_id,
        "scope": scope,
        "dry_run": dry_run,
        "dialogs_seen": 0,
        "dialogs_scanned": 0,
        "messages_scanned": 0,
        "matched": 0,
        "acted": 0,
        "private_history_deletions": 0,
        "private_history_deletions_succeeded": 0,
        "errors": [],
        "findings": [],
        "cancelled": False,
    }
    now = datetime.now(timezone.utc)
    scan_settings = get_scan_settings(cfg)
    scan_account_id = account_id or store.account_id

    def cancelled() -> bool:
        return bool(cancel_event and cancel_event.is_set())

    def progress(message: str) -> None:
        if progress_callback:
            progress_callback(message)

    def add_error(message: str) -> None:
        result["errors"].append(message)
        progress(f"⚠️ {message}")

    client = TelegramClient(str(store.session_file), cfg["api_id"], cfg["api_hash"])
    connected = False
    try:
        progress("正在連線 Telegram…")
        await client.connect()
        connected = True
        store.ensure()
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram Session 已失效，請先重新登入")
        if cancelled():
            result["cancelled"] = True
            return result

        contacts = (await client(GetContactsRequest(hash=0))).users
        contact_ids = {contact.id for contact in contacts}

        if scope == "private":
            eligible_dialogs = []
            dialog_iterator = getattr(client, "iter_dialogs", None)
            if callable(dialog_iterator):
                # No folder is specified so Telethon includes archived dialogs.
                # The limit is applied after local eligibility filtering below;
                # groups, channels, contacts, and other excluded dialogs do not
                # consume the private-dialog scan budget.
                async for dialog in dialog_iterator(limit=None):
                    result["dialogs_seen"] += 1
                    if cancelled():
                        result["cancelled"] = True
                        break
                    entity = dialog.entity
                    if (
                        not isinstance(entity, User)
                        or entity.is_self
                        or entity.bot
                        or entity.id in contact_ids
                        or is_whitelisted(entity.id, cfg)
                    ):
                        continue
                    eligible_dialogs.append(dialog)
                    if len(eligible_dialogs) >= scan_settings["private_dialog_limit"]:
                        break
            else:
                # Keep lightweight test doubles and older compatible clients
                # working; the production Telethon client always has iter_dialogs.
                dialogs = await client.get_dialogs(limit=scan_settings["private_dialog_limit"])
                for dialog in dialogs:
                    result["dialogs_seen"] += 1
                    if cancelled():
                        result["cancelled"] = True
                        break
                    entity = dialog.entity
                    if (
                        not isinstance(entity, User)
                        or entity.is_self
                        or entity.bot
                        or entity.id in contact_ids
                        or is_whitelisted(entity.id, cfg)
                    ):
                        continue
                    eligible_dialogs.append(dialog)

            if result["cancelled"]:
                return result

            total = len(eligible_dialogs)
            for index, dialog in enumerate(eligible_dialogs, 1):
                if cancelled():
                    result["cancelled"] = True
                    break
                entity = dialog.entity
                result["dialogs_scanned"] += 1
                progress(f"掃描私訊 {index}/{total}…")
                try:
                    messages = await client.get_messages(entity, limit=scan_settings["private_message_limit"])
                except Exception as exc:
                    add_error(f"私訊讀取失敗（{entity.id}）：{exc}")
                    continue
                result["messages_scanned"] += len(messages)

                for message in messages:
                    if cancelled():
                        result["cancelled"] = True
                        break
                    if not message:
                        continue
                    if message.date and message.date < now - timedelta(days=scan_settings["private_days"]):
                        continue
                    reason = message.text or ""
                    if not is_spam(reason, cfg, account_id=scan_account_id) and message.photo:
                        ocr_text = await check_photo(client, message)
                        if ocr_text and is_spam(ocr_text, cfg, account_id=scan_account_id):
                            reason = f"[OCR] {ocr_text[:100]}"
                    if not reason or not is_spam(reason, cfg, account_id=scan_account_id):
                        continue

                    result["matched"] += 1
                    name = f"{entity.first_name or ''} {entity.last_name or ''}".strip()
                    finding = {
                        "user_id": entity.id,
                        "name": name or str(entity.id),
                        "reason": reason[:120],
                    }
                    result["findings"].append(finding)
                    progress(f"⚠️ 發現私訊廣告：{finding['name']}")
                    if dry_run:
                        break
                    try:
                        deletion = await block_private_user(
                            client,
                            entity,
                            finding["name"],
                            reason,
                            "scan",
                            cfg,
                        )
                        result["acted"] += 1
                        if deletion:
                            result["private_history_deletions"] += 1
                            if deletion["succeeded"]:
                                result["private_history_deletions_succeeded"] += 1
                            else:
                                add_error(
                                    f"封鎖成功，但刪除私訊紀錄失敗（{entity.id}）：{deletion.get('error', '未知錯誤')}"
                                )
                        progress(f"✅ 已封鎖：{finding['name']}")
                    except Exception as exc:
                        add_error(f"封鎖失敗（{entity.id}）：{exc}")
                    break
        if not dry_run:
            cfg["blocked_count"] = cfg.get("blocked_count", 0) + result["acted"]
            cfg["last_scan"] = now.isoformat()
        else:
            cfg["last_preview"] = now.isoformat()
        save_config(cfg)
        return result
    finally:
        try:
            if connected:
                await client.disconnect()
        finally:
            store.ensure()


# ──────────── 即時監聽（私訊） ────────────

async def listen(
    stop_event: Optional[asyncio.Event] = None,
    ready_callback=None,
    account_id: Optional[str] = None,
) -> bool:
    """Run realtime protection for exactly one account."""
    with account_context(account_id) as store:
        with _account_session_lease(store=store):
            return await _listen(stop_event, ready_callback)


async def _listen(stop_event: Optional[asyncio.Event] = None, ready_callback=None) -> bool:
    from telethon import TelegramClient
    from telethon.tl.types import User
    from telethon.tl.functions.contacts import GetContactsRequest

    store = _resolve_account_store()
    store.ensure()
    cfg = load_config()
    if not cfg.get("api_id"):
        print("❌ 尚未設定")
        return False

    print("👂 TeleShield 即時監聽啟動中...")
    print("    ✅ 私訊廣告 → 自動封鎖")
    print("    📸 OCR 支援 → 純圖片廣告也辨識")
    print("    按 Ctrl+C 停止\n")

    client = TelegramClient(str(store.session_file), cfg["api_id"], cfg["api_hash"])

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        msg = event.message
        if not msg or not msg.sender_id:
            return

        # Reload settings so desktop and Bot controls take effect without
        # restarting the long-running Telethon client.
        cfg = load_config()

        sender_id = msg.sender_id
        chat = await event.get_chat()
        sender = await event.get_sender()

        # 跳過自己
        if hasattr(sender, 'is_self') and sender.is_self:
            return

        # 檢查黑名單（無論在哪）
        if is_blacklisted(sender_id, cfg):
            if not isinstance(chat, User):
                return
            try:
                name = f"{getattr(chat, 'first_name', '') or ''} {getattr(chat, 'last_name', '') or ''}".strip()
                deletion = await block_private_user(
                    client,
                    chat,
                    name or str(sender_id),
                    "黑名單",
                    "blacklist",
                    cfg,
                )
                cfg["blocked_count"] = cfg.get("blocked_count", 0) + 1
                save_config(cfg)
                if deletion and not deletion["succeeded"]:
                    print(f"     ⚠️ 黑名單封鎖成功，但刪除私訊紀錄失敗: {deletion.get('error', '未知錯誤')}")
            except Exception as exc:
                print(f"     ❌ 黑名單處理失敗: {exc}")
            return

        if is_whitelisted(sender_id, cfg):
            return

        # 私訊處理
        if isinstance(chat, User):
            sender = chat
            if sender_id == cfg.get("user_id"):
                return
            if sender.bot:
                return

            # 檢查聯絡人
            try:
                contacts = (await client(GetContactsRequest(hash=0))).users
                contact_ids = {c.id for c in contacts}
                if sender_id in contact_ids:
                    return
            except:
                pass

            # 檢測
            spam_text = msg.text or ""
            is_spam_by_text = is_spam(spam_text, cfg, account_id=store.account_id)
            ocr_found_spam = False
            if not is_spam_by_text and msg.photo:
                ocr_text = await check_photo(client, msg)
                if ocr_text and is_spam(ocr_text, cfg, account_id=store.account_id):
                    ocr_found_spam = True
                    spam_text = ocr_text[:100]

            if not is_spam_by_text and not ocr_found_spam:
                return

            name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
            uname = f"@{sender.username}" if sender.username else ""
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            icon = "📸" if ocr_found_spam else ""
            print(f"\n[{ts}] {icon}⚠️  私訊廣告: {name} {uname}")
            print(f"    {spam_text[:100]}")

            try:
                deletion = await block_private_user(
                    client,
                    sender,
                    name,
                    spam_text,
                    "private",
                    cfg,
                )
                cfg["blocked_count"] = cfg.get("blocked_count", 0) + 1
                save_config(cfg)
                print(f"     ✅ 封鎖（累計 {cfg['blocked_count']}）")
                if deletion and not deletion["succeeded"]:
                    print(f"     ⚠️ 封鎖成功，但刪除私訊紀錄失敗: {deletion.get('error', '未知錯誤')}")
            except Exception as e:
                print(f"     ❌ 封鎖失敗: {e}")
            return

    try:
        # Do not call ``start(phone=...)`` here: in a windowless packaged app
        # Telethon could fall back to stdin prompts if the Session expires.
        await client.connect()
        store.ensure()
        if not await client.is_user_authorized():
            print("❌ Telegram Session 已失效，請從桌面 App 重新登入")
            return False
        if ready_callback:
            ready_callback()
        print(f"✅ TeleShield 已上線 — 監聽中...")
        run_task = asyncio.create_task(client.run_until_disconnected())
        if stop_event is None:
            await run_task
        else:
            stop_task = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {run_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if run_task in done:
                # Retrieve listener exceptions so asyncio does not emit an
                # unhandled "Task exception was never retrieved" warning.
                await run_task
            elif stop_task in done:
                await client.disconnect()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    except KeyboardInterrupt:
        print("\n\n👋 已停止")
        return True
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        return False
    finally:
        try:
            await client.disconnect()
        finally:
            store.ensure()

    return True

# This module is intentionally library-only.  The macOS SwiftUI app starts
# ``core_service.py`` as the only supported executable entry point.
