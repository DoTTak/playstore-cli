import pytest

from playstore_cli.__main__ import _category_id


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
