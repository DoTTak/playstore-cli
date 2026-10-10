import base64
import hashlib

import pytest

from playstore_cli import download
from playstore_cli import googleplay_pb2 as pb
from playstore_cli.download import Downloader, DownloadError, PlayFile

BASE = b"base-apk-bytes" * 1000
SPLIT = b"split-apk-bytes"


def b64url(algo: str, data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.new(algo, data).digest()).rstrip(b"=").decode()


def delivery(url: str, **extra) -> pb.AndroidAppDeliveryData:
    return pb.AndroidAppDeliveryData(
        downloadUrl=f"{url}/base", downloadSize=len(BASE),
        sha1=b64url("sha1", BASE), sha256=b64url("sha256", BASE),
        downloadAuthCookie=[pb.HttpCookie(name="MarketDA", value="c")], **extra)


def test_play_files_names():
    data = pb.AndroidAppDeliveryData(
        downloadUrl="u0", downloadSize=10, sha1="s", sha256="S",
        split=[pb.Split(name="config.arm64_v8a", downloadUrl="u1", size=2, sha256="x")],
        additionalFile=[pb.AppFileMetadata(fileType=0, versionCode=7, downloadUrl="u2", size=3, sha1="m"),
                        pb.AppFileMetadata(fileType=1, versionCode=8, downloadUrl="u3", size=4, sha1="p")])

    assert download.play_files("com.example.game", data) == [
        PlayFile("base.apk", "u0", 10, "s", "S"),
        PlayFile("split_config.arm64_v8a.apk", "u1", 2, "", "x"),
        PlayFile("main.7.com.example.game.obb", "u2", 3, "m"),
        PlayFile("patch.8.com.example.game.obb", "u3", 4, "p"),
    ]


def test_fetch_verifies_and_sends_cookie(fake_server, tmp_path):
    fake_server.respond(BASE, path="/base")
    fake_server.respond(SPLIT, path="/split")
    data = delivery(fake_server.url, split=[pb.Split(
        name="config.ko", downloadUrl=f"{fake_server.url}/split", size=len(SPLIT),
        sha256=b64url("sha256", SPLIT))])
    seen = []

    d = Downloader(data)
    for f in download.play_files("com.example", data):
        assert d.fetch(f, tmp_path, progress=lambda done, total: seen.append((done, total)))

    assert (tmp_path / "base.apk").read_bytes() == BASE
    assert (tmp_path / "split_config.ko.apk").read_bytes() == SPLIT
    assert sorted(p.name for p in tmp_path.iterdir()) == ["base.apk", "split_config.ko.apk"]
    assert fake_server.by_path("/base")[0]["headers"]["Cookie"] == "MarketDA=c"
    assert seen[-1] == (len(SPLIT), len(SPLIT))


def test_fetch_skips_file_already_downloaded(fake_server, tmp_path):
    (tmp_path / "base.apk").write_bytes(BASE)
    data = delivery(fake_server.url)

    assert not Downloader(data).fetch(download.play_files("com.example", data)[0], tmp_path)
    assert not fake_server.requests


def test_fetch_replaces_corrupted_file(fake_server, tmp_path):
    (tmp_path / "base.apk").write_bytes(b"x" * len(BASE))  # same size, different content
    fake_server.respond(BASE, path="/base")
    data = delivery(fake_server.url)

    assert Downloader(data).fetch(download.play_files("com.example", data)[0], tmp_path)
    assert (tmp_path / "base.apk").read_bytes() == BASE


def test_fetch_hash_mismatch_leaves_nothing(fake_server, tmp_path):
    fake_server.respond(b"y" * len(BASE), path="/base")
    data = delivery(fake_server.url)

    with pytest.raises(DownloadError, match="base.apk: sha256 mismatch"):
        Downloader(data).fetch(download.play_files("com.example", data)[0], tmp_path)
    assert not list(tmp_path.iterdir())


def test_fetch_size_mismatch(fake_server, tmp_path):
    fake_server.respond(BASE[:-1], path="/base")
    data = delivery(fake_server.url)

    with pytest.raises(DownloadError, match="size mismatch"):
        Downloader(data).fetch(download.play_files("com.example", data)[0], tmp_path)


def test_fetch_http_error(fake_server, tmp_path):
    fake_server.respond("gone", status=403, path="/base")
    data = delivery(fake_server.url)

    with pytest.raises(DownloadError, match="HTTP 403"):
        Downloader(data).fetch(download.play_files("com.example", data)[0], tmp_path)
    assert not list(tmp_path.iterdir())


def test_obb_verified_with_sha1(fake_server, tmp_path):
    fake_server.respond(SPLIT, path="/obb")
    f = PlayFile("main.1.com.example.obb", f"{fake_server.url}/obb", len(SPLIT), sha1=b64url("sha1", SPLIT))

    assert Downloader(pb.AndroidAppDeliveryData()).fetch(f, tmp_path)
    assert (tmp_path / f.name).read_bytes() == SPLIT
