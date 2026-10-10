import pytest

from playstore_cli import auth, browser, store
from playstore_cli.__main__ import _category_id, main


class FakeApi:
    def __init__(self):
        self.calls = []

    def categories(self, parent):
        self.calls.append(parent)
        return {"APPLICATION": [("FINANCE", "Finance"), ("SPORTS", "Sports"),
                                ("HEALTH_AND_FITNESS", "Health & Fitness")],
                "GAME": [("GAME_SPORTS", "Sports"), ("GAME_PUZZLE", "Puzzle")]}[parent]


@pytest.mark.parametrize("name", ["FINANCE", "GAME", "game_action"])
def test_category_id_given_as_id_skips_lookup(name):
    fake = FakeApi()
    assert _category_id(fake, name) == name.upper()
    assert fake.calls == []


@pytest.mark.parametrize("name, cat_id", [
    ("Finance", "FINANCE"), ("finance", "FINANCE"), ("Games", "GAME"), ("apps", "APPLICATION"),
    ("Health & Fitness", "HEALTH_AND_FITNESS"),
    # an English name that is not its ID
    ("Puzzle", "GAME_PUZZLE"),
])
def test_category_id_by_name(name, cat_id):
    assert _category_id(FakeApi(), name) == cat_id


def test_category_id_single_word_without_name_match_is_an_id():
    # e.g. "finance" while category names come in another language
    assert _category_id(FakeApi(), "family") == "FAMILY"


def test_category_id_ambiguous_name():
    with pytest.raises(ValueError, match="SPORTS, GAME_SPORTS"):
        _category_id(FakeApi(), "Sports")


def test_category_id_unknown_name():
    with pytest.raises(ValueError, match="Category not found: No such thing"):
        _category_id(FakeApi(), "No such thing")


@pytest.fixture
def login(tmp_path, monkeypatch):
    """Runs `login` with a fake browser lookup, typed answers and token exchange; returns the exchanges."""
    monkeypatch.setenv("PLAYSTORE_HOME", str(tmp_path))
    monkeypatch.setattr(browser, "capture_oauth_token", lambda *a, **k: pytest.fail("opened the browser"))
    exchanged = []
    monkeypatch.setattr(auth, "exchange_oauth_token",
                        lambda email, token: exchanged.append((email, token)) or "aas_et/XYZ")

    def run(*argv, chrome=None, answers=()):
        monkeypatch.setattr(browser, "find_chrome", lambda: chrome)
        typed = iter(answers)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
        return main(["login", *argv]), exchanged

    return run


def test_login_without_chrome_asks_for_the_cookie(login, capsys):
    code, exchanged = login(answers=["a@b.com", " oauth2_4/abc \n"])

    assert code == 0 and exchanged == [("a@b.com", "oauth2_4/abc")]
    assert store.load_account()["aasToken"] == "aas_et/XYZ"
    out = capsys.readouterr().out
    assert "No Chrome-based browser found" in out and browser.EMBEDDED_SETUP_URL in out


def test_login_manual_skips_chrome(login, capsys):
    code, exchanged = login("--manual", "--email", "a@b.com", chrome="/usr/bin/chrome",
                            answers=["oauth_token=oauth2_4/abc"])

    assert code == 0 and exchanged == [("a@b.com", "oauth2_4/abc")]
    assert "No Chrome-based browser found" not in capsys.readouterr().out


def test_login_manual_without_a_token(login, capsys):
    code, exchanged = login("--manual", answers=["a@b.com", ""])

    assert code == 1 and exchanged == []
    assert "no oauth_token entered" in capsys.readouterr().err
