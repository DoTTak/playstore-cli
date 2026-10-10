"""Where login and device registration results are stored.

The AAS token is a long-lived token roughly as sensitive as the account password, so files are mode 600.
The default location is ~/.config/playstore-cli; override it with PLAYSTORE_HOME.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("PLAYSTORE_HOME") or Path.home() / ".config" / "playstore-cli")


def account_path() -> Path:
    return home() / "account.json"


def device_path() -> Path:
    return home() / "device.json"


def session_path() -> Path:
    return home() / "session.json"


def chrome_profile_dir() -> Path:
    return home() / "chrome-profile"


def _write_private(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)  # O_CREAT's mode does not apply to a file that already exists
    return path


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def save_account(email: str, aas_token: str, path: Path | None = None) -> Path:
    data = {"email": email, "aasToken": aas_token, "createdAt": int(time.time())}
    return _write_private(path or account_path(), data)


def load_account(path: Path | None = None) -> dict | None:
    return _read(path or account_path())


def save_device(data: dict, path: Path | None = None) -> Path:
    """The virtual device registered by checkin (profile, gsfId, tokens)."""
    return _write_private(path or device_path(), {**data, "checkedInAt": int(time.time())})


def load_device(path: Path | None = None) -> dict | None:
    return _read(path or device_path())


def save_session(data: dict, path: Path | None = None) -> Path:
    """Play session (Play token, deviceConfigToken, dfeCookie). Rebuilt from the AAS token when it expires."""
    return _write_private(path or session_path(), {**data, "createdAt": int(time.time())})


def load_session(path: Path | None = None) -> dict | None:
    return _read(path or session_path())
