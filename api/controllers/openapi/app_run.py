"""POST /openapi/v1/apps/<app_id>/run — mode-agnostic runner.

Server reads ``apps.mode`` after AppResolver and dispatches via
_DISPATCH to the per-mode helper. Per-mode constraints (e.g. chat-family
requires ``query``; workflow rejects ``query``) are enforced inside
the helper, post-resolve, since ``mode`` is not in the request body.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, Literal
from uuid import UUID

from flask import request
from flask_restx import Resource
from pydantic import BaseModel, ValidationError, field_validator
from werkzeug.exceptions import BadRequest, InternalServerError, NotFound, UnprocessableEntity

import services
from controllers.openapi import openapi_ns
from controllers.openapi._audit import emit_app_run
from controllers.openapi._models import (
    ChatMessageResponse,
    CompletionMessageResponse,
    WorkflowRunResponse,
)
from controllers.openapi.auth.composition import OAUTH_BEARER_PIPELINE
from controllers.service_api.app.error import (
    AppUnavailableError,
    CompletionRequestError,
    ConversationCompletedError,
    NotChatAppError,
    NotWorkflowAppError,
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


class AppRunRequest(BaseModel):
    inputs: dict[str, Any]
    query: str | None = None
    files: list[dict[str, Any]] | None = None
    response_mode: Literal["blocking", "streaming"] | None = None
    conversation_id: UUIDStrOrEmpty | None = None
    auto_generate_name: bool = True
    workflow_id: str | None = None

    @field_validator("conversation_id", mode="before")
    @classmethod
    def _normalize_conv(cls, value: str | UUID | None) -> str | None:
        if isinstance(value, str):
            value = value.strip()
        if not value:
            return None
        try:
            return helper.uuid_value(value)
        except ValueError as exc:
            raise ValueError("conversation_id must be a valid UUID") from exc


def _enforce_chat_constraint(payload: AppRunRequest) -> None:
    if not payload.query or not payload.query.strip():
        raise UnprocessableEntity("query_required_for_chat")


def _enforce_workflow_constraint(payload: AppRunRequest) -> None:
    if payload.query is not None:
        raise UnprocessableEntity("query_not_supported_for_workflow")


def _unpack_blocking(response: Any) -> Mapping[str, Any]:
    if isinstance(response, tuple):
        body_dict: Any = response[0]
    else:
        body_dict = response
    if not isinstance(body_dict, Mapping):
        raise InternalServerError("blocking generate returned non-mapping response")
    return dict(body_dict)


def _generate(app: App, caller: Any, args: dict[str, Any], streaming: bool):
    return AppGenerateService.generate(
        app_model=app,
        user=caller,
        args=args,
        invoke_from=InvokeFrom.OPENAPI,
        streaming=streaming,
    )


def _run_chat(app: App, caller: Any, payload: AppRunRequest, streaming: bool):
    _enforce_chat_constraint(payload)
    args = payload.model_dump(exclude_none=True)
    try:
        response = _generate(app, caller, args, streaming)
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

    if streaming:
        return response, None
    body = _unpack_blocking(response)
    return None, ChatMessageResponse.model_validate(body).model_dump(mode="json")


def _run_completion(app: App, caller: Any, payload: AppRunRequest, streaming: bool):
    args = payload.model_dump(exclude_none=True)
    # Completion mode disables auto-naming + tolerates absent query (legacy parity).
    args["auto_generate_name"] = False
    args.setdefault("query", "")
    try:
        response = _generate(app, caller, args, streaming)
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
    except InvokeError as e:
        raise CompletionRequestError(e.description)

    if streaming:
        return response, None
    body = _unpack_blocking(response)
    return None, CompletionMessageResponse.model_validate(body).model_dump(mode="json")


def _run_workflow(app: App, caller: Any, payload: AppRunRequest, streaming: bool):
    _enforce_workflow_constraint(payload)
    args = payload.model_dump(exclude={"query", "conversation_id", "auto_generate_name"}, exclude_none=True)
    try:
        response = _generate(app, caller, args, streaming)
    except WorkflowNotFoundError as ex:
        raise NotFound(str(ex))
    except (IsDraftWorkflowError, WorkflowIdFormatError) as ex:
        raise BadRequest(str(ex))
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

    if streaming:
        return response, None
    body = _unpack_blocking(response)
    return None, WorkflowRunResponse.model_validate(body).model_dump(mode="json")


_DISPATCH: dict[AppMode, Callable[[App, Any, AppRunRequest, bool], tuple[Any, dict[str, Any] | None]]] = {
    AppMode.CHAT: _run_chat,
    AppMode.AGENT_CHAT: _run_chat,
    AppMode.ADVANCED_CHAT: _run_chat,
    AppMode.COMPLETION: _run_completion,
    AppMode.WORKFLOW: _run_workflow,
}


@openapi_ns.route("/apps/<string:app_id>/run")
class AppRunApi(Resource):
    @OAUTH_BEARER_PIPELINE.guard(scope=Scope.APPS_RUN)
    def post(self, app_id: str, app_model: App, caller, caller_kind: str):
        body = request.get_json(silent=True) or {}
        body.pop("user", None)
        try:
            payload = AppRunRequest.model_validate(body)
        except ValidationError as exc:
            raise UnprocessableEntity(exc.json())

        mode = app_model.mode
        handler = _DISPATCH.get(mode)
        if handler is None:
            raise UnprocessableEntity("mode_not_runnable")

        streaming = payload.response_mode == "streaming"
        # Preserve specific HTTPException codes that the catch-all would otherwise mask.
        try:
            stream_obj, blocking_body = handler(app_model, caller, payload, streaming)
        except UnprocessableEntity:
            raise
        except (NotChatAppError, NotWorkflowAppError):
            raise
        except Exception:
            logger.exception("internal server error.")
            raise InternalServerError()

        emit_app_run(app_id=app_model.id, tenant_id=app_model.tenant_id,
                     caller_kind=caller_kind, mode=str(app_model.mode))

        if streaming:
            return helper.compact_generate_response(stream_obj)
        return blocking_body, 200
