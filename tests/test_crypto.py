import pytest

from app.core.exceptions import ConfigError
from app.security.crypto import CryptoService


def test_roundtrip():
    c = CryptoService(CryptoService.generate_key())
    tok = c.encrypt("my-access-token")
    assert tok != "my-access-token" and c.decrypt(tok) == "my-access-token"


def test_wrong_key_fails():
    a, b = CryptoService(CryptoService.generate_key()), CryptoService(CryptoService.generate_key())
    with pytest.raises(ConfigError):
        b.decrypt(a.encrypt("x"))


def test_invalid_key_rejected():
    with pytest.raises(ConfigError):
        CryptoService("not-a-key")
