"""앱 파일 다운로드 — delivery 응답의 base APK, split APK, 확장 파일(OBB)을 받아 검증한다.

파일 이름은 기기의 /data/app 배치를 따른다 (base.apk, split_<이름>.apk).
그래서 받은 폴더를 `adb install-multiple <폴더>/*.apk` 로 그대로 설치할 수 있다.
OBB 는 안드로이드 확장 파일 규칙인 [main|patch].<versionCode>.<패키지>.obb (gpapi delivery 주석).
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import googleplay_pb2 as pb

CHUNK = 1 << 16


class DownloadError(Exception):
    pass


@dataclass(frozen=True)
class PlayFile:
    name: str
    url: str
    size: int
    sha1: str = ""    # delivery 응답 값 그대로 — base64url(패딩 없음)
    sha256: str = ""

    def expected_digest(self) -> tuple[str, str]:
        """검증에 쓸 (알고리즘, 기대값). sha256 이 있으면 그쪽을 쓴다 (OBB 는 sha1 만 온다)."""
        return ("sha256", self.sha256) if self.sha256 else ("sha1", self.sha1)


def play_files(package: str, data: pb.AndroidAppDeliveryData) -> list[PlayFile]:
    files = [PlayFile("base.apk", data.downloadUrl, data.downloadSize, data.sha1, data.sha256)]
    files += [PlayFile(f"split_{s.name}.apk", s.downloadUrl, s.size, s.sha1, s.sha256)
              for s in data.split]
    files += [PlayFile(f"{'patch' if f.fileType == 1 else 'main'}.{f.versionCode}.{package}.obb",
                       f.downloadUrl, f.size, f.sha1)
              for f in data.additionalFile]
    return files


def _b64url(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _verify(f: PlayFile, size: int, h) -> str | None:
    """크기·해시가 delivery 값과 다르면 그 이유를, 같으면 None."""
    if f.size and size != f.size:
        return f"크기 불일치 ({size} != {f.size} 바이트)"
    algo, expected = f.expected_digest()
    if expected and _b64url(h.digest()) != expected.rstrip("="):
        return f"{algo} 불일치"
    return None


def _already_have(path: Path, f: PlayFile) -> bool:
    if not path.exists() or (f.size and path.stat().st_size != f.size):
        return False
    h = hashlib.new(f.expected_digest()[0])
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(CHUNK), b""):
            h.update(chunk)
    return _verify(f, path.stat().st_size, h) is None


class Downloader:
    """delivery 의 다운로드 URL 들을 받는 HTTP 세션."""

    def __init__(self, data: pb.AndroidAppDeliveryData):
        self._http = requests.Session()
        host = urlsplit(data.downloadUrl).hostname
        for c in data.downloadAuthCookie:
            # play.googleapis.com 은 CDN 으로 리다이렉트한다. CDN 에는 인증 쿠키를 보내지 않는다
            self._http.cookies.set(c.name, c.value, domain=host)

    def fetch(self, f: PlayFile, dest_dir: Path, progress=None) -> bool:
        """f 를 dest_dir 에 받아 크기·해시를 검증한다. 이미 받아 둔 같은 파일이 있으면 받지 않고 False.

        progress(받은 바이트, 전체 바이트) 는 조각을 받을 때마다 불린다.
        """
        path = dest_dir / f.name
        if _already_have(path, f):
            return False
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        h = hashlib.new(f.expected_digest()[0])
        size = 0
        try:
            with self._http.get(f.url, stream=True, timeout=60) as r:
                if r.status_code != 200:
                    raise DownloadError(f"{f.name}: HTTP {r.status_code}")
                with open(part, "wb") as out:
                    for chunk in r.iter_content(CHUNK):
                        out.write(chunk)
                        h.update(chunk)
                        size += len(chunk)
                        if progress:
                            progress(size, f.size)
            if problem := _verify(f, size, h):
                raise DownloadError(f"{f.name}: {problem}")
        except BaseException:  # Ctrl-C 포함 — 덜 받은 파일을 남기지 않는다
            part.unlink(missing_ok=True)
            raise
        os.replace(part, path)
        return True
