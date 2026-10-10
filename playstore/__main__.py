"""uv run python -m playstore <command>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from . import apksig, auth, browser, checkin, download, session, store
from .api import NotDownloadable, PlayApi, PlayApiError, certificate_hashes
from .device import DEFAULT_PROFILE, Profile


class _Missing(Exception):
    """A previous step's result (login, checkin) is missing."""


def _mask(token: str) -> str:
    return f"{token[:12]}… ({len(token)}자)"


def _account() -> dict:
    account = store.load_account()
    if not account:
        raise _Missing("저장된 계정이 없습니다. 먼저 login 을 실행하세요.")
    return account


def _device() -> dict:
    device = store.load_device()
    if not device:
        raise _Missing("등록된 기기가 없습니다. 먼저 checkin 을 실행하세요.")
    return device


def _new_session(account: dict, device: dict) -> dict:
    print("Play 세션 생성 중...")
    sess = session.build(account, device, log=lambda m: print(f"  {m}"))
    store.save_session(sess)
    return sess


def _play_api(*, fresh: bool = False) -> PlayApi:
    account, device = _account(), _device()
    sess = None if fresh else store.load_session()
    if not sess or sess.get("gsfId") != device["gsfId"]:  # a newly registered device needs a new session
        sess = _new_session(account, device)
    return PlayApi(device, sess)


def _with_api(call):
    """Run call(api). If the Play token expired (401), rebuild the session from the saved AAS token and retry once."""
    try:
        return call(_play_api())
    except PlayApiError as e:
        if e.status != 401:
            raise
        print("Play 토큰이 만료돼 세션을 새로 만듭니다.")
        return call(_play_api(fresh=True))


def cmd_login(args) -> int:
    email, oauth_token = args.email, args.oauth_token
    if not oauth_token:
        print("브라우저 창에서 구글 계정으로 로그인하세요 (2단계 인증 포함). 끝나면 창이 자동으로 닫힙니다.")
        scraped, oauth_token = browser.capture_oauth_token(store.chrome_profile_dir(),
                                                           timeout=args.timeout)
        print(f"oauth_token 획득: {_mask(oauth_token)}")
        email = email or scraped
    if not email:
        # The oauth_token expires within minutes, so this must be entered right away
        email = input("이메일을 자동으로 찾지 못했습니다. 계정 이메일: ").strip()

    aas = auth.exchange_oauth_token(email, oauth_token)
    path = store.save_account(email, aas)
    print(f"AAS 토큰 획득: {_mask(aas)} — {email}")
    print(f"저장: {path}")
    return 0


def cmd_checkin(args) -> int:
    account = _account()
    existing = store.load_device()
    if existing and not args.force:
        # Every checkin registers a new device on the account, so reuse the existing one
        print(f"이미 등록된 기기가 있습니다: gsfId={existing['gsfId']} ({existing['profile']})")
        print("새로 등록하려면 --force")
        return 0

    profile = Profile.load(args.profile)
    print(f"ac2dm 토큰 교환 중... ({account['email']})")
    ac2dm = auth.exchange_aas_token(account["email"], account["aasToken"], "ac2dm")
    print(f"checkin 중... ({profile.props['UserReadableName']}, {args.locale}, {args.timezone})")
    result = checkin.checkin(profile, account["email"], ac2dm,
                             locale=args.locale, timezone=args.timezone)
    path = store.save_device({"profile": profile.name, "locale": args.locale,
                              "timezone": args.timezone, **result})
    print(f"gsfId: {result['gsfId']}")
    print(f"저장: {path}")
    return 0


def cmd_session(args) -> int:
    _new_session(_account(), _device())
    print(f"저장: {store.session_path()}")
    return 0


def cmd_details(args) -> int:
    doc = _with_api(lambda api: api.details(args.package))
    if not doc.docid:
        print(f"앱 정보가 없습니다: {args.package}", file=sys.stderr)
        return 1
    a = doc.details.appDetails
    print(f"제목     {doc.title}")
    print(f"패키지   {a.packageName or doc.docid}")
    print(f"버전     {a.versionString} ({a.versionCode})")
    print(f"개발자   {a.developerName or doc.creator}")
    print(f"업데이트 {a.uploadDate}")
    print(f"설치수   {a.numDownloads}")  # the value already includes localized wording such as "… downloads"
    print(f"크기     {a.installationSize / 1024 / 1024:.1f} MB")
    print(f"권한     {len(a.permission)}개")
    for perm in a.permission:
        print(f"  {perm}")
    return 0


def _price(doc) -> str:
    return next((o.formattedAmount for o in doc.offer if o.offerType == 1 and o.micros), "무료")


def cmd_search(args) -> int:
    apps = _with_api(lambda api: api.search(args.query, args.limit))
    if not apps:
        print(f"검색 결과가 없습니다: {args.query}", file=sys.stderr)
        return 1
    for i, doc in enumerate(apps, 1):
        a = doc.details.appDetails
        print(f"{i:>3}. {doc.title} — {doc.creator}")
        print(f"     {doc.docid} · {a.versionString} · {a.numDownloads} · {_price(doc)}")
    return 0


def cmd_cert(args) -> int:
    doc = _with_api(lambda api: api.details(args.package))
    if not doc.docid:
        print(f"앱 정보가 없습니다: {args.package}", file=sys.stderr)
        return 1
    a = doc.details.appDetails
    print(f"{doc.title} {a.versionString} ({a.versionCode}) — {_price(doc)}")

    hashes = certificate_hashes(doc)
    print("Play details 의 서명 인증서:" if hashes else "details 응답에 서명 정보(certificateSet)가 없습니다.")
    for sha1, sha256 in hashes:
        print(f"  SHA-1   {sha1}")
        print(f"  SHA-256 {sha256}")
    if not args.apk:
        return 0 if hashes else 1

    _, data = _with_api(lambda api: api.app_delivery(args.package))
    reader = apksig.HttpRangeReader(data.downloadUrl,
                                    {c.name: c.value for c in data.downloadAuthCookie},
                                    size=data.downloadSize)
    signers = apksig.read_signers(reader)
    print(f"APK 서명 블록 (base.apk {data.downloadSize / 1024 / 1024:.1f} MB 중 "
          f"Range {reader.requests}회 {reader.fetched / 1024:.1f} KB):")
    for s in signers:
        print(f"  {s.scheme:<4} SHA-1   {s.sha1}")
        print(f"       SHA-256 {s.sha256}")
    same = {s.sha1 for s in signers} & {sha1 for sha1, _ in hashes}
    print("→ details 와 일치" if same else "→ details 와 불일치!")
    return 0 if same else 1


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _progress_line(prefix: str):
    """On a terminal, overwrite progress on the same line (in 1% steps)."""
    if not sys.stdout.isatty():
        return None
    last = -1

    def show(done: int, total: int):
        nonlocal last
        pct = done * 100 // total if total else 0
        if pct != last:
            last = pct
            print(f"\r{prefix}  {pct:3d}%", end="", flush=True)
    return show


def cmd_download(args) -> int:
    doc, data = _with_api(lambda api: api.app_delivery(args.package, args.version_code))
    a = doc.details.appDetails
    vc = args.version_code or a.versionCode
    # versionString is only in details (the latest version)
    version = (f"{a.versionString} ({vc})" if vc == a.versionCode
               else f"versionCode {vc} (최신 {a.versionString} / {a.versionCode})")
    out = Path(args.out) / f"{args.package}-{vc}"
    files = download.play_files(args.package, data)
    print(f"{doc.title} {version} — 파일 {len(files)}개, {_mb(sum(f.size for f in files))} → {out}")

    downloader = download.Downloader(data)
    for i, f in enumerate(files, 1):
        prefix = f"  [{i}/{len(files)}] {f.name} ({_mb(f.size)})"
        fetched = downloader.fetch(f, out, progress=_progress_line(prefix))
        print(f"\r{prefix}  {'OK' if fetched else '이미 있음'}  ")

    apks = [f.name for f in files if f.name.endswith(".apk")]
    print(f"설치: adb install-multiple {out}/*.apk" if len(apks) > 1 else f"설치: adb install {out}/base.apk")
    if len(apks) < len(files):
        print(f"OBB 는 기기의 /sdcard/Android/obb/{args.package}/ 에 넣으세요.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="playstore")
    sub = p.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="구글 계정 로그인 → AAS 토큰 저장")
    login.add_argument("--email", help="계정 이메일 (브라우저 로그인 시 자동 감지)")
    login.add_argument("--oauth-token",
                       help="EmbeddedSetup 의 oauth_token 쿠키 값(oauth2_4/...). 주면 브라우저를 띄우지 않음")
    login.add_argument("--timeout", type=float, default=600, help="브라우저 로그인 대기 시간(초)")
    login.set_defaults(func=cmd_login)

    ci = sub.add_parser("checkin", help="가상 기기 등록 → gsfId 저장")
    ci.add_argument("--profile", default=DEFAULT_PROFILE, help=f"기기 프로필 (기본 {DEFAULT_PROFILE})")
    ci.add_argument("--locale", default="ko_KR")
    ci.add_argument("--timezone", default="Asia/Seoul")
    ci.add_argument("--force", action="store_true", help="등록된 기기가 있어도 새로 등록")
    ci.set_defaults(func=cmd_checkin)

    se = sub.add_parser("session", help="Play 세션 생성 (Play 토큰·기기 구성 업로드·약관)")
    se.set_defaults(func=cmd_session)

    de = sub.add_parser("details", help="앱 상세 정보 조회")
    de.add_argument("package", help="패키지명 (예: com.google.android.youtube)")
    de.set_defaults(func=cmd_details)

    sr = sub.add_parser("search", help="앱 검색 → 패키지명 찾기")
    sr.add_argument("query", help="검색어 (예: youtube)")
    sr.add_argument("-n", "--limit", type=int, default=20, help="최대 결과 수 (기본 20)")
    sr.set_defaults(func=cmd_search)

    ce = sub.add_parser("cert", help="정식 서명 인증서 해시 (details 의 certificateSet)")
    ce.add_argument("package", help="패키지명 (예: com.google.android.youtube)")
    ce.add_argument("--apk", action="store_true",
                    help="APK 끝부분을 받아 서명 블록과 대조 (무료 앱만, 보관함에 없으면 purchase 함)")
    ce.set_defaults(func=cmd_cert)

    dl = sub.add_parser("download", help="APK 다운로드 (base + split + OBB, 무료 앱만)")
    dl.add_argument("package", help="패키지명 (예: com.google.android.youtube)")
    dl.add_argument("-v", "--version-code", type=int,
                    help="받을 버전의 versionCode (Play 에 남아 있는 과거 버전). 생략하면 최신")
    dl.add_argument("-o", "--out", default="downloads",
                    help="저장 위치. 그 아래 <패키지>-<versionCode>/ 에 받는다 (기본 downloads)")
    dl.set_defaults(func=cmd_download)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (_Missing, browser.LoginError, auth.AuthError, checkin.CheckinError, PlayApiError,
            NotDownloadable, apksig.SignatureError, download.DownloadError,
            requests.RequestException, ValueError) as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
