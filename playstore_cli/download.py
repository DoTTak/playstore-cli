"""App file download: fetch and verify the base APK, split APKs and expansion files (OBB) of a delivery response.

File names follow the on-device /data/app layout (base.apk, split_<name>.apk), so the downloaded folder
can be installed as-is with `adb install-multiple <folder>/*.apk`.
OBBs use Android's expansion file naming, [main|patch].<versionCode>.<package>.obb (per gpapi's delivery docs).
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
    sha1: str = ""    # as given in the delivery response: base64url (no padding)
    sha256: str = ""

    def expected_digest(self) -> tuple[str, str]:
        """(algorithm, expected value) used for verification. Prefer sha256 when present (OBBs only come with sha1)."""
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
    """The reason if size or hash differs from the delivery values, otherwise None."""
    if f.size and size != f.size:
        return f"size mismatch ({size} != {f.size} bytes)"
    algo, expected = f.expected_digest()
    if expected and _b64url(h.digest()) != expected.rstrip("="):
        return f"{algo} mismatch"
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
    """HTTP session that downloads the URLs of a delivery response."""

    def __init__(self, data: pb.AndroidAppDeliveryData):
        self._http = requests.Session()
        host = urlsplit(data.downloadUrl).hostname
        for c in data.downloadAuthCookie:
            # play.googleapis.com redirects to a CDN. Never send the auth cookie to the CDN
            self._http.cookies.set(c.name, c.value, domain=host)

    def fetch(self, f: PlayFile, dest_dir: Path, progress=None) -> bool:
        """Download f into dest_dir and verify its size and hash. Returns False without downloading
        if the same file is already there.

        progress(bytes received, total bytes) is called for every chunk.
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
        except BaseException:  # including Ctrl-C: never leave a partial file behind
            part.unlink(missing_ok=True)
            raise
        os.replace(part, path)
        return True
