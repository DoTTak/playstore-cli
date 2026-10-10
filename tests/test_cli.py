import pytest

from playstore.__main__ import _category_id


class FakeApi:
    def __init__(self):
        self.calls = []

    def categories(self, parent):
        self.calls.append(parent)
        return {"APPLICATION": [("FINANCE", "금융"), ("SPORTS", "스포츠"), ("ANDROID_AUTO_PROJECTED", "Android Auto")],
                "GAME": [("GAME_SPORTS", "스포츠")]}[parent]


@pytest.mark.parametrize("name", ["FINANCE", "finance", "game_action"])
def test_category_id_given_as_id_skips_lookup(name):
    fake = FakeApi()
    assert _category_id(fake, name) == name.upper()
    assert fake.calls == []


@pytest.mark.parametrize("name, cat_id", [
    ("금융", "FINANCE"), ("게임", "GAME"), ("앱", "APPLICATION"), ("Android Auto", "ANDROID_AUTO_PROJECTED")])
def test_category_id_by_name(name, cat_id):
    assert _category_id(FakeApi(), name) == cat_id


def test_category_id_ambiguous_name():
    with pytest.raises(ValueError, match="SPORTS, GAME_SPORTS"):
        _category_id(FakeApi(), "스포츠")


def test_category_id_unknown_name():
    with pytest.raises(ValueError, match="카테고리를 찾지 못했습니다: 없음"):
        _category_id(FakeApi(), "없음")
