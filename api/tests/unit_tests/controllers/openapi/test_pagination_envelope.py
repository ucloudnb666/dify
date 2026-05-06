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
