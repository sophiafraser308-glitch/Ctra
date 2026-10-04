"""Lazy access to the official Spotware SDK (`ctrader-open-api`). Imported lazily so that the rest of the
application (and unit tests) can be imported without the SDK or Twisted installed."""
from __future__ import annotations

from types import SimpleNamespace

from app.core.exceptions import CTraderError

_sdk: SimpleNamespace | None = None


def sdk() -> SimpleNamespace:
    global _sdk
    if _sdk is None:
        try:
            from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
            from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as common
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
            from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
        except ImportError as exc:  # pragma: no cover
            raise CTraderError("Package 'ctrader-open-api' (and twisted) must be installed: pip install -r requirements.txt") from exc
        _sdk = SimpleNamespace(Client=Client, EndPoints=EndPoints, Protobuf=Protobuf, TcpProtocol=TcpProtocol,
                               common=common, msgs=msgs, model=model)
    return _sdk


def enum_name(enum_type, value: int) -> str:
    try:
        return enum_type.Name(value)
    except Exception:
        return str(value)


def has(msg, field: str) -> bool:
    try:
        return msg.HasField(field)
    except ValueError:
        return False


def opt(msg, field: str, default=None):
    return getattr(msg, field) if has(msg, field) else default
