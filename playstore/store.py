"""로그인 결과(계정 이메일, AAS 토큰) 저장 위치.

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


def chrome_profile_dir() -> Path:
    return home() / "chrome-profile"


def save_account(email: str, aas_token: str, path: Path | None = None) -> Path:
    path = path or account_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"email": email, "aasToken": aas_token, "createdAt": int(time.time())}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)  # 이미 있던 파일에는 O_CREAT 의 mode 가 적용되지 않는다
    return path


def load_account(path: Path | None = None) -> dict | None:
    path = path or account_path()
    if not path.exists():
        return None
    return json.loads(path.read_text())
