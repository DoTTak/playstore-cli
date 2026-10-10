"""로그인·기기 등록 결과 저장 위치.

AAS 는 계정 비밀번호에 준하는 장기 토큰이라 파일 권한을 600 으로 둔다.
기본 위치는 ~/.config/dottakstore, PLAYSTORE_HOME 으로 바꿀 수 있다.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("PLAYSTORE_HOME") or Path.home() / ".config" / "dottakstore")


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
    os.chmod(path, 0o600)  # 이미 있던 파일에는 O_CREAT 의 mode 가 적용되지 않는다
    return path


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def save_account(email: str, aas_token: str, path: Path | None = None) -> Path:
    data = {"email": email, "aasToken": aas_token, "createdAt": int(time.time())}
    return _write_private(path or account_path(), data)


def load_account(path: Path | None = None) -> dict | None:
    return _read(path or account_path())


def save_device(data: dict, path: Path | None = None) -> Path:
    """checkin 으로 등록한 가상 기기 (프로필, gsfId, 토큰들)."""
    return _write_private(path or device_path(), {**data, "checkedInAt": int(time.time())})


def load_device(path: Path | None = None) -> dict | None:
    return _read(path or device_path())


def save_session(data: dict, path: Path | None = None) -> Path:
    """Play 세션 (Play 토큰, deviceConfigToken, dfeCookie). 만료되면 AAS 로 다시 만든다."""
    return _write_private(path or session_path(), {**data, "createdAt": int(time.time())})


def load_session(path: Path | None = None) -> dict | None:
    return _read(path or session_path())
