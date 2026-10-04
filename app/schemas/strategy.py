from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

SEMVER = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\- ]{1,47}$")
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


class StrategyMetadata(BaseModel):
    name: str
    version: str | None = None
    description: str = ""
    author: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=lambda: ["M5"])
    dependencies: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("name must start with a letter, 2-48 chars of letters/digits/_/-/space")
        return v.strip()

    @field_validator("version")
    @classmethod
    def _ver(cls, v: str | None) -> str | None:
        if v is None:
            return None
        m = SEMVER.match(v.strip())
        if not m:
            raise ValueError("version must look like 1.2.3")
        return "{}.{}.{}".format(*m.groups())

    @field_validator("timeframes")
    @classmethod
    def _tf(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if t not in TIMEFRAMES]
        if bad:
            raise ValueError(f"unsupported timeframes {bad}; allowed {TIMEFRAMES}")
        return v

    @field_validator("parameters")
    @classmethod
    def _params(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 50:
            raise ValueError("too many parameters (max 50)")
        for k, val in v.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", str(k)):
                raise ValueError(f"bad parameter name {k!r}")
            if not isinstance(val, (int, float, str, bool)):
                raise ValueError(f"parameter {k} must be int/float/str/bool")
        return v


def bump_patch(version: str) -> str:
    a, b, c = (int(x) for x in version.split("."))
    return f"{a}.{b}.{c + 1}"


def version_key(version: str) -> tuple[int, int, int]:
    a, b, c = (int(x) for x in version.lstrip("v").split("."))
    return a, b, c
