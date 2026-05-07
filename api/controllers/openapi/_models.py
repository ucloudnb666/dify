"""Shared response substructures for openapi endpoints."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Server-side cap on `limit` query param for any /openapi/v1/* list endpoint.
# Sibling endpoints (`/apps`, `/account/sessions`, future routes) all clamp to
# this; do not introduce per-endpoint caps without raising the constant.
MAX_PAGE_LIMIT = 200


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class MessageMetadata(BaseModel):
    usage: UsageInfo | None = None
    retriever_resources: list[dict[str, Any]] = []


class PaginationEnvelope[T](BaseModel):
    """Canonical pagination envelope for `/openapi/v1/*` list endpoints."""

    page: int
    limit: int
    total: int
    has_more: bool
    data: list[T]

    @classmethod
    def build(cls, *, page: int, limit: int, total: int, items: list[T]) -> PaginationEnvelope[T]:
        return cls(page=page, limit=limit, total=total, has_more=page * limit < total, data=items)


class AppListRow(BaseModel):
    id: str
    name: str
    description: str | None = None
    mode: str
    tags: list[dict[str, str]] = []
    updated_at: str | None = None
    created_by_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None


class AppInfoResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    mode: str
    author: str | None = None
    tags: list[dict[str, str]] = []


class AppDescribeInfo(AppInfoResponse):
    updated_at: str | None = None
    service_api_enabled: bool


class AppDescribeResponse(BaseModel):
    info: AppDescribeInfo | None = None
    parameters: dict[str, Any] | None = None
    input_schema: dict[str, Any] | None = None


class ChatMessageResponse(BaseModel):
    event: str
    task_id: str
    id: str
    message_id: str
    conversation_id: str
    mode: str
    answer: str
    metadata: MessageMetadata = Field(default_factory=MessageMetadata)
    created_at: int


class CompletionMessageResponse(BaseModel):
    event: str
    task_id: str
    id: str
    message_id: str
    mode: str
    answer: str
    metadata: MessageMetadata = Field(default_factory=MessageMetadata)
    created_at: int


class WorkflowRunData(BaseModel):
    id: str
    workflow_id: str
    status: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    elapsed_time: float | None = None
    total_tokens: int | None = None
    total_steps: int | None = None
    created_at: int | None = None
    finished_at: int | None = None


class WorkflowRunResponse(BaseModel):
    workflow_run_id: str
    task_id: str
    mode: Literal["workflow"] = "workflow"
    data: WorkflowRunData
