"""Virtual device profile: the device information sent with checkin and Play API requests.

Profiles use Aurora Store's device .properties format as-is (playstore/profiles/).
pixel_9a.properties is an Aurora/Calyx profile (GPL-3.0-or-later).
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import quote

from . import googleplay_pb2 as pb

PROFILES_DIR = Path(__file__).parent / "profiles"
DEFAULT_PROFILE = "pixel_9a"


def parse_properties(text: str) -> dict[str, str]:
    """Java .properties (key=value, # comments, \\: \\= escapes) → dict."""
    props = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        key, _, value = line.partition("=")
        props[key.strip()] = value.strip().replace("\\:", ":").replace("\\=", "=")
    return props


def available_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.properties"))


class Profile:
    def __init__(self, name: str, props: dict[str, str]):
        self.name = name
        self.props = props

    @classmethod
    def load(cls, name: str = DEFAULT_PROFILE) -> Profile:
        path = PROFILES_DIR / f"{name}.properties"
        if not path.exists():
            raise ValueError(f"프로필 없음: {name} (있는 것: {', '.join(available_profiles())})")
        return cls(name, parse_properties(path.read_text()))

    def _list(self, key: str) -> list[str]:
        return [x for x in self.props[key].split(",") if x]

    def user_agent(self) -> str:
        """User-Agent of the Play Store app (Finsky)."""
        p = self.props
        params = [
            "api=3",
            f"versionCode={p['Vending.version']}",
            f"sdk={p['Build.VERSION.SDK_INT']}",
            f"device={p['Build.DEVICE']}",
            f"hardware={p['Build.HARDWARE']}",
            f"product={p['Build.PRODUCT']}",
            f"platformVersionRelease={p['Build.VERSION.RELEASE']}",
            f"model={quote(p['Build.MODEL'])}",
            f"buildId={p['Build.ID']}",
            "isWideScreen=0",
            f"supportedAbis={p['Platforms'].replace(',', ';')}",
        ]
        return f"Android-Finsky/{p['Vending.versionString']} ({','.join(params)})"

    def device_config(self) -> pb.DeviceConfigurationProto:
        p = self.props
        return pb.DeviceConfigurationProto(
            touchScreen=int(p["TouchScreen"]),
            keyboard=int(p["Keyboard"]),
            navigation=int(p["Navigation"]),
            screenLayout=int(p["ScreenLayout"]),
            hasHardKeyboard=p["HasHardKeyboard"] == "true",
            hasFiveWayNavigation=p["HasFiveWayNavigation"] == "true",
            screenDensity=int(p["Screen.Density"]),
            screenWidth=int(p["Screen.Width"]),
            screenHeight=int(p["Screen.Height"]),
            glEsVersion=int(p["GL.Version"]),
            nativePlatform=self._list("Platforms"),
            systemSharedLibrary=self._list("SharedLibraries"),
            systemAvailableFeature=self._list("Features"),
            systemSupportedLocale=self._list("Locales"),
            glExtension=self._list("GL.Extensions"),
        )

    def checkin_request(self, locale: str, timezone: str) -> pb.AndroidCheckinRequest:
        """Checkin request for a new device (id=0). Built like gpapi DeviceBuilder.getAndroidCheckinRequest."""
        p = self.props
        build = pb.AndroidBuildProto(
            id=p["Build.FINGERPRINT"],
            product=p["Build.HARDWARE"],
            carrier=p["Build.BRAND"],
            radio=p["Build.RADIO"],
            bootloader=p["Build.BOOTLOADER"],
            client=p["Client"],
            timestamp=int(time.time()),
            googleServices=int(p["GSF.version"]),
            device=p["Build.DEVICE"],
            sdkVersion=int(p["Build.VERSION.SDK_INT"]),
            model=p["Build.MODEL"],
            manufacturer=p["Build.MANUFACTURER"],
            buildProduct=p["Build.PRODUCT"],
            otaInstalled=False,
        )
        return pb.AndroidCheckinRequest(
            id=0,
            checkin=pb.AndroidCheckinProto(
                build=build,
                lastCheckinMsec=0,
                cellOperator=p["CellOperator"],
                simOperator=p["SimOperator"],
                roaming=p["Roaming"],
                userNumber=0,
            ),
            locale=locale,
            timeZone=timezone,
            version=3,
            deviceConfiguration=self.device_config(),
            fragment=0,
        )
