"""Login step 1: get the oauth_token cookie through a browser login.

accounts.google.com/EmbeddedSetup is the web version of Android's "Add account" screen.
Finishing the login sets an oauth_token (oauth2_4/...) cookie, the one-time token exchanged for AAS.
Chrome is started in remote debugging (CDP) mode to read that cookie (same approach as Aurora Store's
GoogleLoginScreen).
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

# <div data-profile-identifier data-email="..."> on the post-login page (the same place Aurora reads)
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
    """Minimal DevTools protocol client (a single page target)."""

    def __init__(self, ws_url: str):
        # Omit the Origin header so the connection works without --remote-allow-origins
        self._ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)
        self._id = 0

    def call(self, method: str, **params) -> dict:
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") == self._id:  # messages without an id are events → ignore
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
    """Wait until DevTools is up and return the WebSocket URL of the first page tab."""
    http = requests.Session()
    http.trust_env = False  # bypass the system proxy (Burp, etc.)
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
    # The cookie may appear before the page renders, so retry briefly
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
    """Open a browser window and, once login finishes, return (email or None, oauth_token).

    profile_dir is a dedicated Chrome profile. If kept, 2-step verification is usually asked only the first time.
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
        # Turn off the automation marker (navigator.webdriver). Google blocks logins from automated browsers.
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
        # Clear the (already used) cookie left from a previous run so only a freshly issued one is caught
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
