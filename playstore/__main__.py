"""uv run python -m playstore <command>"""
from __future__ import annotations

import argparse
import sys

from . import auth, browser, checkin, session, store
from .api import PlayApi, PlayApiError
from .device import DEFAULT_PROFILE, Profile


class _Missing(Exception):
    """앞 단계(login, checkin) 결과가 없음."""


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
    if not sess or sess.get("gsfId") != device["gsfId"]:  # 기기를 새로 등록했으면 세션도 새로
        sess = _new_session(account, device)
    return PlayApi(device, sess)


def cmd_login(args) -> int:
    email, oauth_token = args.email, args.oauth_token
    if not oauth_token:
        print("브라우저 창에서 구글 계정으로 로그인하세요 (2단계 인증 포함). 끝나면 창이 자동으로 닫힙니다.")
        scraped, oauth_token = browser.capture_oauth_token(store.chrome_profile_dir(),
                                                           timeout=args.timeout)
        print(f"oauth_token 획득: {_mask(oauth_token)}")
        email = email or scraped
    if not email:
        # oauth_token 은 몇 분 안에 만료되므로 바로 입력해야 한다
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
        # checkin 할 때마다 계정에 새 기기가 등록되므로, 있으면 재사용한다
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
    try:
        doc = _play_api().details(args.package)
    except PlayApiError as e:
        if e.status != 401:
            raise
        print("Play 토큰이 만료돼 세션을 새로 만듭니다.")
        doc = _play_api(fresh=True).details(args.package)
    if not doc.docid:
        print(f"앱 정보가 없습니다: {args.package}", file=sys.stderr)
        return 1
    a = doc.details.appDetails
    print(f"제목     {doc.title}")
    print(f"패키지   {a.packageName or doc.docid}")
    print(f"버전     {a.versionString} ({a.versionCode})")
    print(f"개발자   {a.developerName or doc.creator}")
    print(f"업데이트 {a.uploadDate}")
    print(f"설치수   {a.numDownloads}")  # 값에 "다운로드 …회 이상" 문구가 포함돼 옴
    print(f"크기     {a.installationSize / 1024 / 1024:.1f} MB")
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

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (_Missing, browser.LoginError, auth.AuthError, checkin.CheckinError, PlayApiError,
            ValueError) as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
