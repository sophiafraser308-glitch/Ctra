"""Callback data helpers. Format  section:action:arg...  (Telegram limit: 64 bytes)."""
from __future__ import annotations


def cb(*parts: object) -> str:
    data = ":".join(str(p) for p in parts)
    if len(data.encode()) > 64:
        raise ValueError(f"callback_data too long: {data!r}")
    return data


def split(data: str | None) -> list[str]:
    return (data or "").split(":")


def split_confirm(data: str) -> tuple[str, str | None]:
    parts = data.split(":")
    if parts[-1] in ("c1", "c2"):
        return ":".join(parts[:-1]), parts[-1]
    return data, None
