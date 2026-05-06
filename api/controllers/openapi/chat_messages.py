"""POST /openapi/v1/apps/<app_id>/chat-messages — port of
service_api/app/completion.py:ChatApi.

Differences from service_api:
- App is in URL path, not header.
- One decorator: @OAUTH_BEARER_PIPELINE.guard(scope=Scope.APPS_RUN).
- Request body has no `user` field (Model 2: identity is the bearer).
- Typed Request and Response models.
- invoke_from = InvokeFrom.OPENAPI.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from flask import request
from flask_restx import Resource
from pydantic import BaseModel, Field, ValidationError, field_validator
from werkzeug.exceptions import BadRequest, InternalServerError, NotFound

import services
from controllers.openapi import openapi_ns
from controllers.openapi._audit import emit_app_run
from controllers.openapi._models import MessageMetadata
from controllers.openapi.auth.composition import OAUTH_BEARER_PIPELINE
from controllers.service_api.app.error import (
    AppUnavailableError,
    CompletionRequestError,
    ConversationCompletedError,
    NotChatAppError,
    ProviderModelCurrentlyNotSupportError,
    ProviderNotInitializeError,
    ProviderQuotaExceededError,
)
from controllers.web.error import InvokeRateLimitError as InvokeRateLimitHttpError
from core.app.entities.app_invoke_entities import InvokeFrom
from core.errors.error import (
    ModelCurrentlyNotSupportError,
    ProviderTokenNotInitError,
    QuotaExceededError,
)
from graphon.model_runtime.errors.invoke import InvokeError
from libs import helper
from libs.helper import UUIDStrOrEmpty
from libs.oauth_bearer import Scope
from models.model import App, AppMode
from services.app_generate_service import AppGenerateService
from services.errors.app import (
    IsDraftWorkflowError,
    WorkflowIdFormatError,
    WorkflowNotFoundError,
)
from services.errors.llm import InvokeRateLimitError

logger = logging.getLogger(__name__)


class ChatMessageRequest(BaseModel):
    inputs: dict[str, Any]
    query: str
    files: list[dict[str, Any]] | None = None
    response_mode: Literal["blocking", "streaming"] | None = None
    conversation_id: UUIDStrOrEmpty | None = Field(default=None)
    auto_generate_name: bool = Field(default=True)
    workflow_id: str | None = Field(default=None)

    @field_validator("conversation_id", mode="before")
    @classmethod
    def normalize_conversation_id(cls, value: str | UUID | None) -> str | None:
        if isinstance(value, str):
            value = value.strip()
        if not value:
            return None
        try:
            return helper.uuid_value(value)
        except ValueError as exc:
            raise ValueError("conversation_id must be a valid UUID") from exc


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


def _unpack_app(app_model):
    return app_model


def _unpack_caller(caller):
    return caller


@openapi_ns.route("/apps/<string:app_id>/chat-messages")
class ChatMessagesApi(Resource):
    @OAUTH_BEARER_PIPELINE.guard(scope=Scope.APPS_RUN)
    def post(self, app_id: str, app_model: App, caller, caller_kind: str):
        app = _unpack_app(app_model)
        if AppMode.value_of(app.mode) not in {
            AppMode.CHAT,
            AppMode.AGENT_CHAT,
            AppMode.ADVANCED_CHAT,
        }:
            raise NotChatAppError()

        body = request.get_json(silent=True) or {}
        body.pop("user", None)
        try:
            payload = ChatMessageRequest.model_validate(body)
        except ValidationError as exc:
            raise BadRequest(str(exc))
        args = payload.model_dump(exclude_none=True)
        streaming = payload.response_mode == "streaming"

        try:
            response = AppGenerateService.generate(
                app_model=app,
                user=_unpack_caller(caller),
                args=args,
                invoke_from=InvokeFrom.OPENAPI,
                streaming=streaming,
            )
        except WorkflowNotFoundError as ex:
            raise NotFound(str(ex))
        except (IsDraftWorkflowError, WorkflowIdFormatError) as ex:
            raise BadRequest(str(ex))
        except services.errors.conversation.ConversationNotExistsError:
            raise NotFound("Conversation Not Exists.")
        except services.errors.conversation.ConversationCompletedError:
            raise ConversationCompletedError()
        except services.errors.app_model_config.AppModelConfigBrokenError:
            logger.exception("App model config broken.")
            raise AppUnavailableError()
        except ProviderTokenNotInitError as ex:
            raise ProviderNotInitializeError(ex.description)
        except QuotaExceededError:
            raise ProviderQuotaExceededError()
        except ModelCurrentlyNotSupportError:
            raise ProviderModelCurrentlyNotSupportError()
        except InvokeRateLimitError as ex:
            raise InvokeRateLimitHttpError(ex.description)
        except InvokeError as e:
            raise CompletionRequestError(e.description)
        except ValueError:
            raise
        except Exception:
            logger.exception("internal server error.")
            raise InternalServerError()

        emit_app_run(
            app_id=app.id,
            tenant_id=app.tenant_id,
            caller_kind=caller_kind,
            mode=str(app.mode),
        )

        if streaming:
            return helper.compact_generate_response(response)

        # Some upstream paths (and tests) return (body, status); production
        # generate returns Mapping. Accept both, then validate.
        if isinstance(response, tuple):
            body_dict: Any = response[0]  # pyright: ignore[reportArgumentType]
        else:
            body_dict = response
        if not isinstance(body_dict, Mapping):
            raise InternalServerError("blocking generate returned non-mapping response")
        return ChatMessageResponse.model_validate(dict(body_dict)).model_dump(mode="json"), 200
