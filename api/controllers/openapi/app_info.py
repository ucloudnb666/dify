"""GET /openapi/v1/apps/<app_id>/info — port of service_api/app/app.py:AppInfoApi."""

from __future__ import annotations

from controllers.openapi import openapi_ns
from controllers.openapi.apps import AppReadResource, app_info_payload


@openapi_ns.route("/apps/<string:app_id>/info")
class AppInfoApi(AppReadResource):
    def get(self, app_id: str):
        app, _ = self._load(app_id)
        return app_info_payload(app), 200
