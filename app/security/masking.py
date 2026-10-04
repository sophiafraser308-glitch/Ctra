"""Secret masking used by logging, audit and error reporting."""
from __future__ import annotations

import re
from typing import Any

_TELEGRAM_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_KV = re.compile(r"(?i)\b(access[_-]?token|refresh[_-]?token|client[_-]?secret|encryption[_-]?key|password|secret|api[_-]?key|accessToken|refreshToken)\b(\s*[=:]\s*)([^\s,;&'\"}]+)")
_URL_CRED = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)")
_QS = re.compile(r"(?i)([?&](?:access_token|refresh_token|client_secret|code)=)[^&\s]+")

SENSITIVE_KEYS = ("token", "secret", "password", "passwd", "key", "authorization", "credential", "code_verifier")
MASK = "***"


class Masker:
    def __init__(self) -> None:
        self._exact: list[str] = []

    def register(self, *values: str) -> None:
        for v in values:
            if v and len(v) >= 4 and v not in self._exact:
                self._exact.append(v)
        self._exact.sort(key=len, reverse=True)

    def mask(self, text: str) -> str:
        if not text:
            return text
        for v in self._exact:
            if v in text:
                text = text.replace(v, MASK)
        text = _TELEGRAM_TOKEN.sub(MASK, text)
        text = _BEARER.sub(r"\1" + MASK, text)
        text = _KV.sub(lambda m: m.group(1) + m.group(2) + MASK, text)
        text = _URL_CRED.sub(r"\1" + MASK + r"\3", text)
        text = _QS.sub(r"\1" + MASK, text)
        return text

    def mask_obj(self, obj: Any, _depth: int = 0) -> Any:
        if _depth > 8:
            return MASK
        if isinstance(obj, str):
            return self.mask(obj)
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(k, str) and any(s in k.lower() for s in SENSITIVE_KEYS) and not k.lower().endswith("_at"):
                    out[k] = MASK
                else:
                    out[k] = self.mask_obj(v, _depth + 1)
            return out
        if isinstance(obj, (list, tuple, set)):
            return [self.mask_obj(v, _depth + 1) for v in obj]
        return obj


masker = Masker()


def mask_secret(value: str, keep: int = 4) -> str:
    """For *display* of non-sensitive identifiers such as client IDs."""
    if not value:
        return "—"
    if len(value) <= keep * 2:
        return value[:1] + "…"
    return value[:keep] + "…" + value[-keep:]
