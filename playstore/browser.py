"""로그인 1단계: 브라우저 로그인으로 oauth_token 쿠키를 얻는다.

accounts.google.com/EmbeddedSetup 은 안드로이드 '계정 추가' 화면의 웹 버전이다.
로그인을 마치면 oauth_token(oauth2_4/...) 쿠키가 생기고, 이것이 AAS 교환에 쓰는 1회용 토큰이다.
Chrome 을 원격 디버깅(CDP) 모드로 띄워 그 쿠키를 읽어 온다 (Aurora Store GoogleLoginScreen 과 같은 방식).
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import requests
import websocket

EMBEDDED_SETUP_URL = "https://accounts.google.com/EmbeddedSetup"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]

# 로그인 후 페이지의 <div data-profile-identifier data-email="..."> (Aurora 가 읽는 위치와 같음)
_EMAIL_JS = (
    "(() => { const el = document.querySelector('[data-profile-identifier][data-email]');"
    " return el ? el.getAttribute('data-email') : null; })()"
)


class LoginError(Exception):
    pass


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        path = c if Path(c).is_absolute() else shutil.which(c)
        if path and Path(path).exists():
            return path
    return None


class _Cdp:
    """DevTools 프로토콜 최소 클라이언트 (페이지 타깃 하나)."""

    def __init__(self, ws_url: str):
        # Origin 헤더를 빼야 --remote-allow-origins 없이 접속된다
        self._ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)
        self._id = 0

    def call(self, method: str, **params) -> dict:
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") == self._id:  # id 가 없는 건 이벤트 → 무시
                if "error" in msg:
                    raise LoginError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})

    def close(self):
        self._ws.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _page_ws_url(port: int, proc: subprocess.Popen, timeout: float = 15) -> str:
    """DevTools 가 뜰 때까지 기다렸다가 첫 페이지 탭의 웹소켓 주소를 반환."""
    http = requests.Session()
    http.trust_env = False  # 시스템 프록시(Burp 등)를 거치지 않게
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise LoginError("브라우저가 바로 종료됐습니다")
        try:
            for t in http.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json():
                if t.get("type") == "page":
                    return t["webSocketDebuggerUrl"]
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise LoginError("브라우저 DevTools 에 연결하지 못했습니다")


def _profile_email(cdp: _Cdp) -> str | None:
    # 쿠키가 페이지 렌더링보다 먼저 생길 수 있어 잠깐 재시도
    for _ in range(6):
        try:
            value = cdp.call("Runtime.evaluate", expression=_EMAIL_JS,
                             returnByValue=True).get("result", {}).get("value")
        except LoginError:
            value = None
        if value:
            return value
        time.sleep(0.5)
    return None


def capture_oauth_token(profile_dir: Path, *, url: str = EMBEDDED_SETUP_URL,
                        timeout: float = 600, chrome: str | None = None,
                        headless: bool = False) -> tuple[str | None, str]:
    """브라우저 창을 띄우고, 로그인이 끝나면 (이메일 또는 None, oauth_token) 을 반환.

    profile_dir 는 전용 Chrome 프로필. 유지해 두면 2단계 인증은 보통 처음 한 번만 묻는다.
    """
    chrome = chrome or find_chrome()
    if not chrome:
        raise LoginError("Chrome 계열 브라우저를 찾지 못했습니다. --oauth-token 으로 직접 넣으세요.")
    profile_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # 자동화 표식(navigator.webdriver)을 끈다. 구글은 자동화된 브라우저의 로그인을 막는다.
        "--disable-blink-features=AutomationControlled",
        "--window-size=480,720",
    ]
    if headless:
        args.append("--headless=new")
    args.append("about:blank")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    cdp = None
    try:
        cdp = _Cdp(_page_ws_url(port, proc))
        # 지난 실행에서 남은(이미 쓴) 쿠키를 지우고 시작해야 새로 발급된 것만 잡힌다
        cdp.call("Network.deleteCookies", name="oauth_token", url=url)
        cdp.call("Page.navigate", url=url)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise LoginError("로그인을 마치기 전에 브라우저가 닫혔습니다")
            cookies = cdp.call("Network.getCookies", urls=[url])["cookies"]
            token = next((c["value"] for c in cookies if c["name"] == "oauth_token"), None)
            if token:
                return _profile_email(cdp), token
            time.sleep(1)
        raise LoginError(f"{timeout:.0f}초 안에 로그인이 끝나지 않았습니다")
    except (OSError, websocket.WebSocketException) as e:
        raise LoginError(f"브라우저 연결이 끊겼습니다: {e}") from e
    finally:
        if cdp:
            cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
