import builtins

from flask import Flask
from flask.views import MethodView

from controllers.openapi import bp as openapi_bp
from controllers.openapi.app_info import AppInfoApi

if not hasattr(builtins, "MethodView"):
    builtins.MethodView = MethodView  # type: ignore[attr-defined]


def _openapi_app() -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(openapi_bp)
    return app


def _rule(app: Flask, path: str):
    return next(r for r in app.url_map.iter_rules() if r.rule == path)


def test_app_info_route_registered():
    rules = {r.rule for r in _openapi_app().url_map.iter_rules()}
    assert "/openapi/v1/apps/<string:app_id>/info" in rules


def test_app_info_dispatches_to_class():
    app = _openapi_app()
    rule = _rule(app, "/openapi/v1/apps/<string:app_id>/info")
    assert app.view_functions[rule.endpoint].view_class is AppInfoApi
    assert "GET" in rule.methods


def test_app_info_payload_shape():
    from types import SimpleNamespace

    from controllers.openapi.apps import app_info_payload

    app_obj = SimpleNamespace(
        id="app1",
        name="X",
        description="d",
        mode="chat",
        author_name="alice",
        tags=[SimpleNamespace(name="prod")],
    )
    payload = app_info_payload(app_obj)
    assert payload == {
        "id": "app1",
        "name": "X",
        "description": "d",
        "mode": "chat",
        "author": "alice",
        "tags": [{"name": "prod"}],
    }
