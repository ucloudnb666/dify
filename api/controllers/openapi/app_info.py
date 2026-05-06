"""GET /openapi/v1/apps/<app_id>/info — port of service_api/app/app.py:AppInfoApi."""

from __future__ import annotations

from flask import g
from flask_restx import Resource
from werkzeug.exceptions import NotFound

from controllers.openapi import openapi_ns
from controllers.openapi.apps import account_or_404, app_info_payload
from extensions.ext_database import db
from libs.oauth_bearer import (
    ACCEPT_USER_ANY,
    Scope,
    require_scope,
    require_workspace_member,
    validate_bearer,
)
from models import App


@openapi_ns.route("/apps/<string:app_id>/info")
class AppInfoApi(Resource):
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
