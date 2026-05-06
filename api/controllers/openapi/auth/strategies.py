"""Strategy classes for the openapi auth pipeline.

App authorization (Acl/Membership) and caller mounting (Account/EndUser)
vary along independent axes; each strategy is one class so the pipeline
composition stays a flat list.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from flask import current_app
from flask_login import user_logged_in
from sqlalchemy import select

from controllers.openapi.auth.context import Context
from core.app.entities.app_invoke_entities import InvokeFrom
from extensions.ext_database import db
from libs.oauth_bearer import SubjectType
from models import Account, TenantAccountJoin
from services.end_user_service import EndUserService
from services.enterprise.enterprise_service import EnterpriseService


class AppAuthzStrategy(Protocol):
    def authorize(self, ctx: Context) -> bool: ...


class AclStrategy:
    """Per-app ACL via the workspace-auth inner API.

    Used when webapp-auth is enabled (EE deployment). The inner-API
    allowlist is the source of truth.
    """

    def authorize(self, ctx: Context) -> bool:
        if ctx.subject_email is None or ctx.app is None:
            return False
        return EnterpriseService.WebAppAuth.is_user_allowed_to_access_webapp(
            user_id=ctx.subject_email,
            app_id=ctx.app.id,
        )


class MembershipStrategy:
    """Tenant-membership fallback.

    Used when webapp-auth is disabled (CE deployment). Account-bearing
    subjects pass if they have a TenantAccountJoin row; EXTERNAL_SSO is
    denied (it requires the webapp-auth surface).
    """

    def authorize(self, ctx: Context) -> bool:
        if ctx.subject_type == SubjectType.EXTERNAL_SSO:
            return False
        if ctx.tenant is None:
            return False
        return _has_tenant_membership(ctx.account_id, ctx.tenant.id)


def _has_tenant_membership(account_id: uuid.UUID | str | None, tenant_id: str) -> bool:
    if not account_id:
        return False
    row = db.session.execute(
        select(TenantAccountJoin.id).where(
            TenantAccountJoin.tenant_id == tenant_id,
            TenantAccountJoin.account_id == account_id,
        )
    ).scalar_one_or_none()
    return row is not None


def _login_as(user) -> None:
    """Set Flask-Login request user so downstream services see the caller."""
    current_app.login_manager._update_request_context_with_user(user)
    user_logged_in.send(current_app._get_current_object(), user=user)


class CallerMounter(Protocol):
    def applies_to(self, subject_type: SubjectType) -> bool: ...

    def mount(self, ctx: Context) -> None: ...


class AccountMounter:
    def applies_to(self, subject_type: SubjectType) -> bool:
        return subject_type == SubjectType.ACCOUNT

    def mount(self, ctx: Context) -> None:
        if ctx.account_id is None:
            raise RuntimeError("AccountMounter: account_id unset — BearerCheck did not run")
        account = db.session.get(Account, ctx.account_id)
        if account is None:
            raise RuntimeError("AccountMounter: account row missing for resolved bearer")
        account.current_tenant = ctx.tenant
        _login_as(account)
        ctx.caller, ctx.caller_kind = account, "account"


class EndUserMounter:
    def applies_to(self, subject_type: SubjectType) -> bool:
        return subject_type == SubjectType.EXTERNAL_SSO

    def mount(self, ctx: Context) -> None:
        if ctx.tenant is None or ctx.app is None or ctx.subject_email is None:
            raise RuntimeError("EndUserMounter: tenant/app/subject_email unset — earlier steps did not run")
        end_user = EndUserService.get_or_create_end_user_by_type(
            InvokeFrom.OPENAPI,
            tenant_id=ctx.tenant.id,
            app_id=ctx.app.id,
            user_id=ctx.subject_email,
        )
        _login_as(end_user)
        ctx.caller, ctx.caller_kind = end_user, "end_user"
