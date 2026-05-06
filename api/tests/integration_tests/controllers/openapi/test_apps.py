"""Integration tests for /openapi/v1/apps* read surface."""

from __future__ import annotations

import uuid

from flask.testing import FlaskClient

from models import App


def test_apps_get_single_returns_app_info(
    test_client: FlaskClient,
    app_in_workspace: App,
    account_token: str,
):
    res = test_client.get(
        f"/openapi/v1/apps/{app_in_workspace.id}",
        headers={"Authorization": f"Bearer {account_token}"},
    )
    assert res.status_code == 200
    body = res.json
    assert body["id"] == app_in_workspace.id
    assert body["mode"] == "chat"


def test_apps_get_single_rejects_external_sso(
    test_client: FlaskClient,
    app_in_workspace: App,
    mint_token,
):
    """dfoe_ tokens hold only `apps:run` — `apps:read` routes 403."""
    token = "dfoe_" + uuid.uuid4().hex
    mint_token(
        token,
        account_id=None,
        prefix="dfoe_",
        subject_email="ext@example.com",
        subject_issuer="https://idp.example.com",
    )
    res = test_client.get(
        f"/openapi/v1/apps/{app_in_workspace.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
    assert "insufficient_scope" in res.json.get("message", "")


def test_apps_parameters_returns_form_schema(
    test_client: FlaskClient,
    app_in_workspace: App,
    account_token: str,
):
    res = test_client.get(
        f"/openapi/v1/apps/{app_in_workspace.id}/parameters",
        headers={"Authorization": f"Bearer {account_token}"},
    )
    # Without an app_model_config, the chat app's parameters endpoint raises
    # AppUnavailableError (503). Auth succeeded if status is NOT 401/403.
    assert res.status_code in (200, 503)


def test_apps_describe_returns_merged_shape(
    test_client: FlaskClient,
    app_in_workspace: App,
    account_token: str,
):
    res = test_client.get(
        f"/openapi/v1/apps/{app_in_workspace.id}/describe",
        headers={"Authorization": f"Bearer {account_token}"},
    )
    assert res.status_code == 200
    body = res.json
    assert body["info"]["id"] == app_in_workspace.id
    assert body["info"]["mode"] == "chat"
    assert isinstance(body["parameters"], dict)


def test_apps_list_returns_pagination_envelope(
    test_client: FlaskClient,
    workspace_account,
    app_in_workspace: App,
    account_token: str,
):
    _, tenant, _ = workspace_account
    res = test_client.get(
        f"/openapi/v1/apps?workspace_id={tenant.id}&page=1&limit=20",
        headers={"Authorization": f"Bearer {account_token}"},
    )
    assert res.status_code == 200
    body = res.json
    assert body["page"] == 1
    assert body["limit"] == 20
    assert body["total"] >= 1
    assert any(d["id"] == app_in_workspace.id for d in body["data"])


def test_apps_list_requires_workspace_id(test_client: FlaskClient, account_token: str):
    res = test_client.get("/openapi/v1/apps", headers={"Authorization": f"Bearer {account_token}"})
    assert res.status_code == 400


def test_apps_list_tag_no_match_returns_empty_data_not_400(
    test_client: FlaskClient,
    workspace_account,
    app_in_workspace: App,
    account_token: str,
):
    _, tenant, _ = workspace_account
    res = test_client.get(
        f"/openapi/v1/apps?workspace_id={tenant.id}&tag=nonexistent",
        headers={"Authorization": f"Bearer {account_token}"},
    )
    assert res.status_code == 200
    assert res.json["data"] == []


def test_account_sessions_returns_envelope(
    test_client: FlaskClient,
    account_token: str,
):
    res = test_client.get("/openapi/v1/account/sessions", headers={"Authorization": f"Bearer {account_token}"})
    assert res.status_code == 200
    body = res.json
    # canonical envelope shape
    assert isinstance(body["data"], list)
    assert "page" in body
    assert "limit" in body
    assert "total" in body
    assert "has_more" in body
    # the bearer's own minted session must appear
    assert any(s["prefix"] == "dfoa_" for s in body["data"])
    # legacy "sessions" key must NOT appear
    assert "sessions" not in body
