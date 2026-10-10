import pytest

from playstore_cli import device
from playstore_cli.device import Profile, available_profiles, find_profile, parse_properties


def test_parse_properties():
    text = "# comment\n! comment\n\nBuild.FINGERPRINT=google/tegu/tegu\\:15/BD4A\\:user\nA = b=c\n"
    assert parse_properties(text) == {"Build.FINGERPRINT": "google/tegu/tegu:15/BD4A:user", "A": "b=c"}


def test_user_profile_loads(playstore_home):
    assert "test_device" in available_profiles()
    assert Profile.load("test_device").props["Build.MODEL"] == "Test Phone"


def test_default_profile_is_bundled():
    p = Profile.load()
    assert (p.name, p.props["Build.MODEL"], p.props["Build.FINGERPRINT"].endswith(":user/release-keys")) == (
        "galaxy_s10e", "SM-G970N", True)
    assert (p.props["CellOperator"], p.props["SimOperator"]) == ("", "")  # no operator codes in a shared profile
    assert p.device_config().systemAvailableFeature and p.device_config().systemSupportedLocale


def test_bundled_profile_loads(tmp_path, monkeypatch):
    (tmp_path / "bundled.properties").write_text("Build.MODEL=Bundled\n")
    monkeypatch.setattr(device, "PROFILES_DIR", tmp_path)
    assert "bundled" in available_profiles()
    assert Profile.load("bundled").props["Build.MODEL"] == "Bundled"


def test_user_profile_wins_over_bundled(playstore_home, tmp_path, monkeypatch):
    (tmp_path / "test_device.properties").write_text("Build.MODEL=Bundled\n")
    monkeypatch.setattr(device, "PROFILES_DIR", tmp_path)
    assert Profile.load("test_device").props["Build.MODEL"] == "Test Phone"


def test_profile_from_path(tmp_path, monkeypatch):
    path = tmp_path / "phone.properties"
    path.write_text("Build.MODEL=From Path\n")
    monkeypatch.chdir(tmp_path)
    assert find_profile("phone.properties") == path.resolve()
    assert Profile.load("phone.properties").props["Build.MODEL"] == "From Path"


def test_unknown_profile():
    with pytest.raises(ValueError, match="test_device"):
        Profile.load("nope")


def test_user_agent():
    ua = Profile.load("test_device").user_agent()
    assert ua.startswith("Android-Finsky/45.8.21-31 [0] [PR] 747433787 (api=3,versionCode=84582130,sdk=35,")
    assert "model=Test%20Phone," in ua and ua.endswith("supportedAbis=arm64-v8a)")


def test_checkin_request():
    req = Profile.load("test_device").checkin_request("en_US", "UTC")

    assert (req.id, req.version, req.locale, req.timeZone) == (0, 3, "en_US", "UTC")
    assert req.HasField("id")  # id=0 is sent explicitly too, to mean 'new device'
    b = req.checkin.build
    assert b.id == "test/testphone/testphone:15/TP1A.000000.001/1:user/release-keys"
    assert (b.product, b.device, b.sdkVersion, b.googleServices) == ("testhw", "testphone", 35, 251333035)
    assert (req.checkin.cellOperator, req.checkin.roaming) == ("310260", "mobile-notroaming")
    dc = req.deviceConfiguration
    assert list(dc.nativePlatform) == ["arm64-v8a"]
    assert (dc.screenWidth, dc.screenHeight, dc.screenDensity, dc.glEsVersion) == (1080, 2400, 420, 196610)
    assert list(dc.systemAvailableFeature) == ["android.hardware.touchscreen", "android.hardware.wifi"]
    assert list(dc.systemSupportedLocale) == ["en", "en_US", "ko", "ko_KR"]
