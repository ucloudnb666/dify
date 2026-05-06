"""GET /openapi/v1/apps and per-app reads (single, parameters, describe).

Read endpoints use validate_bearer + require_scope + require_workspace_member.
The OAuth bearer pipeline is reserved for /run (which gates on webapp_auth ACL).
"""

from __future__ import annotations

from typing import Any, cast

import sqlalchemy as sa
from flask import g, request
from flask_restx import Resource
from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from controllers.common.fields import Parameters
from controllers.openapi import openapi_ns
from controllers.openapi._models import PaginationEnvelope
from controllers.service_api.app.error import AppUnavailableError
from core.app.app_config.common.parameters_mapping import get_parameters_from_feature_dict
from extensions.ext_database import db
from libs.helper import escape_like_pattern
from libs.oauth_bearer import (
    ACCEPT_USER_ANY,
    Scope,
    SubjectType,
    require_scope,
    require_workspace_member,
    validate_bearer,
)
from models import App
from models.model import AppMode, Tag, TagBinding


def account_or_404(ctx) -> None:
    """Per-app reads are account-only; SSO subjects 404 to avoid leaking ID space."""
    if ctx.subject_type != SubjectType.ACCOUNT or ctx.account_id is None:
        raise NotFound("app not found")


@openapi_ns.route("/apps/<string:app_id>")
class AppByIdApi(Resource):
    @validate_bearer(accept=ACCEPT_USER_ANY)
    @require_scope(Scope.APPS_READ)  # type: ignore[reportUntypedFunctionDecorator]
    def get(self, app_id: str):
        ctx = g.auth_ctx
        account_or_404(ctx)

        app = db.session.get(App, app_id)
        if not app or app.status != "normal":
            raise NotFound("app not found")

        require_workspace_member(ctx, str(app.tenant_id))
        return app_info_payload(app), 200


def app_info_payload(app: App) -> dict:
    return {
        "id": str(app.id),
        "name": app.name,
        "description": app.description,
        "mode": app.mode,
        "author": app.author_name,
        "tags": [{"name": t.name} for t in app.tags],
    }


@openapi_ns.route("/apps/<string:app_id>/parameters")
class AppParametersApi(Resource):
    @validate_bearer(accept=ACCEPT_USER_ANY)
    @require_scope(Scope.APPS_READ)  # type: ignore[reportUntypedFunctionDecorator]
    def get(self, app_id: str):
        ctx = g.auth_ctx
        account_or_404(ctx)

        app = db.session.get(App, app_id)
        if not app or app.status != "normal":
            raise NotFound("app not found")

        require_workspace_member(ctx, str(app.tenant_id))
        return parameters_payload(app), 200


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


_EMPTY_PARAMETERS: dict[str, Any] = {
    "opening_statement": None,
    "suggested_questions": [],
    "user_input_form": [],
    "file_upload": None,
    "system_parameters": {},
}


@openapi_ns.route("/apps/<string:app_id>/describe")
class AppDescribeApi(Resource):
    @validate_bearer(accept=ACCEPT_USER_ANY)
    @require_scope(Scope.APPS_READ)  # type: ignore[reportUntypedFunctionDecorator]
    def get(self, app_id: str):
        ctx = g.auth_ctx
        account_or_404(ctx)

        app = db.session.get(App, app_id)
        if not app or app.status != "normal":
            raise NotFound("app not found")

        require_workspace_member(ctx, str(app.tenant_id))

        try:
            parameters = parameters_payload(app)
        except AppUnavailableError:
            # Apps without a model config still expose info; absent parameters
            # render as explicit empty/null fields per spec.
            parameters = dict(_EMPTY_PARAMETERS)

        return {
            "info": {
                "id": str(app.id),
                "name": app.name,
                "mode": app.mode,
                "description": app.description,
                "tags": [{"name": t.name} for t in app.tags],
                "author": app.author_name,
                "updated_at": app.updated_at.isoformat() if app.updated_at else None,
                "service_api_enabled": bool(app.enable_api),
            },
            "parameters": parameters,
        }, 200


@openapi_ns.route("/apps")
class AppListApi(Resource):
    @validate_bearer(accept=ACCEPT_USER_ANY)
    @require_scope(Scope.APPS_READ)  # type: ignore[reportUntypedFunctionDecorator]
    def get(self):
        ctx = g.auth_ctx
        if ctx.subject_type != SubjectType.ACCOUNT or ctx.account_id is None:
            raise Forbidden("subject not permitted to list apps")

        workspace_id = request.args.get("workspace_id")
        if not workspace_id:
            raise BadRequest("workspace_id query param is required")

        require_workspace_member(ctx, workspace_id)

        page = int(request.args.get("page", "1"))
        limit = min(int(request.args.get("limit", "20")), 100)
        mode = request.args.get("mode")
        name_filter = request.args.get("name")
        tag_name = request.args.get("tag")

        filters = [
            App.tenant_id == workspace_id,
            App.is_universal == False,
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
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "mode": r.mode,
                "tags": [{"name": t.name} for t in r.tags],
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "created_by_name": getattr(r, "author_name", None),
            }
            for r in rows
        ]
        return (
            PaginationEnvelope.build(page=page, limit=limit, total=int(total), items=items).model_dump(mode="json"),
            200,
        )
