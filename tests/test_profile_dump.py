import io
import struct
import zipfile

import pytest

from playstore_cli import profile_dump
from playstore_cli.device import parse_properties
from playstore_cli.profile_dump import Adb, ProfileError, arsc_locales, dump, to_properties


def type_chunk(language: bytes = b"\0\0", country: bytes = b"\0\0", script: bytes = b"") -> bytes:
    """RES_TABLE_TYPE_TYPE chunk with a 64-byte ResTable_config and no entries."""
    config = bytearray(64)
    struct.pack_into("<I", config, 0, len(config))
    config[8:10], config[10:12], config[36:40] = language, country, script.ljust(4, b"\0")
    body = struct.pack("<BBHII", 1, 0, 0, 0, 0) + bytes(config)
    return struct.pack("<HHI", 0x0201, 8 + len(body), 8 + len(body)) + body


def arsc(*types: bytes) -> bytes:
    pkg_header = struct.pack("<I", 0x7F) + bytes(256) + bytes(20)  # id, name, string pool offsets
    pkg_body = b"".join(types)
    pkg = struct.pack("<HHI", 0x0200, 8 + len(pkg_header), 8 + len(pkg_header) + len(pkg_body))
    pkg += pkg_header + pkg_body
    return struct.pack("<HHII", 0x0002, 12, 12 + len(pkg), 1) + pkg


def test_arsc_locales():
    data = arsc(type_chunk(), type_chunk(b"en"), type_chunk(b"en", b"US"), type_chunk(b"en"),
                type_chunk(b"es", b"\xa4\x24"),  # region 419, packed as digits
                type_chunk(b"sr", script=b"Latn"),
                type_chunk(b"\xad\x05"))  # fil, a three-letter language packed in two bytes
    assert arsc_locales(data) == ["en", "en_US", "es_419", "fil", "sr_Latn"]


def test_arsc_rejects_other_data():
    with pytest.raises(ProfileError):
        arsc_locales(struct.pack("<HHI", 0x0003, 8, 8))


GETPROP = """[ro.build.fingerprint]: [test/phone/phone:15/TP1A.1/7:user/release-keys]
[ro.build.id]: [TP1A.1]
[ro.product.brand]: [test]
[ro.product.manufacturer]: [Test]
[ro.product.model]: [Phone 1]
[ro.product.device]: [phone]
[ro.product.name]: [phone_eea]
[ro.hardware]: [testhw]
[gsm.version.baseband]: []
[ro.build.version.sdk]: [35]
[ro.build.version.release]: [15]
[gsm.operator.numeric]: [45005,]
[gsm.sim.operator.numeric]: [45005]
[ro.product.cpu.abilist]: [arm64-v8a]
[ro.opengles.version]: [196610]
"""

GET_CONFIG = """config: mcc450-mnc5-en-rUS-ldltr-sw411dp-w411dp-h914dp-normal-long-port-420dpi-finger-keyshidden-nokeys-navhidden-nonav-2400
abi: arm64-v8a
stable-width-px: 1080
stable-height-px: 2400
stable-density-dpi: 420
opengl-version: 0x30002
opengl-extensions: GL_EXT_one
opengl-extensions: GL_EXT_two
features: android.hardware.camera
features: android.hardware.wifi
shared-libraries: android.test.base
"""


class FakeAdb(Adb):
    def __init__(self, getprop=GETPROP, vending_code=1801):
        super().__init__()
        self.outputs = {
            ("getprop",): getprop,
            ("am", "get-config", "--device"): GET_CONFIG,
            ("dumpsys", "package", "com.google.android.gms"):
                "    versionCode=251333035 minSdk=31 targetSdk=35\n    versionName=25.13.33\n",
            ("dumpsys", "package", "com.android.vending"):
                f"    versionCode={vending_code} minSdk=35 targetSdk=35\n    versionName=1.8\n",
        }

    def shell(self, *args):
        return self.outputs[args]

    def pull(self, remote, local):
        assert remote == "/system/framework/framework-res.apk"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("resources.arsc", arsc(type_chunk(b"en", b"US"), type_chunk(b"ko", b"KR")))
        local.write_bytes(buf.getvalue())


def test_dump():
    props = dump(FakeAdb())

    assert props["UserReadableName"] == "Test Phone 1"
    assert props["Build.FINGERPRINT"] == "test/phone/phone:15/TP1A.1/7:user/release-keys"
    assert (props["Build.DEVICE"], props["Build.PRODUCT"], props["Build.RADIO"]) == ("phone", "phone_eea", "unknown")
    assert (props["CellOperator"], props["SimOperator"]) == ("45005", "45005")
    assert props["GSF.version"] == "251333035"
    # the Play Store on emulator images without Google Play is a stub, so the default version is used
    assert props["Vending.version"] == str(profile_dump.DEFAULT_VENDING_VERSION)
    assert (props["Screen.Width"], props["Screen.Height"], props["Screen.Density"]) == ("1080", "2400", "420")
    assert (props["TouchScreen"], props["Keyboard"], props["Navigation"], props["ScreenLayout"]) == ("3", "1", "1", "2")
    assert (props["HasHardKeyboard"], props["HasFiveWayNavigation"]) == ("false", "false")
    assert props["GL.Extensions"] == "GL_EXT_one,GL_EXT_two"
    assert props["Features"] == "android.hardware.camera,android.hardware.wifi"
    assert props["Locales"] == "en_US,ko_KR"


def test_dump_without_operator():
    props = dump(FakeAdb(), operator=False)
    assert (props["CellOperator"], props["SimOperator"]) == ("", "")


def test_dump_keeps_a_real_play_store_version():
    assert dump(FakeAdb(vending_code=84582130))["Vending.versionString"] == "1.8"


def test_dump_without_device():
    with pytest.raises(ProfileError, match="no device properties"):
        dump(FakeAdb(getprop=""))


def test_to_properties_round_trip():
    props = dump(FakeAdb())
    text = to_properties(props)
    assert text.startswith("# Device profile generated by `playstore-cli profile` from Test Phone 1\n")
    assert "Build.FINGERPRINT=test/phone/phone\\:15/TP1A.1/7\\:user/release-keys\n" in text
    assert parse_properties(text) == props
