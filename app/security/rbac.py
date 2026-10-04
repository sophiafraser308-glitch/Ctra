from __future__ import annotations

from enum import Enum

from app.core.enums import Role
from app.core.exceptions import AuthorizationError


class Permission(str, Enum):
    VIEW = "view"
    BOT_CONTROL = "bot_control"
    BOT_MANAGE = "bot_manage"
    TRADE = "trade"                 # close positions / modify SL/TP / cancel orders
    ACCOUNT_MANAGE = "account_manage"
    STRATEGY_MANAGE = "strategy_manage"
    RISK_MANAGE = "risk_manage"
    SETTINGS = "settings"
    EMERGENCY = "emergency"
    EMERGENCY_RESET = "emergency_reset"
    USER_MANAGE = "user_manage"


_ROLE_PERMS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),
    Role.OPERATOR: {Permission.VIEW, Permission.BOT_CONTROL, Permission.TRADE, Permission.EMERGENCY},
    Role.READ_ONLY: {Permission.VIEW},
}


def has_permission(role: Role | None, perm: Permission) -> bool:
    return role is not None and perm in _ROLE_PERMS.get(role, set())


def require(role: Role | None, perm: Permission) -> None:
    if not has_permission(role, perm):
        raise AuthorizationError(f"Role {role.value if role else 'NONE'} lacks permission '{perm.value}'")
