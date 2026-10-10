"""Generate a device profile (.properties) from an Android device or emulator over adb.

Every value comes from the device itself:
  getprop                       Build.*, GL version, ABIs, operator codes
  am get-config --device        screen size and density, input config, features, shared libraries,
                                GL extensions (the same data the Play Store uploads as its device config)
  dumpsys package               Google Play services and Play Store versions
  framework-res.apk             system locales (the locale qualifiers of its resources.arsc)
"""
from __future__ import annotations

import io
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

# Used when the device has no real Play Store (emulator images without Google Play ship a stub)
DEFAULT_VENDING_VERSION = 84582130
DEFAULT_VENDING_VERSION_STRING = "45.8.21-31 [0] [PR] 747433787"
MIN_VENDING_VERSION = 80000000

# Configuration qualifiers → DeviceConfigurationProto values (android.content.res.Configuration)
_TOUCHSCREEN = {"notouch": 1, "stylus": 2, "finger": 3}
_KEYBOARD = {"nokeys": 1, "qwerty": 2, "12key": 3}
_NAVIGATION = {"nonav": 1, "dpad": 2, "trackball": 3, "wheel": 4}
_SCREEN_SIZE = {"small": 1, "normal": 2, "large": 3, "xlarge": 4}

_RES_TABLE_TYPE = 0x0002
_RES_TABLE_PACKAGE_TYPE = 0x0200
_RES_TABLE_TYPE_TYPE = 0x0201


class ProfileError(Exception):
    pass


class Adb:
    def __init__(self, serial: str | None = None):
        self.cmd = [shutil.which("adb") or "adb", *(["-s", serial] if serial else [])]

    def shell(self, *args: str) -> str:
        return self._run("shell", *args).stdout.replace("\r\n", "\n")

    def pull(self, remote: str, local: Path) -> None:
        self._run("pull", remote, str(local))

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        try:
            r = subprocess.run([*self.cmd, *args], capture_output=True, text=True, timeout=120)
        except FileNotFoundError as e:
            raise ProfileError("adb not found; install Android platform-tools") from e
        if r.returncode != 0:
            raise ProfileError(f"adb {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
        return r


def _chunks(data: bytes, start: int, end: int):
    """(type, chunk start, header size, chunk size) of the resource chunks in data[start:end]."""
    off = start
    while off + 8 <= end:
        ctype, header_size, size = struct.unpack_from("<HHI", data, off)
        if size < 8 or off + size > end:
            raise ProfileError("malformed resources.arsc")
        yield ctype, off, header_size, size
        off += size


def _unpack_code(raw: bytes, base: str) -> str:
    """ResTable_config language or region: two ASCII bytes, or three characters packed in 15 bits
    (letters for a language, digits for a region such as 419), as in ResTable_config::unpackLanguageOrRegion."""
    if not raw[0]:
        return ""
    if raw[0] & 0x80:
        codes = (raw[1] & 0x1F, ((raw[1] >> 5) | (raw[0] << 3)) & 0x1F, (raw[0] >> 2) & 0x1F)
        return "".join(chr(ord(base) + c) for c in codes)
    return raw.decode("ascii")


def arsc_locales(arsc: bytes) -> list[str]:
    """Locales (af, af_ZA, ...) that the resource table has resources for, sorted."""
    ctype, header_size, size = struct.unpack_from("<HHI", arsc, 0)
    if ctype != _RES_TABLE_TYPE:
        raise ProfileError("not a resources.arsc")
    found = set()
    for ctype, off, header_size, size in _chunks(arsc, header_size, size):
        if ctype != _RES_TABLE_PACKAGE_TYPE:
            continue
        for ttype, toff, _, _ in _chunks(arsc, off + header_size, off + size):
            if ttype != _RES_TABLE_TYPE_TYPE:
                continue
            config = toff + 20  # ResTable_type: chunk header (8), id/flags/reserved (4), entryCount, entriesStart
            language = _unpack_code(arsc[config + 8: config + 10], "a")
            country = _unpack_code(arsc[config + 10: config + 12], "0")
            config_size = struct.unpack_from("<I", arsc, config)[0]
            script = arsc[config + 36: config + 40].rstrip(b"\0").decode("ascii") if config_size >= 40 else ""
            if language:
                found.add("_".join(x for x in (language, script, country) if x))
    return sorted(found)


def _getprop(adb: Adb) -> dict[str, str]:
    props = {}
    for line in adb.shell("getprop").splitlines():  # [key]: [value]
        if line.startswith("[") and "]: [" in line:
            key, _, value = line[1:].partition("]: [")
            props[key] = value.rstrip("]")
    return props


def _get_config(adb: Adb) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in adb.shell("am", "get-config", "--device").splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            out.setdefault(key, []).append(value.strip())
    return out


def _version(adb: Adb, package: str) -> tuple[int, str] | None:
    code = name = None
    for line in adb.shell("dumpsys", "package", package).splitlines():
        line = line.strip()
        if code is None and line.startswith("versionCode="):
            code = int(line.split("=", 1)[1].split()[0])
        elif name is None and line.startswith("versionName="):
            name = line.split("=", 1)[1]
    return (code, name or "") if code else None


def _locales(adb: Adb) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        apk = Path(tmp) / "framework-res.apk"
        adb.pull("/system/framework/framework-res.apk", apk)
        with zipfile.ZipFile(apk) as z:
            return arsc_locales(z.read("resources.arsc"))


def dump(adb: Adb, *, operator: bool = True) -> dict[str, str]:
    """Read a device profile from the device behind adb.

    operator=False leaves out the mobile operator codes (MCC/MNC), as on a device without a SIM.
    """
    p = _getprop(adb)
    if not p.get("ro.build.fingerprint"):
        raise ProfileError("no device properties (is a device connected? see adb devices)")
    config = _get_config(adb)
    qualifiers = config.get("config", [""])[0].split("-")

    def pick(table: dict[str, int], default: int) -> int:
        return next((v for q, v in table.items() if q in qualifiers), default)

    keyboard, navigation = pick(_KEYBOARD, 1), pick(_NAVIGATION, 1)
    gms = _version(adb, "com.google.android.gms")
    vending = _version(adb, "com.android.vending")
    if not vending or vending[0] < MIN_VENDING_VERSION:
        vending = (DEFAULT_VENDING_VERSION, DEFAULT_VENDING_VERSION_STRING)
    manufacturer, model = p.get("ro.product.manufacturer", ""), p.get("ro.product.model", "")
    return {
        "UserReadableName": f"{manufacturer} {model}".strip(),
        "Build.FINGERPRINT": p["ro.build.fingerprint"],
        "Build.ID": p.get("ro.build.id", ""),
        "Build.BRAND": p.get("ro.product.brand", ""),
        "Build.MANUFACTURER": manufacturer,
        "Build.MODEL": model,
        "Build.DEVICE": p.get("ro.product.device", ""),
        "Build.PRODUCT": p.get("ro.product.name", ""),
        "Build.HARDWARE": p.get("ro.hardware", ""),
        "Build.RADIO": p.get("gsm.version.baseband") or "unknown",
        "Build.BOOTLOADER": p.get("ro.bootloader") or "unknown",
        "Build.VERSION.SDK_INT": p.get("ro.build.version.sdk", ""),
        "Build.VERSION.RELEASE": p.get("ro.build.version.release", ""),
        "Client": p.get("ro.com.google.clientidbase") or "android-google",
        "GSF.version": str(gms[0] if gms else 0),
        "Vending.version": str(vending[0]),
        "Vending.versionString": vending[1],
        "CellOperator": p.get("gsm.operator.numeric", "").split(",")[0] if operator else "",
        "SimOperator": p.get("gsm.sim.operator.numeric", "").split(",")[0] if operator else "",
        "Roaming": "mobile-notroaming",
        "Platforms": p.get("ro.product.cpu.abilist", ""),
        "Screen.Width": config.get("stable-width-px", ["0"])[0],
        "Screen.Height": config.get("stable-height-px", ["0"])[0],
        "Screen.Density": config.get("stable-density-dpi", ["0"])[0],
        "TouchScreen": str(pick(_TOUCHSCREEN, 3)),
        "Keyboard": str(keyboard),
        "Navigation": str(navigation),
        "ScreenLayout": str(pick(_SCREEN_SIZE, 2)),
        "HasHardKeyboard": str(keyboard == _KEYBOARD["qwerty"]).lower(),
        "HasFiveWayNavigation": str(navigation in (_NAVIGATION["dpad"], _NAVIGATION["trackball"])).lower(),
        "GL.Version": p.get("ro.opengles.version", "0"),
        "GL.Extensions": ",".join(config.get("opengl-extensions", [])),
        "Features": ",".join(config.get("features", [])),
        "SharedLibraries": ",".join(config.get("shared-libraries", [])),
        "Locales": ",".join(_locales(adb)),
    }


def to_properties(props: dict[str, str]) -> str:
    """Java .properties text, escaping ':' and '=' in values the way device.parse_properties reads them."""
    buf = io.StringIO()
    buf.write(f"# Device profile generated by `playstore-cli profile` from {props['UserReadableName']}\n")
    buf.write(f"# {props['Build.FINGERPRINT']}\n\n")
    for key, value in props.items():
        buf.write(f"{key}={value.replace(':', chr(92) + ':').replace('=', chr(92) + '=')}\n")
    return buf.getvalue()
