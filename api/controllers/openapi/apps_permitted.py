"""GET /openapi/v1/apps/permitted — external-subject app discovery (EE only)."""

from __future__ import annotations

import sqlalchemy as sa
from flask import g, request
from flask_restx import Resource
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from werkzeug.exceptions import UnprocessableEntity

from controllers.openapi import openapi_ns
from controllers.openapi._models import (
    MAX_PAGE_LIMIT,
    AppListRow,
    PaginationEnvelope,
)
from extensions.ext_database import db
from libs.device_flow_security import enterprise_only
from libs.oauth_bearer import (
    ACCEPT_USER_EXT_SSO,
    Scope,
    require_scope,
    validate_bearer,
)
from models import App, Tenant
from models.model import AppMode
from services.enterprise.app_permitted_service import list_permitted_apps


class AppPermittedListQuery(BaseModel):
    """Query-param validator for `GET /openapi/v1/apps/permitted`.

    Strict — `extra='forbid'` rejects `workspace_id`, `tag`, and any other
    param not explicitly allowed for this surface. Returns 422 on violation.
    """

    model_config = ConfigDict(extra="forbid")

    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=MAX_PAGE_LIMIT)
    mode: AppMode | None = None
    name: str | None = Field(None, max_length=200)


@openapi_ns.route("/apps/permitted")
class AppListPermittedApi(Resource):
    method_decorators = [
        require_scope(Scope.APPS_READ_PERMITTED),
        validate_bearer(accept=ACCEPT_USER_EXT_SSO),
        enterprise_only,
    ]

    def get(self):
        try:
            query = AppPermittedListQuery.model_validate(request.args.to_dict(flat=True))
        except ValidationError as exc:
            raise UnprocessableEntity(exc.json())

        ctx = g.auth_ctx
        page_result = list_permitted_apps(
            subject_email=ctx.subject_email or "",
            subject_issuer=ctx.subject_issuer or "",
            page=query.page,
            limit=query.limit,
            mode=query.mode.value if query.mode else None,
            name=query.name,
        )

        if not page_result.data:
            env = PaginationEnvelope[AppListRow].build(
                page=query.page, limit=query.limit, total=page_result.total, items=[]
            )
            return env.model_dump(mode="json"), 200

        app_ids = [r.app_id for r in page_result.data]
        apps_by_id = {
            str(a.id): a for a in db.session.execute(sa.select(App).where(App.id.in_(app_ids))).scalars().all()
        }
        tenant_ids = list({a.tenant_id for a in apps_by_id.values()})
        tenants_by_id = {
            str(t.id): t for t in db.session.execute(sa.select(Tenant).where(Tenant.id.in_(tenant_ids))).scalars().all()
        }

        items: list[AppListRow] = []
        for r in page_result.data:
            app = apps_by_id.get(r.app_id)
            if not app or app.status != "normal":
                # Allow-list referenced an app that no longer exists or was archived;
                # filter it out rather than emit a partial row.
                continue
            tenant = tenants_by_id.get(str(app.tenant_id))
            items.append(
                AppListRow(
                    id=str(app.id),
                    name=app.name,
                    description=app.description,
                    mode=app.mode,
                    tags=[],  # tags are tenant-scoped; not surfaced cross-tenant
                    updated_at=app.updated_at.isoformat() if app.updated_at else None,
                    created_by_name=None,  # cross-tenant author leak prevention
                    workspace_id=str(app.tenant_id),
                    workspace_name=tenant.name if tenant else None,
                )
            )

        env = PaginationEnvelope[AppListRow].build(
            page=query.page, limit=query.limit, total=page_result.total, items=items
        )
        return env.model_dump(mode="json"), 200
