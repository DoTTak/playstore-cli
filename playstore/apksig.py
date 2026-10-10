"""Extract signing certificates from the APK Signing Block (v2/v3), reading only the end of the APK.

Layout of the end of an APK (zip):
  [ file data … ][ APK Signing Block ][ Central Directory ][ EOCD ]
  - The EOCD (End of Central Directory) holds the Central Directory's start offset.
  - The 24 bytes right before it = signing block size (u64) + "APK Sig Block 42".
  - Among the (ID, value) pairs in the block, the v2/v3/v3.1 IDs hold the signatures.
See: https://source.android.com/docs/security/features/apksigning/v2
"""
from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

EOCD_MAGIC = b"PK\x05\x06"
EOCD_SIZE = 22
EOCD_SEARCH = EOCD_SIZE + 0xFFFF  # EOCD + maximum zip comment length
BLOCK_MAGIC = b"APK Sig Block 42"

# App signer blocks only. Source Stamp (0x6dff800d, etc.) is Play's stamp certificate, so it is excluded.
SCHEMES = {0x7109871A: "v2", 0xF05368C0: "v3", 0x1B93AD61: "v3.1"}


class SignatureError(Exception):
    pass


@dataclass(frozen=True)
class Signer:
    scheme: str
    cert_der: bytes

    @property
    def sha1(self) -> str:
        return hashlib.sha1(self.cert_der).hexdigest()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.cert_der).hexdigest()


class FileReader:
    def __init__(self, path: str | os.PathLike):
        self._path = path
        self.size = os.path.getsize(path)
        self.fetched = 0

    def read(self, offset: int, length: int) -> bytes:
        with open(self._path, "rb") as f:
            f.seek(offset)
            data = f.read(length)
        self.fetched += len(data)
        return data

    def tail(self, length: int) -> bytes:
        length = min(length, self.size)
        return self.read(self.size - length, length)


class HttpRangeReader:
    """Fetch only parts of a remote file with HTTP Range requests.

    Google download URLs (play.googleapis.com) 302-redirect to a CDN (*.gvt1.com), and the CDN rejects
    suffix ranges (bytes=-N) with 416. So the size is learned first (the size argument, or the
    Content-Range of a bytes=0-0 response) and only explicit start-end ranges are requested.
    """

    def __init__(self, url: str, cookies: dict[str, str] | None = None, size: int | None = None):
        self._url = url
        self._http = requests.Session()
        host = urlsplit(url).hostname
        for name, value in (cookies or {}).items():
            self._http.cookies.set(name, value, domain=host)  # never send the auth cookie to the CDN
        self.size = size or None
        self.requests = 0
        self.fetched = 0

    def read(self, offset: int, length: int) -> bytes:
        # stream=True: if a server ignores Range and sends the whole file with 200, hang up without reading it
        with self._http.get(self._url, headers={"Range": f"bytes={offset}-{offset + length - 1}"},
                            stream=True, timeout=30) as r:
            if r.status_code != 206:
                raise SignatureError(f"Range 요청이 거절됨 (HTTP {r.status_code})")
            self.size = int(r.headers["Content-Range"].rsplit("/", 1)[1])
            self._url = r.url  # follow the redirect only once, then request the final URL directly
            data = r.content
        self.requests += 1
        self.fetched += len(data)
        return data

    def tail(self, length: int) -> bytes:
        if self.size is None:
            self.read(0, 1)  # just to learn the total size
        start = max(0, self.size - length)
        return self.read(start, self.size - start)


def _find_eocd(tail: bytes) -> int:
    """Search for the EOCD from the end. Accept only a position whose comment length field fits
    (rules out a stray magic inside the comment)."""
    for i in range(len(tail) - EOCD_SIZE, -1, -1):
        if tail[i:i + 4] == EOCD_MAGIC:
            comment_len = struct.unpack_from("<H", tail, i + 20)[0]
            if i + EOCD_SIZE + comment_len == len(tail):
                return i
    raise SignatureError("zip 끝(EOCD)을 찾지 못함 — APK 가 아니거나 잘린 파일")


def _lp(buf: bytes, off: int) -> tuple[bytes, int]:
    """One u32-length-prefixed item → (content, next offset)."""
    if off + 4 > len(buf):
        raise SignatureError("서명 블록 형식 오류")
    n = struct.unpack_from("<I", buf, off)[0]
    if off + 4 + n > len(buf):
        raise SignatureError("서명 블록 형식 오류")
    return buf[off + 4: off + 4 + n], off + 4 + n


def _lp_items(buf: bytes):
    off = 0
    while off < len(buf):
        item, off = _lp(buf, off)
        yield item


def _pairs(buf: bytes):
    """The (u64 length, u32 ID, value) pairs in the signing block body."""
    off = 0
    while off + 12 <= len(buf):
        length, pair_id = struct.unpack_from("<QI", buf, off)
        if length < 4 or off + 8 + length > len(buf):
            raise SignatureError("서명 블록 형식 오류")
        yield pair_id, buf[off + 12: off + 8 + length]
        off += 8 + length


def _signer_certs(value: bytes) -> list[bytes]:
    """v2/v3 value = lp(signers). signer = lp(signed data) …, signed data = lp(digests) lp(certificates) …
    Returns, for each signer, the first entry of its certificate list (the signer's own certificate).
    (In v3 signed data, minSdk/maxSdk follow certificates without a length prefix, so only the first two
    items are read.)
    """
    signers, _ = _lp(value, 0)
    certs = []
    for signer in _lp_items(signers):
        signed_data, _ = _lp(signer, 0)
        _, off = _lp(signed_data, 0)          # digests
        cert_list, _ = _lp(signed_data, off)  # certificates
        certs.append(next(_lp_items(cert_list)))
    return certs


def read_signers(reader) -> list[Signer]:
    """Read just the signing block through reader (FileReader/HttpRangeReader) and return the signer certificates."""
    tail = reader.tail(EOCD_SEARCH)
    tail_start = reader.size - len(tail)

    def read(offset: int, length: int) -> bytes:
        if offset >= tail_start:  # don't re-request what is already in the fetched tail
            return tail[offset - tail_start: offset - tail_start + length]
        return reader.read(offset, length)

    eocd = _find_eocd(tail)
    cd_offset = struct.unpack_from("<I", tail, eocd + 16)[0]
    if cd_offset < 32:
        raise SignatureError("APK 서명 블록 없음 (v1 서명만 있는 APK)")

    footer = read(cd_offset - 24, 24)
    if footer[8:] != BLOCK_MAGIC:
        raise SignatureError("APK 서명 블록 없음 (v1 서명만 있는 APK)")
    block_size = struct.unpack_from("<Q", footer)[0]
    block_start = cd_offset - 8 - block_size
    if block_size < 24 or block_start < 0:
        raise SignatureError("서명 블록 크기가 잘못됨")

    # block = size(8) + pairs + size(8) + magic(16)
    body = read(block_start + 8, block_size - 24)
    signers = [Signer(SCHEMES[pair_id], cert)
               for pair_id, value in _pairs(body) if pair_id in SCHEMES
               for cert in _signer_certs(value)]
    if not signers:
        raise SignatureError("서명 블록에 v2/v3 서명이 없음")
    return signers
