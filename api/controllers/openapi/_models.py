"""Shared response substructures for openapi endpoints."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class MessageMetadata(BaseModel):
    usage: UsageInfo | None = None
    retriever_resources: list[dict[str, Any]] = []


class PaginationEnvelope(BaseModel, Generic[T]):  # noqa: UP046
    """Canonical pagination envelope for `/openapi/v1/*` list endpoints."""

    page: int
    limit: int
    total: int
    has_more: bool
    data: list[T]

    @classmethod
    def build(cls, *, page: int, limit: int, total: int, items: list[T]) -> PaginationEnvelope[T]:
        return cls(page=page, limit=limit, total=total, has_more=page * limit < total, data=items)
