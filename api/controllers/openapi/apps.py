"""GET /openapi/v1/apps and per-app reads.

Decorator order: `method_decorators` is innermost-first. `validate_bearer`
is last → outermost → sets `g.auth_ctx` before `require_scope` reads it.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

import sqlalchemy as sa
from flask import g, request
from flask_restx import Resource
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from werkzeug.exceptions import Conflict, NotFound, UnprocessableEntity

from controllers.common.fields import Parameters
from controllers.openapi import openapi_ns
from controllers.openapi._input_schema import EMPTY_INPUT_SCHEMA, build_input_schema, resolve_app_config
from controllers.openapi._models import (
    MAX_PAGE_LIMIT,
    AppDescribeInfo,
    AppDescribeResponse,
    AppListRow,
    PaginationEnvelope,
)
from controllers.service_api.app.error import AppUnavailableError
from core.app.app_config.common.parameters_mapping import get_parameters_from_feature_dict
from extensions.ext_database import db
from libs.oauth_bearer import (
    ACCEPT_USER_ANY,
    AuthContext,
    Scope,
    SubjectType,
    require_scope,
    require_workspace_member,
    validate_bearer,
)
from models import App, Tenant
from models.model import AppMode
from services.app_service import AppService
from services.tag_service import TagService

_APPS_READ_DECORATORS = [
    require_scope(Scope.APPS_READ),
    validate_bearer(accept=ACCEPT_USER_ANY),
]

_ALLOWED_DESCRIBE_FIELDS: frozenset[str] = frozenset({"info", "parameters", "input_schema"})


class AppDescribeQuery(BaseModel):
    """`?fields=` allow-list for GET /apps/<id>/describe.

    Empty / omitted → all blocks. Unknown member → ValidationError → 422.
    """

    model_config = ConfigDict(extra="forbid")

    fields: set[str] | None = None
    workspace_id: str | None = None

    @field_validator("workspace_id", mode="before")
    @classmethod
    def _validate_workspace_id(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            raise ValueError("workspace_id must be a string")
        try:
            _uuid.UUID(v)
        except ValueError:
            raise ValueError("workspace_id must be a valid UUID")
        return v

    @field_validator("fields", mode="before")
    @classmethod
    def _parse_fields(cls, v: object) -> set[str] | None:
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            raise ValueError("fields must be a comma-separated string")
        members = {m.strip() for m in v.split(",") if m.strip()}
        unknown = members - _ALLOWED_DESCRIBE_FIELDS
        if unknown:
            raise ValueError(f"unknown field(s): {sorted(unknown)}")
        return members


_EMPTY_PARAMETERS: dict[str, Any] = {
    "opening_statement": None,
    "suggested_questions": [],
    "user_input_form": [],
    "file_upload": None,
    "system_parameters": {},
}


class AppReadResource(Resource):
    """Base for per-app read endpoints; subclasses call `_load()` for SSO/membership/exists checks."""

    method_decorators = _APPS_READ_DECORATORS

    def _load(self, app_id: str, workspace_id: str | None = None) -> tuple[App, AuthContext]:
        ctx = g.auth_ctx
        if ctx.subject_type != SubjectType.ACCOUNT or ctx.account_id is None:
            raise NotFound("app not found")

        try:
            parsed_uuid = _uuid.UUID(app_id)
            is_uuid = True
        except ValueError:
            parsed_uuid = None
            is_uuid = False

        if is_uuid:
            app = db.session.get(App, str(parsed_uuid))  # normalised dashed form
            if not app or app.status != "normal":
                raise NotFound("app not found")
        else:
            if not workspace_id:
                raise UnprocessableEntity("workspace_id is required for name-based lookup")
            matches = list(
                db.session.execute(
                    sa.select(App).where(
                        App.name == app_id,
                        App.tenant_id == workspace_id,
                        App.status == "normal",
                    )
                ).scalars()
            )
            if len(matches) == 0:
                raise NotFound("app not found")
            if len(matches) > 1:
                lines = [f"app name {app_id!r} is ambiguous — re-run with a UUID:\n\n"]
                lines.append(f"  {'ID':<36}  {'MODE':<12}  NAME\n")
                for m in matches:
                    lines.append(f"  {str(m.id):<36}  {str(m.mode.value):<12}  {m.name}\n")
                raise Conflict("".join(lines))
            app = matches[0]

        require_workspace_member(ctx, str(app.tenant_id))
        return app, ctx


def parameters_payload(app: App) -> dict:
    """Mirrors service_api/app/app.py::AppParameterApi response body."""
    features_dict, user_input_form = resolve_app_config(app)
    parameters = get_parameters_from_feature_dict(features_dict=features_dict, user_input_form=user_input_form)
    return Parameters.model_validate(parameters).model_dump(mode="json")


@openapi_ns.route("/apps/<string:app_id>/describe")
class AppDescribeApi(AppReadResource):
    def get(self, app_id: str):
        try:
            query = AppDescribeQuery.model_validate(request.args.to_dict(flat=True))
        except ValidationError as exc:
            raise UnprocessableEntity(exc.json())

        app, _ = self._load(app_id, workspace_id=query.workspace_id)

        requested = query.fields
        want_info = requested is None or "info" in requested
        want_params = requested is None or "parameters" in requested
        want_schema = requested is None or "input_schema" in requested

        info = (
            AppDescribeInfo(
                id=str(app.id),
                name=app.name,
                mode=app.mode,
                description=app.description,
                tags=[{"name": t.name} for t in app.tags],
                author=app.author_name,
                updated_at=app.updated_at.isoformat() if app.updated_at else None,
                service_api_enabled=bool(app.enable_api),
            )
            if want_info
            else None
        )

        parameters: dict[str, Any] | None = None
        input_schema: dict[str, Any] | None = None
        if want_params:
            try:
                parameters = parameters_payload(app)
            except AppUnavailableError:
                parameters = dict(_EMPTY_PARAMETERS)
        if want_schema:
            try:
                input_schema = build_input_schema(app)
            except AppUnavailableError:
                input_schema = dict(EMPTY_INPUT_SCHEMA)

        return (
            AppDescribeResponse(
                info=info,
                parameters=parameters,
                input_schema=input_schema,
            ).model_dump(mode="json", exclude_none=False),
            200,
        )


class AppListQuery(BaseModel):
    """`mode` is a closed enum — unknown values 422 instead of silently-empty data."""

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
            return PaginationEnvelope[AppListRow].build(page=1, limit=0, total=0, items=[]).model_dump(mode="json"), 200

        try:
            query = AppListQuery.model_validate(request.args.to_dict(flat=True))
        except ValidationError as exc:
            raise UnprocessableEntity(exc.json())

        workspace_id = query.workspace_id
        require_workspace_member(ctx, workspace_id)

        empty = (
            PaginationEnvelope[AppListRow]
            .build(page=query.page, limit=query.limit, total=0, items=[])
            .model_dump(mode="json"),
            200,
        )

        tag_ids: list[str] | None = None
        if query.tag:
            tags = TagService.get_tag_by_tag_name("app", workspace_id, query.tag)
            if not tags:
                return empty
            tag_ids = [tag.id for tag in tags]

        args: dict[str, Any] = {
            "page": query.page,
            "limit": query.limit,
            "mode": query.mode.value if query.mode else "",
            "name": query.name,
            "status": "normal",
        }
        if tag_ids:
            args["tag_ids"] = tag_ids

        pagination = AppService().get_paginate_apps(ctx.account_id, workspace_id, args)
        if pagination is None:
            return empty

        tenant_name: str | None = None
        if pagination.items:
            tenant_name = db.session.execute(
                sa.select(Tenant.name).where(Tenant.id == workspace_id)
            ).scalar_one_or_none()

        items = [
            AppListRow(
                id=str(r.id),
                name=r.name,
                description=r.description,
                mode=r.mode,
                tags=[{"name": t.name} for t in r.tags],
                updated_at=r.updated_at.isoformat() if r.updated_at else None,
                created_by_name=getattr(r, "author_name", None),
                workspace_id=str(workspace_id),
                workspace_name=tenant_name,
            )
            for r in pagination.items
        ]
        env = PaginationEnvelope[AppListRow].build(
            page=query.page, limit=query.limit, total=int(pagination.total), items=items
        )
        return env.model_dump(mode="json"), 200
