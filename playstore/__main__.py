"""uv run python -m playstore <command>"""
from __future__ import annotations

import argparse
import sys

from . import auth, browser, store


def _mask(token: str) -> str:
    return f"{token[:12]}… ({len(token)}자)"


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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="playstore")
    sub = p.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="구글 계정 로그인 → AAS 토큰 저장")
    login.add_argument("--email", help="계정 이메일 (브라우저 로그인 시 자동 감지)")
    login.add_argument("--oauth-token",
                       help="EmbeddedSetup 의 oauth_token 쿠키 값(oauth2_4/...). 주면 브라우저를 띄우지 않음")
    login.add_argument("--timeout", type=float, default=600, help="브라우저 로그인 대기 시간(초)")
    login.set_defaults(func=cmd_login)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (browser.LoginError, auth.AuthError) as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
