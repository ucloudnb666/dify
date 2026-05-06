"""Unit tests for the openapi bearer-scope catalog and TokenKind registry."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_apps_read_permitted_scope_present():
    from libs.oauth_bearer import Scope

    assert Scope.APPS_READ_PERMITTED.value == "apps:read:permitted"


def test_dfoe_token_kind_carries_apps_read_permitted():
    from libs.oauth_bearer import Scope, build_registry

    registry = build_registry(MagicMock(), MagicMock())
    dfoe = next(k for k in registry.kinds() if k.prefix == "dfoe_")
    assert Scope.APPS_READ_PERMITTED in dfoe.scopes
