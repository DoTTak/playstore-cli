import pytest

from playstore.device import Profile, available_profiles, parse_properties


def test_parse_properties():
    text = "# 주석\n! 주석\n\nBuild.FINGERPRINT=google/tegu/tegu\\:15/BD4A\\:user\nA = b=c\n"
    assert parse_properties(text) == {"Build.FINGERPRINT": "google/tegu/tegu:15/BD4A:user", "A": "b=c"}


def test_default_profile_loads():
    assert "pixel_9a" in available_profiles()
    p = Profile.load()
    assert p.props["Build.MODEL"] == "Pixel 9a"


def test_unknown_profile():
    with pytest.raises(ValueError, match="pixel_9a"):
        Profile.load("nope")


def test_user_agent():
    ua = Profile.load().user_agent()
    assert ua.startswith("Android-Finsky/45.8.21-31 [0] [PR] 747433787 (api=3,versionCode=84582130,sdk=35,")
    assert "model=Pixel%209a," in ua and ua.endswith("supportedAbis=arm64-v8a)")


def test_checkin_request():
    req = Profile.load().checkin_request("ko_KR", "Asia/Seoul")

    assert (req.id, req.version, req.locale, req.timeZone) == (0, 3, "ko_KR", "Asia/Seoul")
    assert req.HasField("id")  # id=0 is sent explicitly too, to mean 'new device'
    b = req.checkin.build
    assert b.id == "google/tegu/tegu:15/BD4A.250405.003/13238919:user/release-keys"
    assert (b.product, b.device, b.sdkVersion, b.googleServices) == ("tegu", "tegu", 35, 251333035)
    assert (req.checkin.cellOperator, req.checkin.roaming) == ("310", "mobile-notroaming")
    dc = req.deviceConfiguration
    assert list(dc.nativePlatform) == ["arm64-v8a"]
    assert (dc.screenWidth, dc.screenHeight, dc.screenDensity, dc.glEsVersion) == (1080, 2424, 420, 196610)
    assert "android.hardware.sensor.proximity" in dc.systemAvailableFeature
    assert all(dc.systemSharedLibrary) and all(dc.glExtension) and "ko_KR" in dc.systemSupportedLocale
