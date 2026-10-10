"""Play 스토어 내부 API(fdfe) 클라이언트.

요청은 android.clients.google.com/fdfe/<경로>, 응답은 모두 ResponseWrapper protobuf 다.
헤더 구성은 Aurora Store HeaderProvider (alltechdev get_auth_headers 경유)를 따른다.
"""
from __future__ import annotations

import requests
from google.protobuf.message import DecodeError

from . import googleplay_pb2 as pb
from .device import Profile

FDFE_URL = "https://android.clients.google.com/fdfe/"

# Aurora 가 보내는 X-DFE-Encoded-Targets 값 (실험·기능 대상 목록, 불투명 상수)
ENCODED_TARGETS = "CAESN/qigQYC2AMBFfUbyA7SM5Ij/CvfBoIDgxXrBPsDlQUdMfOLAfoFrwEHgAcBrQYhoA0cGt4MKK0Y2gI"


class PlayApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class NotDownloadable(Exception):
    """유료 앱, 기기 비호환 등으로 다운로드 URL 을 받을 수 없음."""


class PlayApi:
    """device: checkin 결과(device.json), session: Play 세션 토큰들(session.json)."""

    def __init__(self, device: dict, session: dict):
        self.profile = Profile.load(device["profile"])
        self.device = device
        self.session = session
        self._http = requests.Session()

    def headers(self) -> dict:
        locale = self.device["locale"]
        h = {
            "Authorization": f"Bearer {self.session['authToken']}",
            "User-Agent": self.profile.user_agent(),
            "X-DFE-Device-Id": self.device["gsfId"],
            "X-DFE-Device-Checkin-Consistency-Token": self.device["deviceCheckinConsistencyToken"],
            "Accept-Language": locale.replace("_", "-"),
            "X-DFE-UserLanguages": locale,
            "X-DFE-MCCMNC": self.profile.props["CellOperator"],
            "X-DFE-Encoded-Targets": ENCODED_TARGETS,
            "X-DFE-Client-Id": "am-android-google",
            "X-DFE-Network-Type": "4",
            "X-DFE-Content-Filters": "",
            "X-DFE-Request-Params": "timeoutMs=4000",
            "X-DFE-No-Prefetch": "true",
            "X-Limit-Ad-Tracking-Enabled": "false",
            "X-Ad-Id": "",
        }
        if self.session.get("deviceConfigToken"):
            h["X-DFE-Device-Config-Token"] = self.session["deviceConfigToken"]
        if self.session.get("dfeCookie"):
            h["X-DFE-Cookie"] = self.session["dfeCookie"]
        return h

    def request(self, method: str, path: str, *, params=None, data=None,
                extra_headers: dict | None = None) -> pb.ResponseWrapper:
        resp = self._http.request(method, FDFE_URL + path, params=params, data=data, timeout=30,
                                  headers={**self.headers(), **(extra_headers or {})})
        try:
            wrapper = pb.ResponseWrapper.FromString(resp.content)
        except DecodeError:
            wrapper = None
        if resp.status_code != 200:
            message = wrapper and wrapper.commands.displayErrorMessage
            raise PlayApiError(resp.status_code, message or resp.text[:200])
        if wrapper is None:
            raise PlayApiError(resp.status_code, f"protobuf 가 아닌 응답: {resp.content[:80]!r}")
        return wrapper

    def upload_device_config(self) -> str:
        """기기 구성(화면·기능·ABI 등)을 계정에 올리고 deviceConfigToken 을 받는다."""
        body = pb.UploadDeviceConfigRequest(deviceConfiguration=self.profile.device_config())
        w = self.request("POST", "uploadDeviceConfig", data=body.SerializeToString(), extra_headers={
            "Content-Type": "application/x-protobuf",
            # gpapi getDeviceUploadHeaders / Aurora uploadDeviceConfig 와 같은 추가 헤더
            "X-DFE-Enabled-Experiments": "cl:billing.select_add_instrument_by_default",
            "X-DFE-Unsupported-Experiments": (
                "nocache:billing.use_charging_poller,market_emails,buyer_currency,prod_baseline,"
                "checkin.set_asset_paid_app_field,shekel_test,content_ratings,buyer_currency_in_app,"
                "nocache:encrypted_apk,recent_changes"),
            "X-DFE-SmallestScreenWidthDp": "320",
            "X-DFE-Filter-Level": "3",
        })
        return w.payload.uploadDeviceConfigResponse.uploadDeviceConfigToken

    def toc(self) -> pb.TocResponse:
        return self.request("GET", "toc").payload.tocResponse

    def accept_tos(self, tos_token: str) -> None:
        self.request("POST", "acceptTos", data={"tost": tos_token, "toscme": "false"})

    def details(self, package: str) -> pb.DocV2:
        return self.request("GET", "details", params={"doc": package}).payload.detailsResponse.docV2

    def purchase(self, package: str, version_code: int) -> str:
        """무료 앱을 계정 보관함에 추가('획득')한다. downloadToken 이 오면 delivery 에 넘긴다."""
        w = self.request("POST", "purchase", data={"doc": package, "ot": "1", "vc": str(version_code)})
        return w.payload.buyResponse.downloadToken

    def delivery(self, package: str, version_code: int,
                 download_token: str = "") -> pb.AndroidAppDeliveryData:
        params = {"doc": package, "ot": "1", "vc": str(version_code)}
        if download_token:
            params["dtok"] = download_token
        return self.request("GET", "delivery", params=params).payload.deliveryResponse.appDeliveryData

    def app_delivery(self, package: str) -> tuple[pb.DocV2, pb.AndroidAppDeliveryData]:
        """details → delivery (보관함에 없으면 purchase 후 재시도). 다운로드 URL 이 든 데이터를 반환."""
        doc = self.details(package)
        paid = next((o for o in doc.offer if o.offerType == 1 and o.micros > 0), None)
        if paid:
            raise NotDownloadable(f"유료 앱 ({paid.formattedAmount})")
        vc = doc.details.appDetails.versionCode
        if not vc:
            raise NotDownloadable("이 기기 프로필에 호환되는 버전이 없음 (versionCode=0)")

        try:
            data = self.delivery(package, vc)
        except PlayApiError:
            data = None
        if not (data and data.downloadUrl):
            data = self.delivery(package, vc, self.purchase(package, vc))
        if not data.downloadUrl:
            raise NotDownloadable("delivery 응답에 다운로드 URL 이 없음")
        return doc, data
