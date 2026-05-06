"""Unit tests for PaginationEnvelope generic Pydantic model."""

from __future__ import annotations

from pydantic import BaseModel

from controllers.openapi._models import PaginationEnvelope


class _Row(BaseModel):
    id: str
    name: str


def test_envelope_basic_fields():
    env = PaginationEnvelope[_Row](page=1, limit=20, total=42, has_more=True, data=[_Row(id="a", name="A")])
    dumped = env.model_dump(mode="json")
    assert dumped == {
        "page": 1,
        "limit": 20,
        "total": 42,
        "has_more": True,
        "data": [{"id": "a", "name": "A"}],
    }


def test_envelope_empty_data_no_more():
    env = PaginationEnvelope[_Row](page=1, limit=20, total=0, has_more=False, data=[])
    assert env.model_dump(mode="json")["data"] == []
    assert env.model_dump(mode="json")["has_more"] is False


def test_envelope_has_more_true_when_total_exceeds_page_window():
    env = PaginationEnvelope[_Row].build(page=1, limit=20, total=42, items=[_Row(id="a", name="A")])
    assert env.has_more is True


def test_envelope_has_more_false_when_total_within_page_window():
    env = PaginationEnvelope[_Row].build(page=2, limit=20, total=22, items=[_Row(id="a", name="A")])
    assert env.has_more is False


def test_envelope_has_more_false_for_last_page():
    env = PaginationEnvelope[_Row].build(page=3, limit=20, total=42, items=[_Row(id="a", name="A")])
    assert env.has_more is False


def test_max_page_limit_is_200():
    from controllers.openapi._models import MAX_PAGE_LIMIT

    assert MAX_PAGE_LIMIT == 200


def test_envelope_uses_pep695_generics():
    """Verify the class accepts type parameter via PEP 695 syntax —
    i.e., model_fields surfaces the generic-parameterized data list."""
    from controllers.openapi._models import PaginationEnvelope

    Parameterized = PaginationEnvelope[dict]
    fields = PaginationEnvelope.model_fields
    assert {"page", "limit", "total", "has_more", "data"} <= set(fields)


def test_app_info_response_dump_matches_spec():
    from controllers.openapi._models import AppInfoResponse

    obj = AppInfoResponse(
        id="app1",
        name="X",
        description="d",
        mode="chat",
        author="alice",
        tags=[{"name": "prod"}],
    )
    assert obj.model_dump(mode="json") == {
        "id": "app1",
        "name": "X",
        "description": "d",
        "mode": "chat",
        "author": "alice",
        "tags": [{"name": "prod"}],
    }


def test_app_describe_response_nests_info_and_parameters():
    from controllers.openapi._models import AppDescribeInfo, AppDescribeResponse

    info = AppDescribeInfo(
        id="app1",
        name="X",
        mode="chat",
        description=None,
        tags=[],
        author=None,
        updated_at="2026-05-05T00:00:00+00:00",
        service_api_enabled=True,
    )
    obj = AppDescribeResponse(info=info, parameters={"opening_statement": None})
    dumped = obj.model_dump(mode="json")
    assert dumped["info"]["service_api_enabled"] is True
    assert dumped["parameters"]["opening_statement"] is None
