import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.exceptions import Unauthorized

from controllers.openapi.auth.context import Context
from controllers.openapi.auth.steps import BearerCheck
from libs.oauth_bearer import AuthContext, InvalidBearerError, Scope, SubjectType


def _ctx(headers):
    req = MagicMock()
    req.headers = headers
    return Context(request=req, required_scope="apps:run")


def test_bearer_check_rejects_missing_header():
    with pytest.raises(Unauthorized):
        BearerCheck()(_ctx({}))


@patch("controllers.openapi.auth.steps.get_authenticator")
def test_bearer_check_rejects_unknown_prefix(get_auth):
    get_auth.return_value.authenticate.side_effect = InvalidBearerError("unknown token prefix")
    with pytest.raises(Unauthorized):
        BearerCheck()(_ctx({"Authorization": "Bearer xxx_abc"}))


@patch("controllers.openapi.auth.steps.get_authenticator")
def test_bearer_check_populates_context(get_auth):
    tok_id = uuid.uuid4()
    authn = AuthContext(
        subject_type=SubjectType.ACCOUNT,
        subject_email="a@x.com",
        subject_issuer=None,
        account_id=None,
        scopes=frozenset({Scope.FULL}),
        token_id=tok_id,
        source="oauth-account",
        expires_at=datetime.now(UTC),
        token_hash="hash-1",
        verified_tenants={},
    )
    get_auth.return_value.authenticate.return_value = authn

    ctx = _ctx({"Authorization": "Bearer dfoa_abc"})
    BearerCheck()(ctx)

    assert ctx.subject_type == SubjectType.ACCOUNT
    assert ctx.subject_email == "a@x.com"
    assert ctx.scopes == frozenset({Scope.FULL})
    assert ctx.source == "oauth-account"
    assert ctx.token_id == tok_id
    assert ctx.token_hash == "hash-1"
