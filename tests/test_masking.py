from app.security.masking import Masker, mask_secret


def test_telegram_token_and_kv_masked():
    m = Masker()
    t = "token 123456789:AAFabcdefghijklmnopqrstuvwxyz0123456 and access_token=abc123secretvalue&x=1"
    out = m.mask(t)
    assert "AAFabcdef" not in out and "abc123secretvalue" not in out


def test_exact_registered_secret_masked():
    m = Masker()
    m.register("super-secret-value")
    assert "super-secret-value" not in m.mask("x super-secret-value y")


def test_mask_obj_sensitive_keys():
    m = Masker()
    out = m.mask_obj({"access_token": "abc", "nested": {"client_secret": "x", "ok": 1}, "expires_at": "t"})
    assert out["access_token"] == "***" and out["nested"]["client_secret"] == "***"
    assert out["nested"]["ok"] == 1 and out["expires_at"] == "t"


def test_bearer_and_url_credentials():
    m = Masker()
    assert "abcdefghijk" not in m.mask("Authorization: Bearer abcdefghijklmnop")
    assert "pa55word" not in m.mask("postgresql://user:pa55word@host/db")


def test_mask_secret_display():
    assert mask_secret("1234567890abcdef") == "1234…cdef"
