"""Inner endpoint: batch-fetch app metadata for the EE permitted-apps flow.

Called by ``dify-enterprise`` server to hydrate ``web_app_settings.app_id`` rows
with their ``apps`` table fields. Filters out non-``normal`` apps server-side.
"""

import sqlalchemy as sa
from flask_restx import Resource
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from controllers.common.schema import register_schema_model
from controllers.console.wraps import setup_required
from controllers.inner_api import inner_api_ns
from controllers.inner_api.wraps import enterprise_inner_api_only
from extensions.ext_database import db
from models import App
from models.enums import AppStatus


class InnerAppBatchMetadataPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=500, description="App UUIDs to fetch metadata for (1-500 entries)")


register_schema_model(inner_api_ns, InnerAppBatchMetadataPayload)


@inner_api_ns.route("/enterprise/apps/batch-metadata")
class InnerAppBatchMetadataApi(Resource):
    @setup_required
    @enterprise_inner_api_only
    @inner_api_ns.doc("enterprise_app_batch_metadata")
    @inner_api_ns.expect(inner_api_ns.models[InnerAppBatchMetadataPayload.__name__])
    @inner_api_ns.doc(
        responses={
            200: "Batch metadata returned",
            401: "Inner API key invalid",
            422: "Payload validation failed",
        }
    )
    def post(self):
        """Batch-fetch app metadata for the EE permitted-apps flow."""
        try:
            payload = InnerAppBatchMetadataPayload.model_validate(inner_api_ns.payload or {})
        except ValidationError as exc:
            return {"message": exc.json()}, 422

        rows = (
            db.session.execute(sa.select(App).where(App.id.in_(payload.ids), App.status == AppStatus.NORMAL))
            .scalars()
            .all()
        )

        return {
            "data": [
                {
                    "id": str(app.id),
                    "tenant_id": str(app.tenant_id),
                    "mode": app.mode,
                    "name": app.name,
                    "updated_at": app.updated_at.isoformat(),
                }
                for app in rows
            ]
        }, 200
