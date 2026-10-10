import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from playstore_cli import browser


@pytest.fixture
def fake_setup_page():
    """Stand-in for the EmbeddedSetup login-complete page: oauth_token cookie + profile email."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<div data-profile-identifier data-email="burner@example.com"></div>'
            self.send_response(200)
            self.send_header("Set-Cookie", "oauth_token=oauth2_4/fake-token; Path=/")
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


@pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome not found")
def test_capture_oauth_token(tmp_path, fake_setup_page):
    email, token = browser.capture_oauth_token(
        tmp_path / "profile", url=fake_setup_page, headless=True, timeout=30)
    assert (email, token) == ("burner@example.com", "oauth2_4/fake-token")


def test_find_chrome_in_windows_install_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "CHROME_CANDIDATES", [])
    for root in browser.WINDOWS_ROOTS:
        monkeypatch.setenv(root, str(tmp_path / root))
    edge = tmp_path / "PROGRAMFILES(X86)" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    chrome = tmp_path / "LOCALAPPDATA" / "Google" / "Chrome" / "Application" / "chrome.exe"
    for exe in (edge, chrome):
        exe.parent.mkdir(parents=True)
        exe.touch()

    assert browser.find_chrome() == str(chrome)  # Chrome first, wherever it is installed
    chrome.unlink()
    assert browser.find_chrome() == str(edge)
    edge.unlink()
    assert browser.find_chrome() is None
