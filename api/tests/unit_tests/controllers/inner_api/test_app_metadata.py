"""Inner endpoint to batch-fetch app metadata for the EE permitted-apps flow."""

import builtins
import inspect
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask.views import MethodView
from pydantic import ValidationError

from controllers.inner_api import bp as inner_api_bp
from controllers.inner_api.app.metadata import InnerAppBatchMetadataApi, InnerAppBatchMetadataPayload

if not hasattr(builtins, "MethodView"):
    builtins.MethodView = MethodView  # type: ignore[attr-defined]


@pytest.fixture
def inner_app() -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(inner_api_bp)
    return app


def test_route_registered(inner_app: Flask):
    rules = {r.rule for r in inner_app.url_map.iter_rules()}
    assert "/inner/api/enterprise/apps/batch-metadata" in rules


def test_dispatches_to_class(inner_app: Flask):
    rule = next(r for r in inner_app.url_map.iter_rules() if r.rule == "/inner/api/enterprise/apps/batch-metadata")
    assert inner_app.view_functions[rule.endpoint].view_class is InnerAppBatchMetadataApi


def test_post_method_only(inner_app: Flask):
    rule = next(r for r in inner_app.url_map.iter_rules() if r.rule == "/inner/api/enterprise/apps/batch-metadata")
    assert "POST" in rule.methods
    assert "GET" not in rule.methods


def test_payload_rejects_empty_ids():
    with pytest.raises(ValidationError):
        InnerAppBatchMetadataPayload.model_validate({"ids": []})


def test_payload_rejects_too_many_ids():
    with pytest.raises(ValidationError):
        InnerAppBatchMetadataPayload.model_validate({"ids": ["x"] * 501})


def _make_app_row(*, id=None, tenant_id=None, mode="chat", name="Test", updated_at=None, status="normal"):
    """Build a stand-in App row for handler tests."""
    row = MagicMock()
    row.id = id or uuid.uuid4()
    row.tenant_id = tenant_id or uuid.uuid4()
    row.mode = mode
    row.name = name
    row.status = status
    row.updated_at = updated_at or datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)
    return row


def test_post_returns_metadata_for_normal_apps(inner_app: Flask):
    app_row = _make_app_row(name="App A", mode="workflow")

    api_instance = InnerAppBatchMetadataApi()
    handler = inspect.unwrap(api_instance.post)

    with inner_app.test_request_context(
        path="/inner/api/enterprise/apps/batch-metadata",
        method="POST",
        json={"ids": [str(app_row.id)]},
    ):
        with (
            patch("controllers.inner_api.app.metadata.inner_api_ns") as mock_ns,
            patch("controllers.inner_api.app.metadata.db") as mock_db,
        ):
            mock_ns.payload = {"ids": [str(app_row.id)]}
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = [app_row]
            mock_db.session.execute.return_value.scalars.return_value = mock_scalars

            body, status = handler(api_instance)

    assert status == 200
    assert body == {
        "data": [
            {
                "id": str(app_row.id),
                "tenant_id": str(app_row.tenant_id),
                "mode": "workflow",
                "name": "App A",
                "updated_at": "2026-05-06T12:00:00+00:00",
            }
        ]
    }


def test_post_returns_empty_data_when_no_apps(inner_app: Flask):
    api_instance = InnerAppBatchMetadataApi()
    handler = inspect.unwrap(api_instance.post)

    with inner_app.test_request_context(
        path="/inner/api/enterprise/apps/batch-metadata",
        method="POST",
        json={"ids": [str(uuid.uuid4())]},
    ):
        with (
            patch("controllers.inner_api.app.metadata.inner_api_ns") as mock_ns,
            patch("controllers.inner_api.app.metadata.db") as mock_db,
        ):
            mock_ns.payload = {"ids": [str(uuid.uuid4())]}
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = []
            mock_db.session.execute.return_value.scalars.return_value = mock_scalars

            body, status = handler(api_instance)

    assert status == 200
    assert body == {"data": []}


def test_post_returns_422_on_invalid_payload(inner_app: Flask):
    api_instance = InnerAppBatchMetadataApi()
    handler = inspect.unwrap(api_instance.post)

    with inner_app.test_request_context(path="/inner/api/enterprise/apps/batch-metadata", method="POST"):
        with patch("controllers.inner_api.app.metadata.inner_api_ns") as mock_ns:
            mock_ns.payload = {"ids": []}  # min_length=1 violation

            body, status = handler(api_instance)

    assert status == 422
    assert "message" in body
