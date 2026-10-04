import pytest

from app.core.enums import Role
from app.core.exceptions import AuthorizationError
from app.security.rbac import Permission, has_permission, require


def test_admin_has_everything():
    assert all(has_permission(Role.ADMIN, p) for p in Permission)


def test_operator_limits():
    assert has_permission(Role.OPERATOR, Permission.BOT_CONTROL)
    assert has_permission(Role.OPERATOR, Permission.EMERGENCY)
    assert not has_permission(Role.OPERATOR, Permission.SETTINGS)
    assert not has_permission(Role.OPERATOR, Permission.STRATEGY_MANAGE)
    assert not has_permission(Role.OPERATOR, Permission.EMERGENCY_RESET)


def test_read_only_and_none():
    assert has_permission(Role.READ_ONLY, Permission.VIEW)
    assert not has_permission(Role.READ_ONLY, Permission.TRADE)
    with pytest.raises(AuthorizationError):
        require(None, Permission.VIEW)
