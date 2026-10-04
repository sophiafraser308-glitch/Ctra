from __future__ import annotations


class PlatformError(Exception):
    """Base class. Messages must never contain secrets."""
    code = "PLATFORM_ERROR"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class ConfigError(PlatformError):
    code = "CONFIG_ERROR"


class AuthorizationError(PlatformError):
    code = "UNAUTHORIZED"


class NotFoundError(PlatformError):
    code = "NOT_FOUND"


class ValidationFailed(PlatformError):
    code = "VALIDATION_FAILED"


class ConflictError(PlatformError):
    code = "CONFLICT"


class SafetyError(PlatformError):
    """A safety policy (live gating, locks, demo/live separation) blocked the action."""
    code = "SAFETY_BLOCKED"


class CTraderError(PlatformError):
    code = "CTRADER_ERROR"

    def __init__(self, message: str = "", *, code: str | None = None, broker_code: str | None = None) -> None:
        super().__init__(message, code=code)
        self.broker_code = broker_code


class CTraderTimeout(CTraderError):
    code = "CTRADER_TIMEOUT"


class CTraderNotConnected(CTraderError):
    code = "CTRADER_NOT_CONNECTED"


class TokenError(CTraderError):
    code = "TOKEN_ERROR"


class OrderRejected(PlatformError):
    code = "ORDER_REJECTED"


class StrategyCrashed(PlatformError):
    code = "STRATEGY_CRASHED"


class StrategyTimeout(PlatformError):
    code = "STRATEGY_TIMEOUT"
