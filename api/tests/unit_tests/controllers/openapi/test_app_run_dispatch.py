import pytest
from werkzeug.exceptions import UnprocessableEntity

from controllers.openapi.app_run import (
    _DISPATCH,
    AppRunRequest,
    _enforce_chat_constraint,
    _enforce_workflow_constraint,
)
from models.model import AppMode


def test_dispatch_covers_runnable_modes():
    runnable = {AppMode.CHAT, AppMode.AGENT_CHAT, AppMode.ADVANCED_CHAT, AppMode.COMPLETION, AppMode.WORKFLOW}
    assert set(_DISPATCH) == runnable


def test_chat_constraint_requires_query():
    with pytest.raises(UnprocessableEntity, match="query_required_for_chat"):
        _enforce_chat_constraint(AppRunRequest(inputs={}))


def test_workflow_constraint_rejects_query():
    with pytest.raises(UnprocessableEntity, match="query_not_supported_for_workflow"):
        _enforce_workflow_constraint(AppRunRequest(inputs={}, query="hi"))
