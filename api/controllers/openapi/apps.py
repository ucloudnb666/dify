"""GET /openapi/v1/apps and per-app reads (single, parameters, describe).

Read endpoints attach via `AppReadResource`, which stacks
`validate_bearer + require_scope` as method_decorators. List order is
innermost-first: `validate_bearer` is last in the list and ends up
outermost, so it sets `g.auth_ctx` before `require_scope` reads it.
The OAuth bearer pipeline is reserved for /run (which gates on
webapp_auth ACL).
"""

from __future__ import annotations

from typing import Any, cast

import sqlalchemy as sa
from flask import g, request
from flask_restx import Resource
from pydantic import BaseModel, Field, ValidationError
from werkzeug.exceptions import NotFound, UnprocessableEntity

from controllers.common.fields import Parameters
from controllers.openapi import openapi_ns
from controllers.openapi._models import (
    MAX_PAGE_LIMIT,
    AppDescribeInfo,
    AppDescribeResponse,
    AppInfoResponse,
    AppListRow,
    PaginationEnvelope,
)
from controllers.service_api.app.error import AppUnavailableError
from core.app.app_config.common.parameters_mapping import get_parameters_from_feature_dict
from extensions.ext_database import db
from libs.helper import escape_like_pattern
from libs.oauth_bearer import (
    ACCEPT_USER_ANY,
    AuthContext,
    Scope,
    SubjectType,
    require_scope,
    require_workspace_member,
    validate_bearer,
)
from models import App
from models.model import AppMode, Tag, TagBinding

# Shared decorator stack for `apps:read`-scoped endpoints. List order is
# innermost-first; `validate_bearer` (last) wraps outermost so it sets
# `g.auth_ctx` before `require_scope` reads it.
_APPS_READ_DECORATORS = [
    require_scope(Scope.APPS_READ),
    validate_bearer(accept=ACCEPT_USER_ANY),
]

_EMPTY_PARAMETERS: dict[str, Any] = {
    "opening_statement": None,
    "suggested_questions": [],
    "user_input_form": [],
    "file_upload": None,
    "system_parameters": {},
}


class AppReadResource(Resource):
    """Base for `/apps/<id>` read endpoints. Stacks bearer auth + scope check
    on every method, then exposes `_load()` so subclasses don't repeat the
    SSO-guard / app-load / membership-check ritual."""

    method_decorators = _APPS_READ_DECORATORS

    def _load(self, app_id: str) -> tuple[App, AuthContext]:
        ctx = g.auth_ctx
        # Per-app reads are account-only; SSO subjects 404 to avoid leaking
        # ID space (and dfoe_ already lacks apps:read scope, so this is
        # defensive against future scope changes).
        if ctx.subject_type != SubjectType.ACCOUNT or ctx.account_id is None:
            raise NotFound("app not found")

        app = db.session.get(App, app_id)
        if not app or app.status != "normal":
            raise NotFound("app not found")

        require_workspace_member(ctx, str(app.tenant_id))
        return app, ctx


def app_info_payload(app: App) -> dict:
    return AppInfoResponse(
        id=str(app.id),
        name=app.name,
        description=app.description,
        mode=app.mode,
        author=app.author_name,
        tags=[{"name": t.name} for t in app.tags],
    ).model_dump(mode="json")


def parameters_payload(app: App) -> dict:
    """Mirrors service_api/app/app.py::AppParameterApi response body."""
    if app.mode in {AppMode.ADVANCED_CHAT, AppMode.WORKFLOW}:
        workflow = app.workflow
        if workflow is None:
            raise AppUnavailableError()
        features_dict: dict[str, Any] = workflow.features_dict
        user_input_form = workflow.user_input_form(to_old_structure=True)
    else:
        app_model_config = app.app_model_config
        if app_model_config is None:
            raise AppUnavailableError()
        features_dict = cast(dict[str, Any], app_model_config.to_dict())
        user_input_form = features_dict.get("user_input_form", [])

    parameters = get_parameters_from_feature_dict(features_dict=features_dict, user_input_form=user_input_form)
    return Parameters.model_validate(parameters).model_dump(mode="json")


@openapi_ns.route("/apps/<string:app_id>")
class AppByIdApi(AppReadResource):
    def get(self, app_id: str):
        app, _ = self._load(app_id)
        return app_info_payload(app), 200


@openapi_ns.route("/apps/<string:app_id>/parameters")
class AppParametersApi(AppReadResource):
    def get(self, app_id: str):
        app, _ = self._load(app_id)
        return parameters_payload(app), 200


@openapi_ns.route("/apps/<string:app_id>/describe")
class AppDescribeApi(AppReadResource):
    def get(self, app_id: str):
        app, _ = self._load(app_id)
        try:
            parameters = parameters_payload(app)
        except AppUnavailableError:
            parameters = dict(_EMPTY_PARAMETERS)

        info = AppDescribeInfo(
            id=str(app.id),
            name=app.name,
            mode=app.mode,
            description=app.description,
            tags=[{"name": t.name} for t in app.tags],
            author=app.author_name,
            updated_at=app.updated_at.isoformat() if app.updated_at else None,
            service_api_enabled=bool(app.enable_api),
        )
        return AppDescribeResponse(info=info, parameters=parameters).model_dump(mode="json"), 200


class AppListQuery(BaseModel):
    """Query-param validator for `GET /openapi/v1/apps`.

    `mode` is a closed set (AppMode) — invalid values surface as 422
    instead of returning silently-empty data. `workspace_id` is required;
    page / limit have numeric bounds; name / tag have length caps.
    """

    workspace_id: str
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=MAX_PAGE_LIMIT)
    mode: AppMode | None = None
    name: str | None = Field(None, max_length=200)
    tag: str | None = Field(None, max_length=100)


@openapi_ns.route("/apps")
class AppListApi(Resource):
    method_decorators = _APPS_READ_DECORATORS

    def get(self):
        ctx = g.auth_ctx
        if ctx.subject_type != SubjectType.ACCOUNT or ctx.account_id is None:
            # An account-required endpoint reachable only via dfoa_ in practice
            # (dfoe_ lacks apps:read). Defensive guard for future scope shifts.
            return PaginationEnvelope[AppListRow].build(page=1, limit=0, total=0, items=[]).model_dump(mode="json"), 200

        try:
            query = AppListQuery.model_validate(dict(request.args))
        except ValidationError as e:
            raise UnprocessableEntity(str(e.errors()))

        workspace_id = query.workspace_id
        require_workspace_member(ctx, workspace_id)

        page = query.page
        limit = query.limit
        mode = query.mode.value if query.mode else None
        name_filter = query.name
        tag_name = query.tag

        filters = [
            App.tenant_id == workspace_id,
            App.is_universal.is_(False),
            App.status == "normal",
        ]
        if mode:
            filters.append(App.mode == mode)
        if name_filter:
            escaped = escape_like_pattern(name_filter[:30])
            filters.append(App.name.ilike(f"%{escaped}%", escape="\\"))
        if tag_name:
            tag_app_ids = (
                db.session.query(TagBinding.target_id)
                .join(Tag, Tag.id == TagBinding.tag_id)
                .filter(Tag.tenant_id == workspace_id, Tag.type == "app", Tag.name == tag_name)
                .subquery()
            )
            filters.append(App.id.in_(sa.select(tag_app_ids.c.target_id)))

        total = db.session.execute(sa.select(sa.func.count()).select_from(App).where(*filters)).scalar() or 0
        rows = (
            db.session.execute(
                sa.select(App).where(*filters).order_by(App.created_at.desc()).limit(limit).offset((page - 1) * limit)
            )
            .scalars()
            .all()
        )

        items = [
            AppListRow(
                id=str(r.id),
                name=r.name,
                description=r.description,
                mode=r.mode,
                tags=[{"name": t.name} for t in r.tags],
                updated_at=r.updated_at.isoformat() if r.updated_at else None,
                created_by_name=getattr(r, "author_name", None),
            )
            for r in rows
        ]
        env = PaginationEnvelope[AppListRow].build(page=page, limit=limit, total=int(total), items=items)
        return env.model_dump(mode="json"), 200
