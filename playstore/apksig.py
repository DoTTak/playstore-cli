"""APK 서명 블록(v2/v3)에서 서명 인증서를 꺼낸다 — APK 전체가 아니라 끝부분만 읽는다.

APK(zip) 끝부분 구조:
  [ 파일 데이터 … ][ APK Signing Block ][ Central Directory ][ EOCD ]
  - EOCD(End of Central Directory)에 Central Directory 시작 오프셋이 있다.
  - 그 바로 앞 24바이트 = 서명 블록 크기(u64) + "APK Sig Block 42".
  - 블록 안의 (ID, 값) 쌍 중 v2/v3/v3.1 ID 가 서명 정보다.
참고: https://source.android.com/docs/security/features/apksigning/v2
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
EOCD_SEARCH = EOCD_SIZE + 0xFFFF  # EOCD + 최대 zip 주석 길이
BLOCK_MAGIC = b"APK Sig Block 42"

# 앱 서명자 블록만. Source Stamp(0x6dff800d 등)는 Play 의 스탬프 인증서라 제외한다.
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
    """HTTP Range 요청으로 원격 파일의 일부만 받는다.

    구글 다운로드 URL(play.googleapis.com)은 CDN(*.gvt1.com)으로 302 리다이렉트하고,
    CDN 은 끝 기준 범위(bytes=-N)를 416 으로 거절한다. 그래서 크기를 먼저 알고(size 인자
    또는 bytes=0-0 응답의 Content-Range) 시작-끝을 명시한 범위로만 요청한다.
    """

    def __init__(self, url: str, cookies: dict[str, str] | None = None, size: int | None = None):
        self._url = url
        self._http = requests.Session()
        host = urlsplit(url).hostname
        for name, value in (cookies or {}).items():
            self._http.cookies.set(name, value, domain=host)  # CDN 에는 인증 쿠키를 보내지 않는다
        self.size = size or None
        self.requests = 0
        self.fetched = 0

    def read(self, offset: int, length: int) -> bytes:
        # stream=True: Range 를 무시하고 200 으로 전체를 주는 서버면 본문을 받지 않고 끊는다
        with self._http.get(self._url, headers={"Range": f"bytes={offset}-{offset + length - 1}"},
                            stream=True, timeout=30) as r:
            if r.status_code != 206:
                raise SignatureError(f"Range 요청이 거절됨 (HTTP {r.status_code})")
            self.size = int(r.headers["Content-Range"].rsplit("/", 1)[1])
            self._url = r.url  # 리다이렉트는 처음 한 번만 따라가고, 이후엔 최종 주소로 바로 요청
            data = r.content
        self.requests += 1
        self.fetched += len(data)
        return data

    def tail(self, length: int) -> bytes:
        if self.size is None:
            self.read(0, 1)  # 전체 크기 확인용
        start = max(0, self.size - length)
        return self.read(start, self.size - start)


def _find_eocd(tail: bytes) -> int:
    """뒤에서부터 EOCD 를 찾는다. 주석 길이 필드가 맞는 위치만 인정 (주석 안의 우연한 매직 배제)."""
    for i in range(len(tail) - EOCD_SIZE, -1, -1):
        if tail[i:i + 4] == EOCD_MAGIC:
            comment_len = struct.unpack_from("<H", tail, i + 20)[0]
            if i + EOCD_SIZE + comment_len == len(tail):
                return i
    raise SignatureError("zip 끝(EOCD)을 찾지 못함 — APK 가 아니거나 잘린 파일")


def _lp(buf: bytes, off: int) -> tuple[bytes, int]:
    """u32 길이 접두 항목 하나 → (내용, 다음 오프셋)."""
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
    """서명 블록 본문의 (u64 길이, u32 ID, 값) 쌍들."""
    off = 0
    while off + 12 <= len(buf):
        length, pair_id = struct.unpack_from("<QI", buf, off)
        if length < 4 or off + 8 + length > len(buf):
            raise SignatureError("서명 블록 형식 오류")
        yield pair_id, buf[off + 12: off + 8 + length]
        off += 8 + length


def _signer_certs(value: bytes) -> list[bytes]:
    """v2/v3 값 = lp(서명자들). 서명자 = lp(signed data) …, signed data = lp(digests) lp(certificates) …
    서명자마다 인증서 목록의 첫 번째(서명자 자신의 인증서)를 돌려준다.
    (v3 signed data 는 certificates 뒤에 minSdk/maxSdk 가 길이 접두 없이 오므로 앞 두 항목만 읽는다)
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
    """reader(FileReader/HttpRangeReader)로 서명 블록만 읽어 서명자 인증서 목록을 반환."""
    tail = reader.tail(EOCD_SEARCH)
    tail_start = reader.size - len(tail)

    def read(offset: int, length: int) -> bytes:
        if offset >= tail_start:  # 이미 받은 끝부분 안이면 다시 요청하지 않는다
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

    # 블록 = size(8) + 쌍들 + size(8) + magic(16)
    body = read(block_start + 8, block_size - 24)
    signers = [Signer(SCHEMES[pair_id], cert)
               for pair_id, value in _pairs(body) if pair_id in SCHEMES
               for cert in _signer_certs(value)]
    if not signers:
        raise SignatureError("서명 블록에 v2/v3 서명이 없음")
    return signers
