import pytest

from app.bot.callbacks.data import cb, split, split_confirm


def test_cb_roundtrip_and_limit():
    assert cb("pos", "close", "abc123") == "pos:close:abc123"
    with pytest.raises(ValueError):
        cb("x" * 70)


def test_split_confirm():
    assert split_confirm("bot:stop:abc:c1") == ("bot:stop:abc", "c1")
    assert split_confirm("bot:stop:abc") == ("bot:stop:abc", None)
    assert split("a:b:c") == ["a", "b", "c"]
