import hashlib
import io
import os
import struct
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from playstore.apksig import BLOCK_MAGIC, FileReader, HttpRangeReader, SignatureError, read_signers

V2, V3, STAMP = 0x7109871A, 0xF05368C0, 0x6DFF800D


def lp(b: bytes) -> bytes:
    return struct.pack("<I", len(b)) + b


def v2_value(*certs: bytes) -> bytes:
    """Each signer gets a certificate chain [own certificate, intermediate certificate]."""
    signers = b""
    for cert in certs:
        signed_data = lp(b"digests") + lp(lp(cert) + lp(b"intermediate")) + lp(b"attrs")
        signers += lp(lp(signed_data) + lp(b"signatures") + lp(b"pubkey"))
    return lp(signers)


def v3_value(cert: bytes) -> bytes:
    sdk = struct.pack("<II", 28, 0x7FFFFFFF)  # minSdk/maxSdk, no length prefix
    signed_data = lp(b"digests") + lp(lp(cert)) + sdk + lp(b"attrs")
    return lp(lp(lp(signed_data) + sdk + lp(b"signatures") + lp(b"pubkey")))


def make_apk(pairs, *, comment=b"", payload=b"x" * 1000) -> bytes:
    """Build a zip and insert a signing block before the Central Directory (fixing the EOCD's CD offset)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("AndroidManifest.xml", payload)
        z.comment = comment
    data = buf.getvalue()
    if not pairs:
        return data
    body = b"".join(struct.pack("<QI", len(v) + 4, i) + v for i, v in pairs)
    size = len(body) + 24
    block = struct.pack("<Q", size) + body + struct.pack("<Q", size) + BLOCK_MAGIC
    eocd = len(data) - 22 - len(comment)
    cd = struct.unpack_from("<I", data, eocd + 16)[0]
    data = data[:cd] + block + data[cd:]
    eocd += len(block)
    return data[:eocd + 16] + struct.pack("<I", cd + len(block)) + data[eocd + 20:]


def signers_of(tmp_path, apk: bytes):
    path = tmp_path / "base.apk"
    path.write_bytes(apk)
    return [(s.scheme, s.cert_der) for s in read_signers(FileReader(path))]


def test_v2_and_v3_without_source_stamp(tmp_path):
    apk = make_apk([(V2, v2_value(b"cert-A")), (V3, v3_value(b"cert-A")), (STAMP, v2_value(b"google"))])
    assert signers_of(tmp_path, apk) == [("v2", b"cert-A"), ("v3", b"cert-A")]


def test_multiple_v2_signers(tmp_path):
    apk = make_apk([(V2, v2_value(b"cert-A", b"cert-B"))])
    assert signers_of(tmp_path, apk) == [("v2", b"cert-A"), ("v2", b"cert-B")]


def test_eocd_magic_inside_comment(tmp_path):
    apk = make_apk([(V2, v2_value(b"cert-A"))], comment=b"junk PK\x05\x06 junk" + b"\0" * 30)
    assert signers_of(tmp_path, apk) == [("v2", b"cert-A")]


def test_hashes(tmp_path):
    path = tmp_path / "base.apk"
    path.write_bytes(make_apk([(V3, v3_value(b"cert-A"))]))
    (s,) = read_signers(FileReader(path))
    assert s.sha1 == hashlib.sha1(b"cert-A").hexdigest()
    assert s.sha256 == hashlib.sha256(b"cert-A").hexdigest()


def test_v1_only_apk(tmp_path):
    with pytest.raises(SignatureError, match="v1"):
        signers_of(tmp_path, make_apk([]))


def test_block_without_app_signer(tmp_path):
    with pytest.raises(SignatureError, match="v2/v3"):
        signers_of(tmp_path, make_apk([(STAMP, v2_value(b"google"))]))


def test_not_a_zip(tmp_path):
    with pytest.raises(SignatureError, match="EOCD"):
        signers_of(tmp_path, b"not a zip" * 100)


@pytest.fixture
def range_server(monkeypatch):
    """Mimics Google's download path: /download 302s to /cdn, /cdn answers start-end ranges only, with 206.

    Suffix ranges (bytes=-N) get 416, like Google's CDN. With state['honor_range']=False, Range is ignored (200).
    """
    state = {"data": b"", "honor_range": True, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            rng = self.headers.get("Range", "")
            state["requests"].append({"path": self.path, "range": rng, "cookie": self.headers.get("Cookie")})
            if self.path == "/download":
                self.send_response(302)
                self.send_header("Location", "/cdn")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = state["data"]
            if not (state["honor_range"] and rng.startswith("bytes=")):
                self._send(200, data)
                return
            first, last = rng[6:].split("-")
            if first == "":
                self._send(416, b"")
                return
            start, end = int(first), min(int(last or len(data) - 1), len(data) - 1)
            self._send(206, data[start:end + 1], f"bytes {start}-{end}/{len(data)}")

        def _send(self, status, body, content_range=None):
            self.send_response(status)
            if content_range:
                self.send_header("Content-Range", content_range)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_port}/download"
    yield state
    srv.shutdown()


def test_http_range_probes_size_then_reads_tail(range_server):
    data = range_server["data"] = make_apk([(V3, v3_value(b"cert-A"))], payload=os.urandom(2_000_000))
    reader = HttpRangeReader(range_server["url"], {"MarketDA": "cookie-value"})

    assert [s.cert_der for s in read_signers(reader)] == [b"cert-A"]
    assert reader.size == len(data)
    assert reader.fetched < 100_000  # only the tail of 2MB
    reqs = range_server["requests"]
    assert reqs[0] == {"path": "/download", "range": "bytes=0-0", "cookie": "MarketDA=cookie-value"}
    assert reqs[2]["range"] == f"bytes={len(data) - 65557}-{len(data) - 1}"
    assert all(r["path"] == "/cdn" for r in reqs[1:])  # the redirect is followed only once


def test_http_range_with_known_size_skips_probe(range_server):
    data = range_server["data"] = make_apk([(V2, v2_value(b"cert-A"))], payload=os.urandom(200_000))
    read_signers(HttpRangeReader(range_server["url"], size=len(data)))
    assert range_server["requests"][0]["range"] == f"bytes={len(data) - 65557}-{len(data) - 1}"


def test_http_small_file_tail(range_server):
    data = range_server["data"] = make_apk([(V2, v2_value(b"cert-A"))])  # smaller than the EOCD search window
    reader = HttpRangeReader(range_server["url"], size=len(data))
    assert [s.cert_der for s in read_signers(reader)] == [b"cert-A"]
    assert range_server["requests"][0]["range"] == f"bytes=0-{len(data) - 1}"


def test_http_server_ignoring_range(range_server):
    range_server.update(data=make_apk([(V2, v2_value(b"cert-A"))]), honor_range=False)
    with pytest.raises(SignatureError, match="HTTP 200"):
        read_signers(HttpRangeReader(range_server["url"]))
