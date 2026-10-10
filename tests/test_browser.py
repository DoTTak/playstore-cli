import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from playstore import browser

pytestmark = pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome 없음")


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


def test_capture_oauth_token(tmp_path, fake_setup_page):
    email, token = browser.capture_oauth_token(
        tmp_path / "profile", url=fake_setup_page, headless=True, timeout=30)
    assert (email, token) == ("burner@example.com", "oauth2_4/fake-token")
