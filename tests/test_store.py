import os
import stat

import pytest

from playstore_cli import store


def assert_private(path):
    # Windows has no POSIX permission bits (chmod only sets or clears read-only)
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_and_load_account(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYSTORE_HOME", str(tmp_path))
    path = store.save_account("a@b.com", "aas_et/XYZ")

    assert path == tmp_path / "account.json"
    assert_private(path)
    data = store.load_account()
    assert (data["email"], data["aasToken"]) == ("a@b.com", "aas_et/XYZ")


@pytest.mark.skipif(os.name == "nt", reason="no POSIX file modes on Windows")
def test_save_tightens_existing_file(tmp_path):
    path = tmp_path / "account.json"
    path.write_text("{}")
    path.chmod(0o644)
    store.save_account("a@b.com", "aas_et/XYZ", path)
    assert_private(path)


def test_load_missing(tmp_path):
    assert store.load_account(tmp_path / "none.json") is None


def test_save_and_load_device(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYSTORE_HOME", str(tmp_path))
    path = store.save_device({"profile": "pixel_9a", "gsfId": "3a1b"})

    assert_private(path)
    data = store.load_device()
    assert (data["profile"], data["gsfId"]) == ("pixel_9a", "3a1b") and data["checkedInAt"]


def test_save_and_load_session(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYSTORE_HOME", str(tmp_path))
    path = store.save_session({"gsfId": "3a1b", "authToken": "t"})

    assert path == tmp_path / "session.json"
    assert_private(path)
    assert store.load_session()["authToken"] == "t"
